#!/usr/bin/env python3
"""
Build a JSON feed of Robert Shiller's US stock market dataset.

Downloads ie_data.xls from shillerdata.com (resolving the rotating blob URL by
scraping the page, rather than hardcoding it -- hardcoding is what killed the
previous third-party feed when Shiller moved off econ.yale.edu) and emits JSON
matching the schema CAPE Dashboard already decodes.

Usage:
    python3 build_data.py --out ../data            # download from shillerdata.com
    python3 build_data.py --out ../data --local ie_data.xls   # use a local file
"""

import argparse
import datetime as dt
import json
import os
import re
import sys
import urllib.request

import xlrd

SOURCE_PAGE = "https://shillerdata.com/"
UA = "Mozilla/5.0 (compatible; shiller-data-pipeline/1.0; +https://github.com/)"

# Canonical column identity -> substrings that must all appear in the stitched
# header text (lowercased). Header-driven, so blank spacer columns and any
# future column insertion cannot shift the mapping.
COLUMN_MATCHERS = {
    "date":               [["date"], ["fraction"]],          # require 'date', forbid 'fraction'
    "sp500":              [["s&p", "comp"], []],
    "dividend":           [["dividend", " d"], ["real"]],
    "earnings":           [["earnings", " e"], ["real", "scaled", "ratio"]],
    "cpi":                [["consumer price"], []],
    "date_fraction":      [["date fraction"], []],
    "long_interest_rate": [["long interest rate"], []],
    "real_price":         [["real price"], ["total return"]],
    "real_dividend":      [["real dividend"], []],
    "real_earnings":      [["real earnings"], ["scaled"]],
    "cape":               [["cyclically adjusted", "cape"], ["total return", "tr cape", "excess"]],
    "tr_cape":            [["tr cape"], ["excess"]],
    "excess_cape_yield":  [["excess cape yield"], []],
    "forward_10y_real_return": [["10 year annualized stock", "real return"], ["bonds"]],
}

MIN_EXPECTED_ROWS = 1800     # dataset began 1871-01; anything smaller means a bad parse
CAPE_SANE_RANGE = (3.0, 80.0)


def log(msg):
    print(msg, flush=True)


def resolve_xls_url():
    """Scrape shillerdata.com for the current ie_data.xls download URL."""
    req = urllib.request.Request(SOURCE_PAGE, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        html = r.read().decode("utf-8", "replace")

    candidates = re.findall(r'href=["\']([^"\']*ie_data\.xls[^"\']*)["\']', html, re.I)
    if not candidates:
        raise SystemExit(
            "FATAL: no ie_data.xls link found on %s -- the page layout changed. "
            "Refusing to publish stale data." % SOURCE_PAGE
        )
    url = candidates[0]
    if url.startswith("//"):
        url = "https:" + url
    elif url.startswith("/"):
        url = SOURCE_PAGE.rstrip("/") + url
    log("Resolved ie_data.xls -> %s" % url)
    return url


def download(url, dest):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=120) as r, open(dest, "wb") as f:
        f.write(r.read())
    log("Downloaded %s (%d bytes)" % (dest, os.path.getsize(dest)))
    return dest


def stitch_headers(sheet, header_rows=range(1, 8)):
    """Shiller's header spans ~7 rows; join them per column into one string."""
    out = {}
    for c in range(sheet.ncols):
        parts = [str(sheet.cell_value(r, c)).strip() for r in header_rows]
        out[c] = re.sub(r"\s+", " ", " ".join(p for p in parts if p)).strip().lower()
    return out


def map_columns(headers):
    mapping = {}
    for key, (required, forbidden) in COLUMN_MATCHERS.items():
        hits = []
        for c, text in headers.items():
            if not text:
                continue
            if all(tok in text for tok in required) and not any(tok in text for tok in forbidden):
                hits.append(c)
        if not hits:
            raise SystemExit(
                "FATAL: could not locate column %r in the spreadsheet. "
                "Shiller's layout changed -- refusing to publish a mis-mapped feed." % key
            )
        mapping[key] = hits[0]
    return mapping


def num(v):
    """Shiller uses 'NA' and '' for missing. Everything else must be numeric."""
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    return None


