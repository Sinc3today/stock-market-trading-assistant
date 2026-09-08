"""tools/enumerate_conventions.py -- find every place a decision is re-made.

Built 2026-09-07 after the same defect was fixed three times in three files and
still had ten instances live. The lesson: **a findings list tells you where
someone already looked. It cannot tell you where else the shape lives.**

So this enumerates, mechanically:

  E1  STRATEGY VOCABULARY   every strategy name any producer can emit, and
                            whether the one convention-owner understands it
  E2  PRIVATE LISTS         modules that hold their own strategy-name list
                            instead of asking the owner
  E3  PLACEHOLDER PRICES    price-resolving functions that can return a
                            hardcoded number instead of admitting failure

Read-only, AST-based, no imports of the code under inspection (so a broken
module cannot hide from it).

    .venv/bin/python -m tools.enumerate_conventions
"""
from __future__ import annotations

import ast
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {".venv", ".git", "__pycache__", "tests", "logs", "docs", "node_modules"}

# The one owner. Anything else holding a list of these names is a copy.
CONVENTION_OWNER = "journal/trade_recorder.py"
STRATEGY_TOKENS = {
    "iron_condor", "credit_spread", "debit_spread", "broken_wing",
    "single_leg", "stock", "put_debit_spread", "call_debit_spread",
    "put_credit_spread", "call_credit_spread", "butterfly", "custom",
}
# Fields whose value is a price. A hardcoded fallback here becomes a record.
PRICE_NAMES = ("price", "mark", "mid", "premium", "credit", "debit", "cost", "fill")


def _py_files():
    for p in ROOT.rglob("*.py"):
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        yield p


def _rel(p: Path) -> str:
    return str(p.relative_to(ROOT))


def _parse(p: Path):
    try:
        return ast.parse(p.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return None


# ── E1: the strategy vocabulary ──────────────────────────────────

def strategy_vocabulary() -> dict[str, set[str]]:
    """Every string literal that reaches a `strategy=` / `trade_type=` kwarg,
    or is returned by a function whose name mentions strategy.

    Maps name -> set of "file:line" sites that can emit it.
    """
    vocab: dict[str, set[str]] = {}

    def note(name, path, lineno):
        if isinstance(name, str) and name:
            vocab.setdefault(name, set()).add(f"{_rel(path)}:{lineno}")

    for p in _py_files():
        tree = _parse(p)
        if tree is None:
            continue
        for node in ast.walk(tree):
            # strategy= / trade_type= keyword arguments
            if isinstance(node, ast.Call):
                for kw in node.keywords:
                    if kw.arg in ("strategy", "trade_type") and \
                            isinstance(kw.value, ast.Constant):
                        note(kw.value.value, p, kw.value.lineno)
                    # .get("strategy", "default") — the default is a producer too
                    if isinstance(node.func, ast.Attribute) and node.func.attr == "get" \
                            and len(node.args) == 2 and isinstance(node.args[0], ast.Constant) \
                            and node.args[0].value in ("strategy", "trade_type") \
                            and isinstance(node.args[1], ast.Constant):
                        note(node.args[1].value, p, node.lineno)
            # functions that return a strategy name
            if isinstance(node, ast.FunctionDef) and "strateg" in node.name.lower():
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Return) and isinstance(sub.value, ast.Constant):
                        note(sub.value.value, p, sub.lineno)
    return vocab


def journal_vocabulary() -> set[str]:
    """Strategy names that actually exist in the live journal."""
    import json
    try:
        import config
        path = os.path.join(config.LOG_DIR, "trades.json")
        with open(path) as fh:
            return {str(t.get("strategy")) for t in (json.load(fh) or [])
                    if t.get("strategy")}
    except Exception:
        return set()


# ── E2: private strategy lists ───────────────────────────────────

