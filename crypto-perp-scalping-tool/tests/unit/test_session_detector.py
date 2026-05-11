import unittest
from datetime import datetime, timezone

from crypto_perp_tool.session import Session, SessionDetector


def _ts(year: int, month: int, day: int, hour: int, minute: int = 0) -> int:
    dt = datetime(year, month, day, hour, minute, tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


class SessionDetectorTests(unittest.TestCase):
    def setUp(self):
        self.detector = SessionDetector()

    def test_detect_asia_session(self):
        ts = _ts(2026, 5, 12, 3, 0)  # Tuesday UTC 03:00
        self.assertEqual(self.detector.detect(ts), Session.ASIA)

    def test_detect_london_session(self):
        ts = _ts(2026, 5, 12, 10, 0)  # Tuesday UTC 10:00
        self.assertEqual(self.detector.detect(ts), Session.LONDON)

    def test_detect_london_boundary_start(self):
        ts = _ts(2026, 5, 12, 7, 0)  # UTC 07:00 exactly
        self.assertEqual(self.detector.detect(ts), Session.LONDON)

    def test_detect_london_boundary_end(self):
        ts = _ts(2026, 5, 12, 12, 0)  # UTC 12:00, before 12:30
        self.assertEqual(self.detector.detect(ts), Session.LONDON)

    def test_detect_ny_session(self):
        ts = _ts(2026, 5, 12, 15, 0)  # Tuesday UTC 15:00
        self.assertEqual(self.detector.detect(ts), Session.NY)

    def test_detect_ny_boundary_start(self):
        ts = _ts(2026, 5, 12, 12, 30)  # UTC 12:30 exactly
        self.assertEqual(self.detector.detect(ts), Session.NY)

    def test_detect_ny_boundary_end(self):
        ts = _ts(2026, 5, 12, 19, 59)  # just before 20:00
        self.assertEqual(self.detector.detect(ts), Session.NY)

    def test_detect_dead_zone(self):
        ts = _ts(2026, 5, 12, 22, 0)  # Tuesday UTC 22:00
        self.assertEqual(self.detector.detect(ts), Session.DEAD)

    def test_detect_dead_zone_at_midnight(self):
        ts = _ts(2026, 5, 12, 23, 59)  # just before next day
        self.assertEqual(self.detector.detect(ts), Session.DEAD)

    def test_weekend_overrides_to_asia(self):
        ts = _ts(2026, 5, 16, 15, 0)  # Saturday UTC 15:00 (normally NY)
        self.assertEqual(self.detector.detect(ts), Session.ASIA)

    def test_sunday_overrides_to_asia(self):
        ts = _ts(2026, 5, 17, 10, 0)  # Sunday UTC 10:00 (normally London)
        self.assertEqual(self.detector.detect(ts), Session.ASIA)

    def test_is_mean_reverting(self):
        self.assertTrue(self.detector.is_mean_reverting(Session.ASIA))
        self.assertTrue(self.detector.is_mean_reverting(Session.DEAD))
        self.assertFalse(self.detector.is_mean_reverting(Session.LONDON))
        self.assertFalse(self.detector.is_mean_reverting(Session.NY))

    def test_is_trend_following(self):
        self.assertTrue(self.detector.is_trend_following(Session.NY))
        self.assertFalse(self.detector.is_trend_following(Session.ASIA))
        self.assertFalse(self.detector.is_trend_following(Session.LONDON))
        self.assertFalse(self.detector.is_trend_following(Session.DEAD))

    def test_custom_session_hours(self):
        detector = SessionDetector(
            asia_start_hour=2, asia_end_hour=8,
            london_start_hour=8, london_end_hour=13,
            ny_start_hour=13, ny_start_minute=0, ny_end_hour=21,
        )
        ts = _ts(2026, 5, 12, 1, 0)  # UTC 01:00, before custom asia start
        self.assertEqual(detector.detect(ts), Session.DEAD)
        ts = _ts(2026, 5, 12, 3, 0)  # UTC 03:00, within custom asia
        self.assertEqual(detector.detect(ts), Session.ASIA)


if __name__ == "__main__":
    unittest.main()
