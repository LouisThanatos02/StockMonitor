"""Small, dependency-free helpers for timeframe candle handling."""

from datetime import date, datetime, time, timedelta
import math
from zoneinfo import ZoneInfo


EXCHANGE_ZONES = {
    "TW": (ZoneInfo("Asia/Taipei"), time(9, 0), time(13, 30)),
    "US": (ZoneInfo("America/New_York"), time(9, 30), time(16, 0)),
}


def exchange_for_ticker(ticker):
    return "TW" if ticker.upper().endswith((".TW", ".TWO")) else "US"


def _local_datetime(value, zone):
    if isinstance(value, date) and not isinstance(value, datetime):
        return datetime.combine(value, time.min, zone)
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if value.tzinfo is None:
        return value.replace(tzinfo=zone)
    return value.astimezone(zone)


def aggregate_daily_bars(rows, interval, ticker, now=None, session_closes=None):
    """Aggregate daily bars into Monday-to-Sunday weeks or calendar months.

    The current period is included only when its completion can be confirmed
    from the local clock and observed daily bars. Missing trading days are not
    synthesized; ambiguous holiday periods remain excluded until the next
    period provides proof that they ended.
    """
    if interval not in ("1wk", "1mo"):
        raise ValueError("Only weekly and monthly aggregation is supported")

    exchange = exchange_for_ticker(ticker)
    zone = EXCHANGE_ZONES[exchange][0]
    now = _local_datetime(now or datetime.now(zone), zone)
    current_key = (now.year, now.month) if interval == "1mo" else (now - timedelta(days=now.weekday())).date()
    actual_closes = {}
    for raw_date, raw_close in (session_closes or {}).items():
        local_date = date.fromisoformat(raw_date) if isinstance(raw_date, str) else raw_date
        if isinstance(raw_close, datetime):
            raw_close = raw_close.timetz().replace(tzinfo=None)
        elif isinstance(raw_close, str):
            raw_close = time.fromisoformat(raw_close)
        actual_closes[local_date] = raw_close
    current_period_rows = {}
    for source in rows:
        stamp = _local_datetime(dict(source)["Date"], zone)
        key = (stamp.year, stamp.month) if interval == "1mo" else stamp.date() - timedelta(days=stamp.weekday())
        current_period_rows.setdefault(key, []).append(stamp.date())

    def current_period_is_complete(key):
        dates = current_period_rows.get(key, [])
        if not dates:
            return False
        last_observed = max(dates)
        if interval == "1wk":
            friday = key + timedelta(days=4)
            if now.date() > friday:
                # The week is over. If Friday has no bar (holiday or missing
                # source data), do not guess which case it was.
                return last_observed == friday
            close_time = actual_closes.get(friday, EXCHANGE_ZONES[exchange][2])
            return now.date() == friday and now.timetz().replace(tzinfo=None) >= close_time and last_observed == friday

        year, month = key
        month_end = (date(year + (month == 12), 1 if month == 12 else month + 1, 1) - timedelta(days=1))
        if month_end.weekday() >= 5:
            # A weekend month end has no possible remaining session. Require
            # the final weekday bar; otherwise missing data and a holiday
            # cannot be distinguished safely.
            final_weekday = month_end - timedelta(days=month_end.weekday() - 4)
            if now.date() < final_weekday:
                return False
            close_time = actual_closes.get(final_weekday, EXCHANGE_ZONES[exchange][2])
            if now.date() == final_weekday and now.timetz().replace(tzinfo=None) < close_time:
                return False
            return last_observed == final_weekday
        if now.date() < month_end:
            return False
        close_time = actual_closes.get(month_end, EXCHANGE_ZONES[exchange][2])
        if now.date() == month_end and now.timetz().replace(tzinfo=None) < close_time:
            return False
        return last_observed == month_end

    groups = {}

    for source in rows:
        row = dict(source)
        stamp = _local_datetime(row["Date"], zone)
        session_date = stamp.date()
        if interval == "1mo":
            key = (session_date.year, session_date.month)
            label = date(session_date.year, session_date.month, 1)
            incomplete = key > current_key or (key == current_key and not current_period_is_complete(key))
        else:
            label = session_date - timedelta(days=session_date.weekday())
            key = label
            incomplete = label > current_key or (label == current_key and not current_period_is_complete(key))
        if incomplete:
            continue

        bucket = groups.get(key)
        volume = row.get("Volume")
        if bucket is None:
            groups[key] = {
                "Date": label,
                "Open": row.get("Open"),
                "High": row.get("High"),
                "Low": row.get("Low"),
                "Close": row.get("Close"),
                "Volume": volume,
            }
        else:
            bucket["High"] = max(bucket["High"], row["High"])
            bucket["Low"] = min(bucket["Low"], row["Low"])
            bucket["Close"] = row["Close"]
            if bucket["Volume"] is None or volume is None:
                bucket["Volume"] = None
            else:
                bucket["Volume"] += volume

    return [groups[key] for key in sorted(groups)]


