import datetime as dt
import unittest
from zoneinfo import ZoneInfo

import schedule_guard


def to_github_timestamp(local_dt: dt.datetime) -> str:
    return local_dt.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def make_run(run_id: int, run_number: int, local_dt: dt.datetime) -> dict:
    return {
        "id": run_id,
        "run_number": run_number,
        "status": "completed",
        "conclusion": "success",
        "run_started_at": to_github_timestamp(local_dt),
    }


class ScheduleGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.timezone = ZoneInfo("Asia/Shanghai")
        self.morning_brief_time = schedule_guard.parse_hhmm("09:36", "09:36")
        self.evening_brief_time = schedule_guard.parse_hhmm("22:00", "22:00")

    def evaluate(
        self,
        now_local: dt.datetime,
        runs: list[dict],
        *,
        event_name: str = "schedule",
    ) -> schedule_guard.GuardDecision:
        return schedule_guard.evaluate_schedule_guard(
            event_name=event_name,
            now_local=now_local,
            current_run_id=9999,
            runs=runs,
            morning_brief_time=self.morning_brief_time,
            evening_brief_time=self.evening_brief_time,
            timezone=self.timezone,
        )

    def test_morning_run_can_be_sent_late_in_same_day(self) -> None:
        now_local = dt.datetime(2026, 3, 19, 12, 3, tzinfo=self.timezone)

        decision = self.evaluate(now_local, [])

        self.assertTrue(decision.should_send)
        self.assertEqual(decision.slot, "morning")

    def test_duplicate_morning_run_is_skipped(self) -> None:
        now_local = dt.datetime(2026, 3, 19, 12, 26, tzinfo=self.timezone)
        runs = [make_run(1001, 101, dt.datetime(2026, 3, 19, 10, 5, tzinfo=self.timezone))]

        decision = self.evaluate(now_local, runs)

        self.assertFalse(decision.should_send)
        self.assertEqual(decision.slot, "morning")
        self.assertIn("already sent in morning slot", decision.reason)

    def test_evening_run_can_be_sent_late_in_same_day(self) -> None:
        now_local = dt.datetime(2026, 3, 19, 22, 48, tzinfo=self.timezone)
        runs = [make_run(1002, 110, dt.datetime(2026, 3, 19, 12, 3, tzinfo=self.timezone))]

        decision = self.evaluate(now_local, runs)

        self.assertTrue(decision.should_send)
        self.assertEqual(decision.slot, "evening")

    def test_run_before_morning_time_is_skipped(self) -> None:
        now_local = dt.datetime(2026, 3, 19, 9, 0, tzinfo=self.timezone)

        decision = self.evaluate(now_local, [])

        self.assertFalse(decision.should_send)
        self.assertEqual(decision.slot, "waiting")
        self.assertIn("has not arrived yet", decision.reason)

    def test_next_day_does_not_backfill_previous_evening(self) -> None:
        now_local = dt.datetime(2026, 3, 20, 0, 10, tzinfo=self.timezone)
        runs = [make_run(1003, 120, dt.datetime(2026, 3, 19, 22, 30, tzinfo=self.timezone))]

        decision = self.evaluate(now_local, runs)

        self.assertFalse(decision.should_send)
        self.assertEqual(decision.slot, "waiting")

    def test_manual_trigger_bypasses_schedule_dedupe(self) -> None:
        now_local = dt.datetime(2026, 3, 19, 0, 10, tzinfo=self.timezone)
        runs = [make_run(1004, 130, dt.datetime(2026, 3, 19, 22, 30, tzinfo=self.timezone))]

        decision = self.evaluate(now_local, runs, event_name="workflow_dispatch")

        self.assertTrue(decision.should_send)
        self.assertEqual(decision.slot, "manual")


if __name__ == "__main__":
    unittest.main()
