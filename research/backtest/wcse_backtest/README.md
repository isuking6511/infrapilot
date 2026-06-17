# WCSE v3 Backtester

Python port of `WCSE_engine_v3_19.pine` (Wave-Confluence Structural Engine).
The Pine file is the source of truth; this code reproduces its logic bar-by-bar
with strict no-look-ahead semantics.

## Quick start
```bash
cd wcse_backtest

# install deps (one time)
pip3 install ccxt pandas numpy pyyaml matplotlib pyarrow

# sanity check on BTC/USDT 1d (Pine defaults, reversion-only)
python3 run.py mvp

# 4h sanity (more samples)
python3 run.py mvp 4h

# full grid sweep (symbols × TFs × entry modes × neely × vol × score × weights)
python3 run.py grid
```

## File map
| file | purpose |
|---|---|
| `config.yaml` | symbols, TFs, fees, Pine defaults, experiment grid |
| `data.py` | ccxt OHLCV fetcher with parquet cache |
| `structure.py` | pivots, ITH/ITL, BOS/CHoCH, neckline, ATH/ATL anchors, ATR |
| `confluence.py` | level sources (wick, FVG, fib, diag, fork, neckline, Donchian, zone) + clustering |
| `neely.py` | Neely Rule 1~7 + impulse hard rules |
| `strategy.py` | bias + reversion sticky setup + breakout setup |
| `backtest.py` | event loop, walk-forward split, position management |
| `metrics.py` | win rate, PF, expectancy R, MDD, CAGR, by-mode/dir |
| `report.py` | trades.csv, equity png, leaderboard.csv |
| `run.py` | entry point (mvp / grid) |
| `tests/` | pivot / ITH / ATR / cluster / look-ahead unit tests |

## Pine → Python contract

The defaults in `config.yaml > pine_defaults` mirror the inputs in
`WCSE_engine_v3_19.pine` line-for-line. Don't change them to "improve" —
those values *are* v3. Use `config.yaml > grid` instead for experimentation.

### What was ported as-is
- Pivot detection: `ta.pivothigh(high, 8, 8)` — confirms 8 bars after the pivot.
- ITH/ITL 3-pivot classification.
- BOS / CHoCH on close vs cur_ith / cur_itl.
- Neckline = last broken structural level.
- Wick (≥55% body ratio), FVG (3-bar imbalance), consume on touch.
- Dominant-leg Fibonacci with 12 extension multiples + dualFib.
- Diagonal containing lines (ATH-anchored resistance, oldest-low-anchored support).
- Pitchfork median (ATH → first ITL → first ITH).
- Donchian (donLen=20), zone (classified pivots).
- Cluster: tol=ATR×0.5, `isAnchor` + `cnt≥2`, score=weight sum.
- Sticky setup: only updated at bar close; invalidated on SL/TP hit or `msDir` flip.
- Entry = cluster mid (Pine does NOT actually use OTE for entry — it's display-only).
- SL = nearest protective swing within `maxStopATR(4)×ATR`, fallback `stopN(2)×ATR`,
  buffered by `slBufATR(0.5)×ATR`, clamped to `[entry + 0.5ATR, entry + 4ATR]`.
- TP = nearest cluster beyond entry+risk if any, else `entry ± risk×rrMin(2.0)`.
- Sizing: `units = (acctSize × riskPct%) / |entry-SL|`.

### What was added (NOT in Pine)
- **Breakout entry mode** — Pine only labels breakouts, never enters. Our
  `breakout` mode enters at next bar open on Donchian break + range expansion +
  volume spike. Optional 0.5× sizing against the higher-TF bias.
- **Neely entry gate** — Pine displays Neely as context only. With
  `neely_gate=on` we block entries unless `m2/m1 ≤ 0.618` (directional).
- **Walk-forward split** — last 30% reserved as out-of-sample.
- **Grid runner** — exhaustive sweep over TF × mode × gates × score × weights.

## No look-ahead — how it's enforced
- A pivot detected on bar `i` lives at bar `i - swLen`. The state never adds
  pivots beyond `i - swLen` (test: `test_no_lookahead`).
- Confluence is computed using arrays sliced `[: i+1]` only.
- HTF bias uses `close[1]` semantics — the HTF bar that closed *before* the
  LTF bar.
- Signals are generated at the close of bar `i`. The backtester fills on bar
  `i+1`'s open (limit fill for reversion if the bar's range crosses entry).

## Sanity check vs the Pine chart
Pick any recent bar on a TradingView chart with this indicator running and
verify three things match against the same bar in the backtester:

1. **Last classified ITH / ITL prices.** Run
   `python3 -c "from data import fetch_ohlcv; from structure import run_structure, last_classified, PT_HIGH, PT_LOW; df=fetch_ohlcv('binance','BTC/USDT','4h','2022-01-01T00:00:00Z'); eng,_=run_structure(df); print(last_classified(eng.state, PT_HIGH)); print(last_classified(eng.state, PT_LOW))"`
   and compare with the chart label.
2. **Last neckline price.** Same engine: `eng.state.neckline`.
3. **Live confluence cluster mids near close.** Run `python3 confluence.py`
   and compare the top cluster prices with the colored zones on the chart.

If any of those three disagrees, fix the discrepancy before trusting metrics.

## Interpreting the report
- **Equity is denominated in R**, where 1R = `riskPct` of `acctSize` (default
  1% of $10k = $100). Total R is additive — losing trades subtract 1R or so,
  winners add 2R+ depending on the cluster TP.
- **`OOS_*` columns are the only ones that matter for go/no-go decisions.**
  In-sample numbers are descriptive, not validating.
- **`overfit_flag`** is True when IS expectancy is positive but OOS is negative.
- **`sample_warning`** is True for runs with fewer than 30 trades — treat
  metrics as noise.

## Known limitations / TODO
- Pine `lookback=400` window is approximated by `maxPiv=80` cap. Strict
  windowing would also rebuild macro anchors per bar — not done.
- Breakout mode's "counter-HTF 0.5× sizing" is a heuristic, not in Pine.
- Single position at a time — Pine's sticky setup is the same so this matches.
- Fees are taker on both fills; no slippage model beyond the spread implied
  in `slippage_ticks` (not currently applied — TODO).
- `max_dd_pct` when equity has stayed below 0R reports `0.0` (denominator
  guard). Inspect `max_dd_r` directly for such runs.