def parse(path):
    sheet = xlrd.open_workbook(path).sheet_by_name("Data")
    headers = stitch_headers(sheet)
    cols = map_columns(headers)
    log("Column map: %s" % json.dumps(cols, sort_keys=True))

    DATA_START = 8
    records = []
    skipped = 0
    for r in range(DATA_START, sheet.nrows):
        raw_date = sheet.cell_value(r, cols["date"])
        d = num(raw_date)
        # Footnote rows at the bottom carry text in random columns and no date.
        if d is None or d < 1800 or d > 2200:
            skipped += 1
            continue

        year = int(d)
        # Encoded YYYY.MM; float noise means round rather than truncate.
        month = int(round((d - year) * 100))
        if not 1 <= month <= 12:
            skipped += 1
            continue

        rec = {
            "date": round(d, 2),
            "year": year,
            "month": month,
            "date_string": "%04d-%02d-01" % (year, month),
        }
        for key, c in cols.items():
            if key == "date":
                continue
            rec[key] = num(sheet.cell_value(r, c))
        records.append(rec)

    log("Parsed %d records (skipped %d non-data rows)" % (len(records), skipped))
    return records


def validate(records, previous_path):
    """Fail loudly rather than publish a broken feed."""
    if len(records) < MIN_EXPECTED_ROWS:
        raise SystemExit("FATAL: only %d records parsed, expected >= %d."
                         % (len(records), MIN_EXPECTED_ROWS))

    first, last = records[0], records[-1]
    if (first["year"], first["month"]) != (1871, 1):
        raise SystemExit("FATAL: series should start 1871-01, got %s." % first["date_string"])

    capes = [r["cape"] for r in records if r["cape"] is not None]
    if not capes:
        raise SystemExit("FATAL: no CAPE values parsed.")
    lo, hi = min(capes), max(capes)
    if lo < CAPE_SANE_RANGE[0] or hi > CAPE_SANE_RANGE[1]:
        raise SystemExit("FATAL: CAPE out of sane range (%.2f..%.2f)." % (lo, hi))

    # Never go backwards relative to what we already published.
    if os.path.exists(previous_path):
        try:
            with open(previous_path) as f:
                prev = json.load(f)
            prev_recs = prev.get("data", [])
            if len(records) < len(prev_recs):
                raise SystemExit("FATAL: new feed has %d records, published feed has %d. Refusing to regress."
                                 % (len(records), len(prev_recs)))
            if prev_recs:
                p = prev_recs[-1]
                if (last["year"], last["month"]) < (p["year"], p["month"]):
                    raise SystemExit("FATAL: new latest date %s is older than published %s."
                                     % (last["date_string"], p["date_string"]))
        except (ValueError, KeyError) as e:
            log("WARN: could not compare against previous feed (%s); continuing." % e)

    log("Validation OK. Range %s .. %s, latest CAPE=%s"
        % (first["date_string"], last["date_string"], last["cape"]))
    return True


def write(records, outdir):
    os.makedirs(outdir, exist_ok=True)
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    last = records[-1]

    payload = {
        "metadata": {
            "source": "Robert Shiller - Yale Economics",
            "source_url": SOURCE_PAGE,
            "last_updated": now,
            "description": "U.S. Stock Market Data including S&P 500, earnings, dividends, and CAPE ratio",
            "total_records": len(records),
            "latest_date": last["date_string"],
        },
        "data": records,
    }
    full = os.path.join(outdir, "stock_market_data.json")
    with open(full, "w") as f:
        json.dump(payload, f, separators=(",", ":"))
    log("Wrote %s (%d records, %d bytes)" % (full, len(records), os.path.getsize(full)))

    latest = os.path.join(outdir, "latest.json")
    with open(latest, "w") as f:
        json.dump({"metadata": payload["metadata"], "data": [last]}, f, indent=2)
    log("Wrote %s" % latest)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, help="output directory for JSON")
    ap.add_argument("--local", help="use a local ie_data.xls instead of downloading")
    ap.add_argument("--work", default="/tmp/shiller_work", help="scratch dir for the download")
    args = ap.parse_args()

    if args.local:
        path = args.local
        log("Using local file %s" % path)
    else:
        os.makedirs(args.work, exist_ok=True)
        path = download(resolve_xls_url(), os.path.join(args.work, "ie_data.xls"))

    records = parse(path)
    validate(records, os.path.join(args.out, "stock_market_data.json"))
    write(records, args.out)
    log("Done.")


if __name__ == "__main__":
    main()
