"""Synthetic price paths, no remote acquisition or profitability claims."""
import unittest
from datetime import date
import pandas as pd
from session_returns import aligned_return, basket_return, comparison_sessions, format_percent


class FakeCalendar:
    def schedule(self, start_date, end_date):
        # A fixed synthetic session schedule, not a replacement for exchange holidays.
        return pd.DataFrame(index=pd.bdate_range("2026-08-03", "2026-08-31"))


class ReturnTests(unittest.TestCase):
    def setUp(self):
        self.labels = [d.date().isoformat() for d in pd.bdate_range("2026-08-03", periods=6)]
        self.hist = pd.DataFrame({"Close": [100., 101., 102., 103., 104., 105.]},
                                 index=pd.to_datetime(self.labels))

    def test_five_intervals_require_six_closes(self):
        result = aligned_return(self.hist, self.labels)
        self.assertTrue(result["eligible"])
        self.assertAlmostEqual(result["return_pct"], 5)
        self.assertEqual(result["horizon_sessions"], 5)
        self.assertEqual(result["start_session"], self.labels[0])

    def test_missing_latest_is_not_a_stale_valid_return(self):
        result = aligned_return(self.hist.iloc[:-1], self.labels)
        self.assertFalse(result["eligible"])
        self.assertIsNone(result["return_pct"])
        self.assertEqual(result["missing_sessions"], [self.labels[-1]])

    def test_interior_gap_and_duplicate_dates_fail_closed(self):
        self.assertFalse(aligned_return(self.hist.drop(self.hist.index[2]), self.labels)["eligible"])
        doubled = pd.concat([self.hist, self.hist.iloc[:1]])
        self.assertEqual(aligned_return(doubled, self.labels)["reason"], "duplicate_session")

    def test_partial_baskets_not_renormalized_or_zero(self):
        result = basket_return({"AAA": self.hist}, ["AAA", "BBB"], self.labels)
        self.assertIsNone(result["return_pct"])
        self.assertEqual((result["available"], result["expected"]), (1, 2))
        self.assertFalse(result["eligible"])
        self.assertFalse(basket_return({}, [], self.labels)["eligible"])

    def test_zero_is_a_valid_measured_return(self):
        hist = self.hist.copy(); hist["Close"] = 100.
        self.assertEqual(basket_return({"AAA": hist}, ["AAA"], self.labels)["return_pct"], 0)
        self.assertEqual(format_percent(0), "+0.0%")
        self.assertEqual(format_percent(None), "N/A")

    def test_bad_prices_and_future_bar(self):
        for value in (0., -1., float("nan"), float("inf")):
            with self.subTest(value=value):
                hist = self.hist.copy(); hist.iloc[-1, 0] = value
                self.assertFalse(aligned_return(hist, self.labels)["eligible"])
        hist = self.hist.copy(); hist.loc[pd.Timestamp("2026-08-31")] = 999
        self.assertAlmostEqual(aligned_return(hist, self.labels)["return_pct"], 5)

    def test_calendar_window_is_n_plus_one_and_anchored(self):
        labels = comparison_sessions("2026-08-31", 5, FakeCalendar())
        self.assertEqual(len(labels), 6)
        self.assertEqual(labels[-1], "2026-08-31")
        self.assertEqual(comparison_sessions("2026-09-01", 5, FakeCalendar()), [])
        self.assertEqual(comparison_sessions(None, 5), [])
        with self.assertRaises(ValueError): comparison_sessions("2026-08-31", 0)


if __name__ == "__main__": unittest.main()
