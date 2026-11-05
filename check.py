#!/usr/bin/env python3
"""Push a phone alert when 2828.brucepower.com changes direction for Category D staff."""
import argparse
import http.client
import json
import os
import re
import sys
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path

SITE_URL = "https://2828.brucepower.com/"
NTFY_URL = "https://ntfy.sh/"
USER_AGENT = "bruce-2828-alert/1.0 (personal status checker)"
STATE_FILE = Path(__file__).with_name("state.json")

FAILURES_BEFORE_ALERT = 3
HEARTBEAT_EVERY = timedelta(days=7)

RESTRICTED = "RESTRICTED"
NORMAL = "NORMAL"
UNCLEAR = "UNCLEAR"
ERROR = "ERROR"

# ntfy priority levels
LOW, DEFAULT, HIGH, URGENT = 2, 3, 4, 5

NOT_REPORTING = re.compile(r"\bnot\s+(?:to\s+)?report\b", re.I)
REGULAR_START = re.compile(r"report to (?:site|work) at regular start time\.?", re.I)
SITE_OPEN = re.compile(r"site is open to all staff", re.I)
LAST_UPDATE = re.compile(r"(?:last update|current as of):\s*(.+)", re.I)
CATEGORY_D_HEADING = re.compile(r"category\s+d\b", re.I)
CATEGORY_D_IMAGE = re.compile(r"/cat-d\.\w+$", re.I)


@dataclass
class Reading:
    status: str
    text: str = ""
    updated: str = ""


@dataclass
class Notification:
    title: str
    message: str
    priority: int


def clean(text):
    # Page text contains non-breaking and zero-width spaces.
    return " ".join(text.replace("​", " ").split())


class BlockParser(HTMLParser):
    """Collects headings and paragraphs in page order. HTML comments are never collected."""

    HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}

    def __init__(self):
        super().__init__()
        self.blocks = []  # (tag, text)
        self._tag = None
        self._parts = []

    def _close(self):
        if self._tag:
            self.blocks.append((self._tag, clean("".join(self._parts))))
        self._tag = None
        self._parts = []

    def handle_starttag(self, tag, attrs):
        if tag in self.HEADINGS or tag == "p":
            self._close()
            self._tag = tag
        elif tag == "br":
            self._parts.append(" ")
        elif tag == "img" and CATEGORY_D_IMAGE.search(dict(attrs).get("src") or ""):
            # Older page layouts label each category with an image, not a heading.
            self._close()
            self.blocks.append(("h3", "CATEGORY D"))

    def handle_endtag(self, tag):
        if tag == self._tag:
            self._close()

    def handle_data(self, data):
        if self._tag:
            self._parts.append(data)

    def close(self):
        super().close()
        self._close()


def category_d_text(blocks):
    """Return the paragraphs under the Category D heading, or None if there is no such heading."""
    for i, (tag, text) in enumerate(blocks):
        if tag != "p" and CATEGORY_D_HEADING.match(text):
            paragraphs = []
            for next_tag, next_text in blocks[i + 1 :]:
                if next_tag != "p":
                    break
                paragraphs.append(next_text)
            return clean(" ".join(paragraphs))
    return None


def classify(html):
    parser = BlockParser()
    parser.feed(html)
    parser.close()
    blocks = parser.blocks
    headings = [text for tag, text in blocks if tag != "p"]

    if not any("staff direction" in text.lower() for text in headings):
        return Reading(ERROR, "Page has no Staff Direction heading")

    updated = ""
    for text in headings:
        match = LAST_UPDATE.search(text)
        if match:
            updated = match.group(1)
            break

    text = category_d_text(blocks)
    if text is not None:
        if NOT_REPORTING.search(text):
            return Reading(RESTRICTED, text, updated)
        if REGULAR_START.fullmatch(text):
            return Reading(NORMAL, text, updated)
        return Reading(UNCLEAR, text, updated)

    for heading in headings:
        if SITE_OPEN.search(heading):
            return Reading(NORMAL, heading, updated)
    return Reading(UNCLEAR, "The page has no Category D section.", updated)


