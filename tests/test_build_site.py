import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.build_site import (
    NA,
    SECTIONS,
    decimals_for,
    failed_quote,
    format_number,
    parse_chart,
    render_card,
    render_markets,
    render_tencent,
    tencent_financial_rows,
)


def _chart(offset, market_ts, price, bars, hint=2):
    timestamps = [bar[0] for bar in bars]
    closes = [bar[1] for bar in bars]
    opens = [bar[2] if len(bar) > 2 else bar[1] for bar in bars]
    return {
        "chart": {
            "result": [
                {
                    "meta": {
                        "regularMarketPrice": price,
                        "regularMarketTime": market_ts,
                        "gmtoffset": offset,
                        "priceHint": hint,
                        "currency": "USD",
                        "regularMarketDayHigh": 112,
                        "regularMarketDayLow": 108,
                        "fiftyTwoWeekHigh": 150,
                        "fiftyTwoWeekLow": 90,
                        "regularMarketVolume": 1200,
                        "longName": "Example",
                    },
                    "timestamp": timestamps,
                    "indicators": {
                        "quote": [
                            {
                                "close": closes,
                                "open": opens,
                                "high": closes,
                                "low": closes,
                                "volume": [10] * len(closes),
                            }
                        ]
                    },
                }
            ]
        }
    }


class ParseChartTests(unittest.TestCase):
    def test_prior_close_is_previous_session_not_a_made_up_delta(self):
        day1 = 1_700_000_000
        day2 = day1 + 86_400
        payload = _chart(0, day2 + 3_600, 110.25, [(day1, 100.0, 99.0), (day2, 110.0, 101.0)])
        quote = parse_chart("^GSPC", payload)
        self.assertEqual(quote["price"], 110.25)
        self.assertEqual(quote["previousClose"], 100.0)
        self.assertAlmostEqual(quote["change"], 10.25)
        self.assertAlmostEqual(quote["changePercent"], 10.25)
        self.assertEqual(quote["spark"][-1], 110.25)
        self.assertEqual(quote["open"], 101.0)

    def test_uses_last_price_when_today_bar_is_missing(self):
        day1 = 1_700_000_000
        day2 = day1 + 86_400
        today = day2 + 86_400
        payload = _chart(0, today + 100, 115.0, [(day1, 100.0), (day2, 110.0)])
        quote = parse_chart("EURUSD=X", payload)
        self.assertEqual(quote["price"], 115.0)
        self.assertEqual(quote["previousClose"], 110.0)
        self.assertEqual(quote["spark"], [100.0, 110.0, 115.0])
        self.assertIsNone(quote["open"])

    def test_skips_null_closes(self):
        day1 = 1_700_000_000
        day2 = day1 + 86_400
        day3 = day2 + 86_400
        payload = _chart(0, day3, 130.0, [(day1, 100.0), (day2, None), (day3, 130.0)])
        quote = parse_chart("BTC-USD", payload)
        self.assertEqual(quote["previousClose"], 100.0)
        self.assertEqual(quote["spark"][0], 100.0)
        self.assertNotIn(None, quote["spark"])

    def test_missing_result_raises(self):
        with self.assertRaises(ValueError):
            parse_chart("^GSPC", {"chart": {"result": None, "error": {"description": "No data found"}}})


class FormatTests(unittest.TestCase):
    def test_yield_keeps_thousandths(self):
        self.assertEqual(decimals_for("^TNX", 2), 3)
        self.assertEqual(format_number(5.136, 3), "5.136")

    def test_half_up(self):
        self.assertEqual(format_number(290.855, 2), "290.86")


class RenderTests(unittest.TestCase):
    def test_failed_symbol_is_not_available(self):
        html = render_card("S&P 500", failed_quote("^GSPC", "HTTPError"))
        self.assertIn(NA, html)
        self.assertIn("^GSPC", html)
        self.assertNotIn("7,690", html)

    def test_home_lists_every_symbol(self):
        groups = []
        for title, items in SECTIONS:
            groups.append((title, [(name, failed_quote(symbol, "x")) for name, symbol in items]))
        html = render_markets(groups, datetime(2026, 10, 20, 6, 0, tzinfo=timezone.utc))
        self.assertIn("Last updated", html)
        self.assertIn("2026-10-20 14:00 HKT", html)
        self.assertIn('href="tencent/"', html)
        for _, items in SECTIONS:
            for _, symbol in items:
                self.assertIn(symbol, html)

    def test_success_card_shows_change_against_prior_close(self):
        quote = parse_chart("^GSPC", _chart(0, 1_700_086_400, 110.0, [(1_700_000_000, 100.0), (1_700_086_400, 110.0)]))
        html = render_card("S&P 500", quote)
        self.assertIn("110.00", html)
        self.assertIn("+10.00", html)
        self.assertIn("prior close 100.00", html)
        self.assertIn("class='chg up'", html)


