"""signals/concentration.py -- short-strike proximity guard.

Audit finding (2026-07-08): three condors were open with short puts within $13
($700/$705/$713) and a doubled 771 short call — one -3% SPY day breaches all of
them together. Only a COUNT cap existed; nothing looked at where the strikes sit.

The guard: a new entry's SHORT strikes must not land within
config.CONCENTRATION_GUARD_PCT of any EXISTING open short strike of the same
option type (puts vs puts, calls vs calls) across the disciplined + live books.
Long (protective) legs don't count — they reduce risk, not stack it.

Pure helper — the paper broker enforces it for auto-entries; the copilot surfaces
it as a warning for manual ones (the user's real trades are their call).
"""
from __future__ import annotations


def _short_strikes(legs) -> list[tuple[str, float]]:
    out = []
    for leg in legs or []:
        action = (leg.get("action") or "").upper()
        typ = (leg.get("option_type") or leg.get("type") or "").upper()
        strike = leg.get("strike")
        if strike is None or not action.startswith("S"):
            continue
        out.append(("C" if typ.startswith("C") else "P", float(strike)))
    return out


def book_concentration(open_trades, pct: float,
                       books=("disciplined", "live")) -> list[dict]:
    """Pairwise short-strike clusters already PRESENT in the open book (for the
    copilot risk card). Each cluster reported once."""
    trades = [t for t in (open_trades or [])
              if (t.get("outcome") or "open") == "open"
              and (t.get("book") or "disciplined") in books]
    seen, clusters = set(), []
    for i, t in enumerate(trades):
        others = trades[:i] + trades[i + 1:]
        for c in proximity_conflicts(t.get("legs"), others, pct, books=books):
            key = frozenset([(t.get("trade_id"), c["new_strike"]),
                             (c["trade_id"], c["existing_strike"]), c["type"]])
            if key in seen:
                continue
            seen.add(key)
            clusters.append(c)
    return clusters


def proximity_conflicts(new_legs, open_trades, pct: float,
                        books=("disciplined", "live")) -> list[dict]:
    """Conflicts between the new position's short strikes and existing open
    short strikes of the same type within `pct` percent. Empty list = clear."""
    new_shorts = _short_strikes(new_legs)
    if not new_shorts:
        return []
    conflicts = []
    for t in open_trades or []:
        if (t.get("outcome") or "open") != "open":
            continue
        if (t.get("book") or "disciplined") not in books:
            continue
        for etyp, estrike in _short_strikes(t.get("legs")):
            for ntyp, nstrike in new_shorts:
                if ntyp != etyp or estrike <= 0:
                    continue
                dist_pct = abs(nstrike - estrike) / estrike * 100.0
                if dist_pct <= pct:
                    conflicts.append({
                        "new_strike": nstrike,
                        "existing_strike": estrike,
                        "type": ntyp,
                        "distance_pct": round(dist_pct, 2),
                        "trade_id": t.get("trade_id"),
                        "book": t.get("book"),
                    })
    return conflicts
