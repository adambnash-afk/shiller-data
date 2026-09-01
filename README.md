# shiller-data

A small, self-owned JSON feed of Robert Shiller's US stock market dataset, published to GitHub Pages for [CAPE Dashboard](https://apps.apple.com/us/app/capedashboard/id6759525511).

## Why this exists

CAPE Dashboard previously read from a third-party wrapper of Shiller's data. That feed stopped publishing on **22 December 2025** and went unnoticed for eight months, because it kept advertising weekly updates while serving frozen data. The cause: it downloaded from `econ.yale.edu/~shiller/data.htm`, and Shiller moved his files to `shillerdata.com`.

This pipeline is built to fail loudly instead of quietly:

- It **scrapes `shillerdata.com` for the current `ie_data.xls` link** rather than hardcoding it. The real URL is a GoDaddy blob with a rotating UUID and `?ver=` timestamp, so hardcoding guarantees eventual breakage.
- It maps spreadsheet columns **by header text, not position**. Shiller's sheet contains two blank spacer columns; a positional parser silently mis-maps TR CAPE and Excess CAPE Yield.
- It **refuses to publish** on any anomaly: missing download link, an unmappable column, too few records, CAPE outside a sane range, or output that would regress against what is already published. A failed run emails the repo owner.
- A separate **freshness audit** workflow fails if the feed falls behind, so a broken build job cannot also silence the alarm.
- A **keepalive** step prevents GitHub from auto-disabling the cron after 60 days of inactivity.

## Endpoints

| File | Contents |
|---|---|
| `data/stock_market_data.json` | Full monthly series, 1871-01 to present |
| `data/latest.json` | Metadata plus the single most recent record |

Published at `https://<owner>.github.io/shiller-data/data/stock_market_data.json`.

## Schema

`metadata`: `source`, `source_url`, `last_updated`, `description`, `total_records`, `latest_date`.

Each record in `data`:

| Field | Notes |
|---|---|
| `date` | `YYYY.MM` as a float, e.g. `2026.08` |
| `year`, `month`, `date_string` | Explicit, so consumers never parse the float |
| `sp500`, `dividend`, `earnings` | **Nominal** — dollars of that month |
| `real_price`, `real_dividend`, `real_earnings` | **CPI-adjusted** to the latest month |
| `cpi`, `date_fraction`, `long_interest_rate` | As published |
| `cape`, `tr_cape` | Pre-computed by Shiller; `null` before 1881 |
| `excess_cape_yield` | CAPE earnings yield less the 10-year real bond yield |
| `forward_10y_real_return` | Realized subsequent 10-year annualized real return; necessarily `null` for the most recent decade |

> **Never pair a `real_*` field with a nominal one.** `real_price / earnings` is not a P/E — it multiplies in the CPI ratio (~27x for 1871). Use `sp500 / earnings` or `real_price / real_earnings`; they are identical.

Missing values are `null` (Shiller uses `NA` and blanks).

## Running locally

```bash
pip install "xlrd==2.0.*"
python3 scripts/build_data.py --out data                    # download
python3 scripts/build_data.py --out data --local ie_data.xls  # use a local copy
```

## Attribution

Data by Robert J. Shiller, Yale University, published alongside *Irrational Exuberance* (Princeton University Press) and freely available at [shillerdata.com](https://shillerdata.com/). Not affiliated with or endorsed by Yale University or Robert J. Shiller.
