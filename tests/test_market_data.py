import unittest
from datetime import date, datetime
from zoneinfo import ZoneInfo

from market_data import aggregate_daily_bars, remove_incomplete_hourly_bars, volume_breakout_a8


class AggregateDailyBarsTests(unittest.TestCase):
    def setUp(self):
        self.rows = [
            {"Date": date(2024, 1, 2), "Open": 10, "High": 12, "Low": 9, "Close": 11, "Volume": 100},
            {"Date": date(2024, 1, 4), "Open": 11, "High": 14, "Low": 10, "Close": 13, "Volume": 150},
            {"Date": date(2024, 1, 8), "Open": 13, "High": 15, "Low": 12, "Close": 14, "Volume": 200},
            {"Date": date(2024, 1, 9), "Open": 14, "High": 16, "Low": 13, "Close": 15, "Volume": 250},
            {"Date": date(2024, 2, 1), "Open": 15, "High": 18, "Low": 14, "Close": 17, "Volume": 300},
            {"Date": date(2024, 2, 2), "Open": 17, "High": 19, "Low": 16, "Close": 18, "Volume": 350},
            # Current week/month is deliberately excluded.
            {"Date": date(2024, 2, 5), "Open": 18, "High": 22, "Low": 17, "Close": 21, "Volume": 400},
            {"Date": date(2024, 3, 1), "Open": 21, "High": 25, "Low": 20, "Close": 24, "Volume": 500},
        ]

    def test_weekly_ohlcv_and_incomplete_week(self):
        now = datetime(2024, 2, 7, 12, tzinfo=ZoneInfo("Asia/Taipei"))
        bars = aggregate_daily_bars(self.rows, "1wk", "2330.TW", now)
        self.assertEqual(len(bars), 3)
        self.assertEqual(bars[0], {"Date": date(2024, 1, 1), "Open": 10, "High": 14, "Low": 9, "Close": 13, "Volume": 250})
        self.assertEqual(bars[1], {"Date": date(2024, 1, 8), "Open": 13, "High": 16, "Low": 12, "Close": 15, "Volume": 450})
        self.assertEqual(bars[2], {"Date": date(2024, 1, 29), "Open": 15, "High": 19, "Low": 14, "Close": 18, "Volume": 650})

    def test_weekly_period_completes_after_friday_close_and_on_weekend(self):
        zone = ZoneInfo("Asia/Taipei")
        friday = datetime(2024, 2, 2, 13, 31, tzinfo=zone)
        weekend = datetime(2024, 2, 4, 10, 0, tzinfo=zone)
        friday_bars = aggregate_daily_bars(self.rows, "1wk", "2330.TW", friday)
        weekend_bars = aggregate_daily_bars(self.rows, "1wk", "2330.TW", weekend)
        self.assertEqual(friday_bars[-1]["Date"], date(2024, 1, 29))
        self.assertEqual(weekend_bars[-1]["Date"], date(2024, 1, 29))

    def test_us_week_uses_new_york_local_close(self):
        zone = ZoneInfo("America/New_York")
        rows = [
            {"Date": date(2024, 5, 31), "Open": 10, "High": 12, "Low": 9, "Close": 11, "Volume": 100},
        ]
        before_close = aggregate_daily_bars(rows, "1wk", "SPY", datetime(2024, 5, 31, 15, 59, tzinfo=zone))
        after_close = aggregate_daily_bars(rows, "1wk", "SPY", datetime(2024, 5, 31, 16, 1, tzinfo=zone))
        self.assertEqual(before_close, [])
        self.assertEqual(after_close[-1]["Date"], date(2024, 5, 27))

    def test_us_early_close_completes_week_and_weekend_month(self):
        zone = ZoneInfo("America/New_York")
        rows = [
            {"Date": date(2024, 11, 29), "Open": 10, "High": 12, "Low": 9, "Close": 11, "Volume": 100},
        ]
        session_closes = {date(2024, 11, 29): datetime(2024, 11, 29, 13, 0, tzinfo=zone).time()}
        before_close = datetime(2024, 11, 29, 12, 59, tzinfo=zone)
        after_close = datetime(2024, 11, 29, 13, 1, tzinfo=zone)
        self.assertEqual(aggregate_daily_bars(rows, "1wk", "SPY", before_close, session_closes), [])
        self.assertEqual(aggregate_daily_bars(rows, "1wk", "SPY", after_close, session_closes)[-1]["Date"], date(2024, 11, 25))
        self.assertEqual(aggregate_daily_bars(rows, "1mo", "SPY", after_close, session_closes)[-1]["Date"], date(2024, 11, 1))

    def test_weekly_new_week_and_missing_friday_are_conservative(self):
        zone = ZoneInfo("Asia/Taipei")
        monday_rows = self.rows + [
            {"Date": date(2024, 2, 5), "Open": 18, "High": 22, "Low": 17, "Close": 21, "Volume": 400},
        ]
        monday = datetime(2024, 2, 5, 10, 0, tzinfo=zone)
        bars = aggregate_daily_bars(monday_rows, "1wk", "2330.TW", monday)
        self.assertEqual(bars[-1]["Date"], date(2024, 1, 29))
        self.assertNotIn(date(2024, 2, 5), [bar["Date"] for bar in bars])

        holiday_friday = datetime(2024, 2, 2, 14, 0, tzinfo=zone)
        no_friday_rows = [row for row in self.rows if row["Date"] != date(2024, 2, 2)]
        conservative = aggregate_daily_bars(no_friday_rows, "1wk", "2330.TW", holiday_friday)
        self.assertNotIn(date(2024, 1, 29), [bar["Date"] for bar in conservative])

    def test_monthly_ohlcv_and_incomplete_month(self):
        now = datetime(2024, 3, 14, 12, tzinfo=ZoneInfo("Asia/Taipei"))
        bars = aggregate_daily_bars(self.rows, "1mo", "2330.TW", now)
        self.assertEqual(len(bars), 2)
        self.assertEqual(bars[0], {"Date": date(2024, 1, 1), "Open": 10, "High": 16, "Low": 9, "Close": 15, "Volume": 700})
        self.assertEqual(bars[1], {"Date": date(2024, 2, 1), "Open": 15, "High": 22, "Low": 14, "Close": 21, "Volume": 1050})

    def test_month_end_weekend_completes_after_final_friday_close(self):
        zone = ZoneInfo("Asia/Taipei")
        rows = [
            {"Date": date(2024, 2, 29), "Open": 10, "High": 12, "Low": 9, "Close": 11, "Volume": 100},
            {"Date": date(2024, 3, 1), "Open": 11, "High": 14, "Low": 10, "Close": 13, "Volume": 150},
            {"Date": date(2024, 3, 28), "Open": 13, "High": 16, "Low": 12, "Close": 15, "Volume": 200},
            {"Date": date(2024, 3, 29), "Open": 15, "High": 18, "Low": 14, "Close": 17, "Volume": 250},
        ]
        after_close = datetime(2024, 3, 29, 13, 31, tzinfo=zone)
        before_final_close = datetime(2024, 3, 29, 13, 0, tzinfo=zone)
        complete = aggregate_daily_bars(rows, "1mo", "2330.TW", after_close)
        incomplete = aggregate_daily_bars(rows, "1mo", "2330.TW", before_final_close)
        self.assertEqual(complete[-1], {"Date": date(2024, 3, 1), "Open": 11, "High": 18, "Low": 10, "Close": 17, "Volume": 600})
        self.assertEqual(incomplete[-1]["Date"], date(2024, 2, 1))

    def test_month_end_missing_final_weekday_bar_is_not_assumed_complete(self):
        zone = ZoneInfo("Asia/Taipei")
        rows = [
            {"Date": date(2024, 3, 28), "Open": 10, "High": 12, "Low": 9, "Close": 11, "Volume": 100},
        ]
        now = datetime(2024, 3, 29, 14, 0, tzinfo=zone)
        bars = aggregate_daily_bars(rows, "1mo", "2330.TW", now)
        self.assertEqual(bars, [])

    def test_new_month_includes_prior_month_and_excludes_current_month(self):
        zone = ZoneInfo("Asia/Taipei")
        rows = [
            {"Date": date(2024, 3, 29), "Open": 10, "High": 12, "Low": 9, "Close": 11, "Volume": 100},
            {"Date": date(2024, 4, 1), "Open": 11, "High": 14, "Low": 10, "Close": 13, "Volume": 150},
        ]
        now = datetime(2024, 4, 1, 10, 0, tzinfo=zone)
        bars = aggregate_daily_bars(rows, "1mo", "2330.TW", now)
        self.assertEqual([bar["Date"] for bar in bars], [date(2024, 3, 1)])

    def test_hourly_bars_are_filtered_by_local_session_and_completion(self):
        zone = ZoneInfo("Asia/Taipei")
        rows = [
            {"Date": datetime(2024, 3, 11, 9, tzinfo=zone)},
            {"Date": datetime(2024, 3, 11, 10, tzinfo=zone)},
            {"Date": datetime(2024, 3, 11, 13, tzinfo=zone)},
            {"Date": datetime(2024, 3, 11, 14, tzinfo=zone)},
        ]
        now = datetime(2024, 3, 11, 11, 5, tzinfo=zone)
        result = remove_incomplete_hourly_bars(rows, "2330.TW", now)
        self.assertEqual([row["Date"].hour for row in result], [9, 10])
        after_close = remove_incomplete_hourly_bars(
            rows, "2330.TW", datetime(2024, 3, 11, 14, 0, tzinfo=zone)
        )
        self.assertEqual([row["Date"].hour for row in after_close], [9, 10])

    def test_us_hourly_bars_use_new_york_session_time(self):
        zone = ZoneInfo("America/New_York")
        rows = [
            {"Date": datetime(2024, 3, 11, 9, 30, tzinfo=zone)},
            {"Date": datetime(2024, 3, 11, 10, 30, tzinfo=zone)},
            {"Date": datetime(2024, 3, 11, 14, 30, tzinfo=zone)},
            {"Date": datetime(2024, 3, 11, 15, 30, tzinfo=zone)},
            {"Date": datetime(2024, 3, 11, 16, 0, tzinfo=zone)},
        ]
        now = datetime(2024, 3, 11, 16, 5, tzinfo=zone)
        result = remove_incomplete_hourly_bars(rows, "SPY", now)
        self.assertEqual([row["Date"].strftime("%H:%M") for row in result], ["09:30", "10:30", "14:30"])

    def test_us_early_close_schedule_drops_truncated_terminal_bar(self):
        zone = ZoneInfo("America/New_York")
        rows = [
            {"Date": datetime(2024, 11, 29, 11, 30, tzinfo=zone)},
            {"Date": datetime(2024, 11, 29, 12, 30, tzinfo=zone)},
        ]
        close = {date(2024, 11, 29): datetime(2024, 11, 29, 13, 0, tzinfo=zone).time()}
        now = datetime(2024, 11, 29, 13, 5, tzinfo=zone)
        result = remove_incomplete_hourly_bars(rows, "SPY", now, session_closes=close)
        self.assertEqual([row["Date"].strftime("%H:%M") for row in result], ["11:30"])

    def test_unknown_early_close_drops_ambiguous_final_bar(self):
        zone = ZoneInfo("America/New_York")
        rows = [
            {"Date": datetime(2024, 11, 29, 9, 30, tzinfo=zone)},
            {"Date": datetime(2024, 11, 29, 10, 30, tzinfo=zone)},
            {"Date": datetime(2024, 11, 29, 11, 30, tzinfo=zone)},
            {"Date": datetime(2024, 11, 29, 12, 30, tzinfo=zone)},
        ]
        now = datetime(2024, 11, 29, 16, 30, tzinfo=zone)
        result = remove_incomplete_hourly_bars(rows, "SPY", now)
        self.assertEqual([row["Date"].strftime("%H:%M") for row in result], ["09:30", "10:30", "11:30"])

    def test_us_dst_offset_is_taken_from_exchange_timezone(self):
        winter = datetime(2024, 1, 8, 14, 30, tzinfo=ZoneInfo("America/New_York"))
        summer = datetime(2024, 7, 8, 14, 30, tzinfo=ZoneInfo("America/New_York"))
        self.assertNotEqual(winter.utcoffset(), summer.utcoffset())
        rows = [{"Date": summer}, {"Date": summer.replace(hour=15, minute=30)}]
        now = datetime(2024, 7, 8, 16, 5, tzinfo=ZoneInfo("America/New_York"))
        result = remove_incomplete_hourly_bars(rows, "SPY", now)
        self.assertEqual([row["Date"].strftime("%H:%M") for row in result], ["14:30"])

    def test_a8_uses_only_prior_six_and_requires_strictly_greater_than(self):
        self.assertFalse(volume_breakout_a8([100, 100, 100, 100, 100, 100, 150]))
        self.assertTrue(volume_breakout_a8([100, 100, 100, 100, 100, 100, 151]))
        self.assertIsNone(volume_breakout_a8([100, 100, 100, 100, 100, 100]))
        self.assertIsNone(volume_breakout_a8([100, 100, None, 100, 100, 100, 151]))


if __name__ == "__main__":
    unittest.main()