class FinancialTests(unittest.TestCase):
    def test_missing_summary_is_not_available(self):
        rows = tencent_financial_rows(None)
        self.assertEqual(rows[0]["value"], NA)

    def test_rows_use_source_numbers_and_mark_calculations(self):
        summary = {
            "price": {"currency": "HKD", "marketCap": {"raw": 1000, "fmt": "1K", "longFmt": "1,000"}},
            "summaryDetail": {"trailingPE": {"raw": 14.43, "fmt": "14.43"}, "forwardPE": {"raw": 11.53, "fmt": "11.53"}},
            "defaultKeyStatistics": {"mostRecentQuarter": {"raw": 1, "fmt": "2026-06-30"}, "trailingEps": {"raw": 28.41, "fmt": "28.41"}},
            "financialData": {
                "financialCurrency": "CNY",
                "totalRevenue": {"raw": 788, "fmt": "788", "longFmt": "788"},
                "totalCash": {"raw": 500, "fmt": "500", "longFmt": "500"},
                "totalDebt": {"raw": 800, "fmt": "800", "longFmt": "800"},
            },
            "incomeStatementHistory": {
                "incomeStatementHistory": [
                    {
                        "endDate": {"raw": 20, "fmt": "2025-12-31"},
                        "totalRevenue": {"raw": 110, "fmt": "110", "longFmt": "110"},
                        "netIncome": {"raw": 11, "fmt": "11", "longFmt": "11"},
                        "operatingIncome": {},
                    },
                    {
                        "endDate": {"raw": 10, "fmt": "2024-12-31"},
                        "totalRevenue": {"raw": 100, "fmt": "100", "longFmt": "100"},
                        "netIncome": {"raw": 10, "fmt": "10", "longFmt": "10"},
                    },
                ]
            },
            "incomeStatementHistoryQuarterly": {"incomeStatementHistory": []},
            "earnings": {
                "financialCurrency": "CNY",
                "earningsChart": {
                    "quarterly": [
                        {
                            "date": "2Q2026",
                            "actual": {"raw": 7.43, "fmt": "7.43"},
                            "periodEndDate": {"raw": 30, "fmt": "2026-06-30"},
                        }
                    ]
                },
            },
            "calendarEvents": {},
        }
        rows = {row["metric"]: row for row in tencent_financial_rows(summary)}
        self.assertEqual(rows["Market cap"]["value"], "HKD 1,000 (Yahoo: 1K)")
        self.assertEqual(rows["Trailing P/E"]["value"], "14.43")
        self.assertIn("788", rows["Trailing revenue"]["value"])
        self.assertIn("calculated", rows["Net cash (total cash − total debt)"]["value"])
        self.assertIn("-300", rows["Net cash (total cash − total debt)"]["value"])
        self.assertEqual(rows["Operating income FY ending 2025-12-31"]["value"], NA)
        self.assertIn("+10.00%", rows["Revenue FY ending 2025-12-31"]["value"])
        self.assertIn("calculated", rows["Revenue FY ending 2025-12-31"]["value"])
        self.assertEqual(rows["Revenue quarter ending 2026-06-30"]["value"], NA)
        self.assertIn("7.43", rows["Reported EPS 2Q2026"]["value"])

    def test_tencent_page_escapes_and_states_last_updated(self):
        quote = failed_quote("0700.HK", "HTTPError")
        html = render_tencent(quote, tencent_financial_rows(None), datetime(2026, 10, 20, 6, 30, tzinfo=timezone.utc))
        self.assertIn("Last updated", html)
        self.assertIn("2026-10-20 14:30 HKT", html)
        self.assertIn(NA, html)
        self.assertIn('href="../"', html)


if __name__ == "__main__":
    unittest.main()
