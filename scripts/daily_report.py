#!/usr/bin/env python3
"""Build the daily copper procurement report from the local price CSV.

Zero third-party dependencies. Reads config.json + data/prices_lme_cash.csv,
computes two independent signal families, applies a cooldown, backtests on the
available history and writes a Markdown report. Optionally emails on trigger.

Signal A (SURGE)  : price rising fast -> consider locking in a purchase early.
Signal B (DIP)    : price back near recent lows -> consider restocking.
These are research signals, not purchasing or investment advice.
"""

import argparse
import csv
import datetime as dt
import json
import os
import smtplib
import sys
from email.message import EmailMessage

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE_URL = "https://www.westmetall.com/en/markdaten.php?action=table&field=LME_Cu_cash"


def load_config(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def load_prices(path):
    rows = []
    with open(path, encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            rows.append({
                "date": r["date"],
                "cash": float(r["cash_settlement_usd_per_t"]),
                "three": float(r["three_month_usd_per_t"]),
                "stock": float(r["lme_stock_t"]),
            })
    rows.sort(key=lambda x: x["date"])
    return rows


def pct(new, old):
    return (new / old - 1.0) * 100.0 if old else 0.0


def series(rows, cfg):
    """Attach indicators. Index i is only fully usable after max(ma, lookback)."""
    ma_n = cfg["ma_window"]
    look = cfg["lookback_days"]
    prices = [r["cash"] for r in rows]
    out = []
    for i, r in enumerate(rows):
        d = dict(r)
        d["chg_1d"] = pct(prices[i], prices[i - 1]) if i >= 1 else None
        d["chg_5d"] = pct(prices[i], prices[i - 5]) if i >= 5 else None
        d["ma20"] = sum(prices[i - ma_n + 1:i + 1]) / ma_n if i >= ma_n - 1 else None
        d["dev_ma20"] = pct(prices[i], d["ma20"]) if d["ma20"] else None
        window = prices[max(0, i - look + 1):i + 1]
        d["pct_rank"] = sum(1 for p in window if p <= prices[i]) / len(window)
        d["basis"] = pct(r["cash"], r["three"])
        d["stock_5d"] = (pct(rows[i]["stock"], rows[i - 5]["stock"])
                         if i >= 5 and rows[i - 5]["stock"] else None)
        out.append(d)
    return out


def classify(d, cfg):
    """Return (surge_level, dip_level, reasons)."""
    surge, dip = "CLEAR", "CLEAR"
    reasons = []

    one_day = d["chg_1d"] is not None and d["chg_1d"] >= cfg["surge_1d_pct"]
    five_day = d["chg_5d"] is not None and d["chg_5d"] >= cfg["surge_5d_pct"]
    confirmed = (not cfg["surge_requires_5d_confirmation"]
                 or (d["chg_5d"] is not None and d["chg_5d"] >= 0))
    if (one_day and confirmed) or five_day:
        surge = "TRIGGER"
        reasons.append("A: 1d %+.2f%% / 5d %+.2f%% crossed the surge threshold"
                       % (d["chg_1d"], d["chg_5d"]))
    elif (d["chg_5d"] is not None and d["chg_5d"] >= cfg["watch_5d_pct"]) or d["basis"] > 0.5:
        surge = "WATCH"
        reasons.append("A: 5d %+.2f%%, basis %+.2f%% — momentum or backwardation building, not yet a trigger"
                       % (d["chg_5d"] or 0.0, d["basis"]))

    below_ma = (d["dev_ma20"] is not None
                and d["dev_ma20"] <= cfg["dip_vs_ma20_pct"])
    low_rank = d["pct_rank"] <= cfg["percentile_60d_max"]
    if below_ma and low_rank:
        dip = "TRIGGER"
        reasons.append("B: %+.2f%% vs MA%d and rank %.0f%% of last %d sessions"
                       % (d["dev_ma20"], cfg["ma_window"], d["pct_rank"] * 100,
                          cfg["lookback_days"]))
    elif (d["dev_ma20"] is not None
          and (d["dev_ma20"] <= cfg["watch_dev_pct"] or d["pct_rank"] <= 0.35)):
        dip = "WATCH"
        reasons.append("B: price softening towards the restock zone")

    return surge, dip, reasons


def apply_cooldown(levels, as_of, cfg, state_path):
    """One wave of prices should not produce five near-identical alerts."""
    state = {}
    if os.path.exists(state_path):
        try:
            with open(state_path, encoding="utf-8") as fh:
                state = json.load(fh)
        except (ValueError, OSError):
            state = {}
    today = dt.date.fromisoformat(as_of)
    result = {}
    for key, level in levels.items():
        if level != "TRIGGER":
            result[key] = level
            continue
        last = state.get(key)
        if last:
            gap = (today - dt.date.fromisoformat(last)).days
            if 0 <= gap < cfg["cooldown_days"]:
                result[key] = "COOLDOWN"
                continue
        state[key] = as_of
        result[key] = level
    os.makedirs(os.path.dirname(state_path), exist_ok=True)
    with open(state_path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(state, fh, indent=2)
        fh.write("\n")
    return result


def backtest(data, cfg):
    """Historic signal frequency + a same-volume, price-only timing comparison."""
    surge_days, dip_days = [], []
    for d in data:
        s, b, _ = classify(d, cfg)
        if s == "TRIGGER":
            surge_days.append(d)
        if b == "TRIGGER":
            dip_days.append(d)

    first_of_month, seen = [], set()
    for d in data:
        key = d["date"][:7]
        if key not in seen:
            seen.add(key)
            first_of_month.append(d)

    def avg(ds):
        return sum(x["cash"] for x in ds) / len(ds) if ds else None

    base_avg, dip_avg = avg(first_of_month), avg(dip_days)
    delta = (pct(dip_avg, base_avg) if base_avg and dip_avg else None)
    return {
        "surge_days": surge_days,
        "dip_days": dip_days,
        "baseline_days": first_of_month,
        "baseline_avg": base_avg,
        "dip_avg": dip_avg,
        "delta_pct": delta,
    }


def render(data, cfg, as_of, levels, reasons, bt, staleness):
    d = data[-1]
    p = cfg["synthetic_procurement"]

    def row(label, value):
        return "| %s | %s |" % (label, value)

    def fmt(x, suffix="%"):
        return "n/a" if x is None else "%+.2f%s" % (x, suffix)

    lines = [
        "# Copper procurement signal — %s" % dt.date.today().isoformat(),
        "",
        "> Data as of **%s** (latest LME cash settlement). Staleness: %d day(s).  " % (as_of, staleness),
        "> Source: [%s](%s), publishing LME official settlement data.  " % ("Westmetall", SOURCE_URL),
        "> Unit: USD per tonne for prices, tonnes for LME stock. Prices are end-of-day, not real-time.",
        "",
        "## Snapshot",
        "",
        "| Metric | Value |",
        "| --- | --- |",
        row("LME copper cash settlement", "%.2f USD/t" % d["cash"]),
        row("LME copper 3-month", "%.2f USD/t" % d["three"]),
        row("Cash vs 3-month (basis)", fmt(d["basis"])),
        row("LME stock", "%.0f t" % d["stock"]),
        row("1-session change", fmt(d["chg_1d"])),
        row("5-session change", fmt(d["chg_5d"])),
        row("MA%d" % cfg["ma_window"], "n/a" if d["ma20"] is None else "%.2f USD/t" % d["ma20"]),
        row("Deviation from MA%d" % cfg["ma_window"], fmt(d["dev_ma20"])),
        row("Price rank in last %d sessions" % cfg["lookback_days"], "%.0f%%" % (d["pct_rank"] * 100)),
        row("LME stock, 5-session change", fmt(d["stock_5d"])),
        "",
        "## Signals",
        "",
        "| Signal | Status | What it means |",
        "| --- | --- | --- |",
        "| A · Surge → consider locking in | **%s** | Price rising fast; buying later may cost more. A risk warning, not a 'cheap price' signal. |" % levels["A"],
        "| B · Dip → consider restocking | **%s** | Price back near recent lows relative to its MA and 60-session range. |" % levels["B"],
        "",
        "Rules (all thresholds in `config.json`, all example values):",
        "",
        "- A triggers when the 1-session move ≥ %.1f%% (with a non-negative 5-session confirmation) or the 5-session move ≥ %.1f%%." % (cfg["surge_1d_pct"], cfg["surge_5d_pct"]),
        "- B triggers when price is ≤ %.1f%% vs MA%d **and** ranks in the bottom %.0f%% of the last %d sessions." % (cfg["dip_vs_ma20_pct"], cfg["ma_window"], cfg["percentile_60d_max"] * 100, cfg["lookback_days"]),
        "- Any trigger enters a %d-day cooldown so one price wave produces one alert, not five." % cfg["cooldown_days"],
        "",
    ]
    if reasons:
        lines += ["Today's evaluation:", ""] + ["- " + r for r in reasons] + [""]
    else:
        lines += ["Today's evaluation: neither signal is close to its trigger.", ""]

    lines += [
        "## Backtest on the available history",
        "",
        "Window: **%s → %s**, %d trading sessions of real published prices." % (data[0]["date"], data[-1]["date"], len(data)),
        "",
        "Trigger counts below are **raw signals without cooldown**; the live report above applies cooldown.",
        "",
        "| Item | Result |",
        "| --- | --- |",
        row("A (surge) trigger days", str(len(bt["surge_days"]))),
        row("B (dip) trigger days", str(len(bt["dip_days"]))),
        row("Baseline: first session of each month", "%d sessions" % len(bt["baseline_days"])),
        row("Baseline average price", "n/a" if bt["baseline_avg"] is None else "%.2f USD/t" % bt["baseline_avg"]),
        row("Average price on B trigger days", "n/a" if bt["dip_avg"] is None else "%.2f USD/t" % bt["dip_avg"]),
        row("Difference (signal − baseline)", "n/a" if bt["delta_pct"] is None else "%+.2f%%" % bt["delta_pct"]),
        "",
        "Both strategies are compared on the **same total tonnage split evenly across their buy days**,",
        "so only the average purchase price differs. Illustrative volume: %.1f t per order / %.1f t per month" % (p["order_size_t"], p["monthly_usage_t"]),
        "— **SYNTHETIC parameters, not real company data.**",
        "",
    ]
    if bt["dip_days"]:
        sample = ", ".join(x["date"] for x in bt["dip_days"][-12:])
        lines += ["Most recent B trigger dates: %s." % sample, ""]

    lines += [
        "## Caveats",
        "",
        "- This is a price-timing simulation. It ignores premiums/discounts, freight, FX, tax,",
        "  minimum order quantities, supplier terms, carrying cost and credit lines.",
        "- A negative 'difference' means the signal days were cheaper on average **in this sample**.",
        "  It is not evidence of realised savings and does not predict future prices.",
        "- LME cash (USD/t, international benchmark) is not the same as domestic 1# electrolytic",
        "  copper spot (CNY/t). Do not mix the two when setting real purchase thresholds.",
        "- Thresholds are example values; confirm every one with the business before operational use.",
        "- Research and portfolio demonstration only. Not purchasing, trading or investment advice.",
        "",
    ]
    return "\n".join(lines)


def maybe_email(report, levels, cfg, as_of):
    triggered = any(v == "TRIGGER" for v in levels.values())
    if cfg.get("email_on_trigger_only", True) and not triggered:
        return "skipped (no trigger today; daily digest disabled)"
    host = os.environ.get("SMTP_HOST")
    user = os.environ.get("SMTP_USER")
    pwd = os.environ.get("SMTP_PASS")
    to = os.environ.get("ALERT_TO")
    if not all([host, user, pwd, to]):
        return "skipped (SMTP secrets not configured)"
    msg = EmailMessage()
    triggered = ", ".join(k for k, v in levels.items() if v == "TRIGGER")
    msg["Subject"] = "[Copper signal %s] %s triggered" % (as_of, triggered)
    msg["From"] = user
    msg["To"] = to
    msg.set_content(report)
    try:
        with smtplib.SMTP_SSL(host, int(os.environ.get("SMTP_PORT", "465"))) as s:
            s.login(user, pwd)
            s.send_message(msg)
        return "sent to %s" % to
    except Exception as exc:
        return "failed: %r" % exc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prices", default=os.path.join(ROOT, "data", "prices_lme_cash.csv"))
    ap.add_argument("--config", default=os.path.join(ROOT, "config.json"))
    ap.add_argument("--reports-dir", default=os.path.join(ROOT, "reports"))
    ap.add_argument("--state", default=os.path.join(ROOT, "data", "alert_state.json"))
    ap.add_argument("--no-state", action="store_true", help="Do not read or write cooldown state")
    ap.add_argument("--send-email", action="store_true")
    args = ap.parse_args()

    cfg = load_config(args.config)
    rows = load_prices(args.prices)
    if len(rows) < cfg["lookback_days"]:
        print("ERROR: not enough history (%d rows)" % len(rows))
        return 2
    data = series(rows, cfg)
    as_of = data[-1]["date"]
    staleness = (dt.date.today() - dt.date.fromisoformat(as_of)).days
    if staleness > cfg["max_staleness_days"]:
        print("WARNING: data is %d days stale" % staleness)

    raw_a, raw_b, reasons = classify(data[-1], cfg)
    if args.no_state:
        levels = {"A": raw_a, "B": raw_b}
    else:
        levels = apply_cooldown({"A": raw_a, "B": raw_b}, as_of, cfg, args.state)
    bt = backtest(data, cfg)
    report = render(data, cfg, as_of, levels, reasons, bt, staleness)

    os.makedirs(args.reports_dir, exist_ok=True)
    dated = os.path.join(args.reports_dir, "%s.md" % dt.date.today().isoformat())
    for path in (dated, os.path.join(args.reports_dir, "LATEST.md")):
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(report)
    print("as of        : %s (%d sessions, staleness %d day(s))" % (as_of, len(data), staleness))
    print("signal A     : %s -> %s" % (raw_a, levels["A"]))
    print("signal B     : %s -> %s" % (raw_b, levels["B"]))
    print("backtest     : A=%d triggers, B=%d triggers, baseline=%d sessions, delta=%s"
          % (len(bt["surge_days"]), len(bt["dip_days"]), len(bt["baseline_days"]),
             "n/a" if bt["delta_pct"] is None else "%+.2f%%" % bt["delta_pct"]))
    print("report       : %s" % dated)
    print("email        : %s" % (maybe_email(report, levels, cfg, as_of)
                                 if args.send_email else "disabled (use --send-email)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
