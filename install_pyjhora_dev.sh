#!/usr/bin/env bash
# install_pyjhora_dev.sh — install PyJHora from GitHub master (the dev line).
#
# Why a script? Since V4.8.7 there are NO PyPI releases of PyJHora. The dev
# line ("5.0" on master as of Sept 2026) is source-only, and a plain
# `pip install git+...` fails on two packaging quirks in master:
#   1) setup.py looks for Readme.md but the file is readme.md (case mismatch)
#   2) setup.cfg/pyproject.toml carry PEP 639 fields (license-expression /
#      license-files) that the installed setuptools/pip reject
# This script applies both workarounds and then installs.
#
# Usage:  bash install_pyjhora_dev.sh [target_dir]
#         target_dir defaults to ./_pyjhora_src (safe to delete afterwards)
set -euo pipefail

TARGET="${1:-./_pyjhora_src}"

echo "==> Cloning PyJHora master (dev line) into ${TARGET}"
rm -rf "${TARGET}"
git clone --depth 1 https://github.com/naturalstupid/PyJHora.git "${TARGET}"
cd "${TARGET}"

echo "==> Workaround 1: readme case mismatch (setup.py expects Readme.md)"
[ -f Readme.md ] || ln -s readme.md Readme.md 2>/dev/null || cp readme.md Readme.md

echo "==> Workaround 2: drop PEP 639 fields pip/setuptools rejects"
# Master moved these from setup.cfg into pyproject.toml ([project] table);
# handle both layouts so the script keeps working as upstream evolves.
if [ -f setup.cfg ]; then
  sed -i '/^license-expression/d; /^license-files/d' setup.cfg
fi
if [ -f pyproject.toml ]; then
  sed -i '/^license-expression/d; /^license-files[[:space:]]*=/d' pyproject.toml
fi

echo "==> Installing (no-deps: Python deps come from requirements.txt)"
pip install --no-deps --force-reinstall .

echo "==> Copying Swiss Ephemeris data files from the source tree"
python3 - <<'PYEPHE'
import os, shutil, jhora
src = os.path.join(os.getcwd(), "src", "jhora", "data", "ephe")
if not os.path.isdir(src):
    src = os.path.join(os.getcwd(), "jhora", "data", "ephe")
dst = os.path.join(os.path.dirname(jhora.__file__), "data", "ephe")
os.makedirs(dst, exist_ok=True)
n = 0
if os.path.isdir(src):
    for name in os.listdir(src):
        if name.endswith(".se1"):
            shutil.copy2(os.path.join(src, name), os.path.join(dst, name))
            n += 1
print("copied %d .se1 files to %s" % (n, dst))
PYEPHE

cd ..
echo "==> Verifying import + version"
python3 - <<'PY'
import warnings; warnings.filterwarnings("ignore")
import importlib.metadata
from jhora.panchanga import drik
drik.set_ayanamsa_mode("LAHIRI")   # pin Lahiri: master's import-time default may differ
print("PyJHora dev install OK — version:", importlib.metadata.version("PyJHora"))
PY

echo
echo "==> NEXT STEP: fetch the Swiss Ephemeris data files (~50 MB) that ship"
echo "    with the source tree but are NOT installed by pip:"
echo "        python3 setup_ephemeris.py"
echo "    (see REBUILD.md §Ephemeris files)"
