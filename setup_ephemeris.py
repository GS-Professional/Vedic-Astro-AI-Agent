#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
setup_ephemeris.py
==================
The PyJHora wheel does NOT ship the Swiss-Ephemeris binary files, so sunrise/sunset,
and thus any panchanga/samvatsara call, raises:
    swisseph.Error: SwissEph file 'seplm48.se1' not found ...
This script downloads the files PyJHora needs into its configured `data/ephe/`
folder (matching const._ephe_path). Files are pulled from the official
aloistr/swisseph GitHub ephe/ release so they are genuine Swiss Ephemeris data.

Run once after installation:
    python setup_ephemeris.py

It is idempotent and prints what it downloads.
"""

import os
import ssl
import sys
import urllib.request
import shutil

BASE = "https://raw.githubusercontent.com/aloistr/swisseph/master/ephe/"

# Files used by PyJHora's Drik method (planets + Moon + Moshier fallback).
FILES = [
    "sepl_18.se1",    # main planets  (1800-2399)
    "semo_18.se1",    # Moon          (1800-2399)
    "seplm48.se1",    # Moshier planets fallback
    "semom48.se1",    # Moshier Moon fallback
]


def jhora_ephe_path():
    """Locate the jhora data/ephe directory from the installed package."""
    import jhora.const as const
    return getattr(const, "_ephe_path", None)


def download(url, dest):
    ctx = ssl.create_default_context()
    req = urllib.request.Request(url, headers={"User-Agent": "PyJHora-setup"})
    with urllib.request.urlopen(req, context=ctx, timeout=60) as r, open(dest, "wb") as f:
        shutil.copyfileobj(r, f)
    return os.path.getsize(dest)


def main():
    path = jhora_ephe_path()
    if not path:
        # fall back to searching site-packages
        import jhora
        path = os.path.join(os.path.dirname(jhora.__file__), "data", "ephe")
    os.makedirs(path, exist_ok=True)
    print(f"Ephemeris target folder: {path}")

    # Offline fallback: if a cloned PyJHora source tree is available locally,
    # copy the .se1 files from it instead of downloading.
    if not all(os.path.exists(os.path.join(path, n)) and os.path.getsize(os.path.join(path, n)) > 10000 for n in FILES):
        for cand in ("_pyjhora_src/src/jhora/data/ephe", "_pyjhora_src/jhora/data/ephe", "jhora/data/ephe"):
            if os.path.isdir(cand):
                copied = 0
                for name in FILES:
                    src = os.path.join(cand, name)
                    dst = os.path.join(path, name)
                    if os.path.exists(src):
                        shutil.copy2(src, dst)
                        copied += 1
                if copied:
                    print(f"  copied {copied} files from local source tree {cand}")
                    break

    ok = True
    for name in FILES:
        dest = os.path.join(path, name)
        if os.path.exists(dest) and os.path.getsize(dest) > 10000:
            print(f"  exists  {name} ({os.path.getsize(dest)} bytes) — skipped")
            continue
        try:
            size = download(BASE + name, dest)
            print(f"  fetched {name} ({size} bytes)")
        except Exception as e:
            ok = False
            print(f"  FAILED {name}: {e}")
    print("\nDone." if ok else "\nSome files failed — check your internet and re-run.")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
