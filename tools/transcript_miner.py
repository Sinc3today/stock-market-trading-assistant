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

PROMPT = """You are mining a trading educator's video transcript for a quant research team.
Output three sections, concise bullets:
1) TESTABLE RULES — any entry/exit/setup stated as a CONCRETE condition (indicator value,
   price level, time-of-day, pattern) that we could BACKTEST. Prefix each with 'TESTABLE RULE:'
   and quote the exact phrasing. If there are none, write 'TESTABLE RULES: none'.
2) THEMES — recurring market concepts he emphasizes.
3) PROCESS — risk/mindset/decision lessons.
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
    tmp = f"/tmp/miner_{vid}"
    subprocess.run(["yt-dlp", "--cookies", CK, "--write-auto-subs", "--sub-langs", "en",
                    "--skip-download", "--ignore-no-formats-error", "--sub-format", "vtt",
                    "-o", tmp + ".%(ext)s", f"https://www.youtube.com/watch?v={vid}"],
                   capture_output=True, text=True, timeout=180)
    for f in (f"{tmp}.en.vtt", f"{tmp}.en-orig.vtt"):
        if os.path.exists(f):
            t = clean_vtt(f); os.remove(f); return t
    return None


def curate(text):
    body = (PROMPT + text)[:60000]
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
    for i, (vid, title) in enumerate(vids, 1):
        try:
            t = fetch_transcript(vid)
            if not t or len(t.split()) < 200:
                print(f"[{i}/{len(vids)}] SKIP (no/short transcript) {vid} {title[:50]}", flush=True); continue
            c = curate(t)
            with open(KB, "a") as f:
                f.write(f"\n\n## {title}\n<!--vid:{vid}--> ({len(t.split())} words)\n\n{c}\n")
            flagged = c.count("TESTABLE RULE:")
            print(f"[{i}/{len(vids)}] OK {vid} {title[:50]} | testable-rule flags: {flagged}", flush=True)
        except Exception as e:
            print(f"[{i}/{len(vids)}] ERROR {vid}: {e}", flush=True)
        time.sleep(2)
    print("[miner] batch complete", flush=True)


if __name__ == "__main__":
    main()
