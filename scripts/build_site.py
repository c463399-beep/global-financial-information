#!/usr/bin/env python3
"""Fetch Yahoo Finance data and write the static dashboard.

Prices, daily changes, and charts come from the public chart endpoint:

    https://query1.finance.yahoo.com/v8/finance/chart/<SYMBOL>?range=1mo&interval=1d

Tencent fundamentals come from Yahoo's quoteSummary endpoint (the same data
the Yahoo Finance quote page loads). Missing values are rendered as
"not available". This script never fills in a number that was not in a
response.
"""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from html import escape
from pathlib import Path

HKT = timezone(timedelta(hours=8))
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)
CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range={range}&interval=1d"
QUOTE_SUMMARY_MODULES = ",".join(
    [
        "price",
        "summaryDetail",
        "defaultKeyStatistics",
        "financialData",
        "incomeStatementHistory",
        "incomeStatementHistoryQuarterly",
        "earnings",
        "calendarEvents",
    ]
)
YIELD_SYMBOLS = {"^TNX", "^TYX", "^FVX", "^IRX"}
NA = "not available"

# Friendly names match the dashboard sections. Symbols are the Yahoo tickers.
SECTIONS = [
    (
        "Americas",
        [
            ("S&P 500", "^GSPC"),
            ("Dow Jones", "^DJI"),
            ("Nasdaq Composite", "^IXIC"),
        ],
    ),
    (
        "Europe",
        [
            ("FTSE 100", "^FTSE"),
            ("DAX", "^GDAXI"),
            ("CAC 40", "^FCHI"),
            ("Euro Stoxx 50", "^STOXX50E"),
        ],
    ),
    (
        "Asia-Pacific",
        [
            ("Nikkei 225", "^N225"),
            ("Hang Seng", "^HSI"),
            ("Shanghai Composite", "000001.SS"),
            ("KOSPI", "^KS11"),
            ("ASX 200", "^AXJO"),
            ("BSE Sensex", "^BSESN"),
        ],
    ),
    (
        "FX",
        [
            ("EUR/USD", "EURUSD=X"),
            ("USD/JPY", "JPY=X"),
            ("GBP/USD", "GBPUSD=X"),
            ("USD/CNY", "CNY=X"),
            ("USD/HKD", "HKD=X"),
            ("AUD/USD", "AUDUSD=X"),
        ],
    ),
    (
        "Commodities",
        [
            ("Gold", "GC=F"),
            ("Silver", "SI=F"),
            ("WTI Crude", "CL=F"),
            ("Brent Crude", "BZ=F"),
            ("Natural Gas", "NG=F"),
            ("Copper", "HG=F"),
        ],
    ),
    (
        "Crypto",
        [
            ("Bitcoin", "BTC-USD"),
            ("Ethereum", "ETH-USD"),
        ],
    ),
    (
        "US Yields & Volatility",
        [
            ("13-week T-bill", "^IRX"),
            ("5Y Treasury", "^FVX"),
            ("10Y Treasury", "^TNX"),
            ("30Y Treasury", "^TYX"),
            ("VIX", "^VIX"),
        ],
    ),
]

TENCENT_SYMBOL = "0700.HK"


def exchange_date(ts: int, gmtoffset: int):
    return datetime.fromtimestamp(int(ts) + int(gmtoffset or 0), timezone.utc).date()


def format_number(value: float, places: int) -> str:
    quant = Decimal(1).scaleb(-places)
    dec = Decimal(str(value)).quantize(quant, rounding=ROUND_HALF_UP)
    return f"{dec:,.{places}f}"


def decimals_for(symbol: str, price_hint) -> int:
    try:
        hint = int(price_hint)
    except (TypeError, ValueError):
        hint = 2
    hint = min(max(hint, 0), 6)
    if symbol in YIELD_SYMBOLS:
        return max(hint, 3)
    return hint


def hkt_stamp(ts: int) -> str:
    dt = datetime.fromtimestamp(int(ts), HKT)
    return f"{dt.strftime('%b')} {dt.day} {dt.strftime('%H:%M')} HKT"


def hkt_full(dt: datetime) -> str:
    dt = dt.astimezone(HKT)
    return dt.strftime("%Y-%m-%d %H:%M HKT")


def iso_hkt(dt: datetime) -> str:
    return dt.astimezone(HKT).isoformat(timespec="seconds")


def direction(change: float | None) -> str:
    if change is None:
        return "flat"
    if change > 0:
        return "up"
    if change < 0:
        return "down"
    return "flat"


def signed_number(value: float, places: int) -> str:
    text = format_number(abs(value) if value < 0 else value, places)
    if value > 0:
        return "+" + text
    if value < 0:
        return "-" + format_number(abs(value), places)
    return format_number(0, places)