def remove_incomplete_hourly_bars(rows, ticker, now=None, session_closes=None):
    """Keep only full 60-minute bars within confirmed regular sessions.

    ``session_closes`` may map local session dates (date or ISO string) to
    actual local close times from the provider's trading-period metadata.
    When metadata is absent, an ambiguous final bar before the normal closing
    interval is conservatively dropped (it may belong to an early-close day).
    """
    exchange = exchange_for_ticker(ticker)
    zone, open_time, close_time = EXCHANGE_ZONES[exchange]
    now = _local_datetime(now or datetime.now(zone), zone)
    normalized = []
    latest_by_date = {}
    actual_closes = {}
    for raw_date, raw_close in (session_closes or {}).items():
        local_date = date.fromisoformat(raw_date) if isinstance(raw_date, str) else raw_date
        if isinstance(raw_close, datetime):
            raw_close = raw_close.timetz().replace(tzinfo=None)
        elif isinstance(raw_close, str):
            raw_close = time.fromisoformat(raw_close)
        actual_closes[local_date] = raw_close

    for source in rows:
        row = dict(source)
        stamp = _local_datetime(row["Date"], zone)
        normalized.append((row, stamp))
        session_close_time = actual_closes.get(stamp.date(), close_time)
        session_open = datetime.combine(stamp.date(), open_time, zone)
        session_close = datetime.combine(stamp.date(), session_close_time, zone)
        if session_open <= stamp < session_close and (stamp.date() not in latest_by_date or stamp > latest_by_date[stamp.date()]):
            latest_by_date[stamp.date()] = stamp

    result = []
    for row, stamp in normalized:
        session_close_time = actual_closes.get(stamp.date(), close_time)
        session_open = datetime.combine(stamp.date(), open_time, zone)
        session_close = datetime.combine(stamp.date(), session_close_time, zone)
        if not session_open <= stamp < session_close:
            continue
        candle_end = stamp + timedelta(hours=1)
        if candle_end > session_close or candle_end > now:
            continue

        # With no provider schedule, an early close cannot be distinguished
        # from an incomplete final interval. Drop only that ambiguous last row;
        # confirmed full bars earlier in the same session remain available.
        if stamp == latest_by_date[stamp.date()] and stamp.date() not in actual_closes:
            normal_close = datetime.combine(stamp.date(), close_time, zone)
            last_normal_full_start = normal_close - timedelta(hours=1)
            if stamp < last_normal_full_start:
                continue
        result.append(row)
    return result


def volume_breakout_a8(volumes):
    """A8: current volume strictly exceeds 1.5 times the prior six-bar mean."""
    values = list(volumes)
    if len(values) < 7:
        return None
    try:
        if any(value is None or not math.isfinite(float(value)) for value in values[-7:]):
            return None
        prior_six_mean = sum(values[-7:-1]) / 6
        return values[-1] > prior_six_mean * 1.5
    except (TypeError, ValueError):
        return None


def display_time(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat(timespec="minutes") if isinstance(value, datetime) else value.isoformat()
    return str(value)
