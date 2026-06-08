"""tools/transcript_miner.py -- batch-curate gated YouTube transcripts via the
local Ollama model (free). For each curated (non-livestream) member video:
pull the caption track (yt-dlp + cookies) -> dedupe/clean -> local gemma3:4b
extracts TESTABLE RULES + themes/process -> append to a local KB.

Resumable (skips video ids already in the KB). Outputs are gitignored (gated
content — kept local for personal research only). Run detached:
    nohup python -m tools.transcript_miner --limit 120 > docs/miner.log 2>&1 &
"""
from __future__ import annotations
import argparse, json, os, re, subprocess, sys, time, urllib.request

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
CK   = os.path.join(ROOT, "docs", "www.youtube.com_cookies.txt")
KB   = os.path.join(ROOT, "docs", "kb_youtube_thestockmarket.md")   # gitignored (.txt? -> use .md, also ignore)
CHANNEL = "https://www.youtube.com/@TheStockMarket"
OLLAMA = "http://127.0.0.1:11434/api/generate"
MODEL  = "gemma3:4b"

PROMPT = """You are auditing a trading educator's video for a quant research team that will SCORE
his forward-looking calls against historical market data and check them against our own system.
We are NOT just looking for ready-made rules — we want his PREDICTIONS (to verify in hindsight)
and the things he watches that a pure price/indicator system might MISS.

The video's recording date is: {DATE}. Treat that as "today" — every forward call is relative
to it. Output these sections; be concise and faithful to what he actually said.

1) CALLS — every forward-looking, FALSIFIABLE prediction he makes. One per line, pipe-delimited:
   CALL | instrument (SPY/QQQ/IWM/VIX/bonds/dollar/oil/gold/a ticker/"market") | direction (up/down/range/volatile/risk-off/risk-on) | horizon (today/days/1-2wk/weeks/months) | level-or-trigger (any price/condition he names, else "-") | his reasoning in <=12 words
   Only include statements that could later be judged right or wrong. If none: "CALLS: none".
2) MARKET READ — how he characterizes the CURRENT regime in 1-2 lines (e.g. toppy/extended,
   oversold/washed-out, melt-up, risk-off, rotation, breadth weakening, range-bound, trending).
   We will compare this to what our regime classifier computed on the same date.
3) NON-PRICE SIGNALS — inputs he leans on that a pure price/technical system might not track:
   sentiment, positioning/flows, Fed/liquidity, intermarket (bonds/credit/dollar/oil), options
   flow/gamma, seasonality, specific news catalysts. List each he actually uses. If none: "none".
4) THEMES / PROCESS — recurring market concepts + risk/mindset/decision lessons (brief).
Transcript:

"""


def video_list():
    out = subprocess.run(
        ["yt-dlp", "--cookies", CK, "--flat-playlist", "--print", "%(id)s\t%(title)s", CHANNEL],
        capture_output=True, text=True, timeout=300).stdout.splitlines()
    vids = []
    for ln in out:
        if "\t" not in ln:
            continue
        vid, title = ln.split("\t", 1)
        if re.search(r"\bLIVE\b|REAL TIME", title, re.I):
            continue   # skip multi-hour live streams
        vids.append((vid, title))
    return vids


def clean_vtt(path):
    out = []
    for ln in open(path, encoding="utf-8", errors="ignore"):
        if not ln.strip() or "-->" in ln or ln.startswith(("WEBVTT", "Kind:", "Language:")):
            continue
        t = re.sub("<[^>]+>", "", ln).strip()
        if t and (not out or out[-1] != t):
            out.append(t)
    txt = " ".join(out)
    return re.sub(r"\b(\w+ \w+ \w+)( \1\b)+", r"\1", txt)


def fetch_transcript(vid):
    """Pull the en caption track. Winning recipe (verified 2026-06-08):
    enable the EJS remote n-challenge solver AND request BOTH manual
    (--write-subs) and auto (--write-auto-subs) tracks — together this
    clears YouTube's PO-token wall that otherwise discards web-client
    auto-captions. ~75% of curated uploads yield subs this way; the rest
    are members-tier-locked (return ("LOCKED", None) so the caller can
    count them distinctly from genuine no-caption skips).

    Returns (text, upload_date) on success — the date anchors every forward
    CALL so the scorer can check it against our SPY history. Also writes an
    --write-info-json in the same call to grab upload_date for free."""
    tmp = f"/tmp/miner_{vid}"
    r = subprocess.run(
        ["yt-dlp", "--cookies", CK, "--remote-components", "ejs:github",
         "--write-subs", "--write-auto-subs", "--sub-langs", "en",
         "--write-info-json", "--skip-download", "--ignore-no-formats-error",
         "--sub-format", "vtt",
         "-o", tmp + ".%(ext)s", f"https://www.youtube.com/watch?v={vid}"],
        capture_output=True, text=True, timeout=240)
    date = None
    info = f"{tmp}.info.json"
    if os.path.exists(info):
        try:
            d = json.load(open(info)).get("upload_date")  # YYYYMMDD
            if d:
                date = f"{d[:4]}-{d[4:6]}-{d[6:]}"
        except Exception:
            pass
        os.remove(info)
    for f in (f"{tmp}.en.vtt", f"{tmp}.en-orig.vtt"):
        if os.path.exists(f):
            t = clean_vtt(f); os.remove(f); return t, date
    if "member" in (r.stdout + r.stderr).lower():
        return "LOCKED", None
    return None, None


def curate(text, date=None):
    body = (PROMPT.replace("{DATE}", date or "unknown") + text)[:60000]
    req = urllib.request.Request(OLLAMA, data=json.dumps(
        {"model": MODEL, "prompt": body, "stream": False,
         "options": {"num_ctx": 16384, "temperature": 0.2}}).encode(),
        headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=900))["response"]


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--limit", type=int, default=120)
    n = ap.parse_args().limit
    done = set(re.findall(r"<!--vid:(\S+)-->", open(KB).read())) if os.path.exists(KB) else set()
    vids = [v for v in video_list() if v[0] not in done][:n]
    print(f"[miner] {len(vids)} videos to process ({len(done)} already done)", flush=True)
    locked = skipped = ok = 0
    for i, (vid, title) in enumerate(vids, 1):
        try:
            t, date = fetch_transcript(vid)
            if t == "LOCKED":
                locked += 1
                print(f"[{i}/{len(vids)}] LOCKED (members-only) {vid} {title[:50]}", flush=True); continue
            if not t or len(t.split()) < 200:
                skipped += 1
                print(f"[{i}/{len(vids)}] SKIP (no/short transcript) {vid} {title[:50]}", flush=True); continue
            c = curate(t, date)
            with open(KB, "a") as f:
                f.write(f"\n\n## {title}\n<!--vid:{vid} date:{date or '?'}--> ({len(t.split())} words)\n\n{c}\n")
            ok += 1
            # count scoreable forward CALLS this video produced (the unit the scorer judges)
            n_calls = sum(1 for ln in c.splitlines() if ln.strip().startswith("CALL |"))
            print(f"[{i}/{len(vids)}] OK {vid} {date or '?'} {title[:42]} | {len(t.split())}w | calls:{n_calls}", flush=True)
        except Exception as e:
            print(f"[{i}/{len(vids)}] ERROR {vid}: {e}", flush=True)
        time.sleep(2)
    print(f"[miner] batch done — ok:{ok} locked:{locked} skipped:{skipped} of {len(vids)}", flush=True)


if __name__ == "__main__":
    main()