def http_get(url: str, opener=None, timeout: int = 30, attempts: int = 3) -> bytes:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json,text/plain,*/*"},
    )
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            open_fn = opener.open if opener is not None else urllib.request.urlopen
            with open_fn(request, timeout=timeout) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code not in (429, 500, 502, 503, 504) or attempt == attempts - 1:
                raise
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            last_error = exc
            if attempt == attempts - 1:
                raise
        time.sleep(0.6 * (attempt + 1))
    raise RuntimeError(f"fetch failed for {url}") from last_error


def fetch_chart(symbol: str, range_: str) -> dict:
    url = CHART_URL.format(symbol=urllib.parse.quote(symbol, safe=""), range=range_)
    payload = json.loads(http_get(url).decode("utf-8"))
    return parse_chart(symbol, payload)


def parse_chart(symbol: str, payload: dict) -> dict:
    chart = payload.get("chart") or {}
    result = chart.get("result")
    if not result:
        error = chart.get("error") or {}
        detail = error.get("description") or error.get("code") or "no data"
        raise ValueError(str(detail))
    block = result[0]
    meta = block.get("meta") or {}
    indicators = block.get("indicators") or {}
    quote_rows = indicators.get("quote") or [{}]
    quote = quote_rows[0] if quote_rows else {}
    timestamps = block.get("timestamp") or []
    closes = quote.get("close") or []
    opens = quote.get("open") or []
    highs = quote.get("high") or []
    lows = quote.get("low") or []
    volumes = quote.get("volume") or []
    bars = []
    for index, ts in enumerate(timestamps):
        close = closes[index] if index < len(closes) else None
        if close is None or ts is None:
            continue
        bars.append(
            {
                "ts": int(ts),
                "close": float(close),
                "open": _num_at(opens, index),
                "high": _num_at(highs, index),
                "low": _num_at(lows, index),
                "volume": _num_at(volumes, index),
            }
        )
    offset = int(meta.get("gmtoffset") or 0)
    market_ts = meta.get("regularMarketTime")
    price = _as_float(meta.get("regularMarketPrice"))
    if market_ts is None and bars:
        market_ts = bars[-1]["ts"]
    if price is None and bars:
        price = bars[-1]["close"]
    if market_ts is None or price is None:
        raise ValueError("no last price")
    market_ts = int(market_ts)
    session = exchange_date(market_ts, offset)
    previous = None
    for bar in bars:
        if exchange_date(bar["ts"], offset) < session:
            previous = bar["close"]
    spark = [bar["close"] for bar in bars]
    if bars and exchange_date(bars[-1]["ts"], offset) == session:
        spark[-1] = price
    elif not bars or exchange_date(bars[-1]["ts"], offset) < session:
        spark.append(price)
    current_bar = None
    for bar in bars:
        if exchange_date(bar["ts"], offset) == session:
            current_bar = bar
    change = None if previous is None else price - previous
    change_pct = None
    if previous not in (None, 0) and change is not None:
        change_pct = change / previous * 100
    return {
        "ok": True,
        "symbol": symbol,
        "currency": meta.get("currency"),
        "priceHint": meta.get("priceHint"),
        "shortName": meta.get("shortName"),
        "longName": meta.get("longName"),
        "price": price,
        "previousClose": previous,
        "change": change,
        "changePercent": change_pct,
        "marketTime": market_ts,
        "sessionDate": session.isoformat(),
        "spark": spark,
        "series": [{"t": bar["ts"], "c": bar["close"]} for bar in bars],
        "gmtoffset": offset,
        "open": None if current_bar is None else current_bar.get("open"),
        "dayHigh": _as_float(meta.get("regularMarketDayHigh")),
        "dayLow": _as_float(meta.get("regularMarketDayLow")),
        "fiftyTwoWeekHigh": _as_float(meta.get("fiftyTwoWeekHigh")),
        "fiftyTwoWeekLow": _as_float(meta.get("fiftyTwoWeekLow")),
        "volume": _as_float(meta.get("regularMarketVolume")),
        "error": None,
    }


def failed_quote(symbol: str, reason: str) -> dict:
    return {
        "ok": False,
        "symbol": symbol,
        "error": reason,
        "price": None,
        "previousClose": None,
        "change": None,
        "changePercent": None,
        "marketTime": None,
        "spark": [],
        "series": [],
    }


def _num_at(values, index):
    if index >= len(values):
        return None
    return _as_float(values[index])


def _as_float(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:  # NaN
        return None
    return number


def yahoo_session():
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    request = urllib.request.Request("https://fc.yahoo.com", headers={"User-Agent": USER_AGENT})
    try:
        opener.open(request, timeout=30).read()
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            raise
    crumb = http_get("https://query1.finance.yahoo.com/v1/test/getcrumb", opener=opener).decode().strip()
    if not crumb or crumb.lower().startswith("<"):
        raise ValueError("Yahoo crumb was empty")
    return opener, crumb


def fetch_quote_summary(symbol: str) -> dict:
    opener, crumb = yahoo_session()
    query = urllib.parse.urlencode(
        {"modules": QUOTE_SUMMARY_MODULES, "crumb": crumb}
    )
    url = (
        "https://query1.finance.yahoo.com/v10/finance/quoteSummary/"
        + urllib.parse.quote(symbol, safe="")
        + "?"
        + query
    )
    payload = json.loads(http_get(url, opener=opener).decode("utf-8"))
    summary = payload.get("quoteSummary") or {}
    result = summary.get("result")
    if not result:
        error = summary.get("error") or {}
        detail = error.get("description") or error.get("code") or "no data"
        raise ValueError(str(detail))
    return result[0]


def sparkline_svg(values: list[float], kind: str) -> str:
    clean = [value for value in values if value is not None]
    if len(clean) < 2:
        return ""
    low = min(clean)
    high = max(clean)
    span = high - low or 1.0
    last = len(clean) - 1
    points = []
    for index, value in enumerate(clean):
        x = 0 if last == 0 else index * 100 / last
        y = 2 + (1 - (value - low) / span) * 26
        points.append(f"{x:.2f},{y:.2f}")
    color = {"up": "#15803d", "down": "#dc2626"}.get(kind, "#64748b")
    return (
        '<svg class="spark" viewBox="0 0 100 30" preserveAspectRatio="none" '
        'aria-hidden="true">'
        f'<polyline fill="none" stroke="{color}" stroke-width="1.6" '
        f'points="{" ".join(points)}"/></svg>'
    )


def series_chart_svg(points: list[tuple[str, float]]) -> str:
    """Line chart without in-SVG text so labels stay readable on a phone."""
    if len(points) < 2:
        return f"<p>{NA}</p>"
    width, height = 800, 260
    pad_l, pad_r, pad_t, pad_b = 8, 8, 10, 10
    values = [value for _, value in points]
    low = min(values)
    high = max(values)
    span = high - low or 1.0
    inner_w = width - pad_l - pad_r
    inner_h = height - pad_t - pad_b

    def xy(index: int, value: float) -> tuple[float, float]:
        x = pad_l + index * inner_w / (len(points) - 1)
        y = pad_t + (1 - (value - low) / span) * inner_h
        return x, y

    coords = [xy(index, value) for index, (_, value) in enumerate(points)]
    line = " ".join(f"{x:.1f},{y:.1f}" for x, y in coords)
    start, end = coords[0], coords[-1]
    base = pad_t + inner_h
    area = f"{start[0]:.1f},{base:.1f} " + line + f" {end[0]:.1f},{base:.1f}"
    first_label, last_label = points[0][0], points[-1][0]
    label = f"1-year daily close from {first_label} to {last_label}"
    return (
        f'<svg class="chart" viewBox="0 0 {width} {height}" role="img" aria-label="{escape(label)}">'
        f"<title>{escape(label)}</title>"
        f'<polygon fill="#dbeafe" points="{area}"></polygon>'
        f'<polyline fill="none" stroke="#2563eb" stroke-width="2.5" points="{line}"></polyline>'
        "</svg>"
    )


def render_card(name: str, quote: dict) -> str:
    symbol = quote.get("symbol") or ""
    if not quote.get("ok"):
        return (
            "<article class='card'>"
            f"<div class='name'>{escape(name)}<span class='sym'>{escape(symbol)}</span></div>"
            f"<div class='price na'>{NA}</div>"
            "</article>"
        )
    places = decimals_for(symbol, quote.get("priceHint"))
    price = quote.get("price")
    change = quote.get("change")
    pct = quote.get("changePercent")
    kind = direction(change)
    price_html = NA if price is None else escape(format_number(price, places))
    if change is None or pct is None:
        change_html = f"<div class='chg na'>{NA}</div>"
        prior_html = ""
    else:
        change_html = (
            f"<div class='chg {kind}'>{escape(signed_number(change, places))} "
            f"({escape(signed_number(pct, 2))}%)</div>"
        )
        prev = quote.get("previousClose")
        prior_html = (
            ""
            if prev is None
            else f"<div class='when'>prior close {escape(format_number(prev, places))}</div>"
        )
    when = quote.get("marketTime")
    when_html = "" if when is None else f"<div class='when'>{escape(hkt_stamp(when))}</div>"
    return (
        "<article class='card'>"
        f"<div class='name'>{escape(name)}<span class='sym'>{escape(symbol)}</span></div>"
        f"<div class='price'>{price_html}</div>"
        f"{change_html}"
        f"{sparkline_svg(quote.get('spark') or [], kind)}"
        f"{prior_html}{when_html}"
        "</article>"
    )


def render_markets(groups: list[tuple[str, list[tuple[str, dict]]]], updated: datetime) -> str:
    sections = []
    for title, items in groups:
        cards = "".join(render_card(name, quote) for name, quote in items)
        note = ""
        if title == "US Yields & Volatility":
            note = "<p class='lede'>Treasury yields are in percent, as published by Yahoo Finance.</p>"
        sections.append(f"<section><h2>{escape(title)}</h2>{note}<div class='grid'>{cards}</div></section>")
    body = "\n".join(sections)
    updated_text = hkt_full(updated)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Global Markets</title>
  <meta name="description" content="Global indices, FX, commodities, crypto, US yields, and VIX. Refreshed from Yahoo Finance.">
  <link rel="stylesheet" href="assets/style.css">
</head>
<body>
<main>
  <header class="top">
    <div>
      <h1>Global Markets</h1>
      <p class="lede">Change vs prior daily close. Sparkline is one month of daily closes.</p>
      <p class="updated">Last updated <time datetime="{escape(iso_hkt(updated))}">{escape(updated_text)}</time></p>
    </div>
    <nav class="nav"><a href="tencent/">Tencent (0700.HK)</a></nav>
  </header>
  {body}
  <footer>
    <p>Source: Yahoo Finance chart API (<span class="mono">v8/finance/chart</span>, range 1mo, interval 1d). Daily change is the last price minus the previous session's daily close. Market times are Hong Kong time.</p>
    <p>Last updated {escape(updated_text)}. Pages are rebuilt every 4 hours by GitHub Actions.</p>
  </footer>
</main>
</body>
</html>
"""


def _field_raw(node):
    if isinstance(node, dict) and node.get("raw") is not None:
        return node.get("raw")
    return None


def _field_fmt(node):
    if isinstance(node, dict):
        text = node.get("fmt") or node.get("longFmt")
        if text:
            return str(text)
    return None


def _money(node, currency: str | None) -> str | None:
    """Format a Yahoo raw number. The Yahoo display string is kept beside it when it differs."""
    if not isinstance(node, dict) or node.get("raw") is None:
        return None
    raw = node.get("raw")
    try:
        number = float(raw)
    except (TypeError, ValueError):
        return None
    if number.is_integer():
        amount = f"{int(number):,}"
    else:
        amount = format_number(number, 2)
    prefix = f"{currency} " if currency else ""
    pretty = node.get("fmt")
    if pretty and str(pretty) != amount:
        return f"{prefix}{amount} (Yahoo: {pretty})"
    return f"{prefix}{amount}"


def _yoy(current, previous) -> str | None:
    current_raw = _field_raw(current)
    previous_raw = _field_raw(previous)
    if current_raw in (None, ) or previous_raw in (None, 0):
        return None
    try:
        pct = (float(current_raw) - float(previous_raw)) / float(previous_raw) * 100
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    return signed_number(pct, 2) + "%"


def tencent_financial_rows(summary: dict | None) -> list[dict]:
    """Rows of metric / value / source / as-of. Values are Yahoo fields or NA."""
    source_summary = "Yahoo Finance quoteSummary summaryDetail"
    source_stats = "Yahoo Finance quoteSummary defaultKeyStatistics"
    source_financials = "Yahoo Finance quoteSummary financialData"
    source_income = "Yahoo Finance quoteSummary incomeStatementHistory"
    source_quarter = "Yahoo Finance quoteSummary incomeStatementHistoryQuarterly"
    source_earnings = "Yahoo Finance quoteSummary earnings"
    source_calendar = "Yahoo Finance quoteSummary calendarEvents"
    if not summary:
        return [
            {
                "metric": "Key financials",
                "value": NA,
                "source": "Yahoo Finance quoteSummary",
                "asof": "—",
            }
        ]
    price = summary.get("price") or {}
    detail = summary.get("summaryDetail") or {}
    stats = summary.get("defaultKeyStatistics") or {}
    financials = summary.get("financialData") or {}
    earnings = summary.get("earnings") or {}
    calendar = summary.get("calendarEvents") or {}
    currency = price.get("currency") or detail.get("currency") or "HKD"
    fin_ccy = financials.get("financialCurrency") or earnings.get("financialCurrency") or ""
    recent_q = _field_fmt((stats.get("mostRecentQuarter") or {})) or "—"
    rows: list[dict] = []

    def add(metric, value, source, asof):
        rows.append(
            {
                "metric": metric,
                "value": NA if value in (None, "") else str(value),
                "source": source,
                "asof": asof or "—",
            }
        )

    cap_node = price.get("marketCap") or detail.get("marketCap")
    cap_source = "Yahoo Finance quoteSummary price.marketCap" if price.get("marketCap") else source_summary
    add("Market cap", _money(cap_node, currency), cap_source, "quote")
    add("Trailing P/E", _field_fmt(detail.get("trailingPE")), source_summary, "quote")
    add("Forward P/E", _field_fmt(detail.get("forwardPE")), source_summary, "quote")
    add("EPS (trailing)", _eps(stats.get("trailingEps"), fin_ccy), source_stats, recent_q)
    dividend = _dividend(detail, currency)
    add("Dividend (yield)", dividend, source_summary, _field_fmt(detail.get("exDividendDate")) or "quote")
    add("Shares outstanding", _money(stats.get("sharesOutstanding"), None), source_stats, "quote")
    add("Beta", _field_fmt(detail.get("beta")) or _field_fmt(stats.get("beta")), source_summary if detail.get("beta") else source_stats, "quote")
    target = _money(financials.get("targetMeanPrice"), currency)
    opinions = _field_fmt(financials.get("numberOfAnalystOpinions"))
    if target and opinions:
        target = f"{target}; {opinions} analysts"
    add("Analyst mean price target", target, source_financials, "quote")
    earnings_date = _earnings_date(calendar, earnings)
    add("Next earnings date", earnings_date, source_calendar, "quote")
    add(
        "Trailing revenue",
        _money(financials.get("totalRevenue"), fin_ccy),
        source_financials + " totalRevenue",
        recent_q,
    )
    add(
        "Trailing net income",
        _money(stats.get("netIncomeToCommon"), fin_ccy),
        source_stats + " netIncomeToCommon",
        recent_q,
    )
    cash = financials.get("totalCash")
    debt = financials.get("totalDebt")
    add("Total cash", _money(cash, fin_ccy), source_financials, recent_q)
    add("Total debt", _money(debt, fin_ccy), source_financials, recent_q)
    add("Net cash (total cash − total debt)", _net_cash(cash, debt, fin_ccy), "calculated from Yahoo totalCash and totalDebt", recent_q)

    annual = ((summary.get("incomeStatementHistory") or {}).get("incomeStatementHistory")) or []
    annual = sorted(annual, key=_end_raw, reverse=True)
    for index, statement in enumerate(annual):
        end = _field_fmt(statement.get("endDate")) or "—"
        prior = annual[index + 1] if index + 1 < len(annual) else None
        add(
            f"Revenue FY ending {end}",
            _with_yoy(_money(statement.get("totalRevenue"), fin_ccy), statement.get("totalRevenue"), None if prior is None else prior.get("totalRevenue")),
            source_income,
            end,
        )
        add(
            f"Net income FY ending {end}",
            _with_yoy(_money(statement.get("netIncome"), fin_ccy), statement.get("netIncome"), None if prior is None else prior.get("netIncome")),
            source_income,
            end,
        )
        if index == 0:
            add(
                f"Operating income FY ending {end}",
                _money(statement.get("operatingIncome"), fin_ccy),
                source_income,
                end,
            )

    quarterly = ((summary.get("incomeStatementHistoryQuarterly") or {}).get("incomeStatementHistory")) or []
    quarterly = sorted(quarterly, key=_end_raw, reverse=True)
    income_ends = set()
    for statement in quarterly:
        end = _field_fmt(statement.get("endDate")) or "—"
        if end != "—":
            income_ends.add(end)
        add(
            f"Revenue quarter ending {end}",
            _money(statement.get("totalRevenue"), fin_ccy),
            source_quarter,
            end,
        )
        add(
            f"Net income quarter ending {end}",
            _money(statement.get("netIncome"), fin_ccy),
            source_quarter,
            end,
        )

    latest_eps = _latest_reported_eps(earnings, fin_ccy)
    if latest_eps is None:
        add("Latest reported quarterly EPS", NA, source_earnings, "—")
    else:
        add(latest_eps["metric"], latest_eps["value"], source_earnings, latest_eps["asof"])
        eps_end = latest_eps.get("periodEnd")
        if eps_end and eps_end not in income_ends:
            add(
                f"Revenue quarter ending {eps_end}",
                NA,
                source_quarter,
                "—",
            )
            add(
                f"Net income quarter ending {eps_end}",
                NA,
                source_quarter,
                "—",
            )
    return rows


def _precise(node, places: int) -> str | None:
    raw = _field_raw(node)
    if raw is None:
        return _field_fmt(node)
    text = format_number(float(raw), places)
    pretty = _field_fmt(node)
    if pretty and pretty != text:
        return f"{text} (Yahoo: {pretty})"
    return text


def _eps(node, currency: str) -> str | None:
    text = _precise(node, 3)
    if not text:
        return None
    return f"{currency} {text}".strip()


def _dividend(detail: dict, currency: str) -> str | None:
    rate = _field_fmt(detail.get("dividendRate"))
    yield_text = _field_fmt(detail.get("dividendYield"))
    if rate is None and yield_text is None:
        return None
    parts = []
    if rate is not None:
        parts.append(f"{currency} {rate}".strip())
    if yield_text is not None:
        parts.append(f"yield {yield_text}")
    ex = _field_fmt(detail.get("exDividendDate"))
    text = ", ".join(parts)
    if ex:
        text += f", ex-date {ex}"
    return text


def _earnings_date(calendar: dict, earnings: dict) -> str | None:
    block = (calendar or {}).get("earnings") or {}
    dates = block.get("earningsDate") or []
    if not dates:
        dates = ((earnings.get("earningsChart") or {}).get("earningsDate")) or []
    labels = [_field_fmt(item) for item in dates if _field_fmt(item)]
    if not labels:
        return None
    estimate = block.get("isEarningsDateEstimate")
    if estimate is None:
        estimate = (earnings.get("earningsChart") or {}).get("isEarningsDateEstimate")
    suffix = ""
    if estimate is True:
        suffix = " (Yahoo marks this date as an estimate)"
    elif estimate is False:
        suffix = " (Yahoo marks this date as confirmed)"
    return ", ".join(labels) + suffix


def _with_yoy(text: str | None, current, previous) -> str | None:
    if text is None:
        return None
    yoy = _yoy(current, previous)
    if not yoy:
        return text
    return f"{text}; {yoy} vs prior year, calculated from the two reported totals"


def _net_cash(cash, debt, currency: str) -> str | None:
    cash_raw = _field_raw(cash)
    debt_raw = _field_raw(debt)
    if cash_raw is None or debt_raw is None:
        return None
    try:
        net = int(cash_raw) - int(debt_raw)
    except (TypeError, ValueError):
        return None
    return f"{currency} {net:,} (calculated as totalCash − totalDebt)".strip()


def _end_raw(statement: dict) -> int:
    end = statement.get("endDate") or statement.get("periodEndDate") or {}
    if isinstance(end, dict) and end.get("raw") is not None:
        try:
            return int(end["raw"])
        except (TypeError, ValueError):
            return 0
    return 0


def _latest_reported_eps(earnings: dict, currency: str) -> dict | None:
    chart = (earnings or {}).get("earningsChart") or {}
    quarterly = list(chart.get("quarterly") or [])
    if not quarterly:
        return None
    quarterly.sort(key=_end_raw)
    latest = quarterly[-1]
    actual = _precise(latest.get("actual"), 3)
    if actual is None:
        return None
    period = latest.get("date") or "latest quarter"
    end = _field_fmt(latest.get("periodEndDate")) or "—"
    reported = _field_fmt(latest.get("reportedDate"))
    value = f"{currency} {actual}".strip()
    if reported:
        value += f", reported {reported}"
    return {
        "metric": f"Reported EPS {period}",
        "value": value,
        "asof": end if end != "—" else (reported or "—"),
        "periodEnd": end if end != "—" else None,
    }


def _stat_value(value, places: int) -> str:
    if value is None:
        return NA
    return format_number(value, places)


def render_tencent(quote: dict, rows: list[dict], updated: datetime) -> str:
    updated_text = hkt_full(updated)
    places = decimals_for(TENCENT_SYMBOL, quote.get("priceHint") if quote.get("ok") else 3)
    if quote.get("ok") and quote.get("price") is not None:
        kind = direction(quote.get("change"))
        price = f"HK${format_number(quote['price'], places)}"
        if quote.get("change") is None or quote.get("changePercent") is None:
            change_html = f"<span class='na'>{NA}</span>"
        else:
            change_html = (
                f"<span class='{kind}'>{escape(signed_number(quote['change'], places))} "
                f"({escape(signed_number(quote['changePercent'], 2))}%)</span>"
            )
        when = hkt_stamp(quote["marketTime"]) if quote.get("marketTime") else NA
        prev = quote.get("previousClose")
        prev_text = NA if prev is None else format_number(prev, places)
        header = (
            f"<div class='price'>{escape(price)} {change_html}</div>"
            f"<p class='when'>As of {escape(when)}. Prior daily close {escape(prev_text)}. "
            "Source: Yahoo Finance chart API.</p>"
        )
        offset = int(quote.get("gmtoffset") or 0)
        points = []
        for point in quote.get("series") or []:
            label = exchange_date(point["t"], offset).isoformat()
            points.append((label, point["c"]))
        if points and quote.get("sessionDate") and points[-1][0] == quote["sessionDate"]:
            points[-1] = (points[-1][0], quote["price"])
        elif quote.get("sessionDate"):
            points.append((quote["sessionDate"], quote["price"]))
        chart = series_chart_svg(points)
        chart_note = ""
        if len(points) >= 2:
            values = [value for _, value in points]
            low, high = min(values), max(values)
            chart_note = (
                "<p class='when'>Low "
                f"{escape(format_number(low, places))} · high {escape(format_number(high, places))}. "
                f"{escape(points[0][0])} {escape(format_number(points[0][1], places))} to "
                f"{escape(points[-1][0])} {escape(format_number(points[-1][1], places))}. "
                "Source: Yahoo Finance chart API, range 1y, interval 1d.</p>"
            )
        stats = [
            ("Open", _stat_value(quote.get("open"), places)),
            ("Day range", _range(quote.get("dayLow"), quote.get("dayHigh"), places)),
            ("52-week range", _range(quote.get("fiftyTwoWeekLow"), quote.get("fiftyTwoWeekHigh"), places)),
            ("Volume", NA if quote.get("volume") is None else f"{int(quote['volume']):,}"),
        ]
        stats_html = "".join(
            f"<div>{escape(label)}<b>{escape(value)}</b><small>Yahoo Finance chart API</small></div>"
            for label, value in stats
        )
    else:
        header = f"<div class='price na'>{NA}</div><p class='when'>Price {NA}. Source: Yahoo Finance chart API.</p>"
        chart = f"<p>{NA}</p>"
        chart_note = ""
        stats_html = ""

    table_rows = "".join(
        "<tr>"
        f"<td>{escape(row['metric'])}</td>"
        f"<td>{escape(row['value'])}</td>"
        f"<td>{escape(row['source'])}</td>"
        f"<td>{escape(row['asof'])}</td>"
        "</tr>"
        for row in rows
    )
    name = quote.get("longName") or "Tencent Holdings Limited"
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Tencent Holdings (0700.HK)</title>
  <meta name="description" content="Tencent Holdings price, one-year chart, and key financials from Yahoo Finance.">
  <link rel="stylesheet" href="../assets/style.css">
</head>
<body>
<main>
  <header class="top">
    <div>
      <p class="crumb"><a href="../">Global Markets</a></p>
      <h1>{escape(name)} <span class="sym inline">0700.HK · HKEX</span></h1>
      {header}
      <p class="updated">Last updated <time datetime="{escape(iso_hkt(updated))}">{escape(updated_text)}</time></p>
    </div>
  </header>
  <section class="panel">
    <div class="stats">{stats_html}</div>
  </section>
  <section class="panel">
    <h2>1-year price (HKD, daily close)</h2>
    {chart}
    {chart_note}
  </section>
  <section class="panel">
    <h2>Key financials</h2>
    <p class="lede">Share-price statistics are in HKD. Income-statement figures use the currency Yahoo labels on that module (CNY when Yahoo reports financialCurrency as CNY). Figures are copied from the named fields and are not recalculated, except rows that say calculated.</p>
    <div class="wrap">
      <table>
        <thead><tr><th>Metric</th><th>Value</th><th>Source</th><th>As of</th></tr></thead>
        <tbody>{table_rows}</tbody>
      </table>
    </div>
    <p class="when">Fetched {escape(updated_text)}.</p>
  </section>
  <footer>
    <p><a href="../">Back to Global Markets</a></p>
    <p>Last updated {escape(updated_text)}.</p>
  </footer>
</main>
</body>
</html>
"""


def _range(low, high, places: int) -> str:
    if low is None or high is None:
        return NA
    return f"{format_number(low, places)} – {format_number(high, places)}"


STYLE = """:root {
  --bg: #f4f6fa;
  --card: #ffffff;
  --text: #1a1f2b;
  --muted: #5e6b80;
  --line: #e6ebf2;
  --up: #15803d;
  --down: #dc2626;
  --accent: #1e3a8a;
  --shadow: 0 1px 2px rgba(16, 24, 40, 0.05), 0 1px 3px rgba(16, 24, 40, 0.06);
}
* { box-sizing: border-box; }
body {
  margin: 0;
  font-family: system-ui, -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  background: var(--bg);
  color: var(--text);
  line-height: 1.45;
}
main { max-width: 1100px; margin: 0 auto; padding: 20px 16px 48px; }
a { color: var(--accent); }
.top { display: flex; justify-content: space-between; gap: 16px; align-items: flex-end; flex-wrap: wrap; }
h1 { font-size: 1.75rem; line-height: 1.2; margin: 0; letter-spacing: -0.02em; }
h2 { font-size: 1.05rem; margin: 22px 0 8px; }
.lede { margin: 6px 0 0; color: var(--muted); font-size: 0.92rem; }
.updated, .when, footer, small { color: var(--muted); }
.updated { margin: 6px 0 0; font-size: 0.92rem; }
.nav a {
  display: inline-block;
  background: var(--accent);
  color: #fff;
  text-decoration: none;
  padding: 8px 14px;
  border-radius: 999px;
  font-weight: 600;
  font-size: 0.92rem;
}
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(min(100%, 158px), 1fr)); gap: 10px; }
.card, .panel {
  background: var(--card);
  border-radius: 12px;
  box-shadow: var(--shadow);
}
.card { padding: 12px; }
.panel { padding: 16px; margin-top: 14px; }
.name { font-weight: 650; font-size: 0.92rem; }
.sym { display: block; color: var(--muted); font-weight: 450; font-size: 0.75rem; margin-top: 1px; }
.sym.inline { display: inline; font-size: 0.95rem; font-weight: 500; }
.price { font-size: 1.35rem; font-weight: 750; margin: 6px 0 2px; font-variant-numeric: tabular-nums; letter-spacing: -0.02em; }
.chg { font-weight: 650; font-variant-numeric: tabular-nums; font-size: 0.92rem; }
.up { color: var(--up); }
.down { color: var(--down); }
.flat, .na { color: var(--muted); }
.price.na { font-size: 1.05rem; font-weight: 650; }
svg.spark { width: 100%; height: 32px; display: block; margin: 6px 0 2px; }
.chart { width: 100%; height: auto; display: block; }
.stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 10px; }
.stats div { background: var(--bg); border-radius: 8px; padding: 8px 10px; }
.stats b { display: block; margin-top: 2px; font-variant-numeric: tabular-nums; }
.stats small, .when { font-size: 0.75rem; }
.wrap { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; font-size: 0.9rem; min-width: 640px; }
th, td { text-align: left; padding: 8px 6px; border-bottom: 1px solid var(--line); vertical-align: top; }
th { font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.04em; color: var(--muted); }
.crumb { margin: 0 0 6px; font-size: 0.9rem; }
footer { margin-top: 28px; font-size: 0.8rem; }
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
@media (max-width: 640px) {
  main { padding: 14px 12px 36px; }
  h1 { font-size: 1.4rem; }
  .price { font-size: 1.2rem; }
  .nav { width: 100%; }
  .nav a { display: block; text-align: center; }
}
"""


def build(out_dir: Path, sleep_s: float = 0.12) -> dict:
    updated = datetime.now(HKT)
    groups = []
    flat = []
    ok_count = 0
    total = 0
    for title, items in SECTIONS:
        rendered = []
        for name, symbol in items:
            total += 1
            try:
                quote = fetch_chart(symbol, "1mo")
                ok_count += 1
                print(f"ok  {symbol} {quote['price']}", file=sys.stderr)
            except Exception as exc:  # noqa: BLE001 - one symbol must not abort the page
                quote = failed_quote(symbol, exc.__class__.__name__)
                print(f"fail {symbol} {exc.__class__.__name__}: {exc}", file=sys.stderr)
            quote["name"] = name
            rendered.append((name, quote))
            flat.append(quote)
            if sleep_s:
                time.sleep(sleep_s)
        groups.append((title, rendered))

    try:
        tencent = fetch_chart(TENCENT_SYMBOL, "1y")
        print(f"ok  {TENCENT_SYMBOL} {tencent['price']}", file=sys.stderr)
    except Exception as exc:  # noqa: BLE001
        tencent = failed_quote(TENCENT_SYMBOL, exc.__class__.__name__)
        print(f"fail {TENCENT_SYMBOL} {exc.__class__.__name__}: {exc}", file=sys.stderr)
    summary = None
    summary_error = None
    try:
        summary = fetch_quote_summary(TENCENT_SYMBOL)
        print("ok  0700.HK quoteSummary", file=sys.stderr)
    except Exception as exc:  # noqa: BLE001
        summary_error = f"{exc.__class__.__name__}: {exc}"
        print(f"fail 0700.HK quoteSummary {summary_error}", file=sys.stderr)
    rows = tencent_financial_rows(summary)

    site = out_dir
    (site / "assets").mkdir(parents=True, exist_ok=True)
    (site / "tencent").mkdir(parents=True, exist_ok=True)
    (site / "assets" / "style.css").write_text(STYLE, encoding="utf-8")
    (site / "index.html").write_text(render_markets(groups, updated), encoding="utf-8")
    (site / "tencent" / "index.html").write_text(render_tencent(tencent, rows, updated), encoding="utf-8")
    (site / ".nojekyll").write_text("", encoding="utf-8")
    data = {
        "generatedAt": iso_hkt(updated),
        "source": "Yahoo Finance chart API v8/finance/chart",
        "okCount": ok_count,
        "symbolCount": total,
        "quotes": flat,
        "tencent": {
            "quote": {key: value for key, value in tencent.items() if key != "series"},
            "series": tencent.get("series") or [],
            "quoteSummaryError": summary_error,
            "financials": rows,
        },
    }
    (site / "data.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(f"built {ok_count}/{total} symbols -> {site}", file=sys.stderr)
    if ok_count == 0:
        raise SystemExit("every symbol fetch failed; refusing to publish an empty dashboard")
    return data


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Build the financial markets dashboard")
    parser.add_argument("--out", default="site", help="output directory")
    parser.add_argument("--sleep", type=float, default=0.12, help="seconds between chart requests")
    args = parser.parse_args(argv)
    build(Path(args.out), sleep_s=args.sleep)


if __name__ == "__main__":
    main()
