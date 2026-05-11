from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum


class Session(StrEnum):
    ASIA = "asia"
    LONDON = "london"
    NY = "ny"
    DEAD = "dead"


_MEAN_REVERTING_SESSIONS = frozenset({Session.ASIA, Session.DEAD})
_TREND_FOLLOWING_SESSIONS = frozenset({Session.NY})


class SessionDetector:
    def __init__(
        self,
        asia_start_hour: int = 0,
        asia_end_hour: int = 7,
        london_start_hour: int = 7,
        london_end_hour: int = 12,
        london_end_minute: int = 30,
        ny_start_hour: int = 12,
        ny_start_minute: int = 30,
        ny_end_hour: int = 20,
    ) -> None:
        self._asia_range = (asia_start_hour * 60, asia_end_hour * 60)
        self._london_range = (london_start_hour * 60, london_end_hour * 60 + london_end_minute)
        self._ny_range = (ny_start_hour * 60 + ny_start_minute, ny_end_hour * 60)

    def detect(self, timestamp_ms: int) -> Session:
        dt = datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc)
        if dt.weekday() >= 5:
            return Session.ASIA
        minutes = dt.hour * 60 + dt.minute
        if self._asia_range[0] <= minutes < self._asia_range[1]:
            return Session.ASIA
        if self._london_range[0] <= minutes < self._london_range[1]:
            return Session.LONDON
        if self._ny_range[0] <= minutes < self._ny_range[1]:
            return Session.NY
        return Session.DEAD

    def is_mean_reverting(self, session: Session) -> bool:
        return session in _MEAN_REVERTING_SESSIONS

    def is_trend_following(self, session: Session) -> bool:
        return session in _TREND_FOLLOWING_SESSIONS
