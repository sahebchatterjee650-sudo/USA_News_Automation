import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from scheduling import choose_adaptive_slot

ET = ZoneInfo("America/New_York")


class AdaptiveSchedulingTests(unittest.TestCase):
    def test_slot_is_inside_eastern_window(self):
        slot, _ = choose_adaptive_slot(datetime(2026, 11, 26, 7, 30, tzinfo=ET))
        self.assertEqual(slot.tzinfo, ET)
        self.assertIn(slot.hour, range(8, 23, 2))
        self.assertGreaterEqual(slot, datetime(2026, 11, 26, 7, 40, tzinfo=ET))

    def test_explores_an_unmeasured_hour(self):
        stats = {hour: {"count": 3, "views": 900} for hour in range(8, 23, 2)}
        stats[12] = {"count": 0, "views": 0}
        slot, details = choose_adaptive_slot(
            datetime(2026, 11, 26, 7, 0, tzinfo=ET), stats
        )
        self.assertEqual(slot.hour, 12)
        self.assertEqual(details["strategy"], "exploration")

    def test_prefers_stronger_measured_hour_when_all_sampled(self):
        stats = {hour: {"count": 6, "views": 600} for hour in range(8, 23, 2)}
        stats[10] = {"count": 6, "views": 60000}
        slot, details = choose_adaptive_slot(
            datetime(2026, 11, 26, 7, 0, tzinfo=ET), stats
        )
        self.assertEqual(slot.hour, 10)
        self.assertEqual(details["strategy"], "performance_adaptive")

    def test_avoids_existing_scheduled_slot(self):
        reserved = ["2026-11-26T08:00:00-05:00"]
        slot, _ = choose_adaptive_slot(
            datetime(2026, 11, 26, 7, 0, tzinfo=ET), reserved_times=reserved
        )
        self.assertNotEqual(slot, datetime(2026, 11, 26, 8, 0, tzinfo=ET))


if __name__ == "__main__":
    unittest.main()