# Collections of strategy names that are NOT credit/debit decisions. Each entry
# needs a reason — the point is that adding a new one is a deliberate act, not
# an accident. file -> why it is allowed.
# Keyed by file OR "file:line" so allowing one site does not blind the whole
# module — options_layer holds both a legitimate routing check AND, until
# 2026-09-07, a real credit/debit decision.
ALLOWED_LISTS = {
    "tools/enumerate_conventions.py": "this scanner's own token set",
    "signals/spy_options_engine.py": "Literal[] type annotations, not a decision",
    "learning/paper_broker.py": "_VALID_STRATEGIES — a whitelist of what may be "
                                "opened, not a credit/debit classification",
    "alerts/pushover_client.py": "display formatting only",
    "alerts/regime_view.py": "display formatting only",
    "signals/options_layer.py:279": "routing: is this a vertical? the credit/"
                                    "debit call itself uses _pnl_convention",
    "learning/journal_repair.py": "_RISK_EQUALS_PREMIUM — which structures have "
                                  "max_profit == the premium; a BWB's does not. "
                                  "Not a credit/debit classification.",
}


def private_strategy_lists(include_allowed: bool = False
                           ) -> list[tuple[str, int, list[str]]]:
    """Collections of strategy-name literals held outside the owner."""
    found = []
    for p in _py_files():
        if _rel(p) == CONVENTION_OWNER:
            continue                      # the owner is allowed to hold them
        if not include_allowed and _rel(p) in ALLOWED_LISTS:
            continue
        tree = _parse(p)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
                names = [e.value for e in node.elts
                         if isinstance(e, ast.Constant) and e.value in STRATEGY_TOKENS]
                if len(names) >= 2:
                    if not include_allowed and \
                            f"{_rel(p)}:{node.lineno}" in ALLOWED_LISTS:
                        continue
                    found.append((_rel(p), node.lineno, names))
    return sorted(found)


# ── E3: placeholder prices ───────────────────────────────────────

def placeholder_prices() -> list[tuple[str, int, str]]:
    """Price-ish functions that can hand back a hardcoded number, and
    `or <number>` defaults on price-ish names."""
    found = []
    for p in _py_files():
        tree = _parse(p)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and \
                    any(t in node.name.lower() for t in PRICE_NAMES):
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Return) and \
                            isinstance(sub.value, ast.Constant) and \
                            isinstance(sub.value.value, (int, float)) and \
                            not isinstance(sub.value.value, bool):
                        found.append((_rel(p), sub.lineno,
                                      f"{node.name}() returns {sub.value.value!r}"))
            # x = something or 12.34  where x is price-ish
            if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or):
                last = node.values[-1]
                if isinstance(last, ast.Constant) and \
                        isinstance(last.value, (int, float)) and \
                        not isinstance(last.value, bool):
                    src = ast.dump(node.values[0]).lower()
                    if any(t in src for t in PRICE_NAMES):
                        found.append((_rel(p), node.lineno,
                                      f"`or {last.value!r}` on a price value"))
    return sorted(set(found))


def main():
    print("=" * 78)
    print("ENUMERATION — where is each decision re-made?")
    print("=" * 78)

    from journal.trade_recorder import _pnl_convention
    vocab = strategy_vocabulary()
    live = journal_vocabulary()
    all_names = sorted(set(vocab) | live)

    print(f"\nE1. STRATEGY VOCABULARY — {len(all_names)} names reachable\n")
    print(f"  {'name':<24}{'convention':<12}{'in journal':<12}emitted at")
    unknown = []
    for n in all_names:
        conv = _pnl_convention(n)
        sites = sorted(vocab.get(n, set()))
        where = sites[0] if sites else "(journal only)"
        more = f" +{len(sites)-1}" if len(sites) > 1 else ""
        flag = conv or "** NONE **"
        if conv is None:
            unknown.append(n)
        print(f"  {n:<24}{flag:<12}{'yes' if n in live else '-':<12}{where}{more}")
    print(f"\n  -> {len(unknown)} name(s) the convention owner does NOT understand: "
          f"{unknown or 'none'}")

    priv = private_strategy_lists()
    print(f"\nE2. PRIVATE STRATEGY LISTS — {len(priv)} outside the owner\n")
    for f, ln, names in priv:
        print(f"  {f}:{ln}  {names}")

    ph = placeholder_prices()
    print(f"\nE3. PLACEHOLDER PRICES — {len(ph)} site(s)\n")
    for f, ln, what in ph:
        print(f"  {f}:{ln}  {what}")

    print("\n" + "=" * 78)
    print(f"  vocabulary gaps {len(unknown)} · private lists {len(priv)} · "
          f"placeholder prices {len(ph)}")
    print("=" * 78)
    return {"unknown": unknown, "private": priv, "placeholders": ph}


if __name__ == "__main__":
    main()
