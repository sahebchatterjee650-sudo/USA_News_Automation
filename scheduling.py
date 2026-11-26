"""Adaptive YouTube publishing slots, constrained to US Eastern 08:00-22:00."""
from datetime import datetime, timedelta
from math import log, sqrt
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
SLOT_HOURS = tuple(range(8, 23, 2))
MIN_LEAD = timedelta(minutes=10)
RESERVATION_GAP = timedelta(minutes=60)


def choose_adaptive_slot(now, hourly_stats=None, reserved_times=None):
    """Choose a future ET slot using an upper-confidence-bound exploration score."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    now = now.astimezone(ET)
    hourly_stats = hourly_stats or {}
    reserved = []
    for value in reserved_times or []:
        if isinstance(value, str):
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if value.tzinfo is None:
            value = value.replace(tzinfo=ET)
        reserved.append(value.astimezone(ET))

    candidates = []
    for day_offset in range(3):
        day = (now + timedelta(days=day_offset)).date()
        for hour in SLOT_HOURS:
            candidate = datetime(day.year, day.month, day.day, hour, 0, tzinfo=ET)
            if candidate < now + MIN_LEAD:
                continue
            if any(abs(candidate - item) < RESERVATION_GAP for item in reserved):
                continue
            stats = hourly_stats.get(hour, {})
            count = int(stats.get("count", 0))
            views = int(stats.get("views", 0))
            average = views / count if count else 0.0
            candidates.append((candidate, count, average))

    if not candidates:
        raise RuntimeError("No unreserved US Eastern publishing slot is available in the next 3 days.")

    total_samples = max(1, sum(int(v.get("count", 0)) for v in hourly_stats.values()))
    # UCB explores hours with little data, then increasingly favors hours with
    # stronger average views. The exploration term prevents premature lock-in.
    def score(item):
        _, count, average = item
        if count == 0:
            return float("inf")
        return average + 1200.0 * sqrt(2.0 * log(total_samples + 1) / count)

    best_score = max(score(item) for item in candidates)
    tied = [item for item in candidates if score(item) == best_score]
    rotation = (now.timetuple().tm_yday + now.hour) % len(tied)
    selected = tied[rotation]
    return selected[0], {
        "hour_samples": selected[1],
        "hour_average_views": round(selected[2]),
        "candidate_count": len(candidates),
        "strategy": "exploration" if selected[1] == 0 else "performance_adaptive",
    }
