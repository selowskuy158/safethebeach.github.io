"""Run the repo's bot.download_dukascopy unchanged, but read day files from the
cache filled by dukascopy_prefetch.py (same parsing, resampling and CSV output).

Run from the repo root:
    python run_download_cached.py data/duka_cache -- --symbols EURUSD GBPUSD \
        --start 2023-01-01 --end 2025-12-31
"""
import io
import re
import sys
import urllib.error
from pathlib import Path

import bot.download_dukascopy as dl

cache = Path(sys.argv[1])
sys.argv = ["download_dukascopy"] + sys.argv[3:]  # argv[2] is the "--" separator
PAT = re.compile(r"/datafeed/(\w+)/(\d{4})/(\d{2})/(\d{2})/")


def cached_urlopen(url, timeout=None):
    sym, y, m, d = PAT.search(url).groups()
    day = f"{y}-{int(m) + 1:02d}-{d}"  # URL months are 0-based
    p = cache / sym / f"{day}.bi5"
    if p.exists():
        return io.BytesIO(p.read_bytes())
    if p.with_suffix(".404").exists():
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
    raise RuntimeError(f"{sym} {day} is not in the cache: re-run dukascopy_prefetch.py")


dl.urllib.request.urlopen = cached_urlopen
dl.main()
