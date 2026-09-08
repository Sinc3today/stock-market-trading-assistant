"""tests/test_journal_locking.py -- the journal must survive concurrent writers.

Four independently-scheduled jobs read-modify-write logs/trades.json at 09:45
ET every trading day, and one of them (the copilot's manual entry) runs in a
SEPARATE uvicorn process. _save is atomic, so the file can never be torn — but
atomicity does nothing about a LOST UPDATE: two writers both load, both save,
and the second one's snapshot silently erases the first one's trade.

29 same-minute multi-writes already exist in the journal. They survived on
interleaving luck.

A threading.Lock cannot fix this — the race is cross-process, so these tests
use real subprocesses. A test that only spawns threads would pass against
broken code.
"""
import json
import os
import subprocess
import sys
import textwrap

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _writer_script(log_dir: str, tag: str, n: int) -> str:
    return textwrap.dedent(f"""
        import sys, time
        sys.path.insert(0, {REPO!r})
        import config
        config.LOG_DIR = {log_dir!r}
        from journal.trade_recorder import TradeRecorder
        rec = TradeRecorder()
        for i in range({n}):
            rec.log_entry("SPY", 1.50 + i / 100, 1, strategy="iron_condor",
                          book="disciplined", notes="{tag}-" + str(i))
            time.sleep(0.005)
    """)


def test_concurrent_processes_do_not_lose_trades(tmp_path):
    """The real race: two processes, interleaved read-modify-write."""
    log_dir = str(tmp_path) + "/"
    procs = [
        subprocess.Popen([sys.executable, "-c", _writer_script(log_dir, tag, 12)],
                         cwd=REPO, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        for tag in ("alpha", "beta")
    ]
    errs = [p.communicate()[1] for p in procs]
    for p, err in zip(procs, errs):
        assert p.returncode == 0, err.decode()[-2000:]

    with open(os.path.join(log_dir, "trades.json")) as fh:
        trades = json.load(fh)
    notes = [t.get("notes_entry", "") for t in trades]
    alpha = sum(1 for n in notes if "alpha-" in n)
    beta = sum(1 for n in notes if "beta-" in n)
    assert alpha == 12, f"lost {12 - alpha} alpha writes"
    assert beta == 12, f"lost {12 - beta} beta writes"
    assert len(trades) == 24


def test_lock_is_a_sidecar_not_the_journal_itself(tmp_path, monkeypatch):
    """_save replaces the file atomically, which swaps the inode — a lock held
    on trades.json itself would be released by the very write it guards."""
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    from journal.trade_recorder import TradeRecorder
    rec = TradeRecorder()
    rec.log_entry("SPY", 1.50, 1, strategy="iron_condor", book="disciplined")
    assert (tmp_path / "trades.json.lock").exists()


def test_a_corrupt_journal_is_never_silently_replaced(tmp_path, monkeypatch):
    """_load returned [] on any OSError/JSONDecodeError, so one transient
    failure plus the next write durably replaced the whole journal with a
    single row. A read failure must stop the write, not license it."""
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    from journal.trade_recorder import TradeRecorder
    path = tmp_path / "trades.json"
    path.write_text("{{{ not json at all")

    rec = TradeRecorder()
    with pytest.raises(Exception):
        rec.log_entry("SPY", 1.50, 1, strategy="iron_condor", book="disciplined")
    assert path.read_text() == "{{{ not json at all", "must not overwrite"


def test_a_missing_journal_still_starts_empty(tmp_path, monkeypatch):
    """A file that does not exist yet is genuinely empty — not an error."""
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    from journal.trade_recorder import TradeRecorder
    rec = TradeRecorder()
    assert rec.get_all_trades() == []
    rec.log_entry("SPY", 1.50, 1, strategy="iron_condor", book="disciplined")
    assert len(rec.get_all_trades()) == 1


def test_every_mutating_method_takes_the_lock():
    """A new mutator that forgets the lock reintroduces the race silently."""
    import ast
    import inspect
    from journal.trade_recorder import TradeRecorder
    src = textwrap.dedent(inspect.getsource(TradeRecorder))
    tree = ast.parse(src).body[0]
    for fn in [n for n in tree.body if isinstance(n, ast.FunctionDef)]:
        if fn.name == "_save":
            continue
        writes = "_save" in ast.dump(fn)
        if not writes:
            continue
        decorated = any(getattr(d, "id", "") == "_with_journal_lock"
                        for d in fn.decorator_list)
        takes_lock = decorated or "_locked" in ast.dump(fn)
        assert takes_lock, \
            f"{fn.name} writes the journal without taking the lock"
