"""tests/test_rh_session.py -- RH cookie loader + fetcher seam (testable parts).

The live Playwright scrape of the RH option chain is deferred (needs a real
logged-in session to write correct selectors). What we CAN verify now: parsing
the Netscape cookies.txt export into Playwright's add_cookies format, and that
the live fetcher is a clean seam that fails loud until it's wired.
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest

_NETSCAPE = """# Netscape HTTP Cookie File
.robinhood.com\tTRUE\t/\tTRUE\t1799999999\tsession\tABC123
.robinhood.com\tTRUE\t/\tFALSE\t0\tdevice_id\tDEV-9
"""


def test_load_cookies_parses_netscape(tmp_path):
    from data.rh_session import load_cookies
    f = tmp_path / "robinhood.com_cookies.txt"
    f.write_text(_NETSCAPE)
    cookies = load_cookies(str(f))
    assert len(cookies) == 2
    c = cookies[0]
    assert c["name"] == "session" and c["value"] == "ABC123"
    assert c["domain"] == ".robinhood.com"
    assert c["path"] == "/"
    assert c["secure"] is True            # 4th column TRUE
    assert c["expires"] == 1799999999


def test_load_cookies_skips_comments_and_blanks(tmp_path):
    from data.rh_session import load_cookies
    f = tmp_path / "c.txt"
    f.write_text("# comment\n\n" + _NETSCAPE.split("\n", 1)[1])
    assert len(load_cookies(str(f))) == 2


def test_load_cookies_missing_file_raises():
    from data.rh_session import load_cookies
    with pytest.raises(FileNotFoundError):
        load_cookies("/no/such/cookies.txt")


def test_live_fetcher_is_unwired_seam():
    # The live scraper is intentionally a stub until built against a real session.
    from data.rh_session import LiveRHQuoteFetcher
    fetcher = LiveRHQuoteFetcher(cookies_path=None)
    with pytest.raises(NotImplementedError):
        fetcher.fetch_mid("SPY260717C00781000")
