# Copper procurement signal — 2026-11-17

> Data as of **2026-11-16** (latest LME cash settlement). Staleness: 1 day(s).  
> Source: [Westmetall](https://www.westmetall.com/en/markdaten.php?action=table&field=LME_Cu_cash), publishing LME official settlement data.  
> Unit: USD per tonne for prices, tonnes for LME stock. Prices are end-of-day, not real-time.

## Snapshot

| Metric | Value |
| --- | --- |
| LME copper cash settlement | 13375.00 USD/t |
| LME copper 3-month | 13305.00 USD/t |
| Cash vs 3-month (basis) | +0.53% |
| LME stock | 327475 t |
| 1-session change | -1.73% |
| 5-session change | -4.67% |
| MA20 | 14188.58 USD/t |
| Deviation from MA20 | -5.73% |
| Price rank in last 60 sessions | 2% |
| LME stock, 5-session change | +3.17% |

## Signals

| Signal | Status | What it means |
| --- | --- | --- |
| A · Surge → consider locking in | **WATCH** | Price rising fast; buying later may cost more. A risk warning, not a 'cheap price' signal. |
| B · Dip → consider restocking | **TRIGGER** | Price back near recent lows relative to its MA and 60-session range. |

Rules (all thresholds in `config.json`, all example values):

- A triggers when the 1-session move ≥ 2.0% (with a non-negative 5-session confirmation) or the 5-session move ≥ 5.0%.
- B triggers when price is ≤ -3.0% vs MA20 **and** ranks in the bottom 25% of the last 60 sessions.
- Any trigger enters a 5-day cooldown so one price wave produces one alert, not five.

Today's evaluation:

- A: 5d -4.67%, basis +0.53% — momentum or backwardation building, not yet a trigger
- B: -5.73% vs MA20 and rank 2% of last 60 sessions

## Backtest on the available history

Window: **2026-01-02 → 2026-11-16**, 222 trading sessions of real published prices.

Trigger counts below are **raw signals without cooldown**; the live report above applies cooldown.

| Item | Result |
| --- | --- |
| A (surge) trigger days | 18 |
| B (dip) trigger days | 15 |
| Baseline: first session of each month | 11 sessions |
| Baseline average price | 13454.32 USD/t |
| Average price on B trigger days | 12673.37 USD/t |
| Difference (signal − baseline) | -5.80% |

Both strategies are compared on the **same total tonnage split evenly across their buy days**,
so only the average purchase price differs. Illustrative volume: 5.0 t per order / 20.0 t per month
— **SYNTHETIC parameters, not real company data.**

Most recent B trigger dates: 2026-03-19, 2026-03-20, 2026-03-23, 2026-03-24, 2026-03-25, 2026-03-26, 2026-03-27, 2026-11-06, 2026-11-11, 2026-11-12, 2026-11-13, 2026-11-16.

## Caveats

- This is a price-timing simulation. It ignores premiums/discounts, freight, FX, tax,
  minimum order quantities, supplier terms, carrying cost and credit lines.
- A negative 'difference' means the signal days were cheaper on average **in this sample**.
  It is not evidence of realised savings and does not predict future prices.
- LME cash (USD/t, international benchmark) is not the same as domestic 1# electrolytic
  copper spot (CNY/t). Do not mix the two when setting real purchase thresholds.
- Thresholds are example values; confirm every one with the business before operational use.
- Research and portfolio demonstration only. Not purchasing, trading or investment advice.
