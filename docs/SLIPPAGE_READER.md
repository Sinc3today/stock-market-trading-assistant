# Slippage Reader

**Purpose.** The bot's paper marks use day-close/vwap — optimistic, because they
ignore the bid/ask spread a real order crosses. Before trusting realized P&L with
real money, measure the true gap: what a real Robinhood fill costs vs the mark the
bot assumed. This is the fill-quality data the copilot needs before scaling size.

**Why RH/Playwright (not Polygon).** Polygon Starter gives option OHLC/vwap
aggregates but **no live bid/ask quotes** (snapshots return `mid=None`). The only
source for the real spread the user faces is their own logged-in RH session. We
read it (chain pages) — never trade through it (RH has no API; `robin_stocks`
trading is ToS-violating). Read-only Playwright with saved cookies.

## Status

Built + tested (inert until the live fetch is wired — nothing calls it yet):

- `journal/slippage.py`
  - `compute_slippage(mark_price, fill_price, action="credit"|"debit", contracts)`
    → per-share, dollar, and percent slippage. `> 0` = fill worse than mark
    (lost to the spread); `< 0` = did better than mark.
  - `SlippageStore(path)` — append-only JSONL (`record` / `all` / `summary`),
    atomic writes (crash/freeze-safe, same guarantee as the rest of the journal).
- `data/rh_session.py`
  - `load_cookies(path)` — Netscape `cookies.txt` export → Playwright `add_cookies`
    dicts. Tested.
  - `LiveRHQuoteFetcher` — **seam only**, `fetch_mid()` raises `NotImplementedError`.

## Remaining (live — do during market hours with a real session)

Selectors must be written against the real logged-in DOM, so this is deliberately
not stubbed-in blind.

1. `pip install playwright && playwright install chromium` (adds the dep).
2. Export RH cookies to `docs/robinhood.com_cookies.txt`
   (gitignored by `*cookies*.txt` — **session secret, never commit/log**).
3. Implement `LiveRHQuoteFetcher.fetch_mid(occ_symbol)`:
   - launch chromium, `context.add_cookies(load_cookies(self.cookies_path))`,
   - open the option's chain page, read the live bid/ask, return the mid.
4. Wire a recorder: on each closed live trade, fetch the real mid at entry/exit,
   `compute_slippage(...)` vs the bot's mark, `SlippageStore.record(...)`.
5. Surface `SlippageStore.summary()` on the copilot screen (avg $/% give-up) so
   the real-money decision is data-driven.

## Security

- RH cookie file is a session secret: outside git, never logged, never committed.
- Read-only: open chain pages and read prices; never place/modify/cancel orders.
