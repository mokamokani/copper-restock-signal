#!/usr/bin/env python3
"""Fetch LME copper daily prices from Westmetall and write a normalised CSV.

Zero third-party dependencies: standard library only (urllib, re, csv, argparse).
The source publishes LME official settlement data as a plain HTML table.

Source : https://www.westmetall.com/en/markdaten.php?action=table&field=LME_Cu_cash
Columns: date | LME Copper Cash-Settlement | LME Copper 3-month | LME Copper stock
Units  : USD per tonne (prices), tonnes (stock)
"""

import argparse
import csv
import datetime as dt
import html
import os
import re
import sys
import urllib.request

SOURCE_URL = (
    "https://www.westmetall.com/en/markdaten.php"
    "?action=table&field=LME_Cu_cash"
)
SOURCE_NAME = "Westmetall (publishing LME official settlement data)"
UA = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/120 Safari/537.36"
    )
}
MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
}
HEADER = [
    "date", "cash_settlement_usd_per_t", "three_month_usd_per_t",
    "lme_stock_t", "source", "retrieved_at",
]


def fetch(url, attempts=3, timeout=30):
    last = None
    for i in range(attempts):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8", "ignore")
        except Exception as exc:  # network / TLS / transient 5xx
            last = exc
    raise RuntimeError("fetch failed after %d attempts: %r" % (attempts, last))


def parse_date(text):
    m = re.search(r"(\d{1,2})\.\s*([A-Za-z]+)\s*(\d{4})", text)
    if not m:
        return None
    month = MONTHS.get(m.group(2).lower())
    if month is None:
        return None
    try:
        return dt.date(int(m.group(3)), month, int(m.group(1))).isoformat()
    except ValueError:
        return None


def parse_number(text):
    cleaned = text.replace("\xa0", "").replace(",", "").strip()
    m = re.search(r"-?\d+(?:\.\d+)?", cleaned)
    return float(m.group(0)) if m else None


def parse_rows(page):
    out = []
    for row in re.findall(r"<tr[^>]*>([\s\S]*?)</tr>", page, re.I):
        cells = [
            html.unescape(re.sub(r"<[^>]+>", "", c)).strip()
            for c in re.findall(r"<td[^>]*>([\s\S]*?)</td>", row, re.I)
        ]
        if len(cells) < 4:
            continue
        date = parse_date(cells[0])
        cash = parse_number(cells[1])
        three = parse_number(cells[2])
        stock = parse_number(cells[3])
        if not date or cash is None or three is None or stock is None:
            continue
        # Guard against a wrong column order or a units change at the source.
        if not (500 < cash < 100000 and 500 < three < 100000 and 0 < stock < 5e7):
            continue
        out.append((date, cash, three, stock))
    # Source is newest-first; de-duplicate and sort ascending for time series work.
    by_date = {r[0]: r for r in out}
    return [by_date[k] for k in sorted(by_date)]


def main():
    ap = argparse.ArgumentParser()
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ap.add_argument("--out", default=os.path.join(root, "data", "prices_lme_cash.csv"))
    ap.add_argument("--max-staleness-days", type=int, default=7)
    args = ap.parse_args()

    rows = parse_rows(fetch(SOURCE_URL))
    if len(rows) < 60:
        print("ERROR: only %d usable rows; source layout may have changed" % len(rows))
        return 2

    today = dt.date.today()
    latest = dt.date.fromisoformat(rows[-1][0])
    staleness = (today - latest).days
    retrieved = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8", newline="\n") as fh:
        w = csv.writer(fh)
        w.writerow(HEADER)
        for date, cash, three, stock in rows:
            w.writerow([date, "%.2f" % cash, "%.2f" % three, "%.0f" % stock,
                        SOURCE_NAME, retrieved])

    print("source        : %s" % SOURCE_NAME)
    print("rows          : %d (%s -> %s)" % (len(rows), rows[0][0], rows[-1][0]))
    print("latest close  : cash %.2f | 3m %.2f | stock %.0f t"
          % (rows[-1][1], rows[-1][2], rows[-1][3]))
    print("staleness     : %d day(s) vs %s (limit %d)"
          % (staleness, today.isoformat(), args.max_staleness_days))
    print("written       : %s" % args.out)
    if staleness > args.max_staleness_days:
        print("WARNING: data is stale; do not treat it as current")
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
