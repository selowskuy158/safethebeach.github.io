"""Fallback downloader: fetch Dukascopy 1-minute day files slowly, with retries,
into a resumable cache. Only needed if `python -m bot.download_dukascopy` fails
with 429/503 or "connection reset" (Dukascopy throttling your IP).

Run from the repo root:
    python dukascopy_prefetch.py --cache data/duka_cache --symbols EURUSD GBPUSD \
        --start 2023-01-01 --end 2025-12-31 --workers 2
Re-run the same command after any interruption; cached days are skipped.
Then build the CSVs with run_download_cached.py.
"""
import argparse
import random
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path

from bot.download_dukascopy import URL

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


def get(cache: Path, sym: str, d: date) -> str:
    p = cache / sym / f"{d.isoformat()}.bi5"
    miss = p.with_suffix(".404")
    if p.exists() or miss.exists():
        return "cached"
    url = URL.format(sym=sym, y=d.year, m=d.month - 1, d=d.day)  # months are 0-based
    for _ in range(60):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=40) as r:
                raw = r.read()
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(".part")
            tmp.write_bytes(raw)
            tmp.replace(p)
            return "ok"
        except urllib.error.HTTPError as e:
            if e.code == 404:
                miss.parent.mkdir(parents=True, exist_ok=True)
                miss.touch()
                return "404"
        except Exception:
            pass
        time.sleep(5 + 5 * random.random())  # throttled (429/503/reset): back off and retry
    return "fail"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default="data/duka_cache")
    ap.add_argument("--symbols", nargs="+", default=["EURUSD", "GBPUSD"])
    ap.add_argument("--start", required=True, type=date.fromisoformat)
    ap.add_argument("--end", required=True, type=date.fromisoformat)
    ap.add_argument("--workers", type=int, default=2)
    args = ap.parse_args()

    cache = Path(args.cache)
    days = [args.start + timedelta(n) for n in range((args.end - args.start).days + 1)]
    days = [d for d in days if d.weekday() != 5]  # no trading on Saturdays
    jobs = [(s.upper(), d) for s in args.symbols for d in days]
    stats: dict[str, int] = {}
    with ThreadPoolExecutor(args.workers) as pool:
        for i, res in enumerate(pool.map(lambda j: get(cache, *j), jobs), start=1):
            stats[res] = stats.get(res, 0) + 1
            if i % 50 == 0 or i == len(jobs):
                print(f"{i}/{len(jobs)} {stats}", flush=True)
    if stats.get("fail"):
        print("Some days failed: re-run the same command to retry them.")


if __name__ == "__main__":
    main()
