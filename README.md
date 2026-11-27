# Global financial information

Static dashboard of global indices, foreign exchange, commodities, crypto, US Treasury yields, and VIX, plus the 10 most active Nasdaq-listed names by dollar volume, and a Tencent (0700.HK) page. The home page is the market board. Tencent is linked from the header.

Numbers come from public sources at build time:

- Quotes, daily change versus the prior daily close, market time, and charts: `https://query1.finance.yahoo.com/v8/finance/chart/<SYMBOL>?range=1mo&interval=1d` (1 year for Tencent).
- Tencent key financials: Yahoo Finance `quoteSummary`.
- Nasdaq section: `https://api.nasdaq.com/api/marketmovers?assetclass=stocks` (`MostActiveByDollarVolume`), then `https://api.nasdaq.com/api/quote/<SYMBOL>/info` to keep names whose exchange starts with `NASDAQ` and to copy share volume. This is a ranking of active names, not individual large prints. Nasdaq's public realtime-trades feed is empty outside the session and is not a market-wide list of the biggest trades. Dollar volume is shown only when the movers response includes that amount; otherwise the cell says **not available**.

If a symbol or field is missing, the page says **not available**. Nothing is filled in by hand. Times are Hong Kong time. A Nasdaq date that has no clock is labeled clock time not available instead of being given a made-up hour.

## Refresh

[`.github/workflows/pages.yml`](.github/workflows/pages.yml) runs on a schedule every 4 hours, on `workflow_dispatch`, and on every push to `main`. It fetches Yahoo quotes and the Nasdaq most-active list, builds `site/`, and deploys with `actions/upload-pages-artifact` and `actions/deploy-pages`. The Nasdaq section is part of that same build, so it refreshes on the 4-hour schedule.

GitHub Pages has to use **GitHub Actions** as the source. In the repo: **Settings → Pages → Build and deployment → Source → GitHub Actions**.

The live site is [https://c463399-beep.github.io/global-financial-information/](https://c463399-beep.github.io/global-financial-information/).

## Local build

Python 3.11+ and the standard library are enough.

```bash
python scripts/build_site.py --out site
python -m unittest tests/test_build_site.py
```

Open `site/index.html`. `site/` is generated and is not committed.
