# STRATEGY_LOG.md — Strategy Decisions & Reasoning

The **why** behind the bot's strategy choices. `CLAUDE.md` points here for reasoning;
`BUILD_LOG.md` is the session changelog (the *what*); this file is the *why*.

Methodology tenet: every strategy idea is vetted **out-of-sample first** (walk-forward),
with **fewer knobs**, and is **shelved/inert if it shows no OOS edge**. We actively seek
*disconfirming* evidence (falsification), not confirmation of priors.

---

## The core edge

- **Iron condor in calm markets is the only robust, validated edge.** 5yr backtest: in the
  true calm regime (not trending, VIX < 18) it wins **82.3%** for **+$18,000** (1-contract) —
  the overwhelming majority of all P&L. This is what the bot exists to harvest.
- Everything directional (bull/bear debit spreads) is, under the current model, a ~coin-flip
  or negative. The bot is a **premium-seller in range-bound tape**, not a directional trader.

## Tuned regime thresholds (live in `signals/regime_detector.py`)

- `ADX_TREND_MIN = 32` (20 → 25 → 30 → 32) — raised to filter weak/false trends.
- `VIX_CALM_MAX = 18` (17 → 18, 2026-05-20) — promotes more days into the calm-condor regime.
- `VIX_ELEVATED_MAX = 22` — above this, condors are "poison" (vol expansion) → skip.
- `EXTENDED_TREND_MAX_PCT = 9` — skip directional entries when SPY is >9% from its 200-MA
  (over-extended mean-reverts). Originated from the 2026-05-18 bull-put loss at +9.3% extension.
- `MIN_TREND_SEPARATION_PCT = 1.5` — skip when price is too close to the 200-MA (no direction).

## Regime decisions (see `docs/REGIME_PLAYBOOK.md` for the full scorecard)

- **Calm chop → iron condor.** The edge. Trade it.
- **Transition chop (VIX 18–22) → half-size condor.** Split into its own `CHOPPY_TRANSITION`
  label 2026-06-06; the split exposed it as a **−$2,950 / 5yr net loser**. Open question:
  skip it entirely (pending a WF/hypothesis check — not yet changed).
- **Trending up/down calm → directional debit/credit.** Weak/unproven under the synthetic
  payoff model. Bull ~51%; bear ~nil/negative. Both now carry symmetric separation +
  extension guardrails (bear side gained them 2026-06-06).
- **Trending high-vol → SKIP.** A trade-it experiment showed 19% win rate, −$4,600 / 5yr,
  ~half the Sharpe. `tradeable=False`. Settled negative.
- **Event days (FOMC/CPI/NFP/OPEX) → SKIP.** Rule-based prior, not edge-tested.
- **Extension gate:** when SPY is >9% above its 200-MA the daily bull play is skipped; a
  **shadow book** records the counterfactual P&L of the refused trade to test whether the
  cap is too tight (measurement only, no real money).

## Experiments run and SHELVED (falsification working as intended)

- **0DTE / 1DTE:** no edge as designed (real full-year backtest −$515). Shelved 2026-05-21;
  now structurally gated to the **learning sandbox** (never trades the real-money book).
- **Meta-labeling (take/skip + conviction model):** built 2026-05-22, no OOS edge
  (72.4% baseline vs 70.4% filtered). `META_LABEL_ENABLED=False`, inert.
- **Intraday-touch exit (daily HIGH/LOW re-mark):** no OOS edge under the daily-bar model
  (attribution 4.3%, all presets failed). Shelved.
- **Intraday time-exit (hard-close + scratch):** walk-forward disproved it for every combo;
  parity gate failed universally (BS-off-spot live mark can't reproduce real-option-bar
  exits). Ships **inert** 2026-06-06; re-open only behind real intraday option aggregates.

## Pricing / data caveats that bound every conclusion

- Daily directional backtest uses a **synthetic fixed-payoff model** (not real option marks),
  so directional "validation" is soft — a weak prior, not proof.
- Polygon Starter: no intraday option quotes (live can't see real intraday option prices);
  `I:VIX` not authorized → VIX comes from the CBOE CSV.
- Local `spy_history.csv` covers ~2021→2026: includes the **2022 bear**, but **not** the
  2020 COVID crash. Any drawdown study is thin on major-bear samples until history is extended.

## Open threads (next strategy R&D)

1. **Directional walk-forward (the keystone):** does a *real-priced* dip-buy / trend-follow
   beat the weak ~51% bull / ~nil bear baseline **out-of-sample**? Needs an IV-stress arm
   (flat-VIX BS understates crash-time option cost).
2. **Skip the transition zone?** WF/hypothesis check on dropping the −$2,950 half-size condor.
3. **Shadow extension** to trending-down skips (cheap); dip/high-vol regimes need a defined
   strategy from (1) before they can be counterfactually shadowed.
