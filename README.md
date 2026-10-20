# Global financial information

Static dashboard of global indices, foreign exchange, commodities, crypto, US Treasury yields, and VIX, plus a Tencent (0700.HK) page. The home page is the market board. Tencent is linked from the header.

Numbers come from Yahoo Finance at build time:

- Quotes, daily change versus the prior daily close, market time, and charts: `https://query1.finance.yahoo.com/v8/finance/chart/<SYMBOL>?range=1mo&interval=1d` (1 year for Tencent).
- Tencent key financials: Yahoo Finance `quoteSummary`.

If a symbol or field is missing, the page says **not available**. Nothing is filled in by hand.

## Refresh

[`.github/workflows/pages.yml`](.github/workflows/pages.yml) runs on a schedule every 4 hours, on `workflow_dispatch`, and on every push to `main`. It fetches the data, builds `site/`, and deploys with `actions/upload-pages-artifact` and `actions/deploy-pages`.

GitHub Pages has to use **GitHub Actions** as the source. In the repo: **Settings → Pages → Build and deployment → Source → GitHub Actions**.

The live site is [https://c463399-beep.github.io/global-financial-information/](https://c463399-beep.github.io/global-financial-information/).

## Local build

Python 3.11+ and the standard library are enough.

```bash
python scripts/build_site.py --out site
python -m unittest tests/test_build_site.py
```

Open `site/index.html`. `site/` is generated and is not committed.
