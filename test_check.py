import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import check
from check import ERROR, HIGH, NORMAL, RESTRICTED, UNCLEAR, URGENT, Reading, classify, decide

FIXTURES = Path(__file__).with_name("fixtures")
NOW = datetime(2026, 11, 5, 12, 0, tzinfo=timezone.utc)


def fixture(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


def page(category_d):
    return f"<h1>Staff Direction</h1><h3>CATEGORY C</h3><p>Do not report.</p><h3>CATEGORY D</h3><p>{category_d}</p>"


class ClassifyRealPages(unittest.TestCase):
    def test_restricted_page(self):
        reading = classify(fixture("feb2026_restricted.html"))
        self.assertEqual(reading.status, RESTRICTED)
        self.assertEqual(
            reading.text,
            "Do not report to site for evening shift on Friday, February 6 "
            "or for day shift on Saturday, February 7.",
        )
        self.assertEqual(reading.updated, "February 6, 2026 - 12:01:34 PM")

    def test_all_categories_reporting(self):
        reading = classify(fixture("jan2026_all_report.html"))
        self.assertEqual(reading.status, NORMAL)
        self.assertEqual(reading.text, "Report to work at regular start time.")

    def test_site_open_page(self):
        reading = classify(fixture("live_open.html"))
        self.assertEqual(reading.status, NORMAL)
        self.assertEqual(reading.text, "Bruce Power site is open to all staff.")

    def test_old_layout_without_category_d_is_not_guessed(self):
        # Categories A, B and C were told not to report, but D isn't mentioned.
        self.assertEqual(classify(fixture("feb2025_old_layout.html")).status, UNCLEAR)


class ClassifyEdgeCases(unittest.TestCase):
    def test_delayed_start_is_unclear(self):
        reading = classify(page("Delay your start by two hours."))
        self.assertEqual(reading.status, UNCLEAR)
        self.assertEqual(reading.text, "Delay your start by two hours.")

    def test_not_to_report_is_restricted(self):
        self.assertEqual(classify(page("You are directed not to report today.")).status, RESTRICTED)

    def test_regular_start_with_extra_conditions_is_unclear(self):
        self.assertEqual(classify(page("Report to site at regular start time on Monday.")).status, UNCLEAR)

    def test_empty_category_d_is_unclear(self):
        self.assertEqual(classify(page("")).status, UNCLEAR)

    def test_other_category_does_not_count_for_d(self):
        html = "<h1>Staff Direction</h1><h3>CATEGORY D</h3><p>Report to site at regular start time.</p><h3>CATEGORY C</h3><p>Do not report.</p>"
        self.assertEqual(classify(html).status, NORMAL)

    def test_commented_out_markup_is_ignored(self):
        html = "<h1>Staff Direction</h1><!-- <h3>CATEGORY D</h3><p>Do not report.</p> --><h2>Bruce Power site is open to all staff.</h2>"
        self.assertEqual(classify(html).status, NORMAL)

    def test_open_heading_does_not_override_category_d(self):
        html = "<h1>Staff Direction</h1><h2>Bruce Power site is open to all staff.</h2><h3>CATEGORY D</h3><p>Do not report to site.</p>"
        self.assertEqual(classify(html).status, RESTRICTED)

    def test_image_labelled_category_d(self):
        html = '<h1>Staff Direction</h1><img src="https://x/images/cat-c.png"><p>Report to work at regular start time.</p><img src="https://x/images/cat-d.png"><p>Do not report for work.</p>'
        self.assertEqual(classify(html).status, RESTRICTED)

    def test_unrelated_page_is_error(self):
        self.assertEqual(classify("<html><h1>Service Unavailable</h1></html>").status, ERROR)
        self.assertEqual(classify("").status, ERROR)


class Decide(unittest.TestCase):
    def state(self, status=NORMAL, text="open", **extra):
        return {"status": status, "text": text, "failures": 0, "failure_alerted": False,
                "last_heartbeat": NOW.isoformat(), **extra}

    def titles(self, notes):
        return [note.title for note in notes]

    def test_first_run_normal_is_silent(self):
        new, notes = decide({}, Reading(NORMAL, "open"), NOW)
        self.assertEqual(notes, [])
        self.assertEqual(new["status"], NORMAL)
        self.assertEqual(new["last_heartbeat"], NOW.isoformat())

    def test_first_run_restricted_alerts(self):
        _, notes = decide({}, Reading(RESTRICTED, "Do not report"), NOW)
        self.assertEqual(self.titles(notes), ["Category D: do not report to site"])

    def test_first_run_unclear_alerts(self):
        _, notes = decide({}, Reading(UNCLEAR, "Delay"), NOW)
        self.assertEqual(self.titles(notes), ["Check 2828: unclear direction"])

    def test_restriction_alert_is_urgent_with_page_text(self):
        _, notes = decide(self.state(), Reading(RESTRICTED, "Do not report", "Feb 6"), NOW)
        self.assertEqual(notes[0].priority, URGENT)
        self.assertIn("Do not report", notes[0].message)
        self.assertIn("Feb 6", notes[0].message)

    def test_same_restriction_does_not_repeat(self):
        state = self.state(RESTRICTED, "Do not report")
        new, notes = decide(state, Reading(RESTRICTED, "Do not report"), NOW)
        self.assertEqual(notes, [])
        self.assertEqual(new, state)

    def test_restriction_text_change_alerts(self):
        _, notes = decide(self.state(RESTRICTED, "Evening"), Reading(RESTRICTED, "Evening and day"), NOW)
        self.assertEqual(self.titles(notes), ["Category D direction updated"])
        self.assertEqual(notes[0].priority, URGENT)

    def test_unclear_text_change_alerts(self):
        _, notes = decide(self.state(UNCLEAR, "Delay 1h"), Reading(UNCLEAR, "Delay 2h"), NOW)
        self.assertEqual(notes[0].priority, HIGH)

    def test_same_unclear_does_not_repeat(self):
        _, notes = decide(self.state(UNCLEAR, "Delay"), Reading(UNCLEAR, "Delay"), NOW)
        self.assertEqual(notes, [])

    def test_back_to_normal(self):
        for before in (RESTRICTED, UNCLEAR):
            _, notes = decide(self.state(before, "x"), Reading(NORMAL, "open"), NOW)
            self.assertEqual(self.titles(notes), ["Back to normal"])

    def test_normal_rewording_is_silent(self):
        new, notes = decide(self.state(NORMAL, "open"), Reading(NORMAL, "Report to work"), NOW)
        self.assertEqual(notes, [])
        self.assertEqual(new["text"], "Report to work")

    def test_outage_keeps_status_and_alerts_once_at_threshold(self):
        state = self.state(RESTRICTED, "Do not report")
        titles = []
        for _ in range(5):
            state, notes = decide(state, Reading(ERROR, "down"), NOW)
            titles.append(self.titles(notes))
            self.assertEqual(state["status"], RESTRICTED)
            self.assertEqual(state["text"], "Do not report")
        self.assertEqual(titles, [[], [], ["Checker can't read 2828"], [], []])

    def test_short_outage_is_silent_and_does_not_end_restriction(self):
        state = self.state(RESTRICTED, "Do not report")
        state, _ = decide(state, Reading(ERROR, "down"), NOW)
        new, notes = decide(state, Reading(RESTRICTED, "Do not report"), NOW)
        self.assertEqual(notes, [])
        self.assertEqual(new["failures"], 0)

    def test_recovery_alerts_once(self):
        state = self.state(failures=3, failure_alerted=True)
        state, notes = decide(state, Reading(NORMAL, "open"), NOW)
        self.assertEqual(self.titles(notes), ["Checker recovered"])
        _, notes = decide(state, Reading(NORMAL, "open"), NOW)
        self.assertEqual(notes, [])

    def test_weekly_heartbeat(self):
        old = (NOW - timedelta(days=7)).isoformat()
        new, notes = decide(self.state(last_heartbeat=old), Reading(NORMAL, "open"), NOW)
        self.assertEqual(self.titles(notes), ["Still watching 2828"])
        self.assertEqual(new["last_heartbeat"], NOW.isoformat())

    def test_no_heartbeat_before_a_week(self):
        recent = (NOW - timedelta(days=6)).isoformat()
        _, notes = decide(self.state(last_heartbeat=recent), Reading(NORMAL, "open"), NOW)
        self.assertEqual(notes, [])


class Main(unittest.TestCase):
    def run_main(self, state_path, send):
        args = ["check.py", "--file", str(FIXTURES / "feb2026_restricted.html"), "--state", str(state_path)]
        with mock.patch("sys.argv", args), mock.patch.dict("os.environ", {"NTFY_TOPIC": "t"}), \
                mock.patch("check.send", send), mock.patch("builtins.print"):
            return check.main()

    def test_failed_send_is_retried_on_next_run(self):
        import tempfile

        with tempfile.TemporaryDirectory() as folder:
            state_path = Path(folder) / "state.json"
            failing = mock.Mock(side_effect=OSError("ntfy down"))
            self.assertEqual(self.run_main(state_path, failing), 1)
            self.assertFalse(state_path.exists())

            working = mock.Mock()
            self.assertEqual(self.run_main(state_path, working), 0)
            self.assertEqual(working.call_count, 1)
            self.assertEqual(check.load_state(state_path)["status"], RESTRICTED)

            # Already delivered, so a third run stays quiet.
            again = mock.Mock()
            self.assertEqual(self.run_main(state_path, again), 0)
            again.assert_not_called()


if __name__ == "__main__":
    unittest.main()
