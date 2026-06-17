"""data/rh_session.py -- Robinhood session helpers for the slippage reader.

Polygon Starter gives option OHLC/vwap aggregates but NO live bid/ask quotes
(snapshots return mid=None). So the only source for the real spread the user
crosses is their own logged-in RH session. The user opted to drive that with
Playwright + saved cookies (read-only — looking at the chain, never trading; RH
has no API and robin_stocks trading is ToS-violating, so we stay read-only).

What's built + tested here: loading the Netscape cookies.txt export into the
shape Playwright wants. The actual page scrape (LiveRHQuoteFetcher.fetch_mid) is
a deliberate seam — it stays NotImplementedError until written against a real
logged-in session, because selectors guessed blind would just ship broken.

SECURITY: the RH cookie file is a session secret. It must live outside git
(.gitignore already covers *cookies*.txt) and never be logged or committed.
"""
from __future__ import annotations

import os
from typing import Protocol


def load_cookies(path: str) -> list[dict]:
    """Parse a Netscape `cookies.txt` export into Playwright add_cookies dicts.

    Columns: domain, include_subdomains, path, secure, expires, name, value.
    Raises FileNotFoundError if the export is missing.
    """
    if not os.path.exists(path):
        raise FileNotFoundError(f"RH cookie export not found: {path}")
    cookies: list[dict] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line.strip() or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) != 7:
                continue
            domain, _subdom, c_path, secure, expires, name, value = parts
            cookies.append({
                "name": name,
                "value": value,
                "domain": domain,
                "path": c_path or "/",
                "secure": secure.upper() == "TRUE",
                "expires": int(expires) if expires.isdigit() else 0,
            })
    return cookies


class RHQuoteFetcher(Protocol):
    """Returns the real mid (or last) price for an option, for slippage vs mark."""

    def fetch_mid(self, occ_symbol: str) -> float: ...


class LiveRHQuoteFetcher:
    """Playwright-driven RH chain reader. SEAM ONLY — not yet wired.

    To finish (during market hours, with a real logged-in session):
      1. `pip install playwright && playwright install chromium`
      2. Export RH cookies to docs/robinhood.com_cookies.txt (gitignored).
      3. Launch chromium with context.add_cookies(load_cookies(path)), open the
         option's chain page, and read the live bid/ask — then return the mid.
    Selectors must be written against the real DOM, so this stays a stub until
    then (fails loud rather than returning a wrong number).
    """

    def __init__(self, cookies_path: str | None = None):
        self.cookies_path = cookies_path

    def fetch_mid(self, occ_symbol: str) -> float:
        raise NotImplementedError(
            "LiveRHQuoteFetcher is a seam — wire the Playwright RH scrape against "
            "a real logged-in session (see docs/SLIPPAGE_READER.md)."
        )