def restricted_alert(title, reading):
    return Notification(title, _body(reading), URGENT)


def unclear_alert(reading):
    return Notification("Check 2828: unclear direction", _body(reading), HIGH)


def _body(reading):
    if reading.updated:
        return f"{reading.text}\nLast update: {reading.updated}"
    return reading.text


def decide(state, reading, now):
    """Return (new_state, notifications) for this check. Does no I/O."""
    new = dict(state)
    notes = []

    if reading.status == ERROR:
        new["failures"] = state.get("failures", 0) + 1
        if new["failures"] >= FAILURES_BEFORE_ALERT and not state.get("failure_alerted"):
            new["failure_alerted"] = True
            notes.append(Notification(
                "Checker can't read 2828",
                f"{reading.text}\nAlerts may be missed until this clears, so check the site yourself.",
                HIGH,
            ))
    else:
        if state.get("failure_alerted"):
            notes.append(Notification("Checker recovered", "2828 can be read again.", DEFAULT))
        new["failures"] = 0
        new["failure_alerted"] = False

        before = state.get("status")
        changed = reading.status != before
        reworded = reading.text != state.get("text")
        if reading.status == RESTRICTED and changed:
            notes.append(restricted_alert("Category D: do not report to site", reading))
        elif reading.status == RESTRICTED and reworded:
            notes.append(restricted_alert("Category D direction updated", reading))
        elif reading.status == UNCLEAR and (changed or reworded):
            notes.append(unclear_alert(reading))
        elif reading.status == NORMAL and before in (RESTRICTED, UNCLEAR):
            notes.append(Notification("Back to normal", _body(reading), DEFAULT))
        new["status"] = reading.status
        new["text"] = reading.text

    last = state.get("last_heartbeat")
    if last is None:
        new["last_heartbeat"] = now.isoformat()
    elif now - datetime.fromisoformat(last) >= HEARTBEAT_EVERY:
        new["last_heartbeat"] = now.isoformat()
        current = new.get("status", "not read yet")
        notes.append(Notification("Still watching 2828", f"Currently: {current}", LOW))

    return new, notes


def fetch_page():
    request = urllib.request.Request(
        SITE_URL, headers={"User-Agent": USER_AGENT, "Cache-Control": "no-cache"}
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8", errors="replace")


def send(note, topic):
    payload = {
        "topic": topic,
        "title": note.title,
        "message": note.message,
        "priority": note.priority,
        "click": SITE_URL,
    }
    request = urllib.request.Request(
        NTFY_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
    )
    with urllib.request.urlopen(request, timeout=30):
        pass


def load_state(path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def save_state(path, state):
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def read_site(file):
    if file:
        return classify(Path(file).read_text(encoding="utf-8"))
    try:
        return classify(fetch_page())
    except (OSError, http.client.HTTPException) as error:  # OSError covers URLError and timeouts
        return Reading(ERROR, f"Could not fetch the page: {error}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", help="read this saved page instead of the live site")
    parser.add_argument("--state", type=Path, default=STATE_FILE, help="state file to use")
    parser.add_argument("--dry-run", action="store_true", help="print alerts; send and save nothing")
    args = parser.parse_args()

    state = load_state(args.state)
    reading = read_site(args.file)
    print(f"Status: {reading.status} | {reading.text}")
    new_state, notes = decide(state, reading, datetime.now(timezone.utc))

    if args.dry_run:
        for note in notes:
            print(f"Would send [{note.priority}] {note.title}: {note.message}")
        return 0

    if notes:
        topic = os.environ.get("NTFY_TOPIC")
        if not topic:
            print("NTFY_TOPIC is not set, so alerts can't be sent.", file=sys.stderr)
            return 1
        for note in notes:
            try:
                send(note, topic)
            except (OSError, http.client.HTTPException) as error:
                # State stays unsaved so the next run sends this alert again.
                print(f"Could not send '{note.title}': {error}", file=sys.stderr)
                return 1
            print(f"Sent: {note.title}")

    save_state(args.state, new_state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
