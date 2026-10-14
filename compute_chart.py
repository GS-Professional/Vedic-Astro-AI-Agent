#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
compute_chart.py  —  PyJHora computation engine  (Step 1 of the 2-step pipeline)
================================================================================
Purpose
-------
Turn a single birth/birthplace input into a compact, self-consistent, *expert-ready*
JSON fact sheet that can be handed to an AI astrologer (Step 2 — the reading prompt).
The file is intentionally flat: every number is a literal value already computed by
PyJHora (Lahiri ayanamsa, Drik method), with NO interpretation baked in.

Everything below is computed with the full PyJHora capability.  All the heavy,
many-hundred-row tables are exposed behind CLI flags so that:
  * the FAST path (default) gives a clean, prioritized core summary the AI reads
    without drowning, and
  * the FULL path (--full) dumps every varga, every dhasa, every strength, every
    ashtakavarga, sahams, 100+ yogas, doshas, etc. — "everything can be done".

Usage
-----
    python compute_chart.py \
        --name "Demo" \
        --lat 12.9141 --lon 74.8560 --tz 5.5 \
        --date 1990-05-15 --time 10:30:00 \
        --out chart.json                 # FAST (default)
    python compute_chart.py ... --full    # add deep tables
    python compute_chart.py ... --sections core,panchanga,dasha,house_systems,strength_full

    # You can also pass a place name instead of coordinates (uses Nominatim geocoder):
    --place "Mangaluru, India"

Author notes
------------
- Uses `kadamba`-free modern API (drik = sidereal + Lahiri by default).
- Rahu/Ketu are TRUE nodes by default (set_planet_list default).  Use
  --mean-nodes for Mean-node computations.
"""

import argparse
import copy
import datetime as dt
import importlib
import importlib.util
import inspect
import json
import math
import os
import sys

# --------------------------------------------------------------------------- #
# Imports are inside functions on purpose: a missing optional dependency or a
# single module that fails to import must not stop the whole computation.
# --------------------------------------------------------------------------- #

# Canonical names (must match utils.PLANET_NAMES / RAASI_LIST order)
PLANET_NAMES = ["Sun", "Moon", "Mars", "Mercury", "Jupiter", "Venus", "Saturn",
                "Rahu", "Ketu"]          # index 0..8
LAGNA = "Lagna"
PLANET_ORDER = ["Sun", "Moon", "Mars", "Mercury", "Jupiter", "Venus", "Saturn",
                "Rahu", "Ketu"]
RAASI_NAMES = ["Aries", "Taurus", "Gemini", "Cancer", "Leo", "Virgo",
               "Libra", "Scorpio", "Sagittarius", "Capricorn", "Aquarius", "Pisces"]
RAASI_SANKRIT = ["Mesha", "Vrishabha", "Mithuna", "Karka", "Simha", "Kanya",
                 "Tula", "Vrischika", "Dhanu", "Makara", "Kumbha", "Meena"]
NAKSHATRAS = ["Ashwini","Bharani","Krittika","Rohini","Mrigashira","Ardra",
              "Punarvasu","Pushya","Ashlesha","Magha","Purva Phalguni","Uttara Phalguni",
              "Hasta","Chitra","Swati","Vishakha","Anuradha","Jyeshtha",
              "Mula","Purva Ashadha","Uttara Ashadha","Shravana","Dhanishta","Shatabhisha",
              "Purva Bhadrapada","Uttara Bhadrapada","Revati","Abhijit"]


# --------------------------------------------------------------------------- #
# Low-level helpers
# --------------------------------------------------------------------------- #

import contextlib


@contextlib.contextmanager
def _quiet():
    """Suppress noisy PyJHora debug prints to stdout for a small block."""
    import io
    buf = io.StringIO()
    real = sys.stdout
    try:
        sys.stdout = buf
        yield
    finally:
        sys.stdout = real
        buf.close()


def _imports():
    """Import the jhora modules (and note the ephemeris requirement)."""
    import jhora
    import jhora.const as const
    from jhora import utils
    from jhora.panchanga import drik
    from jhora.horoscope.info import Horoscope
    from jhora.horoscope.chart import charts
    from jhora.horoscope.chart import house
    from jhora.horoscope.chart import strength
    from jhora.horoscope.chart import ashtakavarga
    from jhora.horoscope.chart import yoga
    from jhora.horoscope.chart import raja_yoga
    from jhora.horoscope.chart import dosha
    from jhora.horoscope.chart import arudhas
    from jhora.horoscope.chart import sphuta
    from jhora.horoscope.transit import tajaka
    try:
        from jhora.horoscope.transit import tajaka_yoga
    except Exception:
        tajaka_yoga = None   # optional — some PyJHora builds don't ship it
    # --- Optional modules surfaced by this engine (each guarded: a missing or broken
    # optional module must never stop the whole computation) -------------------------
    def _opt(modname):
        try:
            return importlib.import_module(modname)
        except BaseException:
            return None
    vratha = _opt("jhora.panchanga.vratha")
    surya_sidhantha = _opt("jhora.panchanga.surya_sidhantha")
    drik1 = _opt("jhora.panchanga.drik1")
    prediction_general = _opt("jhora.horoscope.prediction.general")
    prediction_longevity = _opt("jhora.horoscope.prediction.longevity")
    exp_marriage5 = _opt("jhora.experiments.marriage5")
    exp_marriage2 = _opt("jhora.experiments.marriage2")
    exp_double_transit = _opt("jhora.experiments.double_transit")
    exp_sensitive_point = _opt("jhora.experiments.sensitive_point")
    aayu = _opt("jhora.horoscope.dhasa.graha.aayu")   # ROUND-14: harana/longevity tables
    return dict(jhora=jhora, const=const, utils=utils, drik=drik,
                Horoscope=Horoscope, charts=charts, house=house,
                strength=strength, ashtakavarga=ashtakavarga, yoga=yoga,
                raja_yoga=raja_yoga, dosha=dosha, arudhas=arudhas,
                sphuta=sphuta, tajaka=tajaka, tajaka_yoga=tajaka_yoga,
                vratha=vratha, surya_sidhantha=surya_sidhantha, drik1=drik1,
                prediction_general=prediction_general,
                prediction_longevity=prediction_longevity,
                exp_marriage5=exp_marriage5, exp_marriage2=exp_marriage2,
                exp_double_transit=exp_double_transit,
                exp_sensitive_point=exp_sensitive_point, aayu=aayu)


def _json_coerce(obj):
    """ROUND-8 (review P0-2): coerce valid-but-not-JSON-native objects to native JSON.

    Returns (value, True) when coerced, (None, False) when the object is genuinely
    unrepresentable (the caller then masks + counts it). Handles: date/datetime →
    ISO strings, set/frozenset → lists, numpy arrays → .tolist(), numpy scalars →
    .item(). PyJHora leans on numpy in ashtakavarga/shadbala/panchanga — masking those
    as __non_serializable__ corrupted perfectly good computed numbers."""
    if isinstance(obj, (dt.date, dt.datetime)):
        return obj.isoformat(), True
    if isinstance(obj, (set, frozenset)):
        return list(obj), True
    try:
        if hasattr(obj, "tolist") and callable(obj.tolist):
            return obj.tolist(), True
        if hasattr(obj, "item") and callable(obj.item):
            return obj.item(), True
    except Exception:
        pass
    return None, False


# Plain-string error markers used by _collect_errors in main(). ROUND-8 (review P1-7):
# a bare "Exception" marker false-positives on literary prose (bundled prediction texts
# can legitimately contain the word) — only the colon/paren forms that appear in real
# str(e) output are kept. Module-level so the audit suite can test the vocabulary.
_ERR_STRING_MARKERS = ("Traceback", "no attribute", "Exception:", "Exception(",
                       "not available in this",
                       # ROUND-7 (review tail): typed leak markers — exception MESSAGES
                       # that reach the JSON as plain strings usually start with these.
                       "TypeError:", "ValueError:", "IndexError:", "AttributeError:",
                       "KeyError:", "NameError:", "ZeroDivisionError:", "ImportError:",
                       "is not subscriptable", "missing 1 required positional",
                       "unexpected keyword argument")


def _pid_label(pid):
    """Planet id -> display name, resilient to ids beyond the classical 0..8 vocabulary.
    ROUND-8 hardening (review P1-9): current builds only ever pass 'L' + ints 0..8, but a
    future dev line exposing outer planets ('U'/'N'/'P' or ids >= 9) would previously
    raise ValueError/IndexError here; unknown ids are now labelled, never fatal."""
    try:
        idx = int(pid)
    except (TypeError, ValueError):
        return str(pid)
    if 0 <= idx < len(PLANET_NAMES):
        return PLANET_NAMES[idx]
    return "Planet_%d" % idx


def _planetary_longitude(planet_positions):
    """Return {planet_name: absolute_longitude_in_sign0} for the new-style list."""
    out = {}
    for item in planet_positions:
        pid, val = item[0], item[1]
        sign, deg = val[0], val[1]
        name = LAGNA if pid == "L" else _pid_label(pid)
        out[name] = sign * 30.0 + deg
    return out


def _sign_of_long(lon):
    return int(lon // 30.0) % 12


def _deg_in_sign(lon):
    return lon - int(lon // 30.0) * 30


def _nakshatra_of_long(lon):
    """Return (nakshatra_name, pada_index_1_4, fraction_in_nakshatra)."""
    span = 360.0 / 27.0          # 13.3333 deg per nakshatra
    idx = int(lon / span) % 27
    within = lon - idx * span
    pada = int(within / (span / 4.0)) + 1
    return NAKSHATRAS[idx], pada, within


# ROUND-8 (review P3-6): Vimshottari nakshatra lordship — Ashwini is ruled by Ketu and
# the 9-lord cycle repeats three times across the 27 nakshatras.
NAKSHATRA_LORDS = ["Ketu", "Venus", "Sun", "Moon", "Mars", "Rahu", "Jupiter",
                   "Saturn", "Mercury"]


def _nak_lord_of_long(lon):
    """Vimshottari ruler of the nakshatra containing this longitude."""
    try:
        return NAKSHATRA_LORDS[int(lon / (360.0 / 27.0)) % 9]
    except Exception:
        return None


def _navamsa_sign_of_long(lon):
    """ROUND-12 (review F2): the navamsa (D9) sign containing this longitude. Each
    3°20' nakshatra pada maps 1:1 onto a navamsa, so the D9 sign is a pure function
    of the D1 longitude: int(lon / (360/108)) % 12. Audit 12f proves equality with
    the library's own divisional_chart(9) placements.
    ROUND-13 (review item 1): longitude is wrapped into [0, 360) first — defensive
    hardening only. The review's boundary CLAIM was wrong: 360.0° ≡ 0.0° = Aries
    0°0'0", whose navamsa IS the first one (index 0), both before and after the
    wrap (their own patched formula also returns 0 for 360.0, not the Pisces/11
    they asserted). Since 108 navamsas per 360° and 108 % 12 == 0, the wrap is
    provably a NO-OP for every non-negative input (audit 13a-1 sweeps it), and the
    engine only ever feeds it sign*30 + deg < 360."""
    return int((float(lon) % 360.0) / (360.0 / 108.0)) % 12


def _house_number(asc_sign, sign):
    """Whole-sign house number of a sign relative to ascendant sign (1..12)."""
    return ((sign - asc_sign) % 12) + 1


SIGN_LORDS = ["Mars", "Venus", "Mercury", "Moon", "Sun", "Mercury",
              "Venus", "Mars", "Jupiter", "Saturn", "Saturn", "Jupiter"]
# Exaltation sign index (classical seven + Rahu/Ketu)
EXALTATION = {"Sun": 0, "Moon": 1, "Mars": 9, "Mercury": 5, "Jupiter": 3,
              "Venus": 11, "Saturn": 6, "Rahu": 1, "Ketu": 7}

# ---- ROUND-11 (review P2-1/P2-3/P2-4) wrapper-owned doctrine constants ----------
# Standard BPHS combustion orbs (degrees from the Sun). Retrograde Mercury/Venus use
# the reduced orbs. These are NOT read from const.combustion_range: the library table
# is indexed at planet_id-2 inside charts.planets_in_combustion (every planet gets
# the PREVIOUS planet's orb) — see planet_flags.combustion_detail.__note__.
STD_COMBUSTION_ORBS = {"Moon": 12.0, "Mars": 17.0, "Mercury": 14.0,
                       "Jupiter": 11.0, "Venus": 10.0, "Saturn": 15.0}
STD_COMBUSTION_ORBS_RETRO = {"Mercury": 12.0, "Venus": 8.0}
CAZIMI_MAX_DEG = 0.2833                # 0 deg 17 min — the Sun's heart zone
# Dagdha ("burnt") rasis per tithi — shukla rows 1..14; krishna tithis 16..29 reuse
# the shukla table (n-15); Purnima (15) and Amavasya (30) have NONE. Sign indices
# 0=Aries..11=Pisces. Primary table per the review's source (anandamoyee.home.blog);
# DAGDHA_RASIS_VARIANTS holds the rows where freevedicastro.com differs — BOTH are
# disclosed in the output, neither is silently chosen.
DAGDHA_RASIS = {
    1: [6, 9], 2: [8, 11], 3: [4, 9], 4: [1, 10], 5: [2, 5],
    6: [0, 4], 7: [3, 8], 8: [2, 5], 9: [4, 7], 10: [4, 7],
    11: [8, 11], 12: [6, 9], 13: [1, 4], 14: [2, 5, 8, 11],
}
DAGDHA_RASIS_VARIANTS = {10: [6, 7], 13: [1, 10]}
# Kakshya (BAV sub-sign) lords: every sign divides into 8 kakshyas of 3 deg 45 min,
# ruled FROM 0 deg of the sign by Saturn, Jupiter, Mars, Sun, Venus, Mercury, Moon,
# Lagna (web-verified consensus; no PyJHora kakshya API exists). KAKSHYA_ROW_IDS maps
# names to prastara row/column ids: 0=Sun,1=Moon,2=Mars,3=Mercury,4=Jupiter,5=Venus,
# 6=Saturn,7=Lagna (matches ashtakavarga's own row order "Sun..Saturn, Lagnam").
KAKSHYA_LORDS = ["Saturn", "Jupiter", "Mars", "Sun", "Venus", "Mercury", "Moon", "Lagna"]
KAKSHYA_ROW_IDS = {"Sun": 0, "Moon": 1, "Mars": 2, "Mercury": 3, "Jupiter": 4,
                   "Venus": 5, "Saturn": 6, "Lagna": 7}
KAKSHYA_SPAN_DEG = 3.75
# ROUND-12 (review Bug-3): BAV/prastara row+column NAMES — the ashtakavarga grid has
# 8 rows/cols for Sun..Saturn + LAGNA (get_ashtaka_varga docstring: "0=Sun..7=Lagnam").
# Rahu/Ketu have NO BAV rows; PLANET_NAMES[7] ("Rahu") must never label row 7.
BAV_ROW_NAMES = ["Sun", "Moon", "Mars", "Mercury", "Jupiter", "Venus", "Saturn", "Lagna"]
# ROUND-12 (review F5): classical Yogini-dasha names keyed by the lord's planet id.
# The library supplies lords + durations (yogini.dhasa_adhipathi_list = {Moon:1,
# Sun:2, Jupiter:3, Mars:4, Mercury:5, Saturn:6, Venus:7, Rahu:8} — the canonical
# Mangala..Sankata order, 36-year cycle) but NO names; names web-verified unanimous.
YOGINI_NAMES_BY_LORD_ID = {1: "Mangala", 0: "Pingala", 4: "Dhanya", 2: "Bhramari",
                           3: "Bhadrika", 6: "Ulka", 5: "Siddha", 7: "Sankata"}


def _dignity(planet, sign):
    """Return objective dignity label from sign placement."""
    if planet in EXALTATION:
        if sign == EXALTATION[planet]:
            return "Exalted"
        if sign == (EXALTATION[planet] + 6) % 12:
            return "Debilitated"
    if SIGN_LORDS[sign] == planet:
        return "Own_sign"
    return "In_other_sign"


# --------------------------------------------------------------------------- #
# Section builders
# --------------------------------------------------------------------------- #

# A process-level lock so concurrent chart requests cannot interleave PyJHora's GLOBAL state
# mutations (set_planet_list / set_ayanamsa_mode). PyJHora and Swiss Ephemeris keep the
# ayanamsa + node mode as process-global state; without the lock two requests can overwrite
# each other mid-run (review §3: "not truly thread-safe"). For a true parallel service use
# separate worker processes (documented in the README); the lock merely serializes them.
_PYJHORA_CONFIG_LOCK = __import__("threading").RLock()


# --------------------------------------------------------------------------- #
# PyJHora compatibility patches (wrapper-side, restored on exit)
# --------------------------------------------------------------------------- #
# Some PyJHora 4.8.7 code paths are internally inconsistent with the rest of the
# same release. Instead of reporting them as uncomputable, we apply minimal,
# documented monkey-patches for the duration of one chart run and restore the
# originals on exit (same discipline as the ayanamsa/node isolation below).
#
# PATCH 1 — strength._cheshta_bala:
#   The old `_cheshta_bala(jd, place)` calls `drik.planet_list.index(p)` and
#   `surya_sidhantha._planet_mean_longitude`, but in this build `drik.planet_list`
#   is a DICT ({swe_num: jhora_id}) and `ss.kali_ahargana` calls `drik.vaara(jd)`
#   without the required `place` — so the old function can never run. The library's
#   OWN maintained replacement `_cheshta_bala_new(jd, place, use_epoch_table=True)`
#   is what `shad_bala` itself uses internally. We delegate to it, so the
#   standalone `cheshta_bala` component now EQUALS the chesta component embedded
#   in the composed `shad_bala` total (verified on both test charts), and
#   `_subha_rashmi`/`subha_phala` (which call `_cheshta_rashmi` -> `_cheshta_bala`)
#   compute too.
_COMPAT_PATCHES = []


def _apply_compat_patch(obj, attr, new_value, reason=""):
    """Record + apply a monkey-patch; restored by `_restore_compat_patches`."""
    _COMPAT_PATCHES.append((obj, attr, getattr(obj, attr, None), hasattr(obj, attr), reason))
    setattr(obj, attr, new_value)


def _restore_compat_patches():
    while _COMPAT_PATCHES:
        obj, attr, orig, existed, _reason = _COMPAT_PATCHES.pop()
        try:
            if existed:
                setattr(obj, attr, orig)
            elif hasattr(obj, attr):
                delattr(obj, attr)
        except Exception:
            pass


def _compat_patch_report():
    """ROUND-7 (review C8): JSON-safe snapshot of the compat patches installed RIGHT NOW.
    Captured into `__meta__.compatibility_patches` after all sections are computed (some
    patches — life_span_range, the experiments marriage expander — install mid-run)."""
    out = []
    for entry in list(_COMPAT_PATCHES):
        obj, attr = entry[0], entry[1]
        reason = entry[4] if len(entry) > 4 else ""
        out.append({"module": getattr(obj, "__name__", str(obj)), "attribute": attr,
                    "reason": reason or "wrapper-side fix for a broken upstream code path"})
    return out


def _pyjhora_version(env=None):
    """PyJHora version string. 5.0 moved it from `jhora.__version__` to
    `jhora._package_info.version`; resolve tolerantly across both."""
    try:
        j = (env or {}).get("jhora")
        v = getattr(j, "__version__", None)
        if v:
            return str(v)
        import jhora._package_info as _pi
        return str(getattr(_pi, "version", "?"))
    except Exception:
        return "?"


def _parse_version_tuple(s):
    """'4.8.7' / '5.0' / '4.9.3.dev0' -> comparable int tuple (dev/rc suffixes dropped)."""
    try:
        core = str(s).split("+")[0]
        for sep in (".dev", "dev", "rc", "a", "b", "-"):
            core = core.split(sep)[0]
        parts = [int(p) for p in core.split(".") if p.isdigit()]
        return tuple(parts) if parts else None
    except Exception:
        return None


def check_for_updates(installed_version, timeout=4.0):
    """ROUND-7 (user request): a SMALL, non-fatal update check on every run.

    Compares the installed PyJHora against (1) the latest PyPI stable release and
    (2) the GitHub master dev version (the 4.9.x/5.x dev line is NOT published on PyPI,
    so PyPI alone would never show it). Returns a small dict for `__meta__.update_check`;
    NEVER raises, NEVER writes to stdout (main() prints notices on stderr). Offline runs
    degrade to per-source error fields."""
    import re as _re
    import urllib.request as _ur
    res = {"installed": str(installed_version),
           "checked_at": dt.datetime.now().isoformat(timespec="seconds"),
           "pypi_latest": None, "github_master_version": None,
           "update_available": False, "newer_than_installed": [], "errors": {}}

    def _get(url):
        req = _ur.Request(url, headers={"User-Agent": "pyjhora-astrology-wrapper/update-check"})
        with _ur.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", "replace")

    try:
        data = json.loads(_get("https://pypi.org/pypi/PyJHora/json"))
        res["pypi_latest"] = (data.get("info") or {}).get("version")
    except Exception as e:
        res["errors"]["pypi"] = repr(e)[:140]
    try:
        txt = _get("https://raw.githubusercontent.com/naturalstupid/PyJHora/master/"
                   "src/jhora/_package_info.py")
        m = _re.search(r"^version\s*=\s*[\"']([^\"']+)[\"']", txt, _re.M)
        res["github_master_version"] = m.group(1) if m else None
    except Exception as e:
        res["errors"]["github"] = repr(e)[:140]
    inst = _parse_version_tuple(installed_version)
    for label, ver in (("pypi", res["pypi_latest"]),
                       ("github_master", res["github_master_version"])):
        vt = _parse_version_tuple(ver) if ver else None
        if inst and vt and vt > inst:
            res["update_available"] = True
            res["newer_than_installed"].append({"source": label, "version": ver})
    return res


def _install_compat_patches():
    """Install every wrapper-side PyJHora compatibility patch (idempotent-ish:
    call once per chart run inside `_isolated_pyjhora_config`)."""
    # PATCH 1 — cheshta bala (see note above). ROUND-7: re-verified STILL broken in the
    # 5.0 dev line (`'dict' object has no attribute 'index'` unpatched), so the patch stays.
    try:
        from jhora.horoscope.chart import strength as _st
        if hasattr(_st, "_cheshta_bala_new"):
            _apply_compat_patch(
                _st, "_cheshta_bala",
                lambda j, p: _st._cheshta_bala_new(j, p, use_epoch_table=True),
                reason="upstream _cheshta_bala raises 'dict' has no attribute 'index' "
                       "(present in 4.8.7 AND 5.0-dev); delegate to the maintained "
                       "_cheshta_bala_new used internally by shad_bala")
    except Exception:
        pass

    # PATCH 2 — previous_ascendant_entry_date (NEW 5.0-dev bug, found by this wrapper's
    # Round-7 run): it forwards `increment_days=` to next_ascendant_entry_date, which
    # DROPPED that kwarg in 5.0 — every call raises TypeError. Strip the kwarg and
    # forward. Signature-guarded: auto-no-ops once upstream fixes the mismatch.
    try:
        from jhora.panchanga import drik as _dk
        import inspect as _insp
        if hasattr(_dk, "previous_ascendant_entry_date") and hasattr(_dk, "next_ascendant_entry_date"):
            _nparams = _insp.signature(_dk.next_ascendant_entry_date).parameters
            _pparams = _insp.signature(_dk.previous_ascendant_entry_date).parameters
            if "increment_days" not in _nparams and "increment_days" in _pparams:
                def _prev_asc_entry_fixed(jd, place, increment_days=0.01, precision=0.1,
                                          raasi=None, divisional_chart_factor=1):
                    return _dk.next_ascendant_entry_date(
                        jd, place, direction=-1, precision=precision, raasi=raasi,
                        divisional_chart_factor=divisional_chart_factor)
                _apply_compat_patch(
                    _dk, "previous_ascendant_entry_date", _prev_asc_entry_fixed,
                    reason="5.0-dev previous_ascendant_entry_date forwards increment_days= to "
                           "next_ascendant_entry_date which dropped that kwarg (TypeError on "
                           "every call); strip it and forward direction=-1")
    except Exception:
        pass

    # PATCH 3 — tajaka_yoga missing `charts` import (NEW 5.0-dev bug found by this
    # wrapper): get_duhphali_kutta_yoga_planet_pairs references a global `charts` the
    # module never imports -> NameError on every call. Inject the module as an attribute
    # (restored by _restore_compat_patches via delattr since it did not exist before).
    try:
        from jhora.horoscope.transit import tajaka_yoga as _ty
        from jhora.horoscope.chart import charts as _ch
        if not hasattr(_ty, "charts"):
            _apply_compat_patch(
                _ty, "charts", _ch,
                reason="5.0-dev tajaka_yoga references global 'charts' without importing it "
                       "(NameError in get_duhphali_kutta_yoga_planet_pairs); inject the module")
    except Exception:
        pass


@contextlib.contextmanager
def _isolated_pyjhora_config(drik, const, nodes_kw=None, ayanamsa="LAHIRI",
                             dasha_year_duration=None, install_patches=True):
    """Isolate PyJHora's GLOBAL engine state for the duration of one chart run.

    PyJHora keeps its ayanamsa and Rahu/Ketu-node choice as module globals
    (`const._DEFAULT_AYANAMSA_MODE`, `const._use_true_nodes_for_rahu_ketu`) plus
    `drik.planet_list`, mutated by `set_planet_list()` / `set_ayanamsa_mode()`. There is
    also Swiss Ephemeris' own sidereal mode changed under the hood by `set_ayanamsa_mode`.

    We (1) hold `_PYJHORA_CONFIG_LOCK` so concurrent requests serialize (the global state
    cannot be claimed thread-safe without it), and (2) restore on exit the prior Python
    globals AND re-apply the prior ayanamsa to Swiss Ephemeris (`drik.set_ayanamsa_mode(
    prior)`), not just the const string, so the actual SWE sidereal mode is restored too
    (review §3: "restores const by assignment without restoring SWE's sidereal mode").

    REVIEW FIX: the prior-state SNAPSHOT is taken INSIDE the lock. Previously it was read
    before `with _PYJHORA_CONFIG_LOCK`, so a concurrent thread could mutate the globals
    between the reader and the acquisition, and we would snapshot a value that was already
    half-overwritten. Now the snapshot and the mutation are atomic w.r.t. the lock.
    """
    with _PYJHORA_CONFIG_LOCK:
        _prior = (getattr(const, "_use_true_nodes_for_rahu_ketu", None),
                  getattr(const, "_DEFAULT_AYANAMSA_MODE", None),
                  getattr(drik, "planet_list", None),
                  getattr(const, "dhasa_year_duration_default", None))
        drik.set_planet_list(**(nodes_kw or {"set_rahu_ketu_as_true_nodes": True}))
        # REVIEW R6: optional dasha year-duration mode (const.DHASA_YEAR_DURATION enum),
        # restored on exit like every other global.
        if dasha_year_duration is not None:
            try:
                const.dhasa_year_duration_default = const.DHASA_YEAR_DURATION(int(dasha_year_duration))
            except Exception:
                pass  # keep the library default if the value is invalid
        try:
            drik.set_ayanamsa_mode(str(ayanamsa).upper())
        except Exception as e:
            print("ayanamsa set failed:", e)
        # wrapper-side compatibility patches for internally-broken PyJHora paths
        # (documented at `_install_compat_patches`); restored in the finally block.
        # ROUND-7 (review C8): `--stock` skips them entirely so consumers can diff
        # wrapper-patched vs pure-upstream behavior; broken paths then surface as
        # ordinary guarded __error__ fields.
        if install_patches:
            _install_compat_patches()
        try:
            yield
        finally:
            _restore_compat_patches()
            prior_nodes, prior_ayan, prior_pl, prior_dyd = _prior
            # restore the PROCESS-global state so the next request starts clean
            try:
                if prior_nodes is not None:
                    const._use_true_nodes_for_rahu_ketu = prior_nodes
                if prior_pl is not None:
                    drik.planet_list = prior_pl
                if prior_dyd is not None:
                    const.dhasa_year_duration_default = prior_dyd
            except Exception:
                pass
            # restore the SWE sidereal mode too (not just the const string)
            if prior_ayan is not None:
                try:
                    const._DEFAULT_AYANAMSA_MODE = str(prior_ayan)
                    drik.set_ayanamsa_mode(str(prior_ayan).upper())
                except Exception:
                    pass

def build_core(env, h, jd, place, planet_positions):
    """Base D1 positions, house occupants, lagna, panchanga basics."""
    ppl = _planetary_longitude(planet_positions)
    asc_sign = _sign_of_long(ppl[LAGNA])
    core = {}

    # ROUND-10 (review P2-7 + P2-8): inline state flags + pushkara-bhaga details.
    # Retrograde comes from drik.planets_in_retrograde (planet_flags documents that
    # charts.planets_in_retrograde is STALE in 5.0); combustion from
    # charts.planets_in_combustion — the SAME sources planet_flags uses, so the two
    # views can never disagree.
    _const = env["const"]
    _retro_names, _comb_names = set(), set()
    try:
        with _quiet():
            _rl = env["drik"].planets_in_retrograde(jd, place)
        _retro_names = {str(n) for n in _decode_id_list(_rl)}
    except Exception:
        pass
    try:
        with _quiet():
            _cl = env["charts"].planets_in_combustion(planet_positions)
        _comb_names = {str(n) for n in _decode_id_list(_cl)}
    except Exception:
        pass
    _PB_STD = [float(x) for x in _const.pushkara_bhagas]
    _PB_JP = [float(x) for x in _const.pushkara_bhaga_jathaka_paarijaatha]

    def _pushkara(sign_idx, deg):
        # Distances are SIGNED (deg − bhaga°). The ±1° orb flags follow the review's
        # request; the LIBRARY's own membership rule (used by
        # charts.planets_in_pushkara_navamsa_bhaga) is the 1° band BEFORE the degree,
        # [bhaga−1°, bhaga°) — reported separately so both doctrines stay visible.
        ds = deg - _PB_STD[sign_idx]
        dj = deg - _PB_JP[sign_idx]
        return {
            "degree_standard_table": _PB_STD[sign_idx],
            "distance_from_standard_deg": round(ds, 4),
            "in_pushkara_bhaga_orb_1deg": abs(ds) <= 1.0,
            "degree_jathaka_paarijaatha_table": _PB_JP[sign_idx],
            "distance_from_jathaka_paarijaatha_deg": round(dj, 4),
            "in_pushkara_bhaga_jathaka_paarijaatha_orb_1deg": abs(dj) <= 1.0,
            "in_library_membership_band": bool(_PB_STD[sign_idx] - 1.0 <= deg < _PB_STD[sign_idx]),
        }

    _lnak, _lpad, _ = _nakshatra_of_long(ppl[LAGNA])
    core["lagna"] = {"sign_index": asc_sign, "sign": RAASI_NAMES[asc_sign],
                     "sankrit": RAASI_SANKRIT[asc_sign],
                     "longitude": round(ppl[LAGNA], 4),
                     "nakshatra": _lnak, "nakshatra_pada": _lpad,
                     "nakshatra_lord": _nak_lord_of_long(ppl[LAGNA]),
                     "navamsa_sign_index": _navamsa_sign_of_long(ppl[LAGNA]),
                     "navamsa_sign": RAASI_NAMES[_navamsa_sign_of_long(ppl[LAGNA])],
                     "pushkara_bhaga": _pushkara(asc_sign, ppl[LAGNA] % 30.0),
                     "kp": _decode_kp_details(ppl[LAGNA]),
                     "library_navamsa": _library_navamsa(ppl[LAGNA])}

    _sun_lon = ppl.get("Sun")
    planets = []
    for pid_name in PLANET_ORDER:
        lon = ppl[pid_name]
        s = _sign_of_long(lon); d = _deg_in_sign(lon)
        nak, pada, frac = _nakshatra_of_long(lon)
        # ROUND-10 (review P2-7): angular distance as the true MINIMAL arc
        # min(|Δ|, 360−|Δ|) — the review's abs((p−s) % 360) alone is NOT minimal
        # (it reports 350° where the separation is 10°).
        _adist = None
        if _sun_lon is not None:
            _dd = abs(lon - _sun_lon) % 360.0
            _adist = round(min(_dd, 360.0 - _dd), 4)
        planets.append({
            "name": pid_name,
            "sign_index": s, "sign": RAASI_NAMES[s], "sankrit": RAASI_SANKRIT[s],
            "degree": round(d, 4), "longitude": round(lon, 4),
            "house": _house_number(asc_sign, s),
            "dignity": _dignity(pid_name, s),
            "nakshatra": nak, "nakshatra_pada": pada,
            "nakshatra_lord": _nak_lord_of_long(lon),
            # ROUND-12 (review F2): inline D9 navamsa sign (pure function of the D1
            # longitude; audited identical to divisional_chart(9) placements)
            "navamsa_sign_index": _navamsa_sign_of_long(lon),
            "navamsa_sign": RAASI_NAMES[_navamsa_sign_of_long(lon)],
            "absolute_longitude": round(lon, 4),
            "is_retrograde": pid_name in _retro_names,
            "is_combust": pid_name in _comb_names,
            "angular_distance_from_sun": _adist,
            "pushkara_bhaga": _pushkara(s, d),
            "kp": _decode_kp_details(lon),
            "library_navamsa": _library_navamsa(lon),
        })
    core["planets"] = planets
    core["planet_inline_flags_note"] = (
        "ROUND-10 (review P2-7/P2-8): is_retrograde / is_combust / "
        "angular_distance_from_sun and pushkara_bhaga are inlined per planet (and "
        "pushkara_bhaga on the lagna) for reader convenience. Sources: "
        "drik.planets_in_retrograde(jd, place) and charts.planets_in_combustion — "
        "identical to planet_flags. ROUND-11 (review P2-1): planet_flags."
        "combustion_detail recomputes combustion under STANDARD BPHS orbs + cazimi "
        "and flags every divergence from the library verdict (the library's orb "
        "table is mis-indexed at planet_id-2 and its retrograde check is stale — "
        "disclosed, not refixed). Pushkara-bhaga degrees come from PyJHora's const "
        "tables: pushkara_bhagas (21/14/24/7 repeating per triplicity) and "
        "pushkara_bhaga_jathaka_paarijaatha (Jathaka Parijaatha variant). The degree "
        "list cited by the review (21/19/23/15/17/19…) matches NEITHER library table "
        "and was not used. planet_flags.pushkara_navamsa also lists library-rule "
        "membership for both pushkara navamsa and bhaga. ROUND-12 (review F2): "
        "navamsa_sign/_index are inlined per planet and on the lagna — the D9 sign "
        "of the same longitude (int(lon/(360/108))%12), audited identical to the "
        "library's divisional_chart(9) placements; vargas.D9 remains the full "
        "divisional chart for navamsa-lagna-relative readings.")


    # house table: for each of the 12 houses of the lagna, sign + lord + occupants
    house_table = []
    for hno in range(1, 13):
        sign = (asc_sign + hno - 1) % 12
        occ = [p for p in PLANET_ORDER if _house_number(asc_sign, _sign_of_long(ppl[p])) == hno]
        house_table.append({
            "house": hno,
            "sign_index": sign, "sign": RAASI_NAMES[sign],
            "sign_lord": SIGN_LORDS[sign],
            "planets_in_house": occ,
        })
    core["houses"] = house_table
    core["lagna_in_house"] = 1
    return core


WEEKDAY_NAMES = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday",
                 "Friday", "Saturday"]
YOGA_NAMES = ["Vishkambha","Priti","Ayushman","Saubhagya","Shobhana","Atiganda",
              "Sukarma","Dhriti","Shula","Ganda","Vriddhi","Dhruva","Vyaghata",
              "Harshana","Vajra","Siddhi","Vyatipata","Variyana","Parigha","Shiva",
              "Siddha","Sadhya","Shubha","Shukla","Brahma","Indra","Vaidhriti"]
KARANA_FIRST = ["Kimstughna"]
KARANA_CYCLE = ["Bava","Balava","Kaulava","Taitila","Gara","Vanija","Vishti"]
TITHI_NAMES = ["Pratipada","Dwitiya","Tritiya","Chaturthi","Panchami","Shashthi",
               "Saptami","Ashtami","Navami","Dashami","Ekadashi","Dwadashi",
               "Trayodashi","Chaturdashi"]  # index -> part (Amavasya/Purnima handled)


def _tithi_name(idx):
    """tithi (1-based 1..30) -> (name, paksha). 1..15 Shukla, 16..30 Krishna.
       #15 = Purnima, #30 = Amavasya."""
    t = int(idx) % 30
    if t == 0:
        t = 30
    if 1 <= t <= 15:
        paksha = "Shukla"; phase = t
    else:
        paksha = "Krishna"; phase = t - 15
    if phase == 15:
        return "Purnima" if paksha == "Shukla" else "Amavasya", paksha
    return TITHI_NAMES[phase - 1], paksha


def _karana_name(idx):
    i = int(round(idx))
    if i <= 1:
        return KARANA_FIRST[0]
    return KARANA_CYCLE[(i - 2) % 7]


def _hours_to_hhmm(h):
    """A fractional local-hour count -> 'HH:MM' with day offset.

    ROUND-15: PyJHora tithi/yoga/karana START hours are frequently NEGATIVE
    (the limb began the previous civil day). The old `int(h // 24)` path
    dropped the negative day marker, so a 25-hour Krishna Panchami showed
    start=10:14 and end=11:40 with no (-1d) — readers treated tithi times
    as empty/nonsensical. floor(h/24) now emits '(-1d)' / '(+Nd)'.
    """
    try:
        h = float(h)
        day_shift = int(math.floor(h / 24.0))
        rem = h - day_shift * 24.0
        if rem < 0:
            rem += 24.0
            day_shift -= 1
        hh = int(rem)
        mm = int((rem - hh) * 60.0)   # truncate, matching the pre-R15 clock display
        clock = f"{hh:02d}:{mm:02d}"
        if day_shift > 0:
            return f"{clock} (+{day_shift}d)"
        if day_shift < 0:
            return f"{clock} ({day_shift}d)"
        return clock
    except Exception:
        return h


def _span_times(start_h, end_h):
    """Decode a (start_hours, end_hours) pair that may be negative / >24."""
    out = {}
    try:
        sh, eh = float(start_h), float(end_h)
        out["start_hours_raw"] = round(sh, 6)
        out["end_hours_raw"] = round(eh, 6)
        out["start_time"] = _hours_to_hhmm(sh)
        out["end_time"] = _hours_to_hhmm(eh)
        out["duration_hours"] = round(eh - sh, 6)
        out["started_previous_civil_day"] = bool(sh < 0)
        out["ends_next_civil_day"] = bool(eh >= 24.0)
    except Exception as e:
        out["__error__"] = str(e)
    return out


SAMVATSARA_NAMES = [
    "Prabhava", "Vibhava", "Shukla", "Pramoda", "Prajapati", "Angira",
    "Shrimukha", "Bhava", "Yuva", "Dhatri", "Ishvara", "Bahudhanya",
    "Pramathi", "Vikrama", "Vrisha", "Chitrabhanu", "Svabhanu", "Tarana",
    "Parthiva", "Vyaya", "Sarvajit", "Sarvadhari", "Virodhi", "Vikriti",
    "Khara", "Nandana", "Vijaya", "Jaya", "Manmatha", "Durmukha",
    "Hevilambi", "Vilambi", "Vikari", "Sharvari", "Plava", "Shubhakrit",
    "Shobhakrit", "Krodhi", "Vishvavasu", "Parabhava", "Plavanga", "Kilaka",
    "Saumya", "Sadharana", "Virodhikrit", "Paridhavi", "Pramadi", "Ananda",
    "Rakshasa", "Nala", "Pingala", "Kalayukta", "Siddharthi", "Raudra",
    "Durmati", "Dundubhi", "Rudhirodgari", "Raktakshi", "Krodhana", "Akshaya",
]
LUNAR_MONTH_NAMES = [
    "Chaitra", "Vaisakha", "Jyeshtha", "Ashadha", "Shravana", "Bhadrapada",
    "Ashvina", "Kartika", "Margashirsha", "Pausha", "Magha", "Phalguna",
]
TITHI_HOUSE_LABELS = [
    "Janma (1 self)", "Dhana (2 wealth)", "Bhratri (3 siblings)", "Matri (4 mother)",
    "Putra (5 children)", "Satru (6 enemies)", "Kalatra (7 spouse)", "Mrityu (8 longevity)",
    "Bhagya (9 fortune)", "Karma (10 career)", "Labha (11 gains)", "Vyaya (12 loss)",
]


def build_panchanga(env, h, jd, place):
    drik = env["drik"]
    out = {}
    def safe(fn, *a, **k):
        try: return fn(*a, **k)
        except Exception as e: return {"__error__": str(e)}

    # NOTE: drik returns 1-based indices. We decode to NAMES so the AI never has to.
    # tithi: [no, start_hr, end_hr, frac, ...]; yogam: [no, start_hr, end_hr, frac, ...];
    # karana: (no, start_hr, end_hr, ...); nakshatra: [no, pada, end_hr, ...].
    # start/end times are LOCAL hours (may exceed 24 = following day).
    _t = safe(drik.tithi, jd, place)
    if isinstance(_t, (list, tuple)) and len(_t):
        _tn, _tp = _tithi_name(_t[0])
        out["tithi"] = {"index": _t[0], "name": _tn, "paksha": _tp}
        if len(_t) > 2:
            out["tithi"].update(_span_times(_t[1], _t[2]))
        # ROUND-15: tithi() also returns the NEXT tithi (index 3+) — previously dropped.
        if len(_t) > 3 and _t[3] not in (None,):
            try:
                _ni = int(_t[3])
                _nn, _np = _tithi_name(_ni)
                out["tithi"]["next_index"] = _ni
                out["tithi"]["next_name"] = _nn
                out["tithi"]["next_paksha"] = _np
                if len(_t) > 5:
                    out["tithi"]["next_start_time"] = _hours_to_hhmm(_t[4])
                    out["tithi"]["next_end_time"] = _hours_to_hhmm(_t[5])
            except Exception:
                pass
        try:
            _td = env["utils"].TITHI_DEITIES
            out["tithi"]["nitya_devi"] = _td[(int(_t[0]) - 1) % len(_td)]
            out["tithi"]["nitya_devi_note"] = (
                "const/utils TITHI_DEITIES = the 16 Nitya Devis cycling over 30 tithis "
                "(NOT a planetary tithi-lord table — that mapping is lineage-specific "
                "and stays in omitted_checks).")
        except Exception:
            pass
    else:
        out["tithi"] = _t
    # ---- ROUND-11 (review P2-3): DAGDHA ("burnt") RASIS of the running tithi ----
    # Wrapper-computed from the classical tithi->signs table (module constant
    # DAGDHA_RASIS). Krishna-paksha tithis 16..29 reuse the shukla table (n-15);
    # Purnima (15) and Amavasya (30) have NO dagdha rasis. Two rows are disputed
    # across sources — primary table AND variant are both disclosed, never silently
    # chosen.
    try:
        _ti11 = int(_t[0]) if isinstance(_t, (list, tuple)) else int(_t)
        _tk11 = 0 if _ti11 in (15, 30) else ((_ti11 - 1) % 15) + 1
        _dsg11 = DAGDHA_RASIS.get(_tk11, [])
        _dr = {"tithi_index": _ti11,
               "tithi_name": _tithi_name(_ti11)[0],
               "paksha": _tithi_name(_ti11)[1],
               "table_row_used": (_tk11 if _tk11 else None),
               "mapping": ("shukla tithi n -> table row n; krishna tithi n -> row "
                           "n-15; Purnima (15) and Amavasya (30) -> no dagdha rasis"),
               "dagdha_signs": [RAASI_NAMES[i] for i in _dsg11],
               "dagdha_sign_indices": list(_dsg11)}
        if _tk11 in DAGDHA_RASIS_VARIANTS:
            _dr["disputed_variant_signs"] = [RAASI_NAMES[i] for i in
                                             DAGDHA_RASIS_VARIANTS[_tk11]]
        try:
            _rc11 = env["charts"].rasi_chart(jd, place)
            _lp11 = _planetary_longitude(_rc11)
            _asc11 = _sign_of_long(_lp11[LAGNA])
            _dr["planets_in_dagdha_signs"] = [
                {"planet": _pn11,
                 "sign": RAASI_NAMES[_sign_of_long(_lp11[_pn11])],
                 "house": _house_number(_asc11, _sign_of_long(_lp11[_pn11]))}
                for _pn11 in PLANET_ORDER
                if _sign_of_long(_lp11[_pn11]) in _dsg11]
            _dr["houses_in_dagdha"] = [_house_number(_asc11, i) for i in _dsg11]
        except Exception:
            _dr["planets_in_dagdha_signs"] = "__error__ (positions unavailable)"
        _dr["__note__"] = (
            "ROUND-11 (review P2-3): dagdha rasis — the signs 'burnt' by the running "
            "tithi; initiating the sign's matters (travel, marriage, new ventures) is "
            "traditionally avoided while it is dagdha, and natal planets sitting in a "
            "dagdha sign at birth are listed for reading context. Primary table per "
            "the review's source (anandamoyee.home.blog, matching the Muhurta "
            "Chintamani-lineage tables); freevedicastro.com differs ONLY on Dashami "
            "(Libra+Scorpio instead of Leo+Scorpio) and Trayodashi (Taurus+Aquarius "
            "instead of Taurus+Leo) — both variants disclosed above. The dagdha YOGA "
            "muhurta combinations (tithi x vara / nakshatra x vara) remain uncomputed "
            "— see panchanga.omitted_checks.")
        out["dagdha_rasis"] = _dr
    except Exception as e:
        out["dagdha_rasis"] = {"__error__": str(e)}
    _y = safe(drik.yogam, jd, place)
    if _y and isinstance(_y, (list, tuple)):
        out["yoga"] = {"index": _y[0], "name": YOGA_NAMES[(int(_y[0]) - 1) % 27],
                       "fraction_left": _y[3] if len(_y) > 3 else None}
        if len(_y) > 2:
            out["yoga"].update(_span_times(_y[1], _y[2]))
        # ROUND-10 (review P2-5, partially ADOPTED): nitya-yoga lord + avayogi from the
        # library's own table const.yogam_lords_and_avayogis (27 (lord_pid, avayogi_pid)
        # pairs). Library-backed — no invented mappings.
        try:
            _lp, _ap = env["const"].yogam_lords_and_avayogis[(int(_y[0]) - 1) % 27]
            out["yoga"]["lord"] = _planet_name(_lp)
            out["yoga"]["avayogi"] = _planet_name(_ap)
            out["yoga"]["lords_source"] = "const.yogam_lords_and_avayogis (PyJHora)"
        except Exception as e:
            out["yoga"]["lord"] = {"__error__": str(e)}
    else:
        out["yoga"] = _y
    _k = safe(drik.karana, jd, place)
    if _k and not isinstance(_k, dict):
        out["karana"] = {"index": _k[0], "name": _karana_name(_k[0])}
        if len(_k) > 2:
            out["karana"].update(_span_times(_k[1], _k[2]))
        try:
            _kl = env["utils"].karana_lord(int(_k[0]))
            out["karana"]["lord"] = _decode_planet_id(_kl)
            out["karana"]["lord_source"] = "utils.karana_lord (PyJHora)"
        except Exception as e:
            out["karana"]["lord"] = {"__error__": str(e)}
    else:
        out["karana"] = _k
    _w = safe(drik.vaara, jd, place)
    if _w is not None and not isinstance(_w, dict):
        out["weekday"] = {"index": _w, "name": WEEKDAY_NAMES[int(_w) % 7]}
    else:
        out["weekday"] = _w
    _n = safe(drik.nakshatra, jd, place)
    if _n and isinstance(_n, (list, tuple)):
        # drik.nakshatra returns [nak_no(1-based), pada, START_hr, END_hr, next_nak_no,
        # next_pada, next_end_hr] — verified against the source. Element [1] IS the pada;
        # [2] is the START (not the end) and [3] is the END (review §2.7). fraction_left is
        # NOT returned as a 0..1; we derive it from the Moon's position within the nakshatra.
        _frac = None
        try:
            _rc = env["charts"].rasi_chart(jd, place)
            _mlon = _planetary_longitude(_rc).get("Moon", 0)
            span = 360.0 / 27.0
            idx = int(_mlon / span) % 27
            _frac = round(max(0.0, min(1.0, 1.0 - (_mlon - idx * span) / span)), 4)
        except Exception:
            _frac = None
        out["moon_nakshatra"] = {"index": _n[0], "name": NAKSHATRAS[(int(_n[0]) - 1) % 27],
                                 "pada": _n[1] if len(_n) > 1 else None,
                                 "start_time": _hours_to_hhmm(_n[2]) if len(_n) > 2 else None,
                                 "end_time": _hours_to_hhmm(_n[3]) if len(_n) > 3 else None,
                                 "fraction_left": _frac,
                                 "next_index": _n[4] if len(_n) > 4 else None,
                                 "next_name": NAKSHATRAS[(int(_n[4]) - 1) % 27] if len(_n) > 4 else None,
                                 "next_pada": _n[5] if len(_n) > 5 else None}
        # ROUND-16: library nakshatra lord (utils.nakshathra_lord, 1-based index).
        try:
            _nl = env["utils"].nakshathra_lord(int(_n[0]))
            out["moon_nakshatra"]["library_lord"] = _decode_planet_id(_nl)
            out["moon_nakshatra"]["library_lord_source"] = "utils.nakshathra_lord (PyJHora)"
        except Exception as e:
            out["moon_nakshatra"]["library_lord"] = {"__error__": str(e)}
    else:
        out["moon_nakshatra"] = _n
    try:
        out["sunrise"] = drik.sunrise(jd, place)
    except Exception as e: out["sunrise"] = str(e)
    try:
        out["sunset"] = drik.sunset(jd, place)
    except Exception as e: out["sunset"] = str(e)
    try:
        out["day_length_hours"] = drik.day_length(jd, place)
    except Exception as e: out["day_length_hours"] = str(e)
    try:
        out["moon_speed_daily"] = drik.daily_moon_speed(jd, place)
    except Exception as e: out["moon_speed_daily"] = str(e)
    try:
        out["raahu_kaalam"] = drik.raahu_kaalam(jd, place)
    except Exception as e: out["raahu_kaalam"] = str(e)
    try:
        out["is_night_birth"] = drik.is_night_birth(jd, place)
    except Exception as e: out["is_night_birth"] = str(e)
    # ---- ROUND-8 (review P3-3): birth-panchanga essentials — Vara lord, Janma Hora
    # ---- lord and Ayana. Deterministic derivations; every convention inlined.
    _widx = None
    try:
        if isinstance(out.get("weekday"), dict):
            _widx = int(out["weekday"]["index"]) % 7
        elif _w is not None:
            _widx = int(_w) % 7
        VARA_LORDS = ["Sun", "Moon", "Mars", "Mercury", "Jupiter", "Venus", "Saturn"]
        if _widx is not None:
            out["vara_lord"] = {"weekday_index": _widx,
                                "weekday_name": WEEKDAY_NAMES[_widx],
                                "lord": VARA_LORDS[_widx]}
        else:
            out["vara_lord"] = {"__error__": "weekday not resolvable"}
    except Exception as e:
        out["vara_lord"] = {"__error__": str(e)}
    try:
        # Janma Hora lord: 24 equal horas run sunrise→sunrise; the FIRST hora of a vedic
        # day belongs to the weekday lord, subsequent horas follow the Chaldean descending
        # order (Sun→Venus→Mercury→Moon→Saturn→Jupiter→Mars→Sun…). Pre-sunrise births
        # belong to the previous vedic day.
        CHALDAEAN = ["Sun", "Venus", "Mercury", "Moon", "Saturn", "Jupiter", "Mars"]
        VARA_LORDS = ["Sun", "Moon", "Mars", "Mercury", "Jupiter", "Venus", "Saturn"]
        def _jd_of(x):
            # shape-tolerant JD extractor (5.0 sunrise returns composite structures)
            if isinstance(x, (int, float)) and not isinstance(x, bool) \
                    and 2000000 < float(x) < 3000000:
                return float(x)
            if isinstance(x, (list, tuple)):
                for it in x:
                    v = _jd_of(it)
                    if v is not None:
                        return v
            return None
        sr_jd = _jd_of(drik.sunrise(jd, place))
        w_idx, hrs = _widx, None
        if sr_jd is not None and w_idx is not None:
            hrs = (jd - sr_jd) * 24.0
            if hrs < 0:                     # birth before sunrise → previous vedic day
                w_idx = (w_idx - 1) % 7
                sr_jd = _jd_of(drik.sunrise(jd - 1, place))
                hrs = (jd - sr_jd) * 24.0 if sr_jd is not None else None
        if hrs is not None:
            k = int(hrs) % 24
            start = CHALDAEAN.index(VARA_LORDS[w_idx])
            out["janma_hora_lord"] = {
                "lord": CHALDAEAN[(start + k) % 7],
                "hora_index_of_day": k,
                "hours_since_sunrise": round(hrs, 4),
                "vedic_weekday_used": WEEKDAY_NAMES[w_idx],
                "convention": ("24 equal horas from sunrise; first hora ruled by the "
                               "weekday lord, then Chaldean descending order. Pre-sunrise "
                               "births use the previous vedic day."),
            }
        else:
            out["janma_hora_lord"] = {"__error__": "sunrise JD or weekday not resolvable"}
    except Exception as e:
        out["janma_hora_lord"] = {"__error__": str(e)}
    try:
        # Ayana (sun's half-year course). Convention: SIDEREAL/sankranti-based —
        # Uttarayana spans Makara sankranti (Sun entering Capricorn) to Karka entry.
        _sl = safe(drik.solar_longitude, jd)
        if isinstance(_sl, (int, float)) and not isinstance(_sl, bool):
            _ss = int(float(_sl) // 30) % 12
            out["ayana"] = {
                "name": "Uttarayana" if _ss in (9, 10, 11, 0, 1, 2) else "Dakshinayana",
                "sun_sign": RAASI_NAMES[_ss],
                "convention": ("sidereal/sankranti-based: Uttarayana = Sun in Capricorn.."
                               "Gemini; the tropical solstice definition differs by the "
                               "ayanamsa (~24°)")}
        else:
            out["ayana"] = {"__error__": "solar_longitude unavailable"}
    except Exception as e:
        out["ayana"] = {"__error__": str(e)}
    # Round-8 review asked for Dagdha-tithi and Tithi-Shunya-rasi checks. PyJHora 5.0 has no
    # such functions (verified by exhaustive grep), and the classical formulas vary between
    # authorities — rather than ship an unverifiable computation into a reading pipeline we
    # disclose the omission here. If a source is later validated, implement + audit it.
    out["omitted_checks"] = {
        "dagdha_yoga_tithi_x_vara": (
            "ROUND-11 (review P2-3): the dagdha RASI table (tithi -> burnt signs) is "
            "now computed — see panchanga.dagdha_rasis (both disputed source variants "
            "disclosed there). Still NOT computed: the dagdha YOGA muhurta "
            "combinations (tithi x vara and nakshatra x vara 'burnt' pairings) — no "
            "PyJHora function exists and the combination tables differ across "
            "authorities; omission disclosed per the no-invented-data policy"),
        "tithi_shunya_rasis": ("not computed: same reason — Shunya-rasi tables differ across "
                               "authorities; omission disclosed per no-invented-data policy"),
        # ROUND-10 (review P2-5): the review asked for "panchanga tattva lords" — a
        # tithi/yoga/karana → element (tattva) mapping. That mapping is a modern,
        # lineage-specific table; it is NOT in PyJHora's constants and no authoritative
        # classical source could be verified for it. What IS library-backed is exposed:
        # yoga lord + avayogi (const.yogam_lords_and_avayogis), nakshatra lord
        # (core.planets[].nakshatra_lord AND panchanga.moon_nakshatra.library_lord via
        # utils.nakshathra_lord), vara lord (panchanga.vara_lord), karana planetary
        # lord (panchanga.karana.lord via utils.karana_lord — ROUND-16). The 30-tithi
        # deity table is still absent (const.tithi_deities holds 16 Nitya Devis — a
        # different doctrine) and varies by lineage, so it is omitted rather than invented.
        "panchanga_tattva_lords": ("not computed: the tithi/yoga/karana → element (tattva) "
                                   "lord mapping is a modern lineage-specific table, absent "
                                   "from PyJHora's constants and not verifiable against an "
                                   "authoritative classical source. Library-backed lords are "
                                   "exposed instead: panchanga.yoga.lord + .avayogi, "
                                   "panchanga.vara_lord, panchanga.karana.lord "
                                   "(utils.karana_lord), panchanga.moon_nakshatra.library_lord "
                                   "(utils.nakshathra_lord), core.planets[].nakshatra_lord")}
    out["chandrabalam_thaaraabalam_note"] = (
        "ROUND-10 (review P2-4): in PyJHora 5.0 drik.chandrabalam(jd, place) and "
        "drik.thaaraabalam(jd, place, return_only_good_stars=True) take NO moon_raasi / "
        "nakshatra keyword — the proposed natal-specific call signature does not exist in "
        "this build, so it was not adopted. Both are MUHURTA helpers evaluated at the "
        "query jd (chandrabalam lists favourable ascendant windows for the CURRENT Moon's "
        "sign; thaaraabalam lists good nakshatras for that day). Natal-relative "
        "equivalents already in this document: panchanga.nava_thaara_from_moon (the "
        "9-tara cycle from the NATAL Moon nakshatra), panchanga.nava_thaara_from_lagna, "
        "and transits.moon.transit_house_from_moon (the count from the natal Moon that "
        "drives chandrabalam/tara-bala judgement in gochara).")
    try:
        out["ayanamsa"] = drik.get_ayanamsa_value(jd)
    except Exception as e: out["ayanamsa"] = str(e)
    for name in ["chandrabalam", "thaaraabalam", "durmuhurtam", "varjyam", "amrita_gadiya"]:
        fn = getattr(drik, name, None)
        if fn:
            try:
                with _quiet():
                    out[name] = fn(jd, place)
            except Exception as e:
                out[name] = str(e)
    # ---- Extended panchanga: the rest of PyJHora's daily/lunar/solar surface ----
    def ext(field, fn, *a, **k):
        if not fn:
            return
        try:
            with _quiet():
                out[field] = fn(*a, **k)
        except Exception as e:
            out[field] = str(e)
    # drik.Date struct for functions that want a Date (not a jd) — use natal date
    ddate = getattr(drik, "Date", None)
    dstr = ddate(h.Date.year, h.Date.month, h.Date.day) if ddate else None
    ext("moonrise", getattr(drik, "moonrise", None), jd, place)
    ext("moonset", getattr(drik, "moonset", None), jd, place)
    # nakshatra_pada takes a longitude — use the Moon's absolute longitude at birth
    _np = getattr(drik, "nakshatra_pada", None)
    if _np:
        try:
            with _quiet():
                _rc = env["charts"].rasi_chart(jd, place)
            mlong = _planetary_longitude(_rc).get("Moon", 0)
        except Exception:
            mlong = 0
        ext("nakshatra_pada", _np, mlong)
    ext("moon_fraction_yet_to_traverse", getattr(drik, "fraction_moon_yet_to_traverse", None), jd, place)
    ext("nakshatra_new", getattr(drik, "nakshatra_new", None), jd, place)
    # tithi / yoga / karana end + specials
    ext("tithi_inverse_lagrange", getattr(drik, "tithi_using_inverse_lagrange", None), jd, place)
    ext("tithi_planet_speed", getattr(drik, "tithi_using_planet_speed", None), jd, place)
    ext("special_tithis", getattr(drik, "special_tithis", None), jd, place)
    ext("karaka_tithi", getattr(drik, "karaka_tithi", None), jd, place)
    ext("lunar_phase", getattr(drik, "lunar_phase", None), jd)
    # PyJHora 5.0: full_moon/new_moon now TAKE the current tithi index to narrow the
    # search window — `full_moon(jd, tithi_, opt=-1)`. Pass the chart's tithi; fall back
    # to the 4.8.7 single-arg signature on TypeError.
    _tithi_idx = None
    try:
        _tithi_idx = int(out["tithi"]["index"]) if isinstance(out.get("tithi"), dict) else None
    except Exception:
        _tithi_idx = None

    def _moon_wrap(fname):
        f = getattr(drik, fname, None)
        if not f:
            return None
        def _call(j):
            try:
                return f(j, _tithi_idx if _tithi_idx is not None
                         else (15 if fname == "full_moon" else 30))
            except TypeError:
                return f(j)          # 4.8.7 signature (jd only)
        return _call
    ext("full_moon", _moon_wrap("full_moon"), jd)
    ext("new_moon", _moon_wrap("new_moon"), jd)
    # lunar / solar calendar
    ext("lunar_month", getattr(drik, "lunar_month", None), jd, place)
    ext("lunar_year", getattr(drik, "lunar_year", None), jd, place)
    ext("samvatsara", getattr(drik, "samvatsara", None), dstr, place)
    ext("vedic_date", getattr(drik, "vedic_date", None), jd, place)
    ext("lunar_month_date", getattr(drik, "lunar_month_date", None), jd, place)
    # ROUND-7 (review tail): the Purnimanta (North-Indian) lunar-month reckoning alongside
    # the default Amanta one — same function, `use_purnimanta_system=True`.
    ext("lunar_month_date_purnimanta",
        (lambda j, p: drik.lunar_month_date(j, p, use_purnimanta_system=True))
        if getattr(drik, "lunar_month_date", None) else None, jd, place)
    ext("solar_month_date", getattr(drik, "solar_month_date", None), jd, place)
    ext("tamil_solar_month_date", getattr(drik, "tamil_solar_month_and_date_new", None),
        dstr, place) if dstr else None
    ext("chandrashtama", getattr(drik, "chandrashtama", None), jd, place)
    ext("daily_sun_speed", getattr(drik, "daily_sun_speed", None), jd, place)
    ext("lunar_longitude", getattr(drik, "lunar_longitude", None), jd)
    ext("solar_longitude", getattr(drik, "solar_longitude", None), jd)

    # ---- ROUND-5: the remaining decoded drik panchanga surface ----------------
    # Every value below is DECODED to names/labels where PyJHora returns indices.
    RITU_NAMES = ["Vasanta (spring)", "Greeshma (summer)", "Varsha (monsoon)",
                  "Sharath (autumn)", "Hemantha (pre-winter)", "Shishira (winter)"]
    VIVAHA_PALAN = {1: "Collateral damage to family", 2: "Wealthy and Blessed",
                    3: "Collateral damage to girl's family", 4: "Poverty and cursed",
                    5: "Gainful and beneficial", 6: "Loss of reputation and honour",
                    7: "Devastating results for the girl", 8: "Completely successful",
                    9: "Wonderable pair and blessed"}
    TAMIL_YOGAM_NAMES = ["siddha", "prabalarishta", "marana", "amritha", "amritha_siddha",
                         "mrithyu", "daghda", "yamaghata", "utpata"]
    THAARA_NAMES = ["Janma", "Sampat", "Vipat", "Kshema", "Pratyari", "Sadhaka",
                    "Vadha", "Mitra", "Atimitra"]
    def ext2(field, fn, *a, **k):
        if not fn:
            return None
        try:
            with _quiet():
                v = fn(*a, **k)
            out[field] = v
            return v
        except Exception as e:
            out[field] = str(e)
            return None
    # maasa index needed by ritu / elapsed_year
    _maasa = None
    try:
        with _quiet():
            _lm = drik.lunar_month(jd, place)
        _maasa = int(_lm[0]) if isinstance(_lm, (list, tuple)) and _lm else None
    except Exception:
        _maasa = None
    try:
        _rt = drik.ritu(_maasa) if _maasa is not None else None
        out["ritu"] = {"index": _rt, "name": RITU_NAMES[int(_rt) % 6]} if _rt is not None else None
    except Exception as e:
        out["ritu"] = str(e)
    try:
        _ey = drik.elapsed_year(jd, _maasa) if _maasa is not None else None
        out["elapsed_year"] = ({"kali_year": _ey[0], "vikrama_year": _ey[1], "saka_year": _ey[2]}
                               if isinstance(_ey, (list, tuple)) and len(_ey) >= 3 else _ey)
    except Exception as e:
        out["elapsed_year"] = str(e)
    ext2("lunar_year_index", getattr(drik, "lunar_year_index", None), jd, _maasa)
    try:
        _tg = drik.triguna(jd, place)
        out["triguna_ghatis"] = ({"sattva": _tg[0], "rajas": _tg[1], "tamas": _tg[2]}
                                 if isinstance(_tg, (list, tuple)) and len(_tg) >= 3 else _tg)
    except Exception as e:
        out["triguna_ghatis"] = str(e)
    try:
        _vt = drik.vedic_time(jd, place)
        out["vedic_time"] = ({"ghati": _vt[0], "pala": _vt[1], "vighati": _vt[2]}
                             if isinstance(_vt, (list, tuple)) and len(_vt) >= 3 else _vt)
    except Exception as e:
        out["vedic_time"] = str(e)
    try:
        _cw = drik.civil_weekday(jd)
        out["civil_weekday"] = {"index": _cw, "name": WEEKDAY_NAMES[int(_cw) % 7]}
    except Exception as e:
        out["civil_weekday"] = str(e)
    ext2("ahargana_days", getattr(drik, "ahargana", None), jd)
    ext2("kali_ahargana_days", getattr(drik, "kali_ahargana_days", None), jd)
    try:
        _vp = drik.vivaha_chakra_palan(jd, place)
        out["vivaha_chakra_palan"] = {"index": _vp, "meaning": VIVAHA_PALAN.get(int(_vp), str(_vp))}
    except Exception as e:
        out["vivaha_chakra_palan"] = str(e)
    ext2("sahasra_chandrodayam_date", getattr(drik, "sahasra_chandrodayam", None), jd, place)
    try:
        _ty = drik.tamil_yogam(jd, place)
        if isinstance(_ty, (list, tuple)) and _ty:
            out["tamil_yogam"] = {"index": _ty[0],
                                  "name": TAMIL_YOGAM_NAMES[int(_ty[0]) % 9],
                                  "start_time": _hours_to_hhmm(_ty[1]) if len(_ty) > 1 else None,
                                  "end_time": _hours_to_hhmm(_ty[2]) if len(_ty) > 2 else None,
                                  "next_index": _ty[3] if len(_ty) > 3 else None}
        else:
            out["tamil_yogam"] = _ty
    except Exception as e:
        out["tamil_yogam"] = str(e)
    try:
        _ky = drik.karaka_yogam(jd, place)
        if isinstance(_ky, (list, tuple)) and _ky:
            out["karaka_yogam"] = {"index": _ky[0],
                                   "name": YOGA_NAMES[(int(_ky[0]) - 1) % 27],
                                   "start_time": _hours_to_hhmm(_ky[1]) if len(_ky) > 1 else None,
                                   "end_time": _hours_to_hhmm(_ky[2]) if len(_ky) > 2 else None,
                                   "fraction_left": _ky[3] if len(_ky) > 3 else None}
        else:
            out["karaka_yogam"] = _ky
    except Exception as e:
        out["karaka_yogam"] = str(e)
    # graha drekkana: per-body drekkana-lord (values are planet ids) zipped with the
    # dhasavarga entry order ('L' + Sun..Ketu).
    try:
        with _quiet():
            _gd = drik.graha_drekkana(jd, place)
            _dv10 = drik.dhasavarga(jd, place)
        _keys = [(("Lagna" if k == "L" else _decode_planet_id(k))) for k, _ in _dv10]
        out["graha_drekkana"] = {k: {"drekkana_lord": _decode_planet_id(v)}
                                 for k, v in zip(_keys, _gd)}
    except Exception as e:
        out["graha_drekkana"] = str(e)
    # declinations (Sun..Saturn)
    try:
        with _quiet():
            _dec = drik.declination_of_planets(jd, place)
        out["declination_of_planets"] = {n: round(float(v), 4)
                                         for n, v in zip(["Sun", "Moon", "Mars", "Mercury",
                                                          "Jupiter", "Venus", "Saturn"], _dec)}
    except Exception as e:
        out["declination_of_planets"] = str(e)
    ext2("midday", getattr(drik, "midday", None), jd, place)
    ext2("midnight", getattr(drik, "midnight", None), jd, place)
    ext2("night_length_hours", getattr(drik, "night_length", None), jd, place)
    try:
        _md = drik.maandi_longitude(drik.Date(h.Date.year, h.Date.month, h.Date.day),
                                    _tob(h), place)
        out["maandi"] = ({"sign_index": int(_md[0]), "sign": RAASI_NAMES[int(_md[0]) % 12],
                          "degree_in_sign": round(float(_md[1]), 4)}
                         if isinstance(_md, (list, tuple)) and len(_md) >= 2 else _md)
    except Exception as e:
        out["maandi"] = str(e)
    # sankranti boundaries around birth (Sun's sign entries) — decoded
    def _dec_sankranti(res):
        try:
            (y, mo, d, hrs), raasi, direction = res
            return {"date": _date_str(int(y), int(mo), int(d), float(hrs)),
                    "sun_enters_sign_index": int(raasi) % 12,
                    "sun_enters_sign": RAASI_NAMES[int(raasi) % 12]}
        except Exception:
            return res
    try:
        with _quiet():
            out["next_sankranti"] = _dec_sankranti(drik.next_sankranti_date_from_jd(jd, place))
    except Exception as e:
        out["next_sankranti"] = str(e)
    try:
        with _quiet():
            out["previous_sankranti"] = _dec_sankranti(drik.previous_sankranti_date_from_jd(jd, place))
    except Exception as e:
        out["previous_sankranti"] = str(e)
    # NOTE: drik.next_solar_date(jd, place, years=1) returns the ANCHOR of solar year 1
    # (= the birth date itself, PyJHora's tajaka convention), not the next solar return —
    # deliberately NOT exposed here. Real solar returns live in the `tajaka` section
    # (annual_chart.starts / years_elapsed).
    try:
        with _quiet():
            _nlm = drik.next_lunar_month(jd, place)
            _plm = drik.previous_lunar_month(jd, place)
        out["next_lunar_month_start"] = (_date_str(int(_nlm[0]), int(_nlm[1]), int(_nlm[2]), float(_nlm[3]))
                                         if isinstance(_nlm, (list, tuple)) and len(_nlm) >= 4 else _nlm)
        out["previous_lunar_month_start"] = (_date_str(int(_plm[0]), int(_plm[1]), int(_plm[2]), float(_plm[3]))
                                             if isinstance(_plm, (list, tuple)) and len(_plm) >= 4 else _plm)
    except Exception as e:
        out["next_lunar_month_start"] = str(e)
    # astronomical/vedic year durations (days)
    try:
        dur = {}
        for lab, fn, kw in (("true_tropical_year", drik.true_tropical_year, {}),
                            ("true_sidereal_year", drik.true_sidereal_year, {}),
                            ("tithi_year", drik.tithi_year, {}),
                            ("savana_year", drik.savana_year, {}),
                            ("true_tithi_year", getattr(drik, "true_tithi_year", None), {}),
                            ("true_lunar_year", drik.true_lunar_year, {}),
                            ("dhasa_year_duration", getattr(drik, "dhasa_year_duration", None), {})):
            if not fn:
                continue
            try:
                with _quiet():
                    dur[lab] = fn(jd, place, **kw) if lab != "dhasa_year_duration" else fn()
            except Exception as e2:
                dur[lab] = str(e2)
        out["year_durations_days"] = dur
    except Exception as e:
        out["year_durations_days"] = str(e)
    # nava thaara / special thaara (decode: thaara name by group position + lord planet +
    # nakshatra names). from_lagna_or_moon: None=lagna, 1=moon.
    def _dec_nava(res):
        dec = []
        try:
            for i, (lord, stars) in enumerate(res):
                dec.append({"thaara": THAARA_NAMES[i % 9],
                            "nakshatra_lord": _decode_planet_id(lord),
                            "nakshatras": [NAKSHATRAS[(int(s) - 1) % 27] for s in stars]})
        except Exception:
            return res
        return dec
    try:
        with _quiet():
            out["nava_thaara_from_lagna"] = _dec_nava(drik.nava_thaara(jd, place))
        with _quiet():
            out["nava_thaara_from_moon"] = _dec_nava(drik.nava_thaara(jd, place, 1))
    except Exception as e:
        out["nava_thaara_from_lagna"] = str(e)
    def _dec_special_thaara(res):
        # The library's star list uses TAMIL nakshatra names and includes Abhijit as the
        # 21st star (0-based index 20, between Purva Ashadha and Uttara Ashadha —
        # VERIFIED against utils.get_nakshathra_list_with_abhijith() in this build).
        # Map positions to the engine's standard NAKSHATRAS names so every nakshatra
        # reference in this JSON uses one vocabulary.
        try:
            _stars = env["utils"].get_nakshathra_list_with_abhijith()
            _std = {}
            if isinstance(_stars, (list, tuple)) and len(_stars) == 28:
                # Abhijit is the 21st star = 0-based index 20 (verified against the
                # library list: ... Pooraadam(19), ' Abhijith'(20), Uthiraadam(21) ...)
                for i in range(28):
                    if i < 20:
                        _std[i] = NAKSHATRAS[i]
                    elif i == 20:
                        _std[i] = "Abhijit"
                    else:
                        _std[i] = NAKSHATRAS[i - 1]
            else:
                _std = None
            return [{"lord": _decode_planet_id(l),
                     "nakshatra": (_std.get(int(s), str(s)) if _std else int(s)),
                     "star_index_with_abhijit": int(s)}
                    for l, s in res]
        except Exception:
            return res
    try:
        with _quiet():
            out["special_thaara_from_moon"] = _dec_special_thaara(drik.special_thaara(jd, place, 1))
    except Exception as e:
        out["special_thaara_from_moon"] = str(e)
    # ROUND-7 / PyJHora 5.0: special_thaara's 3rd arg is now `base_star_planet`
    # ('L' or planet id 0..8) and DEFAULTS TO LAGNA — the lagna-based thaaras were
    # previously unreachable. Upstream issue #57 (inconsistent Lagna offsets + Abhijit
    # ordering in 4.8.7) was fixed in the 5.0 dev line; we expose BOTH bases now.
    try:
        with _quiet():
            out["special_thaara_from_lagna"] = _dec_special_thaara(drik.special_thaara(jd, place))
    except Exception as e:
        out["special_thaara_from_lagna"] = str(e)
    ext2("ascendant_speed_deg_per_day", getattr(drik, "ascendant_speed", None), jd, place)
    try:
        with _quiet():
            _ap = drik.ardra_pravesha_date(jd, place)
        out["ardra_pravesha_date"] = (_jd_to_ymd(_ap[0]) if isinstance(_ap, (list, tuple)) and _ap
                                      else _ap)
    except Exception as e:
        out["ardra_pravesha_date"] = str(e)

    # ---- ROUND-15: decode leftover RAW panchanga so tithi-related fields are never empty ----
    # These were previously dumped as index arrays; AIs treated them as blank.
    def _dec_tithi_row(row):
        if not isinstance(row, (list, tuple)) or not row:
            return row
        try:
            nm, pk = _tithi_name(row[0])
            d = {"index": row[0], "name": nm, "paksha": pk}
            if len(row) > 2:
                d.update(_span_times(row[1], row[2]))
            if len(row) > 3:
                try:
                    ni = int(row[3]); nn, npk = _tithi_name(ni)
                    d["next_index"] = ni; d["next_name"] = nn; d["next_paksha"] = npk
                except Exception:
                    pass
            return d
        except Exception:
            return {"raw": list(row)}

    try:
        _st = out.get("special_tithis")
        if isinstance(_st, (list, tuple)) and _st and isinstance(_st[0], (list, tuple)):
            cycles = []
            for ci, cyc in enumerate(_st):
                rows = []
                for ti, row in enumerate(cyc or []):
                    lab = TITHI_HOUSE_LABELS[ti] if ti < 12 else ("tithi_index_%d" % (ti + 1))
                    rec = _dec_tithi_row(row)
                    if isinstance(rec, dict):
                        rec["house_theme"] = lab
                    rows.append(rec)
                cycles.append({"cycle": ci + 1, "tithis": rows})
            out["special_tithis_raw"] = _st
            out["special_tithis"] = {
                "cycles": cycles,
                "__note__": ("ROUND-15: drik.special_tithis = 3 cycles x 12 tithi-indices "
                             "(Janma..Vyaya). Previously a nested numeric array that looked empty "
                             "to readers. Raw array kept as special_tithis_raw.")}
    except Exception as e:
        out["special_tithis_decode_error"] = str(e)
    try:
        _kt = out.get("karaka_tithi")
        if isinstance(_kt, (list, tuple)) and _kt:
            out["karaka_tithi_raw"] = _kt
            rec = _dec_tithi_row(_kt)
            if isinstance(rec, dict):
                rec["__note__"] = ("ROUND-15: tithi formed between Atmakaraka and Amatyakaraka "
                                   "(drik.karaka_tithi). Decoded from the raw [index, start, end, ...].")
            out["karaka_tithi"] = rec
    except Exception as e:
        out["karaka_tithi"] = {"__error__": str(e), "raw": out.get("karaka_tithi")}
    try:
        _cb = out.get("chandrabalam")
        if isinstance(_cb, (list, tuple)) and _cb and all(isinstance(x, (int, float)) for x in _cb):
            out["chandrabalam_raw"] = list(_cb)
            names = []
            for x in _cb:
                i = int(x)
                # library returns 1-based rasi ids from udhaya_lagna_muhurtha
                if 1 <= i <= 12:
                    names.append(RAASI_NAMES[i - 1])
                elif 0 <= i <= 11:
                    names.append(RAASI_NAMES[i])
            out["chandrabalam"] = {
                "favourable_rising_signs": names,
                "raw_indices": list(_cb),
                "__note__": ("MUHURTA helper: rising signs whose count from TODAY's Moon is in "
                             "{1,3,6,7,10}. Not the native's natal Chandra-bala.")}
    except Exception as e:
        out["chandrabalam_decode_error"] = str(e)
    try:
        _tb = out.get("thaaraabalam")
        if isinstance(_tb, (list, tuple)) and _tb and all(isinstance(x, (int, float)) for x in _tb):
            out["thaaraabalam_raw"] = list(_tb)
            names = []
            for x in _tb:
                i = int(x)
                if 1 <= i <= 27:
                    names.append(NAKSHATRAS[i - 1])
            out["thaaraabalam"] = {
                "favourable_birth_stars_today": names,
                "raw_indices": list(_tb),
                "__note__": ("MUHURTA helper: natal-stars (1..27) that have a good tara vs TODAY's "
                             "Moon star (Paramitra/Sampatha/Kshema/Sadhana/Mitra). Not natal tara-bala.")}
    except Exception as e:
        out["thaaraabalam_decode_error"] = str(e)
    try:
        _lm = out.get("lunar_month")
        if isinstance(_lm, (list, tuple)) and _lm:
            mi = int(_lm[0])
            # library: 1=Chaitra..12=Phalguna; (solar+1)%12 can yield 0 = Phalguna
            if mi == 0:
                mi = 12
            out["lunar_month_raw"] = list(_lm)
            out["lunar_month"] = {
                "index": mi,
                "name": LUNAR_MONTH_NAMES[(mi - 1) % 12],
                "is_adhika_leap_month": bool(_lm[1]) if len(_lm) > 1 else None,
                "is_nija_month": bool(_lm[2]) if len(_lm) > 2 else None,
                "reckoning": "Amanta (new-moon to new-moon); 1=Chaitra"}
    except Exception as e:
        out["lunar_month_decode_error"] = str(e)
    try:
        _sv = out.get("samvatsara")
        if isinstance(_sv, (int, float)) and not isinstance(_sv, bool):
            i = int(_sv) % 60
            out["samvatsara"] = {"index": i, "name": SAMVATSARA_NAMES[i],
                                 "cycle": "60-year Shaka samvatsara (0=Prabhava)"}
    except Exception as e:
        out["samvatsara_decode_error"] = str(e)
    try:
        _vd = out.get("vedic_date")
        if isinstance(_vd, (list, tuple)) and len(_vd) >= 3:
            mi = int(_vd[0]) or 12
            ti = int(_vd[1])
            tn, tp = _tithi_name(ti)
            out["vedic_date_raw"] = list(_vd)
            out["vedic_date"] = {
                "lunar_month_index": mi,
                "lunar_month_name": LUNAR_MONTH_NAMES[(mi - 1) % 12],
                "tithi_index": ti, "tithi_name": tn, "tithi_paksha": tp,
                "samvatsara_index": int(_vd[2]) if len(_vd) > 2 else None,
                "samvatsara_name": (SAMVATSARA_NAMES[int(_vd[2]) % 60]
                                    if len(_vd) > 2 and isinstance(_vd[2], (int, float)) else None),
                "flags": list(_vd[3:]) if len(_vd) > 3 else []}
    except Exception as e:
        out["vedic_date_decode_error"] = str(e)
    try:
        _lmd = out.get("lunar_month_date")
        if isinstance(_lmd, (list, tuple)) and len(_lmd) >= 2:
            mi = int(_lmd[0]) or 12
            ti = int(_lmd[1])
            tn, tp = _tithi_name(ti)
            out["lunar_month_date_raw"] = list(_lmd)
            out["lunar_month_date"] = {
                "lunar_month_index": mi,
                "lunar_month_name": LUNAR_MONTH_NAMES[(mi - 1) % 12],
                "tithi_index": ti, "tithi_name": tn, "tithi_paksha": tp,
                "extra": list(_lmd[2:])}
    except Exception as e:
        out["lunar_month_date_decode_error"] = str(e)
    for _tf in ("tithi_inverse_lagrange", "tithi_planet_speed"):
        try:
            _tv = out.get(_tf)
            if isinstance(_tv, (list, tuple)) and _tv and not isinstance(_tv[0], dict):
                out[_tf + "_raw"] = list(_tv)
                out[_tf] = _dec_tithi_row(_tv)
        except Exception:
            pass

    # ---- ROUND-16: remaining drik/charts panchanga surface (no UI, no aliases) ----
    # vedic weekday = sunrise-to-sunrise (same basis as drik.vaara); civil = midnight clock.
    try:
        _vw = drik.vedic_weekday(jd, place)
        out["vedic_weekday"] = {"index": int(_vw), "name": WEEKDAY_NAMES[int(_vw) % 7],
                                "convention": "sunrise-to-sunrise (same basis as vaara / weekday)"}
    except Exception as e:
        out["vedic_weekday"] = {"__error__": str(e)}
    try:
        _vt60 = drik.vedic_time_equal_day_night_ghati(jd, place)
        out["vedic_time_equal_day_night"] = {
            "ghati_60": ({"ghati": _vt60[0], "pala": _vt60[1], "vighati": _vt60[2]}
                         if isinstance(_vt60, (list, tuple)) and len(_vt60) >= 3 else _vt60),
            "note": ("Equal day+night ghatis (60 per sunrise-to-sunrise). Distinct from "
                     "vedic_time which uses true day/night length.")}
        try:
            _vt30 = drik.vedic_time_equal_day_night_ghati(jd, place, vedic_hours_per_day=30)
            out["vedic_time_equal_day_night"]["muhurta_30"] = (
                {"muhurta": _vt30[0], "pala": _vt30[1], "vighati": _vt30[2]}
                if isinstance(_vt30, (list, tuple)) and len(_vt30) >= 3 else _vt30)
        except TypeError:
            pass
    except Exception as e:
        out["vedic_time_equal_day_night"] = {"__error__": str(e)}
    try:
        if dstr is not None:
            out["days_in_tamil_month"] = int(drik.days_in_tamil_month(dstr, place))
    except Exception as e:
        out["days_in_tamil_month"] = {"__error__": str(e)}
    try:
        _spd = {}
        for _i, _nm in enumerate(PLANET_NAMES):
            with _quiet():
                _v = float(drik.daily_planet_speed(jd, place, _i))
            _spd[_nm] = {"deg_per_day": round(_v, 6),
                         "motion": ("retrograde" if _v < 0
                                    else ("stationary" if abs(_v) < 1e-4 else "direct"))}
        out["daily_planet_speed"] = {
            "by_planet": _spd,
            "__note__": ("drik.daily_planet_speed for all 9 grahas (deg/day, signed). "
                         "moon_speed_daily / daily_sun_speed remain the dedicated helpers; "
                         "this table is the general API. Negative = retrograde.")}
    except Exception as e:
        out["daily_planet_speed"] = {"__error__": str(e)}
    try:
        with _quiet():
            out["next_lunar_year"] = _decode_when(drik.next_lunar_year(jd, place))
            out["previous_lunar_year"] = _decode_when(drik.previous_lunar_year(jd, place))
        out["next_lunar_year_note"] = (
            "Next/previous lunar-year start (Amanta by default). Distinct from "
            "next_lunar_month_start.")
    except Exception as e:
        out["next_lunar_year"] = {"__error__": str(e)}
    try:
        with _quiet():
            out["next_solar_year"] = _decode_when(drik.next_solar_year(jd, place))
            out["previous_solar_year"] = _decode_when(drik.previous_solar_year(jd, place))
        out["next_solar_year_note"] = (
            "Library: next solar YEAR = next Mesha sankranti (Sun enters Aries), "
            "NOT the natal solar return. Tajaka annual_chart is the birthday return.")
    except Exception as e:
        out["next_solar_year"] = {"__error__": str(e)}
    try:
        with _quiet():
            out["next_solar_month"] = _decode_when(drik.next_solar_month(jd, place))
            out["previous_solar_month"] = _decode_when(drik.previous_solar_month(jd, place))
        out["next_solar_month_note"] = (
            "Next/previous solar-month boundary (Sun's sign entry). Related to "
            "next_sankranti but this is the library's dedicated solar-month API.")
    except Exception as e:
        out["next_solar_month"] = {"__error__": str(e)}
    try:
        _ti16 = int(out["tithi"]["index"]) if isinstance(out.get("tithi"), dict) else None
        if _ti16:
            with _quiet():
                _nt = drik.next_tithi(jd, place, _ti16, opt=1)
            _dec_nt = _decode_when(_nt)
            if isinstance(_dec_nt, dict):
                _dec_nt["requested_tithi_index"] = _ti16
                _dec_nt["experimental"] = True
                _dec_nt["__note__"] = (
                    "drik.next_tithi is labelled TODO: UNDER EXPERIMENTATION upstream. "
                    "Returns the JD of the next occurrence of this tithi index. "
                    "Corroborate against tithi.next_start_time; do not treat as gospel.")
            out["next_same_tithi"] = _dec_nt
    except Exception as e:
        out["next_same_tithi"] = {"__error__": str(e), "experimental": True}
    # nava/special thaara FROM EVERY PLANET (charts.*_for_all_planets) — natal Moon/Lagna
    # cycles already live in nava_thaara_from_*.
    try:
        with _quiet():
            _pp16 = env["charts"].rasi_chart(jd, place)
        def _dec_nava_0(res):
            dec = []
            try:
                for i, (lord, stars) in enumerate(res):
                    dec.append({"thaara": THAARA_NAMES[i % 9],
                                "nakshatra_lord": _decode_planet_id(lord),
                                "nakshatras": [NAKSHATRAS[int(s) % 27] for s in stars]})
            except Exception:
                return res
            return dec
        _nta = env["charts"].nava_thaara_for_all_planets(_pp16)
        _nta_d = {}
        for _k, _g in (_nta or {}).items():
            _nm = "Lagna" if _k in ("L", "Lagna") else _decode_planet_id(_k)
            _nta_d[_nm] = _dec_nava_0(_g)
        out["nava_thaara_for_all_planets"] = _nta_d
    except Exception as e:
        out["nava_thaara_for_all_planets"] = {"__error__": str(e)}
    try:
        with _quiet():
            _pp16b = env["charts"].rasi_chart(jd, place)
        _sta = env["charts"].special_thaara_for_all_planets(_pp16b)
        _sta_d = {}
        for _k, _pairs in (_sta or {}).items():
            _nm = "Lagna" if _k in ("L", "Lagna") else _decode_planet_id(_k)
            rows = []
            for _pair in _pairs or []:
                try:
                    _lord, _star = _pair[0], int(_pair[1])
                    rows.append({"lord": _decode_planet_id(_lord),
                                 "nakshatra": NAKSHATRAS[_star % 27],
                                 "star_index_0based": _star})
                except Exception:
                    rows.append(_pair)
            _sta_d[_nm] = rows
        out["special_thaara_for_all_planets"] = _sta_d
    except Exception as e:
        out["special_thaara_for_all_planets"] = {"__error__": str(e)}
    # next nakshatra-entry of each graha (experimental for retrograde bodies — the
    # library search can return a PAST jd).
    try:
        with _quiet():
            _pp16c = env["charts"].rasi_chart(jd, place)
        _npr = {}
        for _item in _pp16c or []:
            _pid = _item[0]
            if _pid == "L":
                continue
            try:
                _pid_i = int(_pid)
                _lon = float(_item[1][0]) * 30.0 + float(_item[1][1])
                _cur0 = int(_lon / (360.0 / 27.0)) % 27
                _next1 = (_cur0 + 1) % 27 + 1   # 1-based next nakshatra
                with _quiet():
                    _r = drik.next_planet_nakshathra_pravesha_date(
                        _pid_i, _next1, jd, place)
                _dec = _decode_when(_r)
                if isinstance(_dec, dict):
                    _dec["planet"] = PLANET_NAMES[_pid_i] if 0 <= _pid_i <= 8 else _pid_i
                    _dec["current_nakshatra"] = NAKSHATRAS[_cur0]
                    _dec["target_nakshatra"] = NAKSHATRAS[(_next1 - 1) % 27]
                    _dec["target_index_1based"] = _next1
                    _jdn = _dec.get("jd")
                    if isinstance(_jdn, (int, float)) and _jdn < jd:
                        _dec["unreliable_past_date"] = True
                        _dec["__caution__"] = (
                            "Search returned a JD before birth — typical for retrograde "
                            "grahas. Do not quote as a future ingress.")
                _npr[PLANET_NAMES[_pid_i] if 0 <= _pid_i <= 8 else str(_pid)] = _dec
            except Exception as e:
                _npr[str(_pid)] = {"__error__": str(e)}
        out["next_planet_nakshatra_pravesha"] = {
            "by_planet": _npr,
            "__note__": ("drik.next_planet_nakshathra_pravesha_date: next time each graha "
                         "enters the nakshatra AFTER its natal one. Retrograde bodies often "
                         "yield a past JD (flagged unreliable_past_date).")}
    except Exception as e:
        out["next_planet_nakshatra_pravesha"] = {"__error__": str(e)}
    # ---- ROUND-17 leftover drik calendar/corroboration ----
    try:
        with _quiet():
            _tfj = drik.tamil_solar_month_and_date_from_jd(jd, place)
        if isinstance(_tfj, (list, tuple)) and len(_tfj) >= 2:
            out["tamil_solar_month_date_from_jd"] = {
                "month_index": int(_tfj[0]), "day": int(_tfj[1]),
                "source": "drik.tamil_solar_month_and_date_from_jd"}
        else:
            out["tamil_solar_month_date_from_jd"] = _tfj
    except Exception as e:
        out["tamil_solar_month_date_from_jd"] = {"__error__": str(e)}
    try:
        ppl = _planetary_longitude(env["charts"].rasi_chart(jd, place))
        with _quiet():
            _sr = drik.sree_lagna_from_moon_asc_longitudes(ppl["Moon"], ppl[LAGNA])
        _srd = _decode_special(_sr) if _sr is not None else None
        if isinstance(_srd, dict):
            _srd["source"] = ("drik.sree_lagna_from_moon_asc_longitudes — "
                              "corroborates special.sree_lagna")
        out["sree_lagna_from_moon_asc_longitudes"] = _srd
    except Exception as e:
        out["sree_lagna_from_moon_asc_longitudes"] = {"__error__": str(e)}
    try:
        with _quiet():
            _nte = env["charts"].nava_thaara_for_chart_element(jd, place, 9)
        dec = []
        for i, (lord, stars) in enumerate(_nte or []):
            dec.append({"thaara": THAARA_NAMES[i % 9],
                        "nakshatra_lord": _decode_planet_id(lord),
                        "nakshatras": [NAKSHATRAS[int(s) % 27] for s in stars]})
        out["nava_thaara_for_D9_chart"] = {
            "groups": dec,
            "__note__": "charts.nava_thaara_for_chart_element(factor=9) — 9-star cycle in the D9 frame."}
    except Exception as e:
        out["nava_thaara_for_D9_chart"] = {"__error__": str(e)}
    try:
        with _quiet():
            _ste = env["charts"].special_thaara_for_chart_element(jd, place, 9)
        rows = []
        for _pair in _ste or []:
            try:
                _lord, _star = _pair[0], int(_pair[1])
                rows.append({"lord": _decode_planet_id(_lord),
                             "nakshatra": NAKSHATRAS[_star % 27],
                             "star_index_0based": _star})
            except Exception:
                rows.append(_pair)
        out["special_thaara_for_D9_chart"] = {
            "stars": rows,
            "__note__": "ROUND-18: charts.special_thaara_for_chart_element(factor=9)."}
    except Exception as e:
        out["special_thaara_for_D9_chart"] = {"__error__": str(e)}
    return out


def _motion_word(v):
    """drik.get_planet_speed_sign / const.ret_stat_symbols glyph -> plain word.

    VERIFIED in this build: ret_stat_symbols = {-1: '℞' (retrograde), 0: 'Ꞩ' (stationary),
    1: '' (direct)}; older docs/paths use 'R'/'S'/''. Map ALL of them so consumers never
    see raw glyphs."""
    s = str(v)
    if s in ("", "None"):
        return "direct"
    if s in ("℞", "R"):
        return "retrograde"
    if s in ("Ꞩ", "S"):
        return "stationary"
    return s


def _decode_planet_id(v):
    """A PyJHora planet index (Sun=0..Ketu=8) -> name; returns the original if not a valid id.

    Range-checked (no modulo): an out-of-range value is returned as-is rather than silently
    masked to a valid planet (review §2.4).
    """
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)) and int(v) == v and 0 <= int(v) <= 8:
        return PLANET_NAMES[int(v)]
    return v


def _decode_id_list(value):
    """Recursively replace planet indices (0..8) with names in a list/tuple.

    Range-checked: values outside 0..8 are left untouched (multi-digit ids such as sign
    indices or karana numbers are NOT misread as planet ids).
    """
    if isinstance(value, list):
        return [_decode_id_list(x) for x in value]
    if isinstance(value, tuple):
        return tuple(_decode_id_list(x) for x in value)
    if isinstance(value, (int, float)) and not isinstance(value, bool) and int(value) == value and 0 <= int(value) <= 8:
        return PLANET_NAMES[int(value)]
    return value


def build_planet_flags(env, h, jd, place, planet_positions):
    """Retrograde, combustion, marana karaka, mrityu bhaga, pushkara (names decoded).

    Retrograde MUST come from `drik.planets_in_retrograde(jd, place)` — PyJHora moved this
    computation to the drik layer, and the chart-layer `charts.planets_in_retrograde` is
    STALE: on a chart with retrogrades it returns [] while drik returns the correct planets
    (verified: 2025-03-01 -> drik [7,8] Saturn+Rahu vs charts []). We use drik first, falling
    back to the chart API only for older PyJHora builds that lack the drik function.
    """
    drik = env["drik"]
    charts = env["charts"]
    out = {}
    try:
        with _quiet():
            _r = drik.planets_in_retrograde(jd, place)
        out["retrograde"] = _decode_id_list(_r)
    except Exception:
        try: out["retrograde"] = _decode_id_list(charts.planets_in_retrograde(planet_positions))
        except Exception as e2: out["retrograde"] = str(e2)
    try: out["combust"] = _decode_id_list(charts.planets_in_combustion(planet_positions))
    except Exception as e: out["combust"] = str(e)
    # ---- ROUND-11 (review P2-1): combustion under STANDARD BPHS orbs + cazimi ----
    # `combust` above (and core.planets[].is_combust) remain the LIBRARY's own
    # membership. charts.planets_in_combustion has two verified upstream quirks
    # (disclosed, not silently refixed): (1) it indexes const.combustion_range
    # ([12,17,14,10,11,15], commented "moon,mars,mercury,jupiter,venus,saturn") at
    # planet_id-2, so every planet receives the PREVIOUS planet's orb (Mercury gets
    # Mars's 17 deg instead of its own 14/12R); (2) its retrograde test reads the
    # STALE chart-layer planets_in_retrograde ([] on 5.0 charts that DO have
    # retrogrades), so reduced retrograde orbs never apply. Empirical consequence on
    # the sample chart (Mercury 16.07 deg from the Sun, retrograde): the library
    # judges it combust via the wrong 17 deg orb; under the correct retrograde orb
    # (12 deg) it is NOT combust. combustion_detail recomputes every body under
    # standard doctrine (minimal arc, wrap-aware; retrograde from the drik-based list
    # above) so both verdicts — and any divergence — are visible to the reader.
    try:
        _ppl11 = _planetary_longitude(planet_positions)
        _sun11 = _ppl11.get("Sun")
        _ret11 = {str(x) for x in out["retrograde"]} \
            if isinstance(out.get("retrograde"), (list, tuple)) else set()
        _lib11 = {str(x) for x in out["combust"]} \
            if isinstance(out.get("combust"), (list, tuple)) else set()
        _cd11 = {}
        for _pn11 in ("Moon", "Mars", "Mercury", "Jupiter", "Venus", "Saturn"):
            _l11 = _ppl11.get(_pn11)
            if _l11 is None or _sun11 is None:
                continue
            _dd11 = abs(_l11 - _sun11) % 360.0
            _dist11 = round(min(_dd11, 360.0 - _dd11), 4)
            _r11 = _pn11 in _ret11
            _orb11 = (STD_COMBUSTION_ORBS_RETRO[_pn11]
                      if (_r11 and _pn11 in STD_COMBUSTION_ORBS_RETRO)
                      else STD_COMBUSTION_ORBS[_pn11])
            _std11 = bool(_dist11 <= _orb11)
            _cd11[_pn11] = {
                "angular_distance_from_sun": _dist11,
                "is_retrograde": _r11,
                "standard_orb_deg": _orb11,
                "orb_variant": ("retrograde-reduced"
                                if (_r11 and _pn11 in STD_COMBUSTION_ORBS_RETRO)
                                else "standard"),
                "combust_per_standard_orb": _std11,
                "cazimi_within_17_arcmin": bool(_dist11 <= CAZIMI_MAX_DEG),
                "library_is_combust": _pn11 in _lib11,
                "library_vs_standard_mismatch": bool((_pn11 in _lib11) != _std11),
            }
        for _nx in ("Sun", "Rahu", "Ketu"):
            _cd11[_nx] = {"evaluated": False,
                          "note": ("the Sun is the combustion reference body"
                                   if _nx == "Sun" else
                                   "the nodes are not classically evaluated for combustion")}
        _cd11["__note__"] = (
            "ROUND-11 (review P2-1): standard BPHS combustion orbs — Moon 12, Mars 17, "
            "Mercury 14 (retrograde 12), Jupiter 11, Venus 10 (retrograde 8), Saturn "
            "15 degrees from the Sun, measured as the minimal arc (wrap-aware); cazimi "
            "= within 0 deg 17 min (0.2833), the heart zone where a combust planet is "
            "read as fully empowered rather than weakened. planet_flags.combust and "
            "core.planets[].is_combust remain the LIBRARY verdict "
            "(charts.planets_in_combustion: mis-indexed orb table at planet_id-2 + "
            "stale chart-layer retrograde check — upstream quirk disclosed, not "
            "refixed); library_vs_standard_mismatch marks bodies where the two "
            "verdicts differ. Worked example (sample chart): retrograde Mercury at "
            "16.07 deg — library 'combust' (using Mars's 17 deg orb), standard NOT "
            "combust (Mercury retrograde orb is 12 deg). Prefer "
            "combust_per_standard_orb for readings; cite both verdicts when they "
            "diverge.")
        out["combustion_detail"] = _cd11
    except Exception as e:
        out["combustion_detail"] = {"__error__": str(e)}
    out["retrograde_source_api"] = "drik.planets_in_retrograde(jd, place)"
    try:
        with _quiet():
            # returns [(planet_id, house), ...] -> decode planet id
            mks = charts.get_planets_in_marana_karaka_sthana(planet_positions)
        out["marana_karaka_sthana"] = [
            {"planet": _decode_planet_id(p), "house": hh} for p, hh in mks
        ] if isinstance(mks, (list, tuple)) else mks
    except Exception as e: out["marana_karaka_sthana"] = str(e)
    try:
        with _quiet():
            mb = charts.planets_in_mrityu_bhaga(h.Date, h.birth_time, h.Place, planet_positions)
        # mb is a list of (planet_or_lagna, rasi_index, diff_longitude) — decode ONLY the
        # planet (id or 'L'->Lagna) and the rasi index (-> sign name); do NOT touch the diff.
        out["mrityu_bhaga"] = [_decode_mrityu_bhaga_row(r) for r in mb] \
            if isinstance(mb, (list, tuple)) else _decode_id_list(mb)
    except Exception as e: out["mrityu_bhaga"] = str(e)
    try:
        # [[planet_ids], [planet_ids]] (in navamsa+something) -> decode each
        pk = charts.planets_in_pushkara_navamsa_bhaga(planet_positions)
        if isinstance(pk, (list, tuple)):
            out["pushkara_navamsa"] = [_decode_id_list(group) for group in pk]
        else:
            out["pushkara_navamsa"] = pk
    except Exception as e: out["pushkara_navamsa"] = str(e)
    # ---- ROUND-5 additions ---------------------------------------------------
    # Graha Yudh (planetary war): [(p1_id, p2_id, category)] with the 4 classical
    # yudh categories decoded (source: drik.planets_in_graha_yudh docstring).
    _YUDH = {0: "Bhed-yuti (identical longitude+latitude; one planet covers the other)",
             1: "Ullekh-yuti (equal longitudes; latitudes 15-20' apart, same direction)",
             2: "Apsavya-yuti (equal longitudes; latitudes within 1 degree)",
             3: "Anshumard-yuti (equal longitudes; latitude difference < 2 degrees)"}
    try:
        with _quiet():
            gy = drik.planets_in_graha_yudh(jd, place)
        out["graha_yudh"] = [
            {"planets": [_decode_planet_id(p1), _decode_planet_id(p2)],
             "category_index": int(c), "category": _YUDH.get(int(c), str(c))}
            for p1, p2, c in gy
        ] if isinstance(gy, (list, tuple)) else gy
        out["graha_yudh_note"] = ("Empty list = no planetary war at birth. A graha-yudh "
                                  "pair mutually afflicts both planets' significations.")
    except Exception as e:
        out["graha_yudh"] = str(e)
    # Per-planet motion state at birth via drik.get_planet_speed_sign (takes a planet ID).
    # This build returns GLYPHS ('℞' retrograde / 'Ꞩ' stationary / '' direct) — _motion_word
    # normalizes glyphs AND the old 'R'/'S' spelling to plain words.
    try:
        speed_sign = {}
        for p in PLANET_ORDER:
            pid = _planet_id(p)
            try:
                with _quiet():
                    v = drik.get_planet_speed_sign(jd, place, pid)
                speed_sign[p] = _motion_word(v)
            except Exception:
                speed_sign[p] = "__error__"
        out["speed_sign_at_birth"] = speed_sign
    except Exception as e:
        out["speed_sign_at_birth"] = str(e)
    # ROUND-8 (review P3-2): planetary AVASTHAS. PyJHora 5.0 has NO avastha functions
    # (verified absent), so both classical sets are computed here — deterministic
    # arithmetic with the conventions inlined (same policy as core's dignity field):
    # • Baaladi (degree states, 6° bands): odd signs run Bala→Kumara→Yuva→Vriddha→Mrita
    #   with degree; even signs run the REVERSE order (Mrita 0-6° … Bala 24-30°).
    #   Rahu/Ketu are treated as odd-sign (conventional).
    # • Jagradadi (alertness): Jagrita = own/exalted sign; Sushupti = debilitated, or
    #   enemy sign per the compound relationship to the sign lord (the library's own
    #   9x9 matrix, codes 0..4 = Adhishatru..Adhimitra); otherwise Swapna.
    try:
        ppl = _planetary_longitude(planet_positions)
        BAALADI_ODD = ["Bala", "Kumara", "Yuva", "Vriddha", "Mrita"]
        BAALADI_EVEN = ["Mrita", "Vriddha", "Yuva", "Kumara", "Bala"]
        BAALADI_RESULTS = {"Bala": 25.0, "Kumara": 50.0, "Yuva": 100.0,
                           "Vriddha": 12.5, "Mrita": 0.0}
        house = env["house"]; utils = env["utils"]
        crm = None
        try:
            h2p = utils.get_house_planet_list_from_planet_positions(planet_positions)
            crm = house._get_compound_relationships_of_planets(h2p)
            if not (isinstance(crm, (list, tuple)) and crm and
                    isinstance(crm[0], (list, tuple))):
                crm = None
        except Exception:
            crm = None
        av = {}
        for pname in PLANET_ORDER:
            lon = ppl[pname]
            sg = _sign_of_long(lon); dg = _deg_in_sign(lon)
            odd_sign = (sg % 2 == 0)          # Aries = sign #1 = odd (index 0)
            seg = min(4, max(0, int(dg // 6.0)))
            state = (BAALADI_ODD if odd_sign else BAALADI_EVEN)[seg]
            dig = _dignity(pname, sg)
            if dig in ("Exalted", "Own_sign"):
                jag, jag_why = "Jagrita", dig
            elif dig == "Debilitated":
                jag, jag_why = "Sushupti", dig
            else:
                jag, jag_why = "Swapna", "In_other_sign (default)"
                if crm is not None:
                    try:
                        pid = _planet_id(pname)
                        lid = _planet_id(SIGN_LORDS[sg])
                        code = int(crm[pid][lid])
                        if code <= 1:      # Shatru / Adhishatru vs the sign lord
                            jag, jag_why = "Sushupti", "enemy of sign lord (compound %d)" % code
                        else:
                            jag_why = "friend/neutral of sign lord (compound %d)" % code
                    except Exception:
                        pass
            av[pname] = {
                "baaladi_avastha": state,
                "baaladi_result_percent_conventional": BAALADI_RESULTS[state],
                "sign_parity": "odd" if odd_sign else "even",
                "jagradadi_avastha": jag,
                "jagradadi_basis": jag_why,
            }
        av["__note__"] = (
            "Baaladi: 6-degree bands; odd signs Bala(0-6)→Mrita(24-30), even signs "
            "reversed; result percents are the conventional BPHS-lineage strengths "
            "(Bala 25%, Kumara 50%, Yuva 100%, Vriddha 12.5%, Mrita 0% — some texts "
            "give Vriddha 25%). Jagradadi: Jagrita=own/exalted, Sushupti=debilitated or "
            "enemy-of-sign-lord (compound relationship), else Swapna. Rahu/Ketu use the "
            "odd-sign rule.")
        out["avasthas"] = av
    except Exception as e:
        out["avasthas"] = {"__error__": str(e)}
    # ---- ROUND-11 (review P2-2): GANDANTA zones (wrapper-computed — no PyJHora
    # gandanta API exists, verified by exhaustive grep). Gandanta is the water->fire
    # sign junction "knot": the LAST 3 deg 20 min (26d40m-30d00m) of the water signs
    # (Cancer/Scorpio/Pisces) and the FIRST 3 deg 20 min (0d00m-3d20m) of the fire
    # signs (Aries/Leo/Sagittarius) — exactly one nakshatra pada straddling the
    # junction. Scope: lagna + all nine grahas (classical usage includes the nodes).
    try:
        _pplg = _planetary_longitude(planet_positions)
        _WATER11 = (3, 7, 11)                       # Cancer, Scorpio, Pisces
        _FIRE11 = (0, 4, 8)                         # Aries, Leo, Sagittarius
        _GEND = 26.0 + 40.0 / 60.0                  # 26 deg 40 min
        _FEND = 3.0 + 20.0 / 60.0                   # 3 deg 20 min

        def _gandanta_of(lon):
            sg = _sign_of_long(lon); dg = _deg_in_sign(lon)
            if sg in _WATER11 and dg >= _GEND:
                return {"in_gandanta": True,
                        "zone": "water-sign end (26d40m-30d00m of %s)" % RAASI_NAMES[sg],
                        "junction": "%s->%s" % (RAASI_NAMES[sg], RAASI_NAMES[(sg + 1) % 12]),
                        "degrees_to_exact_junction": round(30.0 - dg, 4)}
            if sg in _FIRE11 and dg < _FEND:
                return {"in_gandanta": True,
                        "zone": "fire-sign start (0d00m-3d20m of %s)" % RAASI_NAMES[sg],
                        "junction": "%s->%s" % (RAASI_NAMES[(sg - 1) % 12], RAASI_NAMES[sg]),
                        "degrees_to_exact_junction": round(dg, 4)}
            return {"in_gandanta": False}

        _g11 = {"Lagna": _gandanta_of(_pplg[LAGNA])}
        for _pn11 in PLANET_ORDER:
            _g11[_pn11] = _gandanta_of(_pplg[_pn11])
        _g11["__note__"] = (
            "ROUND-11 (review P2-2): gandanta = the 'knot' at water->fire sign "
            "junctions — last 3d20m of Cancer/Scorpio/Pisces + first 3d20m of "
            "Aries/Leo/Sagittarius (exactly one pada; nakshatra gandanta — Revati, "
            "Ashlesha, Jyeshtha pada 4 — coincides with the water-side zone). Bodies "
            "here are read as intense, unstable, karmically pressurized until the "
            "exact junction is crossed (degrees_to_exact_junction measures the "
            "distance to that point). Deterministic degree arithmetic — no library "
            "API. Nakshatra/tithi gandanta (solar-lunar junction TIMINGS) are not "
            "computed; this is degree gandanta only.")
        out["gandanta"] = _g11
    except Exception as e:
        out["gandanta"] = {"__error__": str(e)}
    return out


def build_house_variables(env, h, jd, place, planet_positions):
    house = env["house"]
    utils = env["utils"]
    ppl = _planetary_longitude(planet_positions)
    asc_sign = _sign_of_long(ppl[LAGNA])
    out = {}
    def s(fn, *a, **k):
        try: return fn(*a, **k)
        except Exception as e: return {"__error__": str(e)}
    def dlist(v):
        # decode a list of planet ids to names where possible
        if isinstance(v, (list, tuple)) and all(isinstance(x, int) for x in v):
            return [_planet_name(x) for x in v]
        return v

    q = dlist(s(house.lords_of_quadrants_from_planet_positions, planet_positions, asc_sign))
    q = q if isinstance(q, list) else []
    t = dlist(s(house.lords_of_trines_from_planet_positions, planet_positions, asc_sign))
    t = t if isinstance(t, list) else []
    out["quadrant_lords"] = q
    out["trine_lords"] = t
    # ROUND-10 (review P2-3, ADOPTED): Jaimini dual-signature co-lord resolution.
    # Scorpio is co-ruled by Mars/Ketu and Aquarius by Saturn/Rahu; for Jaimini
    # purposes (Chara/Narayana dashas, argala) the STRONGER co-lord rules the sign.
    # house.stronger_planet_from_planet_positions is the library's own resolver —
    # docstring: "To find stronger planet between Rahu/Saturn/Aquarius or
    # Ketu/Mars/Scorpio" — applying its full rule chain (conjunct/aspected planets,
    # exaltation, etc.). check_during_dhasa=False: this is the natal-strength answer.
    try:
        _c = env["const"]
        _str_fn = house.stronger_planet_from_planet_positions
        def _co(pid1, pid2, names):
            with _quiet():
                w = _str_fn(planet_positions, planet1=pid1, planet2=pid2)
            wn = _planet_name(w) if isinstance(w, int) else str(w)
            return {"co_lords": list(names), "stronger_co_lord": wn,
                    "rules_sign_for_jaimini_purposes": wn}
        out["dual_sign_rulers"] = {
            "scorpio": _co(_c.MARS_ID, _c.KETU_ID, ["Mars", "Ketu"]),
            "aquarius": _co(_c.SATURN_ID, _c.RAHU_ID, ["Saturn", "Rahu"]),
            "__note__": ("Jaimini dual-signature resolution via "
                         "house.stronger_planet_from_planet_positions (library rule "
                         "chain; check_during_dhasa=False). Parashari sign lordship is "
                         "unaffected: Scorpio=Mars, Aquarius=Saturn everywhere else in "
                         "this document (core.houses[].sign_lord etc.)."),
        }
    except Exception as e:
        out["dual_sign_rulers"] = {"__error__": str(e)}
    try:
        with _quiet():
            out["marakas"] = dlist(house.marakas_from_planet_positions(planet_positions))
    except Exception as e:
        out["marakas"] = str(e)
    # yogakaraka = planet that lords a quadrant AND a trine (for this lagna)
    out["yogakaraka"] = [p for p in PLANET_ORDER if p in q and p in t]
    out["baadhakas_of_lagna_sign"] = s(house.baadhakas_of_raasi, asc_sign)
    # argala needs the house-to-planet string list
    try:
        h2p = utils.get_house_planet_list_from_planet_positions(planet_positions)
        out["argala"] = s(house.get_argala, h2p)
    except Exception as e:
        out["argala"] = str(e)
    # ROUND-17: strongest among Sun..Saturn (library Jaimini-strength chain).
    try:
        with _quiet():
            _w = house.stronger_planet_from_list_of_planets(planet_positions, list(range(7)))
        out["strongest_among_sun_to_saturn"] = {
            "planet": _decode_planet_id(_w),
            "source": "house.stronger_planet_from_list_of_planets([Sun..Saturn])"}
    except Exception as e:
        out["strongest_among_sun_to_saturn"] = {"__error__": str(e)}
    try:
        _kendra = [(asc_sign + k) % 12 for k in (0, 3, 6, 9)]
        with _quiet():
            _wr = house.stronger_raasi_from_list_of_raasis(planet_positions, _kendra)
        out["strongest_kendra_sign"] = {
            "sign_index": int(_wr) % 12,
            "sign": RAASI_NAMES[int(_wr) % 12],
            "kendra_signs": [RAASI_NAMES[i] for i in _kendra],
            "source": "house.stronger_raasi_from_list_of_raasis(lagna kendras)"}
    except Exception as e:
        out["strongest_kendra_sign"] = {"__error__": str(e)}
    return out


def _strip_html(txt):
    import re
    if not isinstance(txt, str):
        return txt
    txt = re.sub(r"<[^>]+>", " ", txt)
    txt = re.sub(r"&nbsp;", " ", txt)
    txt = re.sub(r"&amp;", "&", txt)
    return re.sub(r"\s+", " ", txt).strip()


def _dosha_is_present(text):
    """PyJHora writes \"There is no X\" when absent. Beyond that, treat as present."""
    t = str(text).lower()
    if "there is no" in t or "there isn't" in t or "is no dosha" in t \
            or "there is no dosha" in t or "not present" in t or "none" in t:
        return False
    # if it just describes the dosha conditionally but has no verdict, report as present
    return True


def _dosha_summary(text, maxlen=180):
    """Trim verbose dosha explanation to a concise summary."""
    t = _strip_html(text)
    if len(t) <= maxlen:
        return t
    return t[:maxlen].rsplit(" ", 1)[0] + "…"


def build_doshas(env, h, jd, place, planet_positions):
    dosha = env["dosha"]
    out = {}
    def s(fn, *a, **k):
        try: return fn(*a, **k)
        except Exception as e: return {"__error__": str(e)}
    try:
        dd = dosha.get_dosha_details(jd, place)
    except Exception as e:
        out["dosha_details"] = str(e)
        dd = None
    if isinstance(dd, dict):
        clean = {}
        for k, v in dd.items():
            txt = _strip_html(v)
            present = _dosha_is_present(txt)
            clean[k] = {"present": present,
                        "summary": _dosha_summary(txt) if present else None}
        # Only emit a compact list of PRESENT doshas for the AI, plus the absent flags.
        out["doshas_present"] = [k for k, info in clean.items() if info["present"]]
        out["doshas_checked"] = {k: (info["present"], info["summary"]) for k, info in clean.items()}
        # Keep the full present texts too (trimmed) for those actually present.
        out["details"] = {k: info["summary"] for k, info in clean.items() if info["present"]}
    else:
        out["dosha_details"] = dd
    out["guru_chandala"] = s(dosha.guru_chandala_dosha, planet_positions)
    out["ghata"] = s(dosha.ghata, planet_positions)

    # ---- ROUND-6: typed per-dosha calls (richer than the prose details above) ----
    # REVIEW R6 ("dosha cancellation"): manglik is run WITH and WITHOUT BV Raman's
    # cancellation exceptions so the cancellation effect is explicit, and each dosha's
    # own typed function is exposed (kala_sarpa/pitru/shrapit/kalathra/ganda_moola).
    try:
        hpl = env["utils"].get_house_planet_list_from_planet_positions(planet_positions)
    except Exception:
        hpl = None
    try:
        _m_exc = dosha.manglik(planet_positions, apply_exceptions=True)
        _m_raw = dosha.manglik(planet_positions, apply_exceptions=False)
        out["manglik"] = {
            "present": bool(_m_exc[0]) if isinstance(_m_exc, (list, tuple)) else _m_exc,
            "present_before_exceptions": bool(_m_raw[0]) if isinstance(_m_raw, (list, tuple)) else _m_raw,
            "exceptions_applied": (bool(_m_exc[1]) if isinstance(_m_exc, (list, tuple))
                                   and len(_m_exc) > 1 else None),
            "exception_indices": (list(_m_exc[2]) if isinstance(_m_exc, (list, tuple))
                                  and len(_m_exc) > 2 else None),
            "note": ("reference = lagna, 2nd house included (South-Indian convention); "
                     "library-exact semantics (ROUND-13 correction): 'present' is the "
                     "house-rule verdict from the apply_exceptions=True call — the "
                     "library REPORTS BV Raman cancellations ALONGSIDE the verdict "
                     "('exceptions_applied'/'exception_indices') WITHOUT flipping the "
                     "boolean; 'present_before_exceptions' is the raw-call verdict "
                     "(identical by library design). ROUND-13: see "
                     "manglik_multi_reference for the Moon/Venus frames.")}
    except Exception as e:
        out["manglik"] = {"__error__": str(e)}
    # ---- ROUND-13 (review item 5, ADOPTED with the library's OWN parameter) ----
    # dosha.manglik natively supports manglik_reference_planet = 'L' (lagna),
    # 1 (Moon) or 5 (Venus) — its docstring says so explicitly. The engine
    # previously called only the lagna default; the girl golden chart PROVES the
    # gap real: Mars sits in the 9th from lagna (not manglik) but the 8th from the
    # Moon → manglik-from-moon TRUE before exceptions, cancelled by BV Raman
    # exceptions [7, 12, 16, 17]. All values below are the LIBRARY's own
    # (exceptions applied + raw), plus Mars's house from each reference via the
    # library's get_relative_house_of_planet. House-set variants DISCLOSED: the
    # library default counts [2, 4, 7, 8, 12] (South-Indian 2nd-house inclusion;
    # the 1st house excluded per the library's Sanjay Rath citation — "lagna house
    # not in ancient text"); the review's hardcoded (1,2,4,7,8,12) union also
    # flags house 1 — exposed as present_with_lagna_house_raw, not the primary.
    try:
        _u13m = env["utils"]
        _p2h13 = _u13m.get_planet_to_house_dict_from_chart(
            _u13m.get_house_planet_list_from_planet_positions(planet_positions))
        _mars_h13 = _p2h13.get(2)
        _rel13 = env["house"].get_relative_house_of_planet
        _mref13 = {}
        for _key13, _ref13, _refname13 in (("from_lagna", "L", "Lagna"),
                                           ("from_moon", 1, "Moon"),
                                           ("from_venus", 5, "Venus")):
            with _quiet():
                _exc13 = dosha.manglik(planet_positions,
                                       manglik_reference_planet=_ref13,
                                       apply_exceptions=True)
                _raw13 = dosha.manglik(planet_positions,
                                       manglik_reference_planet=_ref13,
                                       apply_exceptions=False)
                _lag13 = dosha.manglik(planet_positions,
                                       manglik_reference_planet=_ref13,
                                       include_lagna_house=True,
                                       apply_exceptions=False)
            _refh13 = _p2h13.get(_ref13)
            _mref13[_key13] = {
                "reference": _refname13,
                "mars_house_from_reference": (_rel13(_refh13, _mars_h13)
                                              if _mars_h13 is not None and
                                              _refh13 is not None else None),
                "present": (bool(_exc13[0]) if isinstance(_exc13, (list, tuple))
                            else _exc13),
                "present_before_exceptions": (bool(_raw13[0])
                                              if isinstance(_raw13, (list, tuple))
                                              else _raw13),
                "exceptions_applied": (bool(_exc13[1])
                                       if isinstance(_exc13, (list, tuple)) and
                                       len(_exc13) > 1 else None),
                "exception_indices": (list(_exc13[2])
                                      if isinstance(_exc13, (list, tuple)) and
                                      len(_exc13) > 2 else None),
                "present_with_lagna_house_raw": (
                    bool(_lag13[0]) if isinstance(_lag13, (list, tuple))
                    else _lag13),
            }
        _mref13["__note__"] = (
            "ROUND-13 (review item 5): Kuja/Manglik dosha evaluated from ALL THREE "
            "classical reference frames using the LIBRARY's own "
            "manglik_reference_planet parameter ('L' lagna / 1 Moon / 5 Venus) — "
            "no reimplementation. Library-exact semantics: 'present' is the "
            "house-rule verdict (the library's apply_exceptions REPORTS BV Raman "
            "cancellations alongside the verdict via 'exceptions_applied'/"
            "'exception_indices' WITHOUT flipping the boolean); "
            "'present_before_exceptions' is the raw-call verdict. House set: library "
            "default [2, 4, 7, 8, 12] (South-Indian 2nd-house inclusion; the 1st "
            "house excluded per the library's Sanjay Rath citation — 'lagna house "
            "not in ancient text'); the review's (1,2,4,7,8,12) union is exposed "
            "as present_with_lagna_house_raw. READERS: after lagna, the "
            "Moon-reference is the most widely used classical check; a dosha "
            "present from one frame but absent in another is a PARTIAL dosha — "
            "report the verdict per frame TOGETHER with its active BV Raman "
            "exception indices (where exceptions are active, many texts treat the "
            "dosha as cancelled or weakened — an interpretation call, disclosed "
            "not automated), and cross-check the partner via "
            "match.manglik_compatibility (lagna frame, identical library calls).")
        out["manglik_multi_reference"] = _mref13
    except Exception as e:
        out["manglik_multi_reference"] = {"__error__": str(e)}
    try:
        out["kala_sarpa"] = (bool(dosha.kala_sarpa(hpl)) if hpl is not None
                             else {"__error__": "house list unavailable"})
    except Exception as e:
        out["kala_sarpa"] = {"__error__": str(e)}
    try:
        _pd = dosha.pitru_dosha(planet_positions)
        out["pitru_dosha"] = {
            "present": bool(_pd[0]) if isinstance(_pd, (list, tuple)) else _pd,
            "planets": ([_decode_planet_id(x) for x in _pd[1]]
                        if isinstance(_pd, (list, tuple)) and len(_pd) > 1
                        and isinstance(_pd[1], (list, tuple)) else None)}
    except Exception as e:
        out["pitru_dosha"] = {"__error__": str(e)}
    for _nm, _fn in (("shrapit", dosha.shrapit), ("kalathra", dosha.kalathra)):
        try:
            out[_nm] = bool(_fn(planet_positions))
        except Exception as e:
            out[_nm] = {"__error__": str(e)}
    try:
        _ms = env["drik"].nakshatra(jd, place)[0]
        out["ganda_moola"] = {"present": bool(dosha.ganda_moola(_ms)),
                              "moon_nakshatra_index": int(_ms)}
    except Exception as e:
        out["ganda_moola"] = {"__error__": str(e)}
    return out


def build_vargas(env, h, jd, place, planet_positions, factors, label_varga_factors=False):
    charts = env["charts"]
    out = {}
    for factor in factors:
        key = f"D{factor}"
        try:
            chart = charts.divisional_chart(jd, place, divisional_chart_factor=factor)
            ppl = _planetary_longitude(chart)
            asc_sign = _sign_of_long(ppl[LAGNA])
            table = {}
            for pid_name in PLANET_ORDER:
                lon = ppl[pid_name]
                table[pid_name] = {"sign": RAASI_NAMES[_sign_of_long(lon)],
                                   "degree": round(_deg_in_sign(lon), 3),
                                   "house": _house_number(asc_sign, _sign_of_long(lon))}
            row = {"lagna_sign": RAASI_NAMES[asc_sign], "planets": table}
            # REVIEW (non-planet varga elements): PyJHora's `divisional_chart` returns exactly
            # the set ['L', 0..8] — Lagna + the 9 grahas (Sun..Saturn id 0..6 PLUS the two
            # shadow grahas / nodes Rahu=7, Ketu=8 — grahas in the Vedic planet model; kept
            # in a separate "nodes" key only for presentation). No house cusps or other
            # non-graha points (upagrahas/sphutas/sahams/arudhas) are computed at varga
            # level in this build (R6: verified — divisional_chart has no
            # exclude_non_planets knob; special points per varga are exposed separately via
            # special.special_planet_longitudes_D9).
            row["lagna"] = {"sign": RAASI_NAMES[asc_sign],
                            "sign_index": asc_sign,
                            "degree": round(_deg_in_sign(ppl[LAGNA]), 3),
                            "longitude": round(ppl[LAGNA], 3)}
            row["nodes"] = {"Rahu": table["Rahu"], "Ketu": table["Ketu"]}
            row["bodies"] = ("Lagna + 9 grahas (Sun..Saturn, Rahu, Ketu). PyJHora's "
                             "divisional_chart exposes THIS exact set; at varga level it does "
                             "not compute house cusps or any further non-planet point.")
            # ROUND-10 (review P2-2, ADOPTED with library sources): Shashtiamsa (D60)
            # deities per body, computed from the RASI (D1) sign + degree-in-sign with the
            # library's OWN resolver utils.get_amsa_ruler_from_planet_longitude — the same
            # function PyJHora's yoga predicates use for cruel-shashtiamsa checks, so these
            # values stay consistent with the `yogas` section. Names from
            # const.amsa_rulers[60]; nature from const.shashti_amsa_rulers_kroora/_soumya.
            if int(factor) == 60:
                try:
                    _u60 = env["utils"]; _c60 = env["const"]
                    _deities = {}
                    for _entry in planet_positions:
                        _lab = _entry[0]; _pos = _entry[1]
                        _nm = "Lagna" if _lab == "L" else _planet_name(_lab)
                        if _nm != "Lagna" and _nm not in PLANET_ORDER:
                            continue
                        _sg = int(_pos[0]) % 12; _dg = float(_pos[1])
                        _idx = _u60.get_amsa_ruler_from_planet_longitude(_dg, _sg)
                        _deities[_nm] = {
                            "deity": _c60.amsa_rulers[60][_idx],
                            "deity_index": int(_idx),
                            "nature": ("kroora (cruel)" if _idx in _c60.shashti_amsa_rulers_kroora
                                       else ("soumya (gentle)"
                                             if _idx in _c60.shashti_amsa_rulers_soumya
                                             else "unclassified")),
                        }
                    row["shashtiamsa_deities"] = _deities
                    row["shashtiamsa_deities_note"] = (
                        "Computed by the wrapper from D1 sign + degree-in-sign via "
                        "utils.get_amsa_ruler_from_planet_longitude (library resolver; "
                        "names const.amsa_rulers[60]; nature lists "
                        "const.shashti_amsa_rulers_kroora (23 cruel) / _soumya (37 gentle)). "
                        "PARITY DISCLOSURE: the library reverses the deity count for 0-based "
                        "EVEN sign indices (Aries, Gemini, Leo, Libra, Sagittarius, "
                        "Aquarius) and counts forward for the rest — some classical texts "
                        "state the opposite parity (odd signs forward). We mirror the "
                        "library so these deities agree with PyJHora's own shashtiamsa-based "
                        "yoga predicates; cross-check against your lineage if the parity "
                        "convention matters for interpretation.")
                except Exception as e:
                    row["shashtiamsa_deities"] = {"__error__": str(e)}
            if label_varga_factors:
                # classify standard vs extended (custom-fallback) so the "verified" claim is
                # precise (review §3: not every listed factor is in division_chart_factors).
                row["factor_source"] = ("standard" if int(factor) in _STANDARD_VARGAS
                                        else "extended_custom_fallback")
            # ROUND-7 (review C1) — HONEST STATUS for extended factors. In PyJHora 5.0
            # D150 (Nadiamsa) became a STANDARD divisional (it is in
            # const.division_chart_factors now, so it no longer lands in this branch).
            # D72 is still NOT a classical scheme in the library: any factor up to
            # MAX_DHASAVARGA_FACTOR routes to `custom_divisional_chart`, a GENERIC
            # equal-division algorithm — NOT the classical D72/Shashtiamsa of the texts.
            # "Differs from D1" only proves the custom path resolved; it does NOT prove
            # classical correctness — so this is labeled a CHECK, with an explicit
            # method_status, instead of the old over-claiming "proof" wording.
            if int(factor) not in _STANDARD_VARGAS:
                row["method_status"] = {
                    "implementation": "PyJHora custom_divisional_chart (generic equal-division algorithm)",
                    "classical_scheme_implemented": False,
                    "supported_as_standard_in_pyjhora": False,
                    "maturity": "experimental — do NOT quote as a classical varga; corroborate with standard divisionals",
                }
                try:
                    # ROUND-9 (review Enhancement 1): the D1 reference longitudes are the
                    # `planet_positions` this function was called with — recomputing D1
                    # through the ephemeris per non-standard factor was redundant.
                    _d1 = _planetary_longitude(planet_positions)
                    differs = abs(ppl[LAGNA] - _d1[LAGNA]) > 1e-6
                    row["custom_divisional_check"] = {
                        "d1_lagna_longitude": round(_d1[LAGNA], 3),
                        "this_lagna_longitude": round(ppl[LAGNA], 3),
                        "differs_from_d1_lagna": bool(differs),
                        "path": "charts.divisional_chart -> custom_divisional_chart "
                                "(factor in 1..MAX_DHASAVARGA_FACTOR, not in standard "
                                "divisional_chart_functions)",
                        "meaning": ("proves only that the custom path resolved and produced a "
                                    "chart distinct from D1 — NOT that it matches any classical "
                                    "D%s scheme" % factor),
                    }
                except Exception as e:
                    row["custom_divisional_check"] = {"__error__": str(e)}
            out[key] = row
        except Exception as e:
            out[key] = {"__error__": str(e)}
    return out


# Jaimini chara-karaka role tables — ROUND-8 FIX (review P0-1, CONFIRMED real):
# house.chara_karakas returns the karaka bodies sorted by DECREASING degree. In the
# 8-karaka scheme (Sun..Saturn + Rahu; Ketu excluded) the classical order is
#   AK, AmK, BK, MK, PiK (Pitrikaraka), PK, GK, DK — so Darakaraka (spouse) is the
#   LOWEST-degree body (index 7). The previous single list omitted Pitrikaraka,
#   invented a non-existent "Atmakaraka2", and thereby labelled Gnatikaraka (index 6)
#   as the Darakaraka. The 7-karaka scheme (Rahu also excluded) has no Pitrikaraka:
#   AK, AmK, BK, MK, PK, GK, DK (DK = index 6).
CHARA_KARAKA_ROLES_8 = ["Atmakaraka", "Amatyakaraka", "Bhratrikaraka", "Matrikaraka",
                        "Pitrikaraka", "Putrakaraka", "Gnatikaraka", "Darakaraka"]
CHARA_KARAKA_ROLES_7 = ["Atmakaraka", "Amatyakaraka", "Bhratrikaraka", "Matrikaraka",
                        "Putrakaraka", "Gnatikaraka", "Darakaraka"]
CHARA_KARAKA_ROLES = CHARA_KARAKA_ROLES_8  # legacy alias (8-karaka scheme)


def _planet_name(index):
    try:
        return PLANET_NAMES[int(index)]
    except Exception:
        return str(index)


def _planet_id(name):
    """Planet name -> PyJHora planet id (0=Sun..8=Ketu); None if not a known graha."""
    try:
        return PLANET_NAMES.index(name)
    except Exception:
        return None


def _decode_mrityu_bhaga_row(row):
    """Decode a mrityu-bhaga row `(planet_or_L, rasi_index, diff_lon)`.

    element 0 is a planet id (0..8) or 'L' (lagna); element 1 is a SIGN index (0..11),
    element 2 is the longitude difference. Only the planet and sign are decoded to names.
    """
    try:
        if not isinstance(row, (list, tuple)) or len(row) < 2:
            return row
        pid, rasi = row[0], row[1]
        planet = "Lagna" if pid == "L" else _decode_planet_id(pid)
        sign = RAASI_NAMES[rasi % 12] if isinstance(rasi, (int, float)) and not isinstance(rasi, bool) else rasi
        out = {"planet": planet, "sign": sign}
        if len(row) > 2:
            out["diff_from_mrityu_longitude"] = round(float(row[2]), 4)
        return out
    except Exception:
        return row


def _decode_planet_id_list(ids):
    """Decode a list/tuple of planet ids (0=Sun..8=Ketu) into name strings (range-checked)."""
    if not isinstance(ids, (list, tuple)):
        return ids
    out = []
    for x in ids:
        try:
            n = int(x)
            out.append(PLANET_NAMES[n] if 0 <= n <= 8 else x)
        except Exception:
            out.append(x)
    return out


def _jd_to_ymd(jd):
    """Julian day -> (y, m, d) tuple using PyJHora's own gregorian converter."""
    try:
        from jhora import utils as _u
        y, m, d, _h = _u.jd_to_gregorian(jd)
        return (int(y), int(m), int(d))
    except Exception:
        # fallback to a Date-free approximation
        import datetime as _dt
        base = _dt.datetime(1970, 1, 1) + _dt.timedelta(days=float(jd) - 2440587.5)
        return (base.year, base.month, base.day)


def _decode_when(res):
    """ROUND-16: decode drik next/previous calendar returns.

    Shapes seen in this build:
      * (drik.Date(y,m,d), fractional_hours)  — lunar year/month
      * (julian_day, aux)                     — solar year/month
      * bare julian_day                       — next_tithi (experimental)
    """
    if isinstance(res, dict) and "__error__" in res:
        return res
    if isinstance(res, (list, tuple)) and res:
        a = res[0]
        extra = res[1] if len(res) > 1 else None
        if hasattr(a, "year") and hasattr(a, "month") and hasattr(a, "day"):
            hrs = (float(extra) if isinstance(extra, (int, float))
                   and not isinstance(extra, bool) else 0.0)
            return {"date": _date_str(int(a.year), int(a.month), int(a.day), hrs),
                    "hours_raw": round(hrs, 6)}
        if isinstance(a, (int, float)) and not isinstance(a, bool) and float(a) > 2000000:
            try:
                from jhora import utils as _u
                y, m, d, fh = _u.jd_to_gregorian(float(a))
            except Exception:
                y, m, d = _jd_to_ymd(float(a)); fh = 0.0
            out = {"date": _date_str(int(y), int(m), int(d), float(fh)),
                   "jd": float(a)}
            if isinstance(extra, (int, float)) and not isinstance(extra, bool):
                out["aux"] = extra
            return out
    if isinstance(res, (int, float)) and not isinstance(res, bool) and float(res) > 2000000:
        try:
            from jhora import utils as _u
            y, m, d, fh = _u.jd_to_gregorian(float(res))
        except Exception:
            y, m, d = _jd_to_ymd(float(res)); fh = 0.0
        return {"date": _date_str(int(y), int(m), int(d), float(fh)),
                "jd": float(res)}
    return res


def _decode_sign_deg_rows(rows, name_map=None):
    """ROUND-16: [[abbr, [sign_index, deg_in_sign]], ...] -> {name: {sign, degree, longitude}}."""
    out = {}
    for item in rows or []:
        try:
            key, pair = item[0], item[1]
            sgn, deg = int(pair[0]) % 12, float(pair[1])
            nm = (name_map or {}).get(str(key), str(key))
            out[nm] = {"abbr": str(key),
                       "sign_index": sgn, "sign": RAASI_NAMES[sgn],
                       "degree_in_sign": round(deg, 4),
                       "longitude": round(sgn * 30.0 + deg, 4)}
        except Exception:
            out[str(item)] = item
    return out


def _decode_kp_details(lon):
    """ROUND-17: utils.get_KP_details_from_planet_longitude -> KP-249 sub-lord row."""
    try:
        from jhora import utils as _u
        raw = _u.get_KP_details_from_planet_longitude(float(lon))
        if not raw:
            return None
        kp_no, row = next(iter(raw.items()))
        rasi, nak, sd, ed, rl, sl, ssl = row
        return {
            "kp_sub_number": int(kp_no),
            "sign": RAASI_NAMES[int(rasi) % 12],
            "nakshatra": NAKSHATRAS[int(nak) % 27],
            "sub_start_degree_in_sign": round(float(sd), 4),
            "sub_end_degree_in_sign": round(float(ed), 4),
            "sign_lord": _decode_planet_id(rl),
            "star_lord": _decode_planet_id(sl),
            "sub_lord": _decode_planet_id(ssl),
            "source": "utils.get_KP_details_from_planet_longitude (KP-249 table)",
        }
    except Exception as e:
        return {"__error__": str(e)}


def _library_navamsa(lon):
    """ROUND-17: drik.navamsa_from_long corroboration of core.navamsa_sign."""
    try:
        from jhora.panchanga import drik as _d
        _nv = _d.navamsa_from_long(float(lon))
        if isinstance(_nv, (list, tuple)) and len(_nv) >= 2:
            return {"sign_index": int(_nv[0]) % 12,
                    "sign": RAASI_NAMES[int(_nv[0]) % 12],
                    "degree_in_navamsa": round(float(_nv[1]), 4),
                    "source": "drik.navamsa_from_long"}
        return _nv
    except Exception as e:
        return {"__error__": str(e)}


def build_karakas(env, h, jd, place, planet_positions):
    house = env["house"]
    out = {}
    try:
        with _quiet():
            res = house.chara_karakas(planet_positions)
        # res is a list ordered by decreasing longitude of the karakas (planet ids).
        # In this PyJHora build chara_karakas lives on `house` (there is no charts.chara_karakas
        # — verified absent; review-recommended location does not exist here), so we keep the
        # working call and DECORATE it with the canonical karaka names + the method note.
        # ROUND-8 FIX (review P0-1): pick the 8- or 7-karaka scheme by the result length —
        # Darakaraka is ALWAYS the last (lowest-degree) body, Pitrikaraka exists only in
        # the 8-karaka scheme, and no "Atmakaraka2" is invented.
        if isinstance(res, (list, tuple)) and len(res) >= 7:
            names = [_planet_name(i) for i in res]
            scheme8 = len(names) >= 8
            roles = CHARA_KARAKA_ROLES_8 if scheme8 else CHARA_KARAKA_ROLES_7
            n = len(roles)
            out["chara_karaka_scheme"] = ("8-karaka (Rahu included, Ketu excluded)" if scheme8
                                          else "7-karaka (Rahu and Ketu excluded)")
            out["chara_karakas_order"] = names[:n]
            out["chara_karaka_roles"] = dict(zip(names[:n], roles))
            out["atmakaraka"] = names[0]
            out["amatyakaraka"] = names[1] if n > 1 else None
            out["bhratrikaraka"] = names[2] if n > 2 else None
            out["matrikaraka"] = names[3] if n > 3 else None
            if scheme8:
                out["pitrikaraka"] = names[4]
            out["putrakaraka"] = names[5] if scheme8 else (names[4] if n > 4 else None)
            out["gnatikaraka"] = names[6] if scheme8 else (names[5] if n > 5 else None)
            out["darakaraka"] = names[n - 1]   # lowest-degree body = spouse karaka
            out["nak_karaka_definition"] = ("0=Surya,1=Chandra,2=Mangala,3=Budha,4=Guru,"
                                            "5=Sukra,6=Sani,7=Rahu (8-karaka scheme; Ketu is "
                                            "excluded as it always owns the 7th/8th kendra "
                                            "and is not a Jaimini karaka). Roles assigned in "
                                            "DECREASING degree order — Rahu is ranked by "
                                            "(30° - its degree-in-sign), the library/JHora "
                                            "mirror convention for the shadow planet — so the "
                                            "Darakaraka (spouse) is the LAST body in the list.")
        else:
            out["chara_karakas_raw"] = res
    except Exception as e:
        out["chara_karakas"] = str(e)
    return out


def _decode_special(res):
    """drik special-lagnas return (sign_index, degree_in_sign). Decode to sign+degree+longitude."""
    if isinstance(res, (list, tuple)) and len(res) >= 2 and \
            isinstance(res[0], (int, float)) and not isinstance(res[0], bool) and \
            isinstance(res[1], (int, float)) and not isinstance(res[1], bool):
        si = int(res[0]) % 12
        deg = float(res[1])
        return {"sign_index": si, "sign": RAASI_NAMES[si],
                "degree": round(deg, 3), "longitude": round(si * 30.0 + deg, 3)}
    # fallback: first plausible longitude
    if isinstance(res, (int, float)) and not isinstance(res, bool) and 0 <= float(res) <= 366:
        lon = float(res)
        return {"sign_index": _sign_of_long(lon), "sign": RAASI_NAMES[_sign_of_long(lon)],
                "degree": round(_deg_in_sign(lon), 3), "longitude": round(lon, 3)}
    return res


def build_special(env, h, jd, place):
    """Special lagnas (by name, decoded to sign+degree) + upagrahas for the D1 chart.
       Uses drik's special-ascendant functions so values are real longitudes, not ids."""
    drik = env["drik"]
    out = {}
    special_fns = {}
    for _nm in ["hora_lagna", "ghati_lagna", "vighati_lagna", "pranapada_lagna",
                "indu_lagna", "sree_lagna", "varnada_lagna", "bhava_lagna",
                "bhrigu_bindhu_lagna", "kunda_lagna"]:
        _fn = getattr(drik, _nm, None)
        if callable(_fn):
            special_fns[_nm] = _fn
    for name, fn in special_fns.items():
        try:
            with _quiet():
                res = fn(jd, place)
            out[name] = _decode_special(res)
        except Exception as e:
            out[name] = {"__error__": str(e)}
    # Upagrahas — the 6 Graha-upagrahas (Kaala, Mrityu, Artha Praharaka, Yama Ghantaka,
    # Gulika, Maandi) via drik.upagraha_longitude, and the 5 Chhaya/solar upagrahas
    # (Dhuma, Vyatipaata, Parivesha, Indrachaapa, Upaketu) via drik.solar_upagraha_longitudes.
    try:
        # Sun longitude from the D1 chart positions
        from jhora.horoscope.chart import charts as _charts
        sun_lon = _planetary_longitude(_charts.rasi_chart(jd, place))["Sun"]
    except Exception:
        sun_lon = None
    up = {}
    # REVIEW R6 fix: use the library's DEDICATED per-upagraha longitude functions — the
    # same ones charts.special_planet_longitudes calls internally. The old
    # (planet_index, upagraha_part) mapping had Gulika/Maandi SWAPPED relative to
    # drik.gulika_longitude / drik.maandi_longitude (proven by the agreement crosscheck
    # below: 279.7165 vs 291.6368 on the reference chart).
    graha_up = [("Kaala", "kaala_longitude"), ("Mrityu", "mrityu_longitude"),
                ("Artha_Praharaka", "artha_praharaka_longitude"),
                ("Yama_Ghantaka", "yama_ghantaka_longitude"),
                ("Gulika", "gulika_longitude"), ("Maandi", "maandi_longitude")]
    dob = drik.Date(h.Date.year, h.Date.month, h.Date.day)
    tob = _tob(h)
    for name, fname in graha_up:
        try:
            fn = getattr(drik, fname)
            with _quiet():
                r = fn(dob, tob, place)
            up[name] = _decode_special(r)
        except Exception as e:
            up[name] = {"__error__": str(e)}
    if sun_lon is not None:
        for name in ["dhuma", "vyatipaata", "parivesha", "indrachaapa", "upaketu"]:
            try:
                with _quiet():
                    r = drik.solar_upagraha_longitudes(sun_lon, name)
                up[name] = _decode_special(r)
            except Exception as e:
                up[name] = {"__error__": str(e)}
    out["upagrahas"] = up

    # ---- ROUND-5 additions ----------------------------------------------------
    charts = env["charts"]
    # charts.special_planet_longitudes: ALL 11 special points in one call (cross-check
    # of the drik-layer upagrahas above). Abbreviations decoded to full names.
    # REVIEW R6 fix: the library's abbreviation for Parivesha is "Pv" (see
    # charts.special_planet_longitudes' sub_planet_list_2), not "Pi" — the wrong map left
    # a raw "Pv" key in the output and dropped it from the agreement crosscheck (10/11).
    _ABBR = {"Kl": "Kaala", "Mr": "Mrityu", "Ap": "Artha_Praharaka", "Yg": "Yama_Ghantaka",
             "Gk": "Gulika", "Md": "Maandi", "Dm": "Dhuma", "Vp": "Vyatipaata",
             "Pv": "Parivesha", "Ic": "Indrachaapa", "Uk": "Upaketu"}
    try:
        with _quiet():
            _spl = charts.special_planet_longitudes(dob, tob, place)
        dec = {}
        for item in _spl or []:
            try:
                key, (sgn, deg) = item[0], item[1]
                nm = _ABBR.get(str(key), str(key))
                dec[nm] = {"sign_index": int(sgn) % 12, "sign": RAASI_NAMES[int(sgn) % 12],
                           "degree_in_sign": round(float(deg), 4),
                           "longitude": round(float(sgn) * 30 + float(deg), 4)}
            except Exception:
                dec[str(item)] = item
        out["special_planet_longitudes"] = dec
        # REVIEW R6: the charts-layer call is an INDEPENDENT code path for the same 11
        # special points — surface an explicit agreement check instead of silently
        # emitting both (a mismatch is exactly how the Gulika/Maandi swap was found).
        try:
            _mism, _maxd, _ncmp = [], 0.0, 0
            for _nm, _v2 in dec.items():
                _v1 = up.get(_nm)
                if _v1 is None:
                    for _k, _vv in up.items():
                        if str(_k).lower() == str(_nm).lower():
                            _v1 = _vv
                            break
                if isinstance(_v1, dict) and isinstance(_v2, dict) and \
                        "longitude" in _v1 and "longitude" in _v2:
                    _ncmp += 1
                    _dl = abs(float(_v1["longitude"]) - float(_v2["longitude"]))
                    _maxd = max(_maxd, _dl)
                    if _dl > 0.01:
                        _mism.append({"point": _nm, "drik_layer": _v1["longitude"],
                                      "charts_layer": _v2["longitude"],
                                      "abs_diff_deg": round(_dl, 4)})
            out["special_points_agreement"] = {
                "agree": not _mism, "points_compared": _ncmp,
                "max_abs_diff_deg": round(_maxd, 4), "mismatches": _mism,
                "note": ("drik-layer upagrahas vs charts.special_planet_longitudes "
                         "(independent paths, tolerance 0.01 deg).")}
        except Exception as e:
            out["special_points_agreement"] = {"__error__": str(e)}
        # divisional special points (D9) — the charts layer supports divisional factors
        try:
            with _quiet():
                _spl9 = charts.special_planet_longitudes(dob, tob, place,
                                                         divisional_chart_factor=9)
            dec9 = {}
            for item in _spl9 or []:
                try:
                    key, (sgn, deg) = item[0], item[1]
                    nm = _ABBR.get(str(key), str(key))
                    dec9[nm] = {"sign_index": int(sgn) % 12, "sign": RAASI_NAMES[int(sgn) % 12],
                                "degree_in_sign": round(float(deg), 4),
                                "longitude": round(float(sgn) * 30 + float(deg), 4)}
                except Exception:
                    dec9[str(item)] = item
            out["special_planet_longitudes_D9"] = dec9
        except Exception as e:
            out["special_planet_longitudes_D9"] = {"__error__": str(e)}
        # ROUND-16: charts.special_lagna_longitudes (BL/HL/GL/…) at D9 and D10.
        try:
            _lmap = dict(getattr(env.get("utils"), "_special_lagnas", {}) or {})
            _ldiv = {}
            for _f in (9, 10):
                try:
                    with _quiet():
                        _rows = charts.special_lagna_longitudes(
                            dob, tob, place, divisional_chart_factor=_f)
                    _ldiv["D%d" % _f] = _decode_sign_deg_rows(_rows, _lmap)
                except Exception as e:
                    _ldiv["D%d" % _f] = {"__error__": str(e)}
            out["special_lagna_longitudes_by_varga"] = {
                "by_factor": _ldiv,
                "__note__": ("ROUND-16: charts.special_lagna_longitudes D9/D10. "
                             "Abbreviations (BL=bhava_lagna, HL=hora_lagna, …) from "
                             "utils._special_lagnas. D1 special lagnas remain the named "
                             "keys of this section.")}
        except Exception as e:
            out["special_lagna_longitudes_by_varga"] = {"__error__": str(e)}
    except Exception as e:
        out["special_planet_longitudes"] = {"__error__": str(e)}
    # generic special ascendant (drik.special_ascendant)
    try:
        with _quiet():
            _sa = drik.special_ascendant(jd, place)
        out["special_ascendant"] = _decode_special(_sa)
    except Exception as e:
        out["special_ascendant"] = {"__error__": str(e)}
    # Varnada lagna via the MIXED-chart API (returns (sign_index, longitude))
    try:
        with _quiet():
            _vl = charts.varnada_lagna_mixed_chart(dob, tob, place)
        if isinstance(_vl, (list, tuple)) and len(_vl) >= 2:
            out["varnada_lagna_mixed_chart"] = {
                "sign_index": int(_vl[0]) % 12, "sign": RAASI_NAMES[int(_vl[0]) % 12],
                "longitude": round(float(_vl[1]), 4),
                "degree_in_sign": round(float(_vl[1]) % 30, 4)}
        else:
            out["varnada_lagna_mixed_chart"] = _vl
    except Exception as e:
        out["varnada_lagna_mixed_chart"] = {"__error__": str(e)}
    # Mixed chart D1(degrees) x D9(signs) — the classic vargottama/mixed analysis chart
    try:
        with _quiet():
            _mc = charts.mixed_chart(jd, place, 1, 1, 9, 1)
        dec = {}
        for item in _mc or []:
            try:
                key, (sgn, deg) = item[0], item[1]
                nm = "Lagna" if key == "L" else _decode_planet_id(key)
                dec[nm] = {"sign": RAASI_NAMES[int(sgn) % 12], "degree_in_sign": round(float(deg), 4)}
            except Exception:
                continue
        out["mixed_chart_D1xD9"] = {
            "positions": dec,
            "note": "charts.mixed_chart(varga 1 x varga 9): D9 signs with D1 degrees."}
    except Exception as e:
        out["mixed_chart_D1xD9"] = {"__error__": str(e)}
    # nakshatra-dhasa progression longitudes (the progressed positions Narayana-style
    # dashas use; correction 0.0 = birth-time positions in the progression frame)
    try:
        with _quiet():
            _nd = charts.get_nakshathra_dhasa_progression_longitudes(jd, place, 0.0)
        dec = {}
        for item in _nd or []:
            try:
                key, (sgn, deg) = item[0], item[1]
                nm = "Lagna" if key == "L" else _decode_planet_id(key)
                dec[nm] = {"sign": RAASI_NAMES[int(sgn) % 12], "degree_in_sign": round(float(deg), 4)}
            except Exception:
                continue
        out["nakshathra_dhasa_progression_longitudes"] = dec
    except Exception as e:
        out["nakshathra_dhasa_progression_longitudes"] = {"__error__": str(e)}
    # chart element longitude (used by kalachakra-style progressions)
    try:
        with _quiet():
            out["chart_element_longitude"] = round(float(charts.get_chart_element_longitude(jd, place)), 4)
    except Exception as e:
        out["chart_element_longitude"] = {"__error__": str(e)}
    # ---- ROUND 5: divisional positions derived PURELY from D1 longitudes (no fresh
    # ---- ephemeris call) — a self-consistency crosscheck against the vargas.* charts ----
    try:
        _pp1 = charts.rasi_chart(jd, place)
        dp = {}
        for f in (2, 3, 4, 7, 9, 10, 12):
            try:
                with _quiet():
                    rows = charts.divisional_positions_from_rasi_positions(
                        _pp1, divisional_chart_factor=f, chart_method=1)
                dec = {}
                for item in rows or []:
                    key, (sgn, deg) = item[0], item[1]
                    nm = "Lagna" if key == "L" else _decode_planet_id(key)
                    dec[nm] = {"sign": RAASI_NAMES[int(sgn) % 12], "degree_in_sign": round(float(deg), 4)}
                dp["D%d" % f] = dec
            except Exception as e:
                dp["D%d" % f] = {"__error__": str(e)}
        out["divisional_positions_from_rasi"] = dp
        out["divisional_positions_from_rasi_note"] = (
            "Standard chart_method=1 derivation from D1 longitudes. Compare against the "
            "ephemeris-computed vargas.* charts; small degree differences are method artifacts, "
            "not data errors.")
    except Exception as e:
        out["divisional_positions_from_rasi"] = {"__error__": str(e)}
    # ---- ROUND-17: 12 Varnada lagnas + extra mixed charts ----
    try:
        _vmap = dict(getattr(env.get("utils"), "_varnada_lagnas", {}) or {})
        _vdiv = {}
        for _f in (1, 9, 10):
            try:
                with _quiet():
                    _rows = charts.varnada_lagna_longitudes(dob, tob, place,
                                                            divisional_chart_factor=_f)
                _vdiv["D%d" % _f] = _decode_sign_deg_rows(_rows, _vmap)
            except Exception as e:
                _vdiv["D%d" % _f] = {"__error__": str(e)}
        out["varnada_lagnas"] = {
            "by_factor": _vdiv,
            "__note__": ("ROUND-17: charts.varnada_lagna_longitudes V1..V12 (house-wise "
                         "Varnada lagnas). Distinct from drik.varnada_lagna (a single "
                         "point) and varnada_lagna_mixed_chart.")}
    except Exception as e:
        out["varnada_lagnas"] = {"__error__": str(e)}
    def _dec_mixed(rows):
        dec = {}
        for item in rows or []:
            try:
                key, pair = item[0], item[1]
                sgn, deg = int(pair[0]) % 12, float(pair[1])
                nm = "Lagna" if key == "L" else _decode_planet_id(key)
                dec[nm] = {"sign": RAASI_NAMES[sgn], "sign_index": sgn,
                           "degree_in_sign": round(deg, 4),
                           "longitude": round(sgn * 30.0 + deg, 4)}
            except Exception:
                continue
        return dec
    try:
        mixed = {}
        for lab, a, b in (("D9xD12", 9, 12), ("D1xD10", 1, 10)):
            try:
                with _quiet():
                    _mc = charts.mixed_chart(jd, place, a, 1, b, 1)
                mixed[lab] = _dec_mixed(_mc)
            except Exception as e:
                mixed[lab] = {"__error__": str(e)}
        out["mixed_charts"] = {
            "by_pair": mixed,
            "__note__": ("ROUND-17: charts.mixed_chart extra pairs. D1xD9 remains under "
                         "mixed_chart_D1xD9. D9xD12 is the common Jaimini mixed frame.")}
    except Exception as e:
        out["mixed_charts"] = {"__error__": str(e)}
    try:
        with _quiet():
            _vm = charts.varnada_lagna_longitudes_mixed_chart(dob, tob, place, 1, 1, 9, 1)
        _vmap = dict(getattr(env.get("utils"), "_varnada_lagnas", {}) or {})
        out["varnada_lagnas_mixed_D1xD9"] = _decode_sign_deg_rows(_vm, _vmap)
    except Exception as e:
        out["varnada_lagnas_mixed_D1xD9"] = {"__error__": str(e)}
    try:
        with _quiet():
            _am = charts.arudha_lagna_longitudes_mixed_chart(dob, tob, place, 1, 1, 9, 1)
        out["arudha_longitudes_mixed_D1xD9"] = {
            "points": _decode_sign_deg_rows(_am),
            "__note__": "ROUND-18: charts.arudha_lagna_longitudes_mixed_chart(D1 x D9)."}
    except Exception as e:
        out["arudha_longitudes_mixed_D1xD9"] = {"__error__": str(e)}
    try:
        _lmap = dict(getattr(env.get("utils"), "_special_lagnas", {}) or {})
        with _quiet():
            _slm = charts.special_lagna_longitudes_mixed_chart(dob, tob, place, 1, 1, 9, 1)
        out["special_lagna_longitudes_mixed_D1xD9"] = _decode_sign_deg_rows(_slm, _lmap)
    except Exception as e:
        out["special_lagna_longitudes_mixed_D1xD9"] = {"__error__": str(e)}
    try:
        _ABBR = {"Kl": "Kaala", "Mr": "Mrityu", "Ap": "Artha_Praharaka", "Yg": "Yama_Ghantaka",
                 "Gk": "Gulika", "Md": "Maandi", "Dm": "Dhuma", "Vp": "Vyatipaata",
                 "Pv": "Parivesha", "Ic": "Indrachaapa", "Uk": "Upaketu"}
        with _quiet():
            _spm = charts.special_planet_longitudes_mixed_chart(dob, tob, place, 1, 1, 9, 1)
        out["special_planet_longitudes_mixed_D1xD9"] = _decode_sign_deg_rows(_spm, _ABBR)
    except Exception as e:
        out["special_planet_longitudes_mixed_D1xD9"] = {"__error__": str(e)}
    return out


# PyJHora's vimsottari lords are stored as *planet ids* (Sun=0 … Ketu=8).
# VIMSOTTARI_ORDER is the sequence; planet id -> name via PLANET_NAMES.
VIMSOTTARI_ORDER = ["Ketu", "Venus", "Sun", "Moon", "Mars", "Rahu",
                    "Jupiter", "Saturn", "Mercury"]


def _vimsottari_name(seq_index):
    """Raw vimsottari lord = planet id (0=Sun..8=Ketu) -> planet name.

    Range-checked: PyJHora stores vimsottari lords as planet ids (const.vimsottari_adhipati_list
    = [8,5,0,1,2,7,4,6,3]); we map only 0..8 and leave anything else untouched (review §2.3/§2.4).
    """
    try:
        n = int(seq_index)
    except Exception:
        return str(seq_index)
    if 0 <= n <= 8:
        return PLANET_NAMES[n]
    return str(seq_index)


def _date_str(y, m, d, frac_hour):
    try:
        hh = int(frac_hour); mm = int((frac_hour - hh) * 60)
        return f"{y:04d}-{int(m):02d}-{int(d):02d} {hh:02d}:{mm:02d}"
    except Exception:
        return str([y, m, d, frac_hour])


def build_applicability(env, h, jd, place):
    """Parashari conditional-dasha applicability rule.

    Vimshottari (Udu) is the universal default, but the sages also prescribed
    CONDITIONAL dashas that mathematically APPLY only when a specific combination
    holds (e.g. Ashtottari when Rahu is in a kendra/trikona from lagna under a
    day-Krishna-paksha / night-Shukla-paksha birth; Shatabdika for a vargottama
    lagna or a specific yuga; Shashti-sama for an exceptionally strong Sun, etc.).
    Per the Parashari rule an astrologer checks these FIRST and uses an applicable
    conditional dasha in place of (or alongside) Vimshottari.

    Runs PyJHora's own applicability_check and returns the DECODED list of
    conditional dashas mathematically active for this birth chart, plus a note on
    how to weight them. The `dasha` (Vimsottari) section stays authoritative for
    tone; the applicable conditional dashas (and the rasi dashas) corroborate the
    event layer.
    """
    from jhora.horoscope.dhasa.graha import applicability
    out = {}
    dob = env["drik"].Date(h.Date.year, h.Date.month, h.Date.day)
    tob = _tob(h)
    try:
        with _quiet():
            res = applicability.applicability_check(dob, tob, place)
        # PyJHora returns the conditional-dasha NAME strings (e.g. 'ashtottari').
        # Expose BOTH the raw result and a title-cased decoded list (review §3.3).
        raw = list(res) if res else []
        out["applicable_conditional_dashas_raw"] = raw
        out["applicable_conditional_dashas"] = [str(x).replace("_", " ").title() for x in raw]
        out["parameter"] = {
            "condition": ("PyJHora `applicability_check` (Ashtottari, Chaturashiti-sama, "
                          "Dwadasottari, Dwissaptati-sama, Panchottari, Satabdika, "
                          "Shashti-sama) evaluated on the rasi / D9 / D12 charts"),
            "note": ("An EMPTY list does NOT mean no dasha applies -- Vimshottari always "
                     "applies. It simply means no CONDITIONAL dasha's strict rule is met, "
                     "so default to Vimshottari."),
        }
    except Exception as e:
        out["__error__"] = str(e)
    return out


# PyJHora 5.0 renamed `vimsottari.get_vimsottari_dhasa_bhukthi` to the uniform
# `get_dhasa_bhukthi` (same signature, same row format — verified). Resolve tolerantly
# so the engine works on BOTH 4.8.7 and 5.0+ without a hard pin at every call site.
_DASHA_FN_ALIASES = {
    "get_vimsottari_dhasa_bhukthi": ("get_vimsottari_dhasa_bhukthi", "get_dhasa_bhukthi"),
    "get_dhasa_bhukthi": ("get_dhasa_bhukthi", "get_vimsottari_dhasa_bhukthi"),
    # 5.0 also unified ashtottari's bhukthi fn to get_dhasa_bhukthi.
    "get_ashtottari_dhasa_bhukthi": ("get_ashtottari_dhasa_bhukthi", "get_dhasa_bhukthi"),
}


def _resolve_dasha_fn(mod, fn_name):
    """getattr with version-alias fallback (5.0 renamed vimsottari's bhukthi fn).

    ROUND-8 (review P2-2): after the exact name and its explicit aliases fail, try the
    per-module UNIFIED entry-point names 5.x is converging on ('get_dhasa_bhukthi',
    'get_dhasa_antardhasa', 'get_bhukthi', 'get_dhasa'). This only ever resolves a
    function on the SAME system's module, so it stays semantically correct; how the call
    is then argument-adapted remains visible via the existing `_resolution` token map
    (all_dashas.__resolution__ / dasha_decoded), so nothing resolves silently."""
    for cand in _DASHA_FN_ALIASES.get(fn_name, (fn_name,)):
        f = getattr(mod, cand, None)
        if f is not None:
            return f
    for generic in ("get_dhasa_bhukthi", "get_dhasa_antardhasa", "get_bhukthi", "get_dhasa"):
        f = getattr(mod, generic, None)
        if f is not None:
            return f
    return None


def build_dhasa(env, h, jd, place, planet_positions, jd_now=None,
                vims_opts=None, dasha_duration_type=None, running_depth=None):
    """Vimsottari + Yogini + running dasha (decoded to lords + dates).

    ROUND-7 FIX (review C2/C3, PyJHora 5.0): the vimsottari call no longer relies on
    library-global defaults — `dhasa_duration_type` and the CLI-exposed vimsottari
    variations are passed EXPLICITLY, and the effective dasha-year length in days is
    recorded from `drik.dhasa_year_duration()` so any consumer can see which year
    definition produced the timeline (upstream issue #62 — string config values were
    ignored in 4.8.7 — is fixed in 5.0, but explicit enums remain the robust path).
    `running_depth` is now honored here too (was only honored in running_dashas)."""
    from jhora.horoscope.dhasa.graha import vimsottari
    from jhora.horoscope.dhasa.graha import yogini
    out = {}
    def s(fn, *a, **k):
        try: return fn(*a, **k)
        except Exception as e: return {"__error__": str(e)}

    vims_opts = dict(vims_opts or {})
    vims_kw = {k: v for k, v in vims_opts.items() if v is not None}
    if dasha_duration_type is not None:
        vims_kw["dhasa_duration_type"] = dasha_duration_type
    try:
        raw = _resolve_dasha_fn(vimsottari, "get_vimsottari_dhasa_bhukthi")(jd, place, **vims_kw)
        # raw = [ [maha_seq, ant_seq, dur], [ [maha_seq, ant_seq, [y,m,d,hr], dur], ...] ]
        summary = []
        if isinstance(raw, (list, tuple)) and len(raw) == 2 and isinstance(raw[1], (list, tuple)):
            for item in raw[1]:
                try:
                    maha = _vimsottari_name(item[0][0])
                    ant = _vimsottari_name(item[0][1])
                    summary.append({
                        "mahadasha": maha, "antardasha": ant,
                        "start": _date_str(*item[1]),
                        "duration_years": round(float(item[2]), 3),
                    })
                except Exception:
                    pass
        out["vimsottari_timeline"] = summary
        out["vimsottari_start"] = summary[0] if summary else None
    except Exception as e:
        out["vimsottari"] = str(e)

    drik = env["drik"]
    # ROUND-7 (review C2): record the EFFECTIVE dasha-year length actually used by the
    # timeline above, resolved through the same drik function the library uses.
    try:
        _dyd_days = drik.dhasa_year_duration(dhasa_duration_type=dasha_duration_type,
                                             jd=jd, place=place)
        out["dasha_year_duration_effective"] = {
            "days": round(float(_dyd_days), 6),
            "dhasa_duration_type": ({"name": dasha_duration_type.name,
                                     "value": int(dasha_duration_type)}
                                    if hasattr(dasha_duration_type, "name")
                                    else "library default (const.dhasa_year_duration_default)"),
            "passed_explicitly": dasha_duration_type is not None,
            "vimsottari_options": {k: v for k, v in vims_kw.items()
                                   if k != "dhasa_duration_type"} or "all library defaults",
            "note": ("Upstream issue naturalstupid/PyJHora#62 (string config values ignored, "
                     "silent sidereal fallback) affected 4.8.7; fixed in the 5.0 dev line. This "
                     "engine passes the enum EXPLICITLY regardless, so the mode is never ambient."),
        }
    except Exception as e:
        out["dasha_year_duration_effective"] = {"__error__": str(e)}
    try:
        with _quiet():
            out["yogini"] = yogini.get_dhasa_bhukthi(
                drik.Date(h.Date.year, h.Date.month, h.Date.day), _tob(h), place)
    except Exception as e:
        out["yogini"] = str(e)
    # ---- ROUND-12 (review F5): decoded Yogini dasha view -----------------------
    # Raw rows are [lords_tuple, [y, m, d, local_float_hours], duration_years] with
    # lords_tuple = (mahadasha lord id, antardasha lord id). The library supplies
    # lords + durations (yogini.dhasa_adhipathi_list = {Moon:1, Sun:2, Jupiter:3,
    # Mars:4, Mercury:5, Saturn:6, Venus:7, Rahu:8} — the canonical Mangala..Sankata
    # order, 36-year cycle); the classical NAMES are wrapper-provided (web-verified
    # unanimous) since the library ships none.
    try:
        _rawy = out.get("yogini")
        if isinstance(_rawy, (list, tuple)) and _rawy:
            try:
                _ydur = {int(k): int(v) for k, v in
                         yogini.dhasa_adhipathi_list.items()}
            except Exception:
                _ydur = {1: 1, 0: 2, 4: 3, 2: 4, 3: 5, 6: 6, 5: 7, 7: 8}
            _ytab = [{"number": _ydur[lid],
                      "name": YOGINI_NAMES_BY_LORD_ID[lid],
                      "lord": _planet_name(lid), "lord_id": lid,
                      "duration_years": _ydur[lid]}
                     for lid in sorted(_ydur, key=lambda x: _ydur[x])]

            def _ystart_str(st):
                try:
                    hh = int(st[3]); mm = int(round((float(st[3]) - hh) * 60))
                    if mm == 60:
                        hh, mm = hh + 1, 0
                    return "%04d-%02d-%02d %02d:%02d" % (
                        int(st[0]), int(st[1]), int(st[2]), hh, mm)
                except Exception:
                    return str(st)

            _ytl = []
            for _row in _rawy:
                try:
                    _lords, _start, _dur = _row[0], _row[1], _row[2]
                    _lt = (list(_lords) if isinstance(_lords, (list, tuple))
                           else [_lords])
                    _ytl.append({
                        "levels": [{"yogini": YOGINI_NAMES_BY_LORD_ID.get(int(x)),
                                    "lord": _planet_name(int(x))} for x in _lt],
                        "start": _ystart_str(_start),
                        "duration_years": _dur,
                    })
                except Exception:
                    continue
            _cur = None
            if jd_now is not None:
                try:
                    _uu = env["utils"]
                    for _i, _row in enumerate(_rawy):
                        _st = _row[1]
                        _sjd = _uu.julian_day_number(
                            (int(_st[0]), int(_st[1]), int(_st[2])),
                            (int(_st[3]), int((float(_st[3]) % 1) * 60), 0))
                        if _sjd <= jd_now:
                            _cur = _i
                        else:
                            break
                except Exception:
                    _cur = None
            out["yogini_decoded"] = {
                "yoginis": _ytab,
                "cycle_total_years": sum(_ydur.values()),
                "timeline": _ytl,
                "current_index": _cur,
                "current": (_ytl[_cur]
                            if _cur is not None and _cur < len(_ytl) else None),
                "__note__": (
                    "ROUND-12 (review F5): decoded view of the raw dasha.yogini rows "
                    "([lords, start, duration]). The 8 yoginis — Mangala (Moon, 1y), "
                    "Pingala (Sun, 2y), Dhanya (Jupiter, 3y), Bhramari (Mars, 4y), "
                    "Bhadrika (Mercury, 5y), Ulka (Saturn, 6y), Siddha (Venus, 7y), "
                    "Sankata (Rahu, 8y); 36-year cycle — come from the library's own "
                    "yogini.dhasa_adhipathi_list (lords + durations); the classical "
                    "NAMES are wrapper-provided (web-verified, unanimous across "
                    "sources). Lords tuples decode mahadasha first, then antardasha. "
                    "DURATIONS ARE THE LIBRARY'S: this build splits every mahadasha "
                    "into 8 EQUAL antardashas (MD_years/8); some classical texts "
                    "prescribe PROPORTIONAL antardashas (MD × yogini_years/36) — "
                    "disclosed, not recomputed. Yogini is the fast 36-year cycle used "
                    "for quick event timing (Nadi/traditional lineages); vimsottari "
                    "remains the engine's tone authority."),
            }
    except Exception as e:
        out["yogini_decoded"] = {"__error__": str(e)}

    if jd_now:
        try:
            _run_kw = {}
            if running_depth is not None:
                _run_kw["dhasa_level_index"] = int(running_depth)   # ROUND-7 (review C3)
            if dasha_duration_type is not None:
                _run_kw["dhasa_duration_type"] = dasha_duration_type
            raw_r = vimsottari.get_running_dhasa_for_given_date(jd_now, jd, place, **_run_kw)
            levels = []
            for item in raw_r:
                if isinstance(item, (list, tuple)) and item:
                    chain = item[0]
                    start = item[1] if len(item) > 1 else None
                    end = item[2] if len(item) > 2 else None
                    levels.append({
                        "level": len(chain),
                        "lords": [_vimsottari_name(c) for c in chain],
                        "start": _date_str(*start) if start else None,
                        "end": _date_str(*end) if end else None,
                    })
            out["running_vimsottari"] = levels
            out["running_depth"] = max([0] + [l.get("level", 0) for l in levels])
        except Exception as e:
            out["vimsottari_running"] = str(e)
    return out


def _period_tuple_to_jd(t, utils_mod):
    """(y, m, d[, fractional_hours]) -> Julian Day, HONORING the fractional-hour part.

    ROUND-7 FIX (review C4 — confirmed real): PyJHora period tuples carry a 4th element
    `fh` = fractional hours past local midnight (e.g. 18.5 = 18:30 local). The previous
    `_jp` converted with time (0,0,0), silently DISCARDING fh — every fine-dasha window
    boundary (maha-seed clipping, children, calendar rows) was therefore up to ~1 day off,
    and periods starting late in the day could be clipped in/out incorrectly. Values >24h
    (multi-day carry, seen in some vratha/dasha returns) are handled by plain fractional
    addition. 3-tuples (no fh) still mean local midnight, exactly as before."""
    try:
        y, m, d = int(t[0]), int(t[1]), int(t[2])
        fh = 0.0
        if len(t) > 3 and t[3] is not None:
            fh = float(t[3])
        base = utils_mod.julian_day_number((y, m, d), (0, 0, 0))
        return base + fh / 24.0
    except Exception:
        return None


def build_fine_dhasa(env, h, jd, place, jd_now=None, window_start=None, window_end=None,
                     max_depth=4, step_days=1.0):
    """Enumerate a fine Vimsottari sub-period calendar (pratyantardasha L3, sukshma L4, prana L5…)
    for a date window, using PyJHora's exact recursion (vimsottari_immediate_children).

    We seed from the mahadasha/dasha level-1 window boundaries, then recursively expand each
    parent period into its immediate children up to `max_depth`, keeping only those that
    intersect the requested [window_start, window_end]. The output is a flat, time-ordered
    calendar of every sub-period at the requested depth (and coarser rows interpolated so the
    AI always knows the full lord path from mahadasha down to the finest level available).
    """
    from jhora.horoscope.dhasa.graha import vimsottari
    u_ = env["utils"]

    if window_start is None or window_end is None:
        return None

    def _iso2jd(iso):
        try:
            y, mo, d = [int(x) for x in iso[:10].split("-")]
            hh = int(iso[11:13]) if len(iso) > 11 else 0
            mm = int(iso[14:16]) if len(iso) > 14 else 0
            return u_.julian_day_number((y, mo, d), (hh, mm, 0))
        except Exception:
            return None

    def _jd2tuple(jdval):
        y, m, d, fh = u_.jd_to_gregorian(jdval)
        return (int(y), int(m), int(d), float(fh))

    # ROUND-10 FIX (review P1-5, CONFIRMED): normalize the window bounds to the
    # canonical internal "YYYY-MM-DD HH:MM" form BEFORE parsing. Boundary rows used to
    # carry the raw user strings (often date-only "YYYY-MM-DD"), so `calendar.sort` —
    # which sorts on the STRING — mixed 10-char boundary rows among 16-char internal
    # rows ("2026-01-01" sorts before every "2026-01-01 HH:MM" row of the same day).
    # A date-only END now means end-of-day "23:59" (the inclusive reading: a window
    # "…,2026-06-01" covers all of June 1), a date-only START means "00:00".
    window_start = str(window_start).strip()
    window_end = str(window_end).strip()
    if len(window_start) == 10:
        window_start += " 00:00"
    if len(window_end) == 10:
        window_end += " 23:59"
    s_jd = _iso2jd(window_start)
    e_jd = _iso2jd(window_end)
    if s_jd is None or e_jd is None:
        return None

    max_depth = max(2, min(6, int(max_depth)))

    # Seed the tree from EVERY mahadasha that overlaps the window (review §2.9: previously
    # we only expanded the single enclosing mahadasha, so a window spanning a mahadasha
    # boundary silently lost the later one). vimsottari_mahadasa returns {pid: start_jd};
    # we sort by start, derive each period end from the following start, and expand each.
    maha_map = {}
    try:
        maha_map = vimsottari.vimsottari_mahadasa(jd, place)
    except Exception as e:
        return {"__error__": "vimsottari_mahadasa: %s" % (e,)}
    if not maha_map:
        return {"__error__": "no mahadasha map"}
    def _jp(t):
        """(y,m,d[,fh]) -> JD — ROUND-7 C4 fix: delegates to `_period_tuple_to_jd`,
        which honors the fractional-hours element instead of flooring to midnight."""
        return _period_tuple_to_jd(t, u_)

    maha_periods = []
    for pid, start_jd in sorted(maha_map.items(), key=lambda kv: kv[1]):
        try:
            _s = _jd2tuple(float(start_jd))
        except Exception:
            continue
        # ROUND-8 FIX (review P1-6, CONFIRMED): the old `tuple(int(x) for x in _s)`
        # floored the fractional-hours element, throwing away up to 59m59s of every
        # mahadasha seed boundary — which then propagated into
        # vimsottari_immediate_children and every calendar row. Keep fh as float
        # (the Round-7 `_jp`/`_period_tuple_to_jd` fix already honors it downstream).
        maha_periods.append(([int(pid)],
                             (int(_s[0]), int(_s[1]), int(_s[2]), float(_s[3]))))
    for i, (lord, st) in enumerate(maha_periods):
        en = maha_periods[i + 1][1] if i + 1 < len(maha_periods) else None
        maha_periods[i] = (lord, st, en)
    # keep only mahadashas intersecting the window
    seeds = []
    for (maha_path, maha_start, maha_end) in maha_periods:
        _ms = _jp(maha_start)
        _me = _jp(maha_end) if maha_end else e_jd + (365.25 * 200)
        if _ms is None:
            continue
        if _me < s_jd or _ms > e_jd:
            continue
        seeds.append(((maha_path, maha_start, maha_end), _ms, _me))
    if not seeds:
        return {"__error__": "no mahadasha intersects window"}

    acc = []
    def expand(path_lords, start_t, end_t, depth_remaining):
        acc.append({"lords": [_vimsottari_name(c) for c in path_lords],
                    "level": len(path_lords),
                    "start_tuple": list(start_t) if start_t else None,
                    "end_tuple": list(end_t) if end_t else None})
        if depth_remaining <= 1:
            return
        try:
            with _quiet():
                children = vimsottari.vimsottari_immediate_children(
                    list(path_lords), start_t, parent_end=end_t, jd=jd, place=place)
        except Exception:
            children = []
        if not children:
            return
        for child in children:
            if not (isinstance(child, (list, tuple)) and len(child) >= 3):
                continue
            cpath = list(child[0]); cst = child[1]; cen = child[2]
            cst_jd = _jp(cst); cen_jd = _jp(cen)
            if cst_jd is None or cen_jd is None:
                continue
            if cen_jd < s_jd or cst_jd > e_jd:
                continue
            expand(cpath, cst, cen, depth_remaining - 1)

    for (maha_path, maha_start, maha_end), _ms, _me in seeds:
        expand(maha_path, maha_start, maha_end, max_depth)

    # Serialize, dedupe (same lords+start), reorder by (start, deeper-level-first), clip to window.
    calendar = []
    for r in acc:
        st, en = r["start_tuple"], r["end_tuple"]
        _s = _tuple_iso(st); _e = _tuple_iso(en)
        st_jd = _jp(st); en_jd = _jp(en)
        if st_jd is None or en_jd is None:
            continue
        if en_jd < s_jd or st_jd > e_jd:
            continue
        if st_jd < s_jd:
            _s = window_start
        if en_jd > e_jd:
            _e = window_end
        calendar.append({"level": r["level"], "lords": r["lords"],
                         "start": _s, "end": _e})
    calendar.sort(key=lambda r: (r["start"], -r["level"]))
    dedup, seen = [], set()
    for r in calendar:
        k = (tuple(r["lords"]), r["start"])
        if k in seen:
            continue
        seen.add(k)
        dedup.append(r)
    return {"depth": max_depth, "from": window_start, "to": window_end, "calendar": dedup}


def list_ify(x):
    return list(x) if isinstance(x, (list, tuple)) else [x]


def _tuple_iso(t):
    if not t:
        return None
    try:
        return _date_str(int(t[0]), int(t[1]), int(t[2]), float(t[3]))
    except Exception:
        return None


def _decode_entry_pair(v):
    """ROUND-9 (review Enhancement 2): decode the raw return of
    `*_planet_entry_date_divisional_chart` — a [jd, divisional_longitude] pair — into a
    self-describing dict with an ISO-style date, consistent with the formatted dates in
    `next_conjunctions_divisional`. Shape-tolerant: other builds returning a bare JD or an
    unparseable value pass through (or decode minimally) instead of crashing the block."""
    if isinstance(v, (list, tuple)) and len(v) >= 2:
        try:
            jdv = float(v[0])
            lon = float(v[1])
            if 2000000 < jdv < 3000000:
                from jhora import utils as _u
                y, m, d, fh = _u.jd_to_gregorian(jdv)
                return {"jd": jdv,
                        "date": _date_str(int(y), int(m), int(d), float(fh)),
                        "longitude_in_divisional_frame": round(lon, 4),
                        "sign_in_divisional_frame": RAASI_NAMES[int(lon // 30) % 12],
                        "frame_note": ("longitude/sign are in the DIVISIONAL frame "
                                       "(0-360 of the varga chart), not the D1 zodiac")}
        except Exception:
            pass
    if isinstance(v, (int, float)) and not isinstance(v, bool) and 2000000 < float(v) < 3000000:
        try:
            from jhora import utils as _u
            y, m, d, fh = _u.jd_to_gregorian(v)
            return {"jd": float(v), "date": _date_str(int(y), int(m), int(d), float(fh))}
        except Exception:
            pass
    return v


# --------------------------------------------------------------------------- #
# NARAYANA (Jaimini rasi) dasha — computed AND read at the event level.
# --------------------------------------------------------------------------- #
# Sign-period lords are rasis (0=Aries..11=Pisces). Each period's meaning is judged
# by (a) the house that sign occupies relative to the lagna, (b) the sign's lord and
# its own house/lordships, and (c) rashi drishti / argala. We emit a decoded chain
# plus a per-period "event tone" map so the reading is event-rich, not just tone.
HOUSE_THEMES = {
    1: "self, body, identity, new beginnings",
    2: "wealth, speech, family, values, food, early life",
    3: "courage, siblings, effort, communication, short travel, skill",
    4: "home, land, mother, inner peace, education base, property",
    5: "creativity, children, romance, intellect, speculation",
    6: "service, conflict, health, debts, daily work, competition",
    7: "marriage, partnership, business, public dealings",
    8: "transformation, longevity, shared assets, in-laws, occult, sudden events",
    9: "dharma, fortune, guru, higher learning, faith, luck, father",
    10: "career, karma, status, government, public reputation",
    11: "gains, income, networks, fulfilment of goals",
    12: "foreign, loss, expenses, liberation, sleep, behind-scenes",
}
RASHI_DRISHTI_RULES = [
    "All signs aspect the 7th sign; additionally Mars aspects 4th & 8th, and Saturn "
    "aspects 3rd & 10th. Rashi drishti between the period-sign and the lagna/planet-signs "
    "modifies the event: a strong (full) drishti amplifies, a weak one delivers less.",
]


def _house_number_scaled(asc_sign, sign):
    return ((sign - asc_sign) % 12) + 1


def _narayana_sign_tone(asc_sign, sign, natal_longitude):
    """Event-tone for a Narayana (rasi) period: the house the sign occupies from the
    lagna + its sign-lord + the lord's own house + the lord's functional status."""
    ho = ((sign - asc_sign) % 12) + 1
    here = HOUSE_THEMES.get(ho, "")
    lord = SIGN_LORDS[sign] if 0 <= sign < 12 else "?"
    lord_house = None
    if lord in natal_longitude:
        lord_house = ((_sign_of_long(natal_longitude[lord]) - asc_sign) % 12) + 1
    trine = {(asc_sign + 0) % 12, (asc_sign + 4) % 12, (asc_sign + 8) % 12}
    kendra = {(asc_sign + 0) % 12, (asc_sign + 3) % 12, (asc_sign + 6) % 12, (asc_sign + 9) % 12}
    dust = {(asc_sign + 5) % 12, (asc_sign + 7) % 12, (asc_sign + 11) % 12}
    if sign in trine and sign in kendra:
        fn = "yogakaraka (best planet for this lagna)"
    elif sign in trine:
        fn = "functional benefic (trine/5th/9th)"
    elif sign in dust:
        fn = "functional malefic (dusthana 6/8/12)"
    elif sign in kendra:
        fn = "kendra (result-giving)"
    else:
        fn = "neutral"
    return {
        "house_from_lagna": ho,
        "theme": here,
        "sign_lord": lord,
        "lord_house_from_lagna": lord_house,
        "functional_status": fn,
    }


def build_narayana(env, h, jd, place, planet_positions, jd_now=None, max_level=3):
    """Compute and READ the Narayana (Jaimini rasi) dasha.

    - antardasha_timeline: decoded full table {maha_sign, maha_lord, antar_sign,
      antar_lord, start, duration_years}.
    - running: the decoded sign chain to `max_level`, each level with start/end +
      `event_tone` (house-from-lagna + theme + lord + functional status).
    - method / rashi_drishti_rules: so the AI weights it correctly.
    """
    from jhora.horoscope.dhasa.raasi import narayana
    out = {}
    dob = env["drik"].Date(h.Date.year, h.Date.month, h.Date.day)
    tob = _tob(h)
    natal = _planetary_longitude(planet_positions)
    asc_sign = _sign_of_long(natal[LAGNA])
    max_level = max(1, min(6, int(max_level)))

    def _decode_sign(s):
        s = int(s) % 12
        return {"sign_index": s, "sign": RAASI_NAMES[s], "sankrit": RAASI_SANKRIT[s],
                "lord": SIGN_LORDS[s]}

    # 1) full antardasha / mahadasha table (raw: [[maha_sign, antar_sign], [y,m,d,hr], dur])
    try:
        with _quiet():
            table = narayana.narayana_dhasa_for_rasi_chart(dob, tob, place)
        tl = []
        if isinstance(table, (list, tuple)):
            for row in table:
                if isinstance(row, (list, tuple)) and len(row) >= 2:
                    try:
                        maha = _decode_sign(row[0][0]); antar = _decode_sign(row[0][1])
                        tl.append({
                            "maha_sign": maha["sign"], "maha_lord": maha["lord"],
                            "antar_sign": antar["sign"], "antar_lord": antar["lord"],
                            "start": _date_str(int(row[1][0]), int(row[1][1]), int(row[1][2]), float(row[1][3])),
                            "duration_years": round(float(row[2]), 3),
                        })
                    except Exception:
                        pass
        out["antardasha_timeline"] = tl
    except Exception as e:
        out["antardasha_timeline"] = str(e)

    # 2) running sign chain (decoded) at --now, to max_level
    if jd_now:
        try:
            with _quiet():
                rr = narayana.get_running_dhasa_for_given_date(jd_now, jd, place, dhasa_level_index=max_level)
            levels = []
            for row in rr:
                if isinstance(row, (list, tuple)) and row:
                    chain = row[0]
                    decoded = [_decode_sign(s) for s in chain]
                    op = int(chain[-1]) % 12
                    levels.append({
                        "level": len(chain),
                        "signs": [d["sign"] + "(" + d["lord"] + ")" for d in decoded],
                        "sign_indices": [d["sign_index"] for d in decoded],
                        "start": _date_str(int(row[1][0]), int(row[1][1]), int(row[1][2]), float(row[1][3])) if len(row) > 1 else None,
                        "end": _date_str(int(row[2][0]), int(row[2][1]), int(row[2][2]), float(row[2][3])) if len(row) > 2 else None,
                        "event_tone": _narayana_sign_tone(asc_sign, op, natal),
                    })
            out["running"] = levels
            out["running_depth"] = max([0] + [l.get("level", 0) for l in levels])
        except Exception as e:
            out["running"] = str(e)

    # 3) Divisional (D9) Narayana dasha — ROUND-5 addition. PyJHora can run Narayana on a
    #    DIVISIONAL chart (`narayana_dhasa_for_divisional_chart`); D9 is the classic Jaimini
    #    marriage/dharma layer. Rows decode exactly like the rasi-chart table.
    try:
        with _quiet():
            d9 = narayana.narayana_dhasa_for_divisional_chart(
                dob, tob, place, years=1, months=1, sixty_hours=1,
                divisional_chart_factor=9, chart_method=1)
        tl9 = []
        if isinstance(d9, (list, tuple)):
            for row in d9:
                if isinstance(row, (list, tuple)) and len(row) >= 2:
                    try:
                        maha = _decode_sign(row[0][0]); antar = _decode_sign(row[0][1])
                        tl9.append({
                            "maha_sign": maha["sign"], "maha_lord": maha["lord"],
                            "antar_sign": antar["sign"], "antar_lord": antar["lord"],
                            "start": _date_str(int(row[1][0]), int(row[1][1]), int(row[1][2]), float(row[1][3])),
                            "duration_years": round(float(row[2]), 3),
                        })
                    except Exception:
                        pass
        out["divisional_narayana_D9"] = {
            "antardasha_timeline": tl9,
            "note": ("Narayana dasha computed on the D9 (navamsa) chart — the Jaimini "
                     "marriage/dharma layer. Corroborates the D1 Narayana event reading; "
                     "does not replace it."),
        }
    except Exception as e:
        out["divisional_narayana_D9"] = {"__error__": str(e)}

    out["method"] = ("Narayana is a Jaimini RASI dasha: each period is a SIGN, so its "
                     "meaning is the HOUSE that sign occupies from the lagna + the sign's "
                     "lord + rashi drishti. Use it primarily for EVENTS (marriage, job, "
                     "move, gain/loss) and read Vimsottari for emotional TONE; the two "
                     "complement each other.")
    out["rashi_drishti_rules"] = RASHI_DRISHTI_RULES
    return out


def build_rasi_dasha_interpretation(env, h, jd, place, planet_positions):
    """Decode the other Jaimini / rasi dashas (chara, sudasa, shoola, drig,
    kendradhi_rasi) into sign-lords + event-tone so they corroborate the Narayana read."""
    out = {}
    natal = _planetary_longitude(planet_positions)
    asc_sign = _sign_of_long(natal[LAGNA])
    raw = _cached_all_dashas(env, h, jd, place, planet_positions)
    for sys_name in ("chara", "sudasa", "shoola", "drig", "kendradhi_rasi",
                     "karaka_kendraadhi", "lagna_kendraadhi"):
        data = raw.get(sys_name)
        if not isinstance(data, (list, tuple)):
            out[sys_name] = {"__error__": "no usable timeline"}
            continue
        decoded = []
        for row in data:
            try:
                ids = row[0]
                st = None
                if len(row) > 1 and isinstance(row[1], (list, tuple)) and len(row[1]) >= 4:
                    st = _date_str(int(row[1][0]), int(row[1][1]), int(row[1][2]), float(row[1][3]))
                op = int(ids[-1]) % 12
                decoded.append({
                    "chain": [RAASI_NAMES[int(x) % 12] + "(" + SIGN_LORDS[int(x) % 12] + ")" for x in ids],
                    "start": st,
                    "event_tone": _narayana_sign_tone(asc_sign, op, natal),
                })
            except Exception:
                pass
        out[sys_name] = decoded
    out["note"] = ("These rasi dashas (chara/sudasa/shoola/drig/kendradhi) each use a "
                   "different progression, so their period boundaries differ from Narayana. "
                   "Interpret the operative sign as the house it occupies + its lord + rashi "
                   "drishti. Use them to CORROBORATE the Narayana event reading, not as a "
                   "separate authority when they disagree.")
    return out


def build_ashtakavarga(env, h, jd, place, planet_positions):
    utils = env["utils"]
    ashtakavarga = env["ashtakavarga"]
    out = {}
    def s(fn, *a, **k):
        try: return fn(*a, **k)
        except Exception as e: return {"__error__": str(e)}
    # Ashtakavarga is SIGN-based (bindus per sign, independent of lagna), so we must feed
    # the raw sign-based `[pid,[sign,deg]]` list — NOT a house-relative transform
    # (review §2.4; verified empirically: passing the house-relative listform produces a
    # different, wrong binna/samudhaya).
    h2p = utils.get_house_planet_list_from_planet_positions(planet_positions)
    try:
        out["houses"] = h2p
        res = ashtakavarga.get_ashtaka_varga(h2p)
        # res = (binna, samudhaya, prastara)
        out["binna_ashtakavarga"] = res[0]
        out["samudhaya_ashtakavarga"] = res[1]
        out["prastara_ashtakavarga"] = res[2]
        # ROUND-8 (review P3-4): label the bindu arrays by SIGN and by HOUSE so no
        # reader has to do index arithmetic. Sign index 0 = Aries (ashtakavarga is
        # sign-based); houses are whole-sign counted from the lagna.
        try:
            _sav = [int(x) for x in res[1]]
            _lagna_sign = _sign_of_long(_planetary_longitude(planet_positions)[LAGNA])
            out["samudhaya_by_sign"] = {RAASI_NAMES[i]: _sav[i] for i in range(12)}
            out["samudhaya_by_house"] = {("H%d" % hh): _sav[(_lagna_sign + hh - 1) % 12]
                                         for hh in range(1, 13)}
            # ROUND-12 (review Bug-3, REAL — FIXED): row 7 of the BAV grid is the
            # LAGNA's own row (get_ashtaka_varga docstring: "0=Sun..7=Lagnam"), NOT
            # Rahu — Rahu/Ketu have no BAV rows. PLANET_NAMES[7] = "Rahu" mislabeled
            # it in both the FAST and deep builders; BAV_ROW_NAMES is now used.
            out["binna_by_sign"] = {
                (BAV_ROW_NAMES[pi] if pi < len(BAV_ROW_NAMES) else f"Row_{pi}"):
                    {RAASI_NAMES[i]: int(v) for i, v in enumerate(row)}
                for pi, row in enumerate(res[0])}
            out["bindu_labels_note"] = (
                "ROUND-12 (review Bug-3, FIXED): binna rows follow PyJHora's BAV "
                "order (get_ashtaka_varga docstring '0=Sun..7=Lagnam'): index 0 = "
                "Sun, then Moon, Mars, Mercury, Jupiter, Venus, Saturn, and index 7 "
                "= LAGNA — the ascendant's own BAV row. The previous label used "
                "PLANET_NAMES[7] = 'Rahu', mislabeling the Lagna row (Rahu/Ketu have "
                "NO BAV rows). samudhaya index 0 = Aries. *_by_house uses whole-sign "
                "houses counted from the lagna sign.")
        except Exception:
            pass  # labels are decorative; the raw arrays above remain authoritative
    except Exception as e:
        out["__error__"] = str(e)
    return out


CURATED_YOGAS = ["adhi_yoga", "amala_yoga", "anaphaa_yoga", "gaja_kesari_yoga",
                 "guru_mangala_yoga", "chandra_mangala_yoga", "parvata_yoga",
                 "kahala_yoga", "sarala_yoga", "sarpa_yoga", "matsya_yoga",
                 "kalanidhi_yoga", "hara_yoga", "vipareeta_raja_yoga",
                 "asubha_yoga", "lakshmi_yoga",
                 # ROUND-8 (review P3-1): the classical staples the list was missing —
                 # all verified present in PyJHora 5.0's yoga module under THESE exact
                 # spellings (maalavya/sunaphaa/duradhara/saraswathi double-letter forms).
                 "ruchaka_yoga", "bhadra_yoga", "hamsa_yoga", "maalavya_yoga",
                 "sasa_yoga",                      # Pancha Mahapurusha (all five)
                 "kemadruma_yoga",                 # Moon affliction (critical)
                 "sunaphaa_yoga", "duradhara_yoga",  # Moon-hemming yogas (anaphaa already listed)
                 "vesi_yoga", "vosi_yoga", "ubhayachara_yoga",  # Sun-hemming yogas
                 "saraswathi_yoga"]


def _yoga_present(result):
    """Normalize a yoga check to present/absent + details.

    ROUND-15: lists/tuples longer than 1 used to be returned RAW (not a bool).
    JSON then stored e.g. [True, 'Jupiter in 10th'] — some AIs read that as
    present, others as 'not a flag, skip'. The curated `yogas.*` keys keep their
    historical shape (bool / dict / list) for parity; `verdicts.by_name` is the
    single boolean ledger every reader MUST cite.
    """
    if isinstance(result, bool):
        return result
    if isinstance(result, (list, tuple)):
        return bool(result) if len(result) == 1 else result
    if isinstance(result, dict):
        return result
    return result


def _verdict_from_value(val):
    """Coerce any yoga/dosha payload into {present: true|false|null, status, ...}."""
    if val is None:
        return {"present": None, "status": "unknown", "reason": "null"}
    if isinstance(val, dict):
        if "__error__" in val or "__note__" in val and "requires" in str(val.get("__note__", "")).lower() and "present" not in val:
            if "__error__" in val:
                return {"present": None, "status": "error", "error": str(val["__error__"])[:200]}
        if "present" in val:
            p = val["present"]
            if p is None:
                return {"present": None, "status": "unknown"}
            p = bool(p)
            extra = {k: v for k, v in val.items() if k not in ("present",)}
            out = {"present": p, "status": "present" if p else "absent"}
            if extra:
                out["evidence"] = extra
            return out
        if "__error__" in val:
            return {"present": None, "status": "error", "error": str(val["__error__"])[:200]}
        # dict without present key — treat as evidence payload (truthy = present)
        return {"present": True, "status": "present", "evidence": val}
    if isinstance(val, bool):
        return {"present": val, "status": "present" if val else "absent"}
    if isinstance(val, (list, tuple)):
        if not val:
            return {"present": False, "status": "absent"}
        # (bool, summary) doshas_checked shape
        if len(val) >= 1 and isinstance(val[0], bool):
            p = bool(val[0])
            out = {"present": p, "status": "present" if p else "absent"}
            if len(val) > 1 and val[1] is not None:
                out["evidence"] = val[1]
            return out
        return {"present": True, "status": "present", "evidence": list(val)}
    if isinstance(val, str):
        low = val.lower()
        if any(m in val for m in ("__error__", "Traceback", "TypeError:", "ValueError:",
                                  "AttributeError:", "not available")):
            return {"present": None, "status": "error", "error": val[:200]}
        if "there is no" in low or "not present" in low:
            return {"present": False, "status": "absent", "evidence": val[:200]}
        return {"present": None, "status": "unknown", "raw": val[:200]}
    return {"present": bool(val), "status": "present" if val else "absent", "raw": str(val)[:120]}


def _yoga_arg_pool(env, jd, place, planet_positions):
    """ROUND-10 (review P1-2): shared named-argument pool for yoga-predicate binding.

    PyJHora 5.0 re-shaped many yoga predicates to take NAMED inputs
    (planet_positions_rasi, planet_positions_navamsa, is_shukla_paksha,
    is_daytime_birth, gender, jd, place, ...) instead of a single positional
    planet_positions. This builds the pool of high-confidence such inputs;
    `_yoga_bind_kwargs` maps a predicate's signature onto it. Both build_yogas
    (curated) and build_yogas_all (full scan) use these helpers — previously only
    the full scan had the binding, so a future 5.x signature change on a curated
    predicate would surface as an __error__ row (the review's concern; the
    verified status quo is 0/28 curated errors in this build)."""
    pool = {"planet_positions": planet_positions, "chart_1d": planet_positions,
            "planet_positions_rasi": planet_positions, "pp_rasi": planet_positions,
            "jd": jd, "jd_at_dob": jd, "place": place, "place_as_tuple": place}
    # native gender (0=male, 1=female — PyJHora convention) binds gender-aware
    # predicates such as mahabhagya_yoga_from_jd_place (upstream #61 fix).
    pool["gender"] = int(env.get("_gender", 0))
    try:
        with _quiet():
            _d9 = env["charts"].divisional_chart(jd, place, divisional_chart_factor=9)
        if _d9:
            for _k in ("planet_positions_navamsa", "pp_navamsa", "navamsa_chart", "navamsa"):
                pool[_k] = _d9
    except Exception:
        pass
    try:
        _t = env["drik"].tithi(jd, place)
        _ti = int(_t[0]) if isinstance(_t, (list, tuple)) else int(_t)
        pool["is_shukla_paksha"] = bool(1 <= (_ti % 30 or 30) <= 15)
    except Exception:
        pass
    try:
        _drik = env["drik"]
        def _jd_of(x):
            # find the first Julian-Day-range float inside x (5.0 shapes vary:
            # bare jd, (jd, [h,m,s]), [jd, rise, set], ...)
            if isinstance(x, (int, float)) and not isinstance(x, bool) \
                    and 2000000 < float(x) < 3000000:
                return float(x)
            if isinstance(x, (list, tuple)):
                for it in x:
                    v = _jd_of(it)
                    if v is not None:
                        return v
            return None
        with _quiet():
            _srj = _jd_of(getattr(_drik, "sunrise", None) and _drik.sunrise(jd, place))
            _ssj = _jd_of(getattr(_drik, "sunset", None) and _drik.sunset(jd, place))
        # ROUND-12 (review Bug-2): the library's OWN drik.is_night_birth(jd, place)
        # is now the PRIMARY source for the day/night keys (the JD-window comparison
        # stays as fallback). The review's premise — "_srj/_ssj are UTC while jd is
        # local, so afternoon births classify as night" — is FALSE: is_night_birth's
        # own docstring states "drik.sunrise/sunset return local float hours" and
        # utils.jd_to_gregorian(jd) yields LOCAL hours; audit 12b runs the review's
        # claimed-failing case (17:30 IST birth) and it classifies as DAY under BOTH
        # the old comparison and the library function. Library-first is a provenance
        # upgrade, not a bug fix.
        _is_night = None
        try:
            _fn_night = getattr(env["drik"], "is_night_birth", None)
            if _fn_night is not None:
                with _quiet():
                    _is_night = bool(_fn_night(jd, place))
        except Exception:
            _is_night = None
        if _is_night is None and _srj and _ssj:
            _is_night = not bool(_srj <= jd <= _ssj)
        if _is_night is not None:
            pool["is_daytime_birth"] = not _is_night
            # ROUND-11 (review P1-3): night-birth mirror keys — exact complements of
            # is_daytime_birth; no 5.0 yoga predicate consumes them yet (signature
            # scan), but future upstream night-yogas get values with no engine edit.
            pool["is_night_birth"] = _is_night
            pool["is_nighttime_birth"] = _is_night
    except Exception:
        pass
    return pool


def _yoga_bind_kwargs(fn, pool):
    """Bind fn's NAMED parameters from pool → (kwargs, missing_names).

    missing_names lists REQUIRED positional parameters the pool cannot supply —
    the caller decides to skip or record an honest __error__ (never guess)."""
    import inspect as _insp
    kwargs, missing = {}, []
    for pn, p in _insp.signature(fn).parameters.items():
        if pn in pool:
            kwargs[pn] = pool[pn]
        elif p.default is _insp.Parameter.empty and p.kind in (
                _insp.Parameter.POSITIONAL_ONLY,
                _insp.Parameter.POSITIONAL_OR_KEYWORD):
            missing.append(pn)
    return kwargs, missing


def build_yogas(env, h, jd, place, planet_positions):
    yoga = env["yoga"]
    out = {}
    _cap = {"curated_listed": len(CURATED_YOGAS), "discovered": 0, "executed": 0,
            "errored": 0, "present": 0, "absent_in_this_build": []}
    _pool_cache = None
    for name in CURATED_YOGAS:
        fn = getattr(yoga, name + "_from_planet_positions", None)
        if fn is None:
            fn = getattr(yoga, name, None)
        if fn:
            _cap["discovered"] += 1
            try:
                with _quiet():
                    _res = fn(planet_positions)      # fast path (majority of predicates)
                _r = _yoga_present(_res)
            except TypeError:
                # ROUND-10 hardening (review P1-2): 5.0-style NAMED-input signature —
                # bind by parameter name from the shared pool (same strategy as
                # build_yogas_all). Required inputs the pool cannot supply stay an
                # honest __error__ row; nothing is guessed.
                try:
                    if _pool_cache is None:
                        _pool_cache = _yoga_arg_pool(env, jd, place, planet_positions)
                    _kw, _missing = _yoga_bind_kwargs(fn, _pool_cache)
                    if _missing:
                        _r = {"__error__": "requires unavailable inputs: "
                              + ",".join(_missing[:4])}
                    else:
                        with _quiet():
                            _r = _yoga_present(fn(**_kw))
                except Exception as e2:
                    _r = {"__error__": str(e2)}
            except Exception as e:
                _r = {"__error__": str(e)}
            out[name] = _r
            if isinstance(_r, dict) and "__error__" in _r:
                _cap["errored"] += 1
            else:
                _cap["executed"] += 1
                if _r:
                    _cap["present"] += 1
        else:
            _cap["absent_in_this_build"].append(name)
    # ROUND-8 (review P3-1): classical yogas PyJHora exposes NO predicate for — computed
    # by the wrapper from the chart's own longitudes (deterministic sign/house arithmetic,
    # same policy as core's sign_lord/dignity fields). Definitions are inlined per entry
    # so the reader can reproduce every call from `core`.
    try:
        longs = _planetary_longitude(planet_positions)
        comp = {}
        sun_sign = _sign_of_long(longs["Sun"])
        merc_sign = _sign_of_long(longs["Mercury"])
        comp["budhaditya_yoga"] = {
            "present": sun_sign == merc_sign,
            "definition": ("Sun and Mercury in the same sign (classical Budhaditya). "
                           "Combustion of Mercury is reported separately in planet_flags."),
            "sun_sign": RAASI_NAMES[sun_sign], "mercury_sign": RAASI_NAMES[merc_sign]}
        lagna_sign = _sign_of_long(longs[LAGNA])
        moon_sign = _sign_of_long(longs["Moon"])
        NAT_BENEFICS = ("Jupiter", "Venus", "Mercury")   # Mercury conditional in strict
        NAT_MALEFICS = ("Sun", "Mars", "Saturn", "Rahu", "Ketu")  # texts; noted below
        def _occ(ref_sign, offset):
            sg = (ref_sign + offset) % 12
            return [p for p in PLANET_ORDER if p in longs and _sign_of_long(longs[p]) == sg]
        for ref_name, ref_sign in (("from_lagna", lagna_sign), ("from_moon", moon_sign)):
            h2, h12 = _occ(ref_sign, 1), _occ(ref_sign, -1)
            comp["subha_kartari_" + ref_name] = {
                "present": bool(h2) and bool(h12) and
                           all(p in NAT_BENEFICS for p in h2 + h12),
                "definition": ("natural benefics (Jupiter/Venus/Mercury) in BOTH the 2nd and "
                               "12th %s, with no natural malefic in either — the reference is "
                               "hemmed by benefics" % ref_name.replace("_", " ")),
                "house2_occupants": h2, "house12_occupants": h12}
            comp["paapa_kartari_" + ref_name] = {
                "present": bool(h2) and bool(h12) and
                           all(p in NAT_MALEFICS for p in h2 + h12),
                "definition": ("natural malefics (Sun/Mars/Saturn/Rahu/Ketu) in BOTH the 2nd "
                               "and 12th %s — the reference is hemmed by malefics"
                               % ref_name.replace("_", " ")),
                "house2_occupants": h2, "house12_occupants": h12}
        comp["__note__"] = ("wrapper-computed (no PyJHora predicate exists for these); "
                            "natural-benefic list treats Mercury as benefic unconditionally "
                            "and the nodes as malefics — strict texts make Mercury "
                            "conditional on associates; read house*_occupants to judge.")
        out["computed_yogas"] = comp
        _cap["computed_by_wrapper"] = len([k for k in comp if not k.startswith("__")])
    except Exception as e:
        out["computed_yogas"] = {"__error__": str(e)}
    # ROUND-7 (review tail): explicit capability report for the curated yoga scan.
    out["__capability__"] = _cap
    return out


def build_yogas_all(env, h, jd, place, planet_positions):
    """Dynamically discover and run every PyJHora `*_from_planet_positions` yoga predicate.

    PyJHora exposes ~240 yoga-predicate functions. This runs each and reports ONLY those
    that are present (truthy), plus a count of how many were checked, so the output stays
    compact and readable while giving near-complete yoga coverage. Opt-in (`yogas_all`).
    """
    yoga = env["yoga"]
    out = {}
    present = {}
    absent = []
    checked = 0
    errors = 0
    error_examples = []
    skipped = 0
    skipped_examples = []
    # ROUND-7 / PyJHora 5.0: many yoga predicates changed signature — they now want
    # NAMED inputs (planet_positions_rasi, planet_positions_navamsa, is_shukla_paksha,
    # is_daytime_birth, ...) instead of a single positional planet_positions. We bind by
    # PARAMETER NAME from a pool of high-confidence inputs; predicates needing inputs we
    # cannot supply are counted `skipped_requires_unavailable_inputs` (with the missing
    # names listed) rather than guessed at — same philosophy as the dasha adapters.
    # ROUND-10 (review P1-2): the pool construction moved into the shared
    # `_yoga_arg_pool` helper so build_yogas (curated) and build_yogas_all bind
    # named-input predicates identically. Behaviour is unchanged here.
    _pool = _yoga_arg_pool(env, jd, place, planet_positions)
    for fn_name in dir(yoga):
        if not fn_name.endswith("_from_planet_positions"):
            continue
        fn = getattr(yoga, fn_name, None)
        if not callable(fn):
            continue
        checked += 1
        base = fn_name.replace("_from_planet_positions", "")
        res = None
        try:
            with _quiet():
                res = fn(planet_positions)          # fast path (majority of predicates)
        except TypeError:
            # signature changed in 5.0 — bind by parameter name from the pool
            # (ROUND-10: shared _yoga_bind_kwargs helper — identical binding to
            # build_yogas' curated loop)
            try:
                kwargs, missing = _yoga_bind_kwargs(fn, _pool)
                if missing:
                    skipped += 1
                    if len(skipped_examples) < 25:
                        skipped_examples.append([base, "requires unavailable inputs: "
                                                 + ",".join(missing[:4])])
                    continue
                with _quiet():
                    res = fn(**kwargs)
            except Exception as e2:
                errors += 1
                if len(error_examples) < 30:
                    error_examples.append([base, str(e2)[:100]])
                continue
        except Exception as e:
            errors += 1
            if len(error_examples) < 30:
                error_examples.append([base, str(e)[:100]])
            continue
        try:
            if isinstance(res, bool):
                if res:
                    present[base] = True
                else:
                    absent.append(base)
            elif res:
                present[base] = _yoga_present(res)
            else:
                absent.append(base)
        except Exception:
            pass
    out["yogas_present"] = present
    out["yogas_absent"] = absent
    out["counts"] = {"checked": checked, "present": len(present), "absent": len(absent),
                     "errors": errors,
                     "skipped_requires_unavailable_inputs": skipped}
    out["error_examples"] = error_examples
    out["skipped_examples"] = skipped_examples
    out["__capability__"] = {
        "note": ("ROUND-7 capability report: every *_from_planet_positions predicate in the "
                 "installed PyJHora was DISCOVERED (checked) and either EXECUTED via its exact "
                 "signature (5.0 predicates bind named inputs from a verified pool: D1/D9 "
                 "positions, tithi-paksha, day/night) or SKIPPED with the missing input names "
                 "listed. Nothing is guessed; skipped/errors are enumerated, never hidden."),
        "discovered_and_called": checked,
        "executed": checked - skipped - errors,
        "skipped_requires_unavailable_inputs": skipped,
        "errored": errors,
        "present_in_this_chart": len(present),
        "absent_in_this_chart": len(absent),
        "round15_note": ("yogas_absent lists every executed predicate that returned false/empty — missing from yogas_present is NOT the same as unevaluated."),
    }
    return out


def build_raja_yogas(env, h, jd, place, planet_positions):
    raja_yoga = env["raja_yoga"]
    out = {}
    def s(fn, *a, **k):
        try: return fn(*a, **k)
        except Exception as e: return {"__error__": str(e)}
    ry = raja_yoga
    try:
        with _quiet():
            out["raja_yoga_details"] = ry.get_raja_yoga_details(jd, place)
    except Exception as e:
        out["raja_yoga_details"] = str(e)
    try:
        out["raja_yoga_pairs"] = s(ry.get_raja_yoga_pairs_from_planet_positions, planet_positions)
    except Exception as e:
        out["raja_yoga_pairs"] = str(e)
    # ---- ROUND-5: the named raja-yoga checks, explicit and decoded -------------
    # `get_raja_yoga_pairs(house_to_planet_list)` gives the kendra×trikona lord PAIRS
    # (planet ids); each pair is then tested for the three famous raja-yoga types.
    try:
        utils = env["utils"]
        with _quiet():
            h2p = utils.get_house_planet_list_from_planet_positions(planet_positions)
            pairs = ry.get_raja_yoga_pairs(h2p)
        decoded_pairs = []
        for pr in pairs or []:
            try:
                p1, p2 = int(pr[0]), int(pr[1])
                row = {"planets": [_decode_planet_id(p1), _decode_planet_id(p2)]}
                for lab, fn in (("neecha_bhanga_raja_yoga",
                                 getattr(ry, "neecha_bhanga_raja_yoga_from_planet_positions", None)),
                                ("vipareetha_raja_yoga",
                                 getattr(ry, "vipareetha_raja_yoga_from_planet_positions", None)),
                                ("dharma_karmadhipati_raja_yoga",
                                 getattr(ry, "dharma_karmadhipati_raja_yoga_from_planet_positions", None))):
                    if fn is None:
                        continue
                    try:
                        with _quiet():
                            r = fn(planet_positions, p1, p2)
                        if isinstance(r, tuple) and len(r) == 2:
                            row[lab] = {"present": bool(r[0]), "detail": r[1]}
                        else:
                            row[lab] = {"present": bool(r), "detail": None}
                    except Exception as e2:
                        row[lab] = {"__error__": str(e2)}
                decoded_pairs.append(row)
            except Exception:
                decoded_pairs.append(pr)
        out["raja_yoga_pair_checks"] = decoded_pairs
    except Exception as e:
        out["raja_yoga_pair_checks"] = str(e)
    # the three "other raja yoga" checks (jd, place based). #2 is internally broken in
    # this build (TypeError on int membership) — guarded, never hidden.
    for i in (1, 2, 3):
        fn = getattr(ry, f"check_other_raja_yoga_{i}", None)
        if fn is None:
            continue
        try:
            with _quiet():
                out[f"other_raja_yoga_{i}"] = fn(jd, place)
        except Exception as e:
            out[f"other_raja_yoga_{i}"] = {"__error__": f"{e} (PyJHora-internal in this build)"}
    # raja-yoga pairs across ALL divisional charts — extract only the pair-summary lines
    # (the full return embeds pages of book text; we keep the evidence, not the prose).
    try:
        with _quiet():
            _all = ry.get_raja_yoga_details_for_all_charts(jd, place)
        # this build returns (results_dict, n_yoga_categories_with_results, n_chart_scans)
        _counts = None
        if isinstance(_all, (tuple, list)) and _all and isinstance(_all[0], dict):
            _counts = [x for x in _all[1:] if isinstance(x, int)]
            _all = _all[0]
        summary = {}
        if isinstance(_all, dict):
            for yoga_name, rows in _all.items():
                try:
                    hits = []
                    for r in rows or []:
                        txt = str(r)
                        if "Raja Yoga Pairs" in txt or "Pairs:" in txt:
                            hits.append(_strip_html(txt).strip())
                    if hits:
                        summary[str(yoga_name)] = hits
                except Exception:
                    continue
        if _counts and len(_counts) >= 2:
            out["raja_yogas_all_charts_scan_info"] = {
                "yoga_categories_with_results": _counts[0],
                "total_chart_scans": _counts[1],
            }
        out["raja_yogas_all_charts_pairs"] = summary
        out["raja_yogas_all_charts_note"] = (
            "Pair-summary lines from get_raja_yoga_details_for_all_charts (D1..D12 scan). "
            "Empty dict = no raja-yoga pairs found in any divisional chart.")
    except Exception as e:
        out["raja_yogas_all_charts_pairs"] = str(e)
    return out


def build_transits(env, h, jd, place, natal_planet_positions, transit_jd,
                   compute_conjunctions=True, compute_events=True):
    """Transit positions of the 9 grahas at transit_jd vs natal (signs, houses, sarvashtakavarga).

    `compute_conjunctions` toggles the (expensive) all-pair next-conjunction search.
    `compute_events` toggles the per-planet retrograde/stationary/entry event-date search.
    """
    _compute_conjunctions = compute_conjunctions
    _compute_events = compute_events
    utils = env["utils"]
    charts = env["charts"]
    try:
        transits = charts.rasi_chart(transit_jd, place)
    except Exception as e:
        return {"__error__": str(e)}
    trans_long = _planetary_longitude(transits)
    natal_long = _planetary_longitude(natal_planet_positions)
    asc_sign = _sign_of_long(natal_long[LAGNA])

    # sarva ashtakavarga (samudaya) bindus per sign, if computable
    sava = None
    prastara = None
    try:
        h2p = utils.get_house_planet_list_from_planet_positions(natal_planet_positions)
        res = env["ashtakavarga"].get_ashtaka_varga(h2p)
        sava = res[1]   # samudhaya list per sign
        try:
            # ROUND-11 (review P2-4): keep the natal PRASTARA (per-planet BAV grid) for
            # the kakshya transit rows — prastara[receiver][contributor][sign] with ids
            # 0=Sun,1=Moon,2=Mars,3=Mercury,4=Jupiter,5=Venus,6=Saturn,7=Lagna.
            prastara = res[2]
        except Exception:
            prastara = None
    except Exception:
        sava = None

    # convert transit_jd to a readable date (JD 2440587.5 = 1970-01-01)
    try:
        from jhora import utils as _u
        _y, _m, _d, _hh = _u.jd_to_gregorian(transit_jd)
        out = {"date": f"{int(_y):04d}-{int(_m):02d}-{int(_d):02d}"}
    except Exception:
        out = {"date": None}
    # ---- ROUND-8 (review P3-5): critical GOCHARA classifications relative to the
    # ---- NATAL MOON (Saturn: sade-sati phases / ashtama / kantaka; Jupiter: auspicious
    # ---- houses). Pure sign arithmetic from values already in this section — the
    # ---- conventions are inlined and the raw house number stays authoritative.
    try:
        moon_sign = _sign_of_long(natal_long["Moon"])
        sat_sign = _sign_of_long(trans_long["Saturn"])
        jup_sign = _sign_of_long(trans_long["Jupiter"])
        sat_h = _house_number(moon_sign, sat_sign)   # 1-based from the natal Moon
        jup_h = _house_number(moon_sign, jup_sign)
        SADE = {12: "phase_1_rising (Saturn in the 12th from the natal Moon)",
                1: "phase_2_peak (Saturn in the 1st from the natal Moon — over the Moon)",
                2: "phase_3_setting (Saturn in the 2nd from the natal Moon)"}
        out["saturn_gochara_from_natal_moon"] = {
            "natal_moon_sign": RAASI_NAMES[moon_sign],
            "transit_saturn_sign": RAASI_NAMES[sat_sign],
            "house_from_natal_moon": sat_h,
            "sade_sati": sat_h in (12, 1, 2),
            "sade_sati_phase": SADE.get(sat_h),
            "ashtama_shani": sat_h == 8,
            "kantaka_shani": sat_h in (4, 7, 10),
            # ROUND-9 FIX (review Issue 5, CONFIRMED real): Ardhashtama Shani is the 4th
            # from the natal Moon ("half of the 8th"; it coincides with Kantaka), NOT the
            # 5th. The 5th carries its own factual flag (Panchama Shani).
            "ardhashtama_shani_4th_from_moon": sat_h == 4,
            "panchama_shani_5th_from_moon": sat_h == 5,
            "conventions": ("Sade Sati = Saturn transiting the 12th/1st/2nd from the NATAL "
                            "MOON (rising/peak/setting phases). Ashtama Shani = 8th. Kantaka "
                            "(thorn) = 4th/7th/10th per common usage. Ardhashtama ('half of "
                            "the 8th') = 4th from the natal Moon — it coincides with Kantaka "
                            "and is classically rated about half as severe as Ashtama. "
                            "Saturn in the 5th (Panchama) is flagged separately and is not "
                            "ardhashtama. house_from_natal_moon is the authoritative number."),
        }
        out["jupiter_gochara_from_natal_moon"] = {
            "natal_moon_sign": RAASI_NAMES[moon_sign],
            "transit_jupiter_sign": RAASI_NAMES[jup_sign],
            "house_from_natal_moon": jup_h,
            "auspicious_house": jup_h in (2, 5, 7, 9, 11),
            "conventions": ("Jupiter transiting the 2nd/5th/7th/9th/11th from the natal Moon "
                            "is conventionally auspicious (some texts add the 4th); the "
                            "8th/12th are conventionally difficult."),
        }
    except Exception as e:
        out["saturn_gochara_from_natal_moon"] = {"__error__": str(e)}
    rows = []
    for p in PLANET_ORDER:
        tl = trans_long.get(p); nl = natal_long.get(p)
        if tl is None or nl is None:
            continue
        ts = _sign_of_long(tl); ns = _sign_of_long(nl)
        row = {
            "planet": p,
            "transit_sign": RAASI_NAMES[ts],
            "transit_sign_index": ts,
            "transit_house_from_lagna": _house_number(asc_sign, ts),
            "transit_house_from_moon": (_house_number(_sign_of_long(natal_long["Moon"]), ts)),
            "natal_sign": RAASI_NAMES[ns],
            "natal_house": _house_number(asc_sign, ns),
        }
        if sava:
            row["sarvashtakavarga_bindus_at_transit_sign"] = int(sava[ts])
        # ROUND-11 (review P2-4): KAKSHYA (sub-sign) transit detail — see
        # kakshya_transits_note below. Nodes carry no BAV row and are excluded.
        if prastara and p in KAKSHYA_ROW_IDS:
            try:
                _kd = _deg_in_sign(tl)
                _ki = min(7, int(_kd / KAKSHYA_SPAN_DEG))
                _kl = KAKSHYA_LORDS[_ki]
                _bindu = int(prastara[KAKSHYA_ROW_IDS[p]][KAKSHYA_ROW_IDS[_kl]][ts])
                row["kakshya"] = {
                    "kakshya_number": _ki + 1,
                    "span_in_sign_deg": [round(_ki * KAKSHYA_SPAN_DEG, 4),
                                         round((_ki + 1) * KAKSHYA_SPAN_DEG, 4)],
                    "kakshya_lord": _kl,
                    "natal_bindu_from_kakshya_lord_at_transit_sign": _bindu,
                    "favorable": bool(_bindu == 1),
                }
            except Exception as _ke:
                row["kakshya"] = {"__error__": str(_ke)}
        # ROUND-5: motion state of the planet ON THE TRANSIT DATE (glyph-normalized)
        try:
            with _quiet():
                _ss = env["drik"].get_planet_speed_sign(transit_jd, place, _planet_id(p))
            row["motion_at_transit_date"] = _motion_word(_ss)
        except Exception:
            row["motion_at_transit_date"] = "__error__"
        rows.append(row)
    out["transits"] = rows
    out["kakshya_transits_note"] = (
        "ROUND-11 (review P2-4): each transits[] row for Sun..Saturn carries a "
        "kakshya block — the 3d45m sub-sign division of the transit sign, whose "
        "lords from 0 deg are Saturn, Jupiter, Mars, Sun, Venus, Mercury, Moon, "
        "Lagna. Classical rule: the kakshya is FAVORABLE for the transiting planet "
        "iff the kakshya lord contributed a bindu to that sign in the transiting "
        "planet's OWN natal prastara/BAV row "
        "(natal_bindu_from_kakshya_lord_at_transit_sign = 1). Rahu/Ketu rows have no "
        "kakshya block — the nodes have no BAV row. Wrapper-computed (no PyJHora "
        "kakshya API exists); doctrine web-verified across five independent sources.")
    # ROUND-5: latta stars per natal planet (charts.lattha_stars_planets). When the
    # transiting Moon crosses a planet's latta nakshatra, that planet's transit results
    # are vitiated — a classical gochara filter. Values are nakshatra numbers (1-based,
    # Abhijit included as 21st when applicable) -> decoded to names.
    try:
        with _quiet():
            _ls = charts.lattha_stars_planets(natal_planet_positions)
        _stars28 = None
        try:
            _stars28 = env["utils"].get_nakshathra_list_with_abhijith()
        except Exception:
            _stars28 = None
        def _nak_name(n):
            # The 28-star list (Abhijit included as the 21st star, i.e. 0-based index 20,
            # right after Purva Ashadha) is Tamil-named in the library; translate by
            # POSITION to the engine's standard NAKSHATRAS vocabulary. Verified against
            # utils.get_nakshathra_list_with_abhijith() in this build.
            n = int(n)
            if _stars28 and 1 <= n <= len(_stars28):
                i = n - 1
                if i < 20:
                    return NAKSHATRAS[i]
                if i == 20:
                    return "Abhijit"
                return NAKSHATRAS[i - 1]
            return NAKSHATRAS[(n - 1) % 27]
        dec = {}
        for i, pair in enumerate(_ls or []):
            if i >= len(PLANET_ORDER):
                break
            try:
                dec[PLANET_ORDER[i]] = {"latta_star_1": _nak_name(pair[0]),
                                        "latta_star_2": _nak_name(pair[1]),
                                        "raw_indices": [int(pair[0]), int(pair[1])]}
            except Exception:
                continue
        out["latta_stars"] = dec
        out["latta_stars_note"] = ("Per natal planet: the two nakshatras whose lunar transit "
                                   "afflicts that planet (classical latta-star rule).")
    except Exception as e:
        out["latta_stars"] = str(e)
    # Also report any retrograde / combustion among transiting planets.
    # Retrograde uses drik.planets_in_retrograde (PyJHora's current location); the chart-layer
    # planets_in_retrograde is stale (returns [] on retro charts — verified 2025-03-01).
    try:
        with _quiet():
            _tr = env["drik"].planets_in_retrograde(transit_jd, place)
        out["transit_retrograde"] = _decode_planet_id_list(_tr)
    except Exception:
        try:
            out["transit_retrograde"] = _decode_planet_id_list(charts.planets_in_retrograde(transits))
        except Exception as e:
            out["transit_retrograde"] = str(e)
    try:
        out["transit_combust"] = _decode_planet_id_list(charts.planets_in_combustion(transits))
    except Exception as e:
        out["transit_combust"] = str(e)
    # ---- Transit events: planetary state + upcoming/previous entry & station dates ----
    drik = env["drik"]
    def ev(field, fn, *a, **k):
        if not fn:
            return
        try:
            with _quiet():
                out[field] = fn(*a, **k)
        except Exception as e:
            out[field] = str(e)
    ev("planets_in_retrograde", getattr(drik, "planets_in_retrograde", None), transit_jd, place)
    ev("planets_in_stationary", getattr(drik, "planets_in_stationary", None), transit_jd, place)
    ev("planets_speed_info", getattr(drik, "planets_speed_info", None), transit_jd, place)
    if _compute_events:
        ev("planet_retrograde_dates", None)  # filled per-planet below
        retro = {}
        ddate = getattr(drik, "Date", None)
        tdate = ddate(*_jd_to_ymd(transit_jd)) if ddate else None
        for p in PLANET_ORDER:
            pid = _planet_id(p)
            if pid is None:
                continue
            for field, fn, args in (
                    ("next_retrograde_change", getattr(drik, "next_planet_retrograde_change_date", None),
                     (pid, tdate, place)),
                    ("stationary_duration", getattr(drik, "next_planet_stationary_duration", None),
                     (pid, transit_jd, place))):
                try:
                    with _quiet():
                        retro.setdefault(p, {})[field] = fn(*args)
                except Exception as e:
                    retro.setdefault(p, {})[field] = str(e)
            for field, fn in (("next_entry", getattr(drik, "next_planet_entry_date", None)),
                              ("prev_entry", getattr(drik, "previous_planet_entry_date", None))):
                try:
                    with _quiet():
                        retro.setdefault(p, {})[field] = fn(pid, transit_jd, place)[0] if fn else None
                except Exception as e:
                    retro.setdefault(p, {})[field] = str(e)
        out["transit_events"] = retro
        # ascendant events
        ev("next_ascendant_entry", getattr(drik, "next_ascendant_entry_date", None), transit_jd, place)
        ev("prev_ascendant_entry", getattr(drik, "previous_ascendant_entry_date", None), transit_jd, place)
    conj = {}
    if _compute_conjunctions:
        for i in range(len(PLANET_ORDER)):
            for j in range(i + 1, len(PLANET_ORDER)):
                a, b = _planet_id(PLANET_ORDER[i]), _planet_id(PLANET_ORDER[j])
                if a is None or b is None:
                    continue
                if {PLANET_ORDER[i], PLANET_ORDER[j]} == {"Rahu", "Ketu"}:
                    conj["Rahu_Ketu"] = {"skipped": True,
                                         "reason": "Rahu and Ketu do not conjoin"}
                    continue
                try:
                    with _quiet():
                        _nc = drik.next_conjunction_of_planet_pair(transit_jd, place, a, b)
                    if _nc is None:
                        conj[f"{PLANET_ORDER[i]}_{PLANET_ORDER[j]}"] = {"skipped": True,
                                                                       "reason": "library returned None"}
                    else:
                        conj[f"{PLANET_ORDER[i]}_{PLANET_ORDER[j]}"] = _decode_when(_nc)
                except Exception as e:
                    conj[f"{PLANET_ORDER[i]}_{PLANET_ORDER[j]}"] = {"__error__": str(e)}
        out["next_conjunctions"] = conj
        # ROUND-17: previous conjunctions (mirror of next_conjunctions). Rahu-Ketu
        # never conjoin — the library warns and we skip that pair.
        prevc = {}
        for i in range(len(PLANET_ORDER)):
            for j in range(i + 1, len(PLANET_ORDER)):
                a, b = _planet_id(PLANET_ORDER[i]), _planet_id(PLANET_ORDER[j])
                if a is None or b is None:
                    continue
                if {PLANET_ORDER[i], PLANET_ORDER[j]} == {"Rahu", "Ketu"}:
                    prevc["Rahu_Ketu"] = {"skipped": True,
                                          "reason": "Rahu and Ketu do not conjoin"}
                    continue
                try:
                    with _quiet():
                        _r = drik.previous_conjunction_of_planet_pair(
                            transit_jd, place, a, b)
                    prevc[f"{PLANET_ORDER[i]}_{PLANET_ORDER[j]}"] = _decode_when(_r)
                except Exception as e:
                    prevc[f"{PLANET_ORDER[i]}_{PLANET_ORDER[j]}"] = {"__error__": str(e)}
        out["previous_conjunctions"] = prevc
        # next D9 sign-entry of each graha
        try:
            _d9e = {}
            for p in PLANET_ORDER:
                pid = _planet_id(p)
                if pid is None:
                    continue
                try:
                    with _quiet():
                        _r = env["charts"].next_planet_entry_date_divisional_chart(
                            transit_jd, place, pid, divisional_chart_factor=9)
                    _d9e[p] = _decode_when(_r)
                except Exception as e:
                    _d9e[p] = {"__error__": str(e)}
            out["next_D9_sign_entry"] = {
                "by_planet": _d9e,
                "__note__": ("ROUND-17: charts.next_planet_entry_date_divisional_chart "
                             "factor=9 — next time each graha changes SIGN in Navamsa.")}
        except Exception as e:
            out["next_D9_sign_entry"] = {"__error__": str(e)}
        try:
            _d9p = {}
            for p in PLANET_ORDER:
                pid = _planet_id(p)
                if pid is None:
                    continue
                try:
                    with _quiet():
                        _r = env["charts"].previous_planet_entry_date_divisional_chart(
                            transit_jd, place, pid, divisional_chart_factor=9)
                    _d9p[p] = _decode_when(_r)
                except Exception as e:
                    _d9p[p] = {"__error__": str(e)}
            out["previous_D9_sign_entry"] = {
                "by_planet": _d9p,
                "__note__": "ROUND-18: charts.previous_planet_entry_date_divisional_chart factor=9."}
        except Exception as e:
            out["previous_D9_sign_entry"] = {"__error__": str(e)}
    return out


# --------------------------------------------------------------------------- #
# Extended builders — expose nearly every PyJHora subsystem.
# Each is wrapped so a failure becomes an "__error__" field, never a hard stop.
# --------------------------------------------------------------------------- #

def _dob(h):
    return (h.Date.year, h.Date.month, h.Date.day)


def _tob(h):
    t = getattr(h, "birth_time", None)
    # ROUND-8 hardening (review P0-3): PyJHora's info.Horoscope parses a STRING birth
    # time into an int tuple itself (info.py), so tuples are the norm — but parse
    # strings defensively in case a future build stores the raw string. When nothing
    # is parseable we return None (callers already handle it): we deliberately do NOT
    # default to noon — a silently wrong birth time is worse than an explicit None.
    if isinstance(t, str):
        try:
            parts = [int(float(x)) for x in t.strip().replace(":", " ").split()][:3]
            while len(parts) < 3:
                parts.append(0)
            return tuple(parts)
        except Exception:
            return None
    if isinstance(t, (tuple, list)) and len(t) >= 3:
        return (int(t[0]), int(t[1]), int(t[2]))
    if isinstance(t, (tuple, list)) and len(t) >= 2:
        return (int(t[0]), int(t[1]), 0)
    return None


def _decode_sphuta(res):
    """ROUND-9 (review Enhancement 3): the sphuta functions return a raw
    [sign_index, degree_in_sign] pair — decode it to the engine's standard structured
    form so downstream readers never have to know the tuple convention.

    ROUND-10 (review P1-4): the claim that this build's sphutas return other shapes was
    VERIFIED FALSE — all 12 public functions in horoscope/chart/sphuta.py return
    `drik.dasavarga_from_long(...)` = (sign_index, float degree-in-sign), which the pair
    branch below already handles (Round-9 artifacts decode correctly). Two shapes are
    nonetheless ACCEPTED defensively so a future upstream reshape degrades gracefully:
      * bare scalar 0..360      → treated as ABSOLUTE sidereal longitude
      * [sign, (deg, min, sec)] → DMS second element converted to decimal degrees
    Neither shape occurs in PyJHora 5.0; this is hardening, not a bug fix."""
    # scalar absolute-longitude shape (defensive; not produced by this build)
    if isinstance(res, (int, float)) and not isinstance(res, bool) and 0.0 <= float(res) < 360.0:
        try:
            x = float(res)
            si = int(x // 30) % 12
            return {"sign_index": si, "sign": RAASI_NAMES[si],
                    "sankrit": RAASI_SANKRIT[si],
                    "degree_in_sign": round(x - si * 30.0, 4),
                    "longitude": round(x, 4),
                    "decoded_from": "scalar_longitude"}
        except Exception:
            return res
    if isinstance(res, (list, tuple)) and len(res) == 2:
        try:
            si = int(res[0]) % 12
            second = res[1]
            # (deg, min, sec) sub-shape (defensive; not produced by this build)
            if isinstance(second, (list, tuple)) and len(second) >= 2:
                parts = [float(p) for p in second[:3]]
                while len(parts) < 3:
                    parts.append(0.0)
                deg = parts[0] + parts[1] / 60.0 + parts[2] / 3600.0
            else:
                deg = float(second)
            return {"sign_index": si, "sign": RAASI_NAMES[si],
                    "sankrit": RAASI_SANKRIT[si],
                    "degree_in_sign": round(deg, 4),
                    "longitude": round(si * 30.0 + deg, 4)}
        except Exception:
            return res
    return res


def _decode_saham(value):
    """ROUND-9 (review Enhancement 3): parse a saham position string like
    '♎︎Libra 5° 20' 13"' into {sign, sign_index, degree_in_sign, longitude, raw}.
    Unparseable strings keep the raw text with a note — never invented numbers."""
    import re as _re
    raw = _strip_html(str(value))
    si = None
    for i, nm in enumerate(RAASI_NAMES):
        if nm in raw:
            si = i
            break
    deg = None
    m = _re.search(r"(\d+(?:\.\d+)?)\s*°\s*(\d+(?:\.\d+)?)\s*['\u2019\u2032]\s*(\d+(?:\.\d+)?)", raw)
    if m:
        deg = float(m.group(1)) + float(m.group(2)) / 60.0 + float(m.group(3)) / 3600.0
    else:
        m2 = _re.search(r"(\d+(?:\.\d+)?)\s*°\s*(\d+(?:\.\d+)?)", raw)
        if m2:
            deg = float(m2.group(1)) + float(m2.group(2)) / 60.0
    if si is None or deg is None:
        return {"raw": raw, "__note__": "unparsed position string — quote the raw text"}
    return {"sign": RAASI_NAMES[si], "sign_index": si, "sankrit": RAASI_SANKRIT[si],
            "degree_in_sign": round(deg, 4), "longitude": round(si * 30.0 + deg, 4),
            "raw": raw}


def build_sphutas(env, h, jd, place):
    """The 14 classical sphutas (tri, chatur, pancha, prana, deha, mrityu, sookshma-tri,
       beeja, kshetra, tithi, yoga, rahu-tithi, yogi, avayogi) for the D1 chart."""
    sphuta = env["sphuta"]
    drik = env["drik"]
    out = {}
    dob = drik.Date(h.Date.year, h.Date.month, h.Date.day)
    tob = _tob(h)
    for name in ["tri_sphuta", "chatur_sphuta", "pancha_sphuta", "prana_sphuta",
                 "deha_sphuta", "mrityu_sphuta", "sookshma_tri_sphuta", "beeja_sphuta",
                 "kshetra_sphuta", "tithi_sphuta", "yoga_sphuta", "rahu_tithi_sphuta",
                 "yogi_sphuta", "avayogi_sphuta"]:
        fn = getattr(sphuta, name, None)
        if not fn:
            continue
        try:
            with _quiet():
                res = fn(dob, tob, place)
            out[name] = ({k: _strip_html(v) for k, v in res.items()}
                         if isinstance(res, dict) else _decode_sphuta(res))
        except Exception as e:
            out[name] = {"__error__": str(e)}
    # ---- ROUND-11 (review P2-6): YOGI / DUPLICATE-YOGI / AVAYOGI planets ----
    # The library exposes only the two POINTS (sphuta.yogi_sphuta / .avayogi_sphuta,
    # both decoded above); the three PLANET designations are derived here per the
    # web-verified consensus doctrine:
    #   Yogi           = nakshatra lord of the yogi point;
    #   Duplicate yogi = SIGN lord of the sign containing the YOGI POINT (NOT of the
    #                    yogi planet's own placement — a common misreading the
    #                    sources explicitly correct); aka sahayogi;
    #   Avayogi        = lord of the 6th nakshatra from the yogi nakshatra counting
    #                    inclusively (idx+5) = 6th planet in Vimshottari lord order.
    # Cross-check: the nakshatra lord of the library's avayogi_sphuta point (yogi
    # point + 186 deg 40 min = +14 nakshatras = +5 Vimshottari lords) must equal the
    # derived avayogi.
    try:
        _yp11 = out.get("yogi_sphuta") or {}
        _ap11 = out.get("avayogi_sphuta") or {}
        _ylon = _yp11.get("longitude") if isinstance(_yp11, dict) else None
        _alon = _ap11.get("longitude") if isinstance(_ap11, dict) else None
        if _ylon is None:
            out["yogi_avayogi_planets"] = {
                "__error__": "yogi_sphuta longitude unavailable"}
        else:
            _span11 = 360.0 / 27.0
            _ynak_idx = int(_ylon / _span11) % 27
            _ynak, _ypada, _ = _nakshatra_of_long(_ylon)
            _avy_idx = (_ynak_idx + 5) % 27
            _avy_lord = NAKSHATRA_LORDS[_avy_idx % 9]
            _xchk = (NAKSHATRA_LORDS[int(_alon / _span11) % 27 % 9]
                     if _alon is not None else None)
            out["yogi_avayogi_planets"] = {
                "yogi_planet": NAKSHATRA_LORDS[_ynak_idx % 9],
                "yogi_point_longitude": round(_ylon, 4),
                "yogi_point_nakshatra": _ynak,
                "yogi_point_nakshatra_number": _ynak_idx + 1,
                "duplicate_yogi_planet": SIGN_LORDS[_sign_of_long(_ylon)],
                "duplicate_yogi_basis": (
                    "sign lord of %s — the sign containing the YOGI POINT (not the "
                    "yogi planet's placement)" % RAASI_NAMES[_sign_of_long(_ylon)]),
                "avayogi_planet": _avy_lord,
                "avayogi_nakshatra": NAKSHATRAS[_avy_idx],
                "avayogi_rule": ("lord of the 6th nakshatra from the yogi nakshatra, "
                                 "counting inclusively (index+5)"),
                "avayogi_crosscheck_from_library_point": _xchk,
                "avayogi_crosscheck_matches": (None if _xchk is None
                                               else bool(_xchk == _avy_lord)),
                "__note__": (
                    "ROUND-11 (review P2-6): planets-of-fortune doctrine. The yogi "
                    "planet anchors the yogi point (sphutas.yogi_sphuta); dasha "
                    "periods and transits of the YOGI and DUPLICATE-YOGI (sahayogi) "
                    "planets support results, those of the AVAYOGI obstruct. "
                    "Duplicate-yogi = sign lord of the YOGI POINT's sign per source "
                    "consensus (NOT the sign where the yogi planet sits). The "
                    "library's avayogi_sphuta = yogi point + 186d40m (+14 "
                    "nakshatras = +5 Vimshottari lords) independently confirms the "
                    "avayogi rule — see the crosscheck fields."),
            }
    except Exception as e:
        out["yogi_avayogi_planets"] = {"__error__": str(e)}
    # ROUND-16: charts.sphuta_longitudes at D1/D9/D10
    try:
        charts = env.get("charts")
        _smap = dict(getattr(env.get("utils"), "_sphutas", {}) or {})
        _divs = {}
        for _f in (1, 9, 10):
            try:
                with _quiet():
                    _rows = charts.sphuta_longitudes(dob, tob, place,
                                                     divisional_chart_factor=_f)
                _divs["D%d" % _f] = _decode_sign_deg_rows(_rows, _smap)
            except Exception as e:
                _divs["D%d" % _f] = {"__error__": str(e)}
        out["library_longitudes_by_varga"] = {
            "by_factor": _divs,
            "__note__": ("ROUND-16: charts.sphuta_longitudes for D1/D9/D10. Keys S1..S14 "
                         "map to tri..avayogi (utils._sphutas). D1 should agree with the "
                         "named sphuta functions above; D9/D10 are the same points "
                         "projected into the varga. Horoscope.get_ava_saha_yoga_* is the "
                         "HTML label of S13/S14 — already decoded as yogi_avayogi_planets.")}
    except Exception as e:
        out["library_longitudes_by_varga"] = {"__error__": str(e)}
    try:
        with _quiet():
            _mx = charts.sphuta_longitudes_mixed_chart(dob, tob, place, 1, 1, 9, 1)
        _smap = dict(getattr(env.get("utils"), "_sphutas", {}) or {})
        out["library_longitudes_mixed_D1xD9"] = {
            "points": _decode_sign_deg_rows(_mx, _smap),
            "__note__": "ROUND-17: charts.sphuta_longitudes_mixed_chart(D1 x D9)."}
    except Exception as e:
        out["library_longitudes_mixed_D1xD9"] = {"__error__": str(e)}
    return out


def build_sahams(env, h, jd, place, planet_positions):
    """The 36 Sahams (fortune/lot-subtle-points) for the D1 chart.

    h.get_sahams returns a 2-tuple (key_list_dict, {saham_name: 'degree-position'}). We
    extract and clean the second element into a simple {name: position} dict for the AI.
    """
    try:
        with _quiet():
            try:
                res = h.get_sahams(jd, place)          # PyJHora 5.0 signature (jd_at_dob, place, ...)
            except TypeError:
                res = h.get_sahams(planet_positions)   # PyJHora 4.8.7 signature
        if isinstance(res, (list, tuple)) and len(res) == 2 and isinstance(res[1], dict):
            clean = {}
            for k, v in res[1].items():
                # positions look like 'Capricorn 22° 39' 56"'; strip any leading sign glyph
                # and (ROUND-9) parse into structured {sign, degree, longitude, raw}.
                clean[k] = _decode_saham(v)
            # ROUND-16: charts.saham_longitudes at D9/D10. Nested under a reserved
            # key so the D1 name->position contract (len>=30 named lots) is unchanged.
            try:
                charts = env.get("charts")
                drik = env["drik"]
                dob = drik.Date(h.Date.year, h.Date.month, h.Date.day)
                tob = _tob(h)
                _smap = dict(getattr(env.get("utils"), "_sahams", {}) or {})
                _divs = {}
                for _f in (9, 10):
                    try:
                        with _quiet():
                            _rows = charts.saham_longitudes(dob, tob, place,
                                                            divisional_chart_factor=_f)
                        _divs["D%d" % _f] = _decode_sign_deg_rows(_rows, _smap)
                    except Exception as e:
                        _divs["D%d" % _f] = {"__error__": str(e)}
                clean["__divisional__"] = {
                    "by_factor": _divs,
                    "__note__": ("ROUND-16: charts.saham_longitudes D9/D10. Abbreviations "
                                 "(Pn=punya, …) expanded via utils._sahams. D1 lots remain "
                                 "the named keys of this dict.")}
                try:
                    with _quiet():
                        _mx = charts.saham_longitudes_mixed_chart(dob, tob, place, 1, 1, 9, 1)
                    clean["__mixed_D1xD9__"] = _decode_sign_deg_rows(_mx, _smap)
                except Exception as e:
                    clean["__mixed_D1xD9__"] = {"__error__": str(e)}
            except Exception as e:
                clean["__divisional__"] = {"__error__": str(e)}
            return clean
        return {"__error__": f"unexpected sahams shape {type(res).__name__}"}
    except Exception as e:
        return {"__error__": str(e)}


def _decode_arudha_values(vals):
    """ROUND-7 / PyJHora 5.0: `graha_arudhas_from_planet_positions` now returns
    [(sign_index, degree_in_sign), ...] tuples instead of plain values (chandra/surya/
    bhava arudhas still return plain sign indices / longitudes). Decode per-element so
    BOTH shapes render as explicit, unambiguous data."""
    if not isinstance(vals, (list, tuple)):
        return vals
    out = []
    for v in vals:
        if isinstance(v, (list, tuple)) and len(v) == 2:
            try:
                si = int(v[0]) % 12
                deg = round(float(v[1]), 4)
                out.append({"sign_index": si, "sign": RAASI_NAMES[si],
                            "degree_in_sign": deg,
                            "longitude": round(si * 30 + deg, 4)})
                continue
            except Exception:
                pass
        out.append(v)
    return out


def _decode_arudha_list(longitudes):
    """Turn the 12 arudha longitudes (A1..A12 in the bhava arudha order AL,A2..A11,UL/A12)
    into sign+degree labels, so the AI sees signs rather than raw degrees."""
    if not isinstance(longitudes, (list, tuple)):
        return longitudes
    out = {}
    # PyJHora's bhava arudha order is A1 (Lagna/AL), A2..A12; the 12th is Upapada (UL).
    for i, lon in enumerate(longitudes):
        try:
            lon = float(lon)
        except Exception:
            continue
        label = "AL" if i == 0 else ("UL" if i == 11 else f"A{i + 1}")
        out[label] = {"sign": RAASI_NAMES[_sign_of_long(lon)],
                      "sign_index": _sign_of_long(lon),
                      "degree": round(_deg_in_sign(lon), 3),
                      "longitude": round(lon, 3)}
    return out


def build_arudhas_and_specials(env, h, jd, place, planet_positions, arudha_base=0):
    """Arudhas (current `bhava_arudha_longitudes` API), Karakamsa, KP lords,
    22nd drekkana, 64th navamsa, Karakamsa (AMK).

    NOTE (review §3): `arudhas.bhava_arudha_longitudes_from_planet_positions` is BROKEN in
    this PyJHora build (@calls a removed internal `_bhaava_madhya_new_from_planet_positions`
    -> AttributeError). The authoritative current API is `arudhas.bhava_arudha_longitudes(
    jd, place, arudha_base, divisional_chart_factor, ...)`, which we use directly for the D1
    chart AND to add varga-specific arudhas (D2..D60) plus base-selection support. `arudha_base`
    0/1/2 selects the arudha counting convention (PyJHora's own parameter).
    """
    charts = env["charts"]
    arudhas = env["arudhas"]
    house = env["house"]
    out = {}
    def s(fn, *a, **k):
        try:
            with _quiet():
                return fn(*a, **k)
        except Exception as e:
            return {"__error__": str(e)}
    def _arudha_for_factor(factor):
        try:
            with _quiet():
                raw = arudhas.bhava_arudha_longitudes(jd, place, arudha_base=arudha_base,
                                                      divisional_chart_factor=factor)
            return _decode_arudha_list(raw)
        except Exception as e:
            return {"__error__": str(e)}
    # D1 (base) arudhas via the current API (never the broken from_planet_positions variant).
    out["arudha_lagna_longitudes"] = _arudha_for_factor(1)
    out["arudha_base"] = arudha_base
    # varga-specific arudhas, so the reviewer's "varga Arudhas" coverage gap is closed.
    # ROUND-13 (review item 4, ADOPTED): D10 added — Dasamsa arudhas carry the
    # classical career/income padas: A10 (10th arudha of D10) = Rajyapada (career
    # status/authority) and A11 = Labhapada (income/gains). The library's
    # bhava_arudha_longitudes accepts divisional_chart_factor=10 (verified live:
    # 12 longitudes, decoded like the other factors).
    out["arudhas_by_varga"] = {f"D{f}": _arudha_for_factor(f)
                               for f in (3, 6, 7, 9, 10, 12)}
    out["arudhas_by_varga_note"] = (
        "ROUND-13 (review item 4): D10 (Dasamsa) added to the varga arudhas — in "
        "Jaimini career readings the D10 arudhas are read as A10 = Rajyapada "
        "(career status/authority/rank) and A11 = Labhapada (income/gains); the "
        "other factors keep their classical uses (D9 arudhas for marriage/dharma "
        "status, D3 for siblings/virya, D12 for parents, D7 for children, D6 for "
        "health-conflicts). Every factor is decoded per house (A1..A12) exactly "
        "like the D1 arudha_lagna_longitudes.")
    # graha / chandra / surya arudhas. REVIEW FIX: `arudhas.graha_arudhas(chart)` takes a
    # HOUSE CHART, not planet_positions (passing planet_positions raises `KeyError: 0`), so
    # we use the `*_from_planet_positions` variants which are the correct adapters for the
    # planet-position list we already have. VERIFIED: `graha_arudhas_from_planet_positions(
    # planet_positions)` returns the 10 arudha values, `graha_arudhas(pp)` raises KeyError.
    for lab, fn in (("graha_arudhas", getattr(arudhas, "graha_arudhas_from_planet_positions", None)),
                    ("chandra_arudhas", getattr(arudhas, "chandra_arudhas_from_planet_positions", None)),
                    ("surya_arudhas", getattr(arudhas, "surya_arudhas_from_planet_positions", None))):
        if not fn:
            continue
        try:
            with _quiet():
                out[lab] = _decode_arudha_values(fn(planet_positions))
        except Exception as e:
            out[lab] = str(e)
    try:
        out["KP_lords"] = s(charts.get_KP_lords_from_planet_positions, planet_positions)
    except Exception as e:
        out["KP_lords"] = str(e)
    try:
        # 22nd drekkana is derived from the DREKKANA (D3) chart, not the navamsa (D9).
        d3 = charts.divisional_chart(jd, place, divisional_chart_factor=3)
        out["22nd_drekkana"] = s(charts.get_22nd_drekkana, d3)
    except Exception as e:
        out["22nd_drekkana"] = str(e)
    try:
        d9 = charts.divisional_chart(jd, place, divisional_chart_factor=9)
        out["64th_navamsa"] = s(charts.get_64th_navamsa, d9)
    except Exception as e:
        out["64th_navamsa"] = str(e)
    try:
        # Atmakaraka (AMK) karakamsa = navamsa sign of the Atmakaraka
        ak = None
        with _quiet():
            ck = house.chara_karakas(planet_positions)
        if isinstance(ck, (list, tuple)) and ck:
            ak = ck[0]
        if ak is not None:
            d9 = charts.divisional_chart(jd, place, divisional_chart_factor=9)
            ppl = _planetary_longitude(d9)
            ak_name = _planet_name(ak)
            if ak_name in ppl:
                out["karakamsa"] = {"sign": RAASI_NAMES[_sign_of_long(ppl[ak_name])],
                                    "planet": ak_name}
    except Exception as e:
        out["karakamsa"] = str(e)
    # ROUND-17: house-count Bhava Arudhas (A1..A12 as signs) — distinct from the
    # longitude API already decoded as arudha_lagna_longitudes.
    try:
        with _quiet():
            _ba = arudhas.bhava_arudhas_from_planet_positions(planet_positions)
        rows = []
        for i, sgn in enumerate(_ba or []):
            rows.append({"pada": "A%d" % (i + 1),
                         "sign_index": int(sgn) % 12,
                         "sign": RAASI_NAMES[int(sgn) % 12],
                         "label": ("Arudha Lagna" if i == 0 else
                                   ("Upapada" if i == 11 else None))})
        out["bhava_arudhas_signs"] = {
            "by_house": rows,
            "__note__": ("ROUND-17: arudhas.bhava_arudhas_from_planet_positions — "
                         "A1=Arudha Lagna, A12=Upapada, values are SIGN indices "
                         "(not longitudes). Compare A1 with arudha_lagna_longitudes.")}
    except Exception as e:
        out["bhava_arudhas_signs"] = {"__error__": str(e)}
    return out


def _decode_shadbala_summary(sb_raw, const_mod):
    """ROUND-13 (review item 3, ADOPTED): shared decoder for the library's 9-row
    strength.shad_bala return [sthana, kaala, dig, cheshta, naisargika, drik, sum,
    rupas, ratio] (each row Sun..Saturn). Extracted from the Round-12
    build_strength_full inline block so the FAST `strengths` section and the deep
    `strength_full` section expose the IDENTICAL decoded summary from ONE source.
    (The review's own snippet omitted the row indices for five of the components —
    it would have collapsed them all onto row 0; the indices here are the verified
    ones.) Row 8 is the LIBRARY'S OWN strength ratio: rupas / const.shad_bala_
    factors — its BPHS required minimums. DISPUTE DISCLOSED (Round-12, web-verified
    across four sources): popular tables give Sun = 6.5 rupas / 390 virupas; the
    library (PVR/JHora lineage) gives 5 / 300 — library primary, variant noted.
    1 rupa = 60 virupas (shashtiamsas)."""
    try:
        if not (isinstance(sb_raw, (list, tuple)) and len(sb_raw) == 9):
            return {"__error__":
                    "unexpected shad_bala shape (expected the library's 9 rows)"}
        n7 = PLANET_NAMES[:7]
        def by7(row):
            return {n7[i]: round(float(v), 4) for i, v in enumerate(row)}
        fac = [float(x) for x in const_mod.shad_bala_factors]
        return {
            "components_by_planet": {
                "sthana_bala": by7(sb_raw[0]),
                "kaala_bala": by7(sb_raw[1]),
                "dig_bala": by7(sb_raw[2]),
                "cheshta_bala": by7(sb_raw[3]),
                "naisargika_bala": by7(sb_raw[4]),
                "drik_bala": by7(sb_raw[5]),
            },
            "total_points_by_planet": by7(sb_raw[6]),
            "total_rupas_by_planet": by7(sb_raw[7]),
            "required_minimum_rupas_by_planet": {n7[i]: fac[i] for i in range(7)},
            "strength_ratio_by_planet": by7(sb_raw[8]),
            "meets_required_minimum": {n7[i]: bool(float(sb_raw[8][i]) >= 1.0)
                                       for i in range(7)},
            "__note__": (
                "ROUND-12/13 (review F1 / item 3): decoded from the library's own "
                "9-row shad_bala() return [sthana, kaala, dig, cheshta, naisargika, "
                "drik, total_points, total_rupas, strength_ratio]; every row is "
                "Sun..Saturn order. strength_ratio = total rupas / required minimum "
                "(const.shad_bala_factors — the library's BPHS minimums: Sun 5, "
                "Moon 6, Mars 5, Mercury 7, Jupiter 6.5, Venus 5.5, Saturn 5 rupas; "
                "1 rupa = 60 virupas/shashtiamsas). DISPUTE DISCLOSED: popularly "
                "cited BPHS tables (and the review) give the Sun minimum as 6.5 "
                "rupas (390 virupas) instead of 5 (300); the library's table is "
                "reported as primary and the variant noted — under the variant the "
                "Sun's ratio scales by 5/6.5. drik_bala is legitimately NEGATIVE "
                "under malefic aspect pressure. meets_required_minimum uses the "
                "library ratio (>= 1.0). ROUND-13: this summary is now SHARED by "
                "the FAST strengths section and the deep strength_full section "
                "(one decoder, identical output)."),
        }
    except Exception as e:
        return {"__error__": str(e)}


def _place_tz_offset(utils_mod, lat, lon, jd_local):
    """ROUND-13 (review item 2, ADOPTED as cross-build hardening): resolve a
    place's UTC offset at a given local JD. In the pinned 5.0 dev build the library
    signature is get_place_timezone_offset(latitude, longitude, jd_local=None) —
    audits 12d-1/12d-2 proved the engine's jd_local= keyword call binds and is
    DST-correct. But the dev line is a MOVING TARGET and the review reports builds
    where the 3rd parameter is named `jd`; there, a jd_local= keyword raises
    TypeError and the engine's except-branch silently falls back to the NATIVE's
    timezone — wrong for a partner chart. This helper binds by whatever name the
    INSTALLED build declares (jd_local → jd → positional → 2-parameter legacy), so
    partner-tz resolution never silently degrades across dev builds."""
    fn = utils_mod.get_place_timezone_offset
    try:
        params = list(inspect.signature(fn).parameters)
    except (TypeError, ValueError):
        params = []
    if len(params) >= 3:
        if params[2] == "jd_local":
            return fn(lat, lon, jd_local=jd_local)
        if params[2] == "jd":
            return fn(lat, lon, jd=jd_local)
        return fn(lat, lon, jd_local)          # positional: any other 3rd-param name
    return fn(lat, lon)                        # legacy 2-parameter build: no JD knob


def build_strengths(env, h, jd, place, planet_positions):
    strength = env["strength"]
    out = {}
    def s(fn, *a, **k):
        try:
            with _quiet():
                return fn(*a, **k)
        except Exception as e:
            return {"__error__": str(e)}
    out["shad_bala"] = s(strength.shad_bala, jd, place)
    # ROUND-13 (review item 3): the FAST path now carries the SAME decoded summary
    # as strength_full — shared _decode_shadbala_summary helper (single source).
    out["shad_bala_summary"] = _decode_shadbala_summary(out["shad_bala"],
                                                        env["const"])
    out["pancha_vargeeya_bala"] = s(strength.pancha_vargeeya_bala, jd, place)
    out["dwadhasa_vargeeya_bala"] = s(strength.dwadhasa_vargeeya_bala, jd, place)
    out["bhava_bala"] = s(strength.bhava_bala, jd, place)
    out["bhava_drishti_bala"] = s(strength.bhava_drishti_bala, jd, place)
    out["harsha_bala"] = s(strength.harsha_bala, _dob(h), _tob(h), place)
    out["planet_aspect_relationship_table"] = s(strength.planet_aspect_relationship_table, planet_positions)
    # vimsopaka bala if available (deep quantitative strength).
    # PyJHora 5.0: these take (jd_at_dob, place_as_tuple) — try that FIRST, keep the
    # legacy (planet_positions,) shape as fallback (4.8.x accepted it for some).
    for n in ["vimsopaka_dasavarga_of_planets", "vimsopaka_sapthavarga_of_planets"]:
        fn = getattr(env["charts"], n, None)
        if fn:
            out[n.replace("_of_planets", "")] = _try_arg_sets(
                fn, [(jd, place), (planet_positions,), (jd, place, planet_positions)])
    # ---- ROUND-5 additions ----------------------------------------------------
    # PVR's own aspect-relationship table (percentage drishti, 9x9 planet-keyed).
    try:
        with _quiet():
            _pvr = strength.planet_aspect_relationship_table_pvr(planet_positions)
        if isinstance(_pvr, (list, tuple)):
            out["planet_aspect_relationship_table_pvr"] = {
                PLANET_ORDER[i]: {PLANET_ORDER[j]: (int(v) if float(v).is_integer() else float(v))
                                  for j, v in enumerate(row)}
                for i, row in enumerate(_pvr) if i < len(PLANET_ORDER)}
        else:
            out["planet_aspect_relationship_table_pvr"] = _pvr
    except Exception as e:
        out["planet_aspect_relationship_table_pvr"] = str(e)
    # vimsopaka DASA-varga (per-planet: [count of vargas in good dignity, varga labels, score])
    try:
        with _quiet():
            _vd = env["charts"].vimsopaka_dhasavarga_of_planets(jd, place)
        if isinstance(_vd, dict):
            out["vimsopaka_dhasavarga"] = {
                _decode_planet_id(k): {"good_varga_count": v[0], "vargas": v[1], "score": v[2]}
                if isinstance(v, (list, tuple)) and len(v) >= 3 else v
                for k, v in _vd.items()}
        else:
            out["vimsopaka_dhasavarga"] = _vd
    except Exception as e:
        out["vimsopaka_dhasavarga"] = str(e)
    # vimsamsavarga (20-varga dignity count per planet)
    try:
        with _quiet():
            _vv = env["charts"].vimsamsavarga_of_planets(jd, place)
        if isinstance(_vv, (list, tuple)):
            out["vimsamsavarga"] = {PLANET_ORDER[i]: int(v) for i, v in enumerate(_vv)
                                    if i < len(PLANET_ORDER)}
        else:
            out["vimsamsavarga"] = _vv
    except Exception as e:
        out["vimsamsavarga"] = str(e)
    return out


def build_tajaka(env, h, jd, place, jd_now=None, annual_year=None):
    """Annual (Tajaka) chart + Lord of the Year + Muntha for the *current* annual year.

    `annual_year` (>=1) explicitly selects the Tajaka year; `jd_now` is used to derive the
    elapsed solar years only when `annual_year` is not given, so the chart/LOTY/Muntha are
    for the native's CURRENT year rather than always the first. Without either it defaults
    to years=1 (first annual year).
    """
    tajaka = env["tajaka"]
    out = {}
    def s(fn, *a, **k):
        try:
            with _quiet():
                return fn(*a, **k)
        except Exception as e:
            return {"__error__": str(e)}
    # Tajaka year number (1 = first/solar-return year) as of --now, respecting the birthday
    # boundary (review §3: a date before the birthday belongs to last year's annual chart).
    # An explicit --annual-year overrides the derivation (review §2.2).
    years_elapsed = 1
    if annual_year is not None:
        try:
            years_elapsed = max(1, int(annual_year))
        except Exception:
            years_elapsed = 1
    elif jd_now is not None:
        years_elapsed = _tajaka_years_elapsed(jd_now, h)
    out["years_elapsed"] = years_elapsed
    out["years_used"] = years_elapsed
    ac_raw = None   # the raw PyJHora annual-chart result, kept for Muntha/aspects
    try:
        ac = s(tajaka.annual_chart, jd, place, years=years_elapsed)
        ac_raw = ac
        # ac is [[[id,[sign,deg]],...], [y,m,d] start] — decode planet ids to names.
        if isinstance(ac, (list, tuple)) and len(ac) >= 2 and isinstance(ac[0], (list, tuple)):
            rows = []
            for item in ac[0]:
                try:
                    pid, val = item[0], item[1]
                    sign, deg = int(val[0]), float(val[1])
                    rows.append({"planet": (LAGNA if pid == "L" else _planet_name(pid)),
                                 "sign_index": sign % 12, "sign": RAASI_NAMES[sign % 12],
                                 "degree": round(deg, 3)})
                except Exception:
                    pass
            def _ac_date(x):
                # annual_chart start is [[y,m,d], 'hh:mm:ss'] or (y,m,d); take the y-m-d sub-list.
                try:
                    if isinstance(x, (list, tuple)) and isinstance(x[0], (list, tuple)):
                        x = x[0]
                    return f"{int(x[0]):04d}-{int(x[1]):02d}-{int(x[2]):02d}"
                except Exception:
                    return None
            start_d = _ac_date(ac[1]) if len(ac) > 1 else None
            out["annual_chart"] = {"planets": rows, "starts": start_d}
        else:
            out["annual_chart"] = ac
    except Exception as e:
        out["annual_chart"] = str(e)
        ac = None
    try:
        # returns a classical planet index 0=Sun..6=Saturn -> decode to name
        _loty = s(tajaka.lord_of_the_year, jd, place, years_elapsed)
        out["lord_of_the_year"] = {"planet": _decode_planet_id(_loty),
                                   "index": _loty,
                                   "note": ("Classical Tajaka year-lord (Sun..Saturn); Rahu/Ketu "
                                            "never rule the year in Tajaka.")}
    except Exception as e:
        out["lord_of_the_year"] = str(e)
    try:
        # ROUND-8 FIX (review P1-8, CONFIRMED real): classical Tajaka Muntha advances from
        # the NATAL lagna (1 sign per completed year). The old call fed the ANNUAL chart's
        # lagna sign index into tajaka.muntha_house — a bare (asc+years)%12 lambda — which
        # yields neither the Muntha sign nor any meaningful house.
        # ROUND-9 FIX (review Issue 1, CONFIRMED real): `years_elapsed` is the Tajaka
        # varsha NUMBER N (1 = the birth year — upstream next_solar_date's own docstring:
        # "year=1 for the birth year"), i.e. one MORE than the completed solar years.
        # Classical Muntha sits IN the natal lagna at birth and advances one sign per
        # COMPLETED year, so the formula must use (N - 1). Upstream's muntha docstring
        # agrees: "at the 51st year, the 50th house from lagna". Using N shifted Muntha
        # one whole sign for the entire life.
        #   muntha_sign                = (natal_lagna_sign + completed_years) % 12
        #   muntha_house_from_natal    = completed_years % 12 + 1            (1-based)
        #   muntha_house_from_annual   = (muntha_sign - annual_lagna_sign) % 12 + 1
        # The legacy key muntha_house keeps the most-quoted frame (from the natal lagna).
        _natal_pp = env["charts"].rasi_chart(jd, place)
        natal_asc_sign = _sign_of_long(_planetary_longitude(_natal_pp)[LAGNA])
        annual_asc_sign = None
        if isinstance(ac_raw, (list, tuple)) and ac_raw and isinstance(ac_raw[0], (list, tuple)):
            annual_asc_sign = _sign_of_long(_planetary_longitude(ac_raw[0])[LAGNA])
        _ye = int(years_elapsed)
        _cy = max(0, _ye - 1)   # completed solar years (varsha N has N-1 returns behind it)
        muntha_sign = (int(natal_asc_sign) + _cy) % 12
        out["muntha"] = {
            "muntha_sign": RAASI_NAMES[muntha_sign],
            "muntha_sign_index": muntha_sign,
            "muntha_house_from_natal_lagna": _cy % 12 + 1,
            "muntha_house_from_annual_lagna": (
                (muntha_sign - int(annual_asc_sign)) % 12 + 1
                if annual_asc_sign is not None else None),
            "natal_lagna_sign": RAASI_NAMES[int(natal_asc_sign) % 12],
            "annual_lagna_sign": (RAASI_NAMES[int(annual_asc_sign) % 12]
                                  if annual_asc_sign is not None else None),
            "years_elapsed": _ye,
            "completed_years": _cy,
            "rule": ("Muntha starts in the NATAL lagna at birth (Tajaka varsha 1) and "
                     "advances one sign per COMPLETED solar year — varsha N carries N-1 "
                     "completed years (classical Tajaka doctrine; houses are 1-based). "
                     "PyJHora's tajaka.muntha_house(asc, years) is just (asc+years)%12 and "
                     "expects COMPLETED years against the natal lagna; the wrapper computes "
                     "and labels every frame explicitly instead of delegating."),
        }
        out["muntha_house"] = out["muntha"]["muntha_house_from_natal_lagna"]  # legacy key
        if isinstance(ac_raw, (list, tuple)) and ac_raw and isinstance(ac_raw[0], (list, tuple)):
            ac = ac_raw
    except Exception as e:
        out["muntha_house"] = str(e)
        out["muntha"] = {"__error__": str(e)}
    # Annual (Varsha/Tajika) Dashas: these run inside a single solar-return year.
    # Once a lifetime dasha + the annual chart promise an event, Mudda / Patyayini /
    # Varsha-Vimsottari / Varsha-Narayana pin it to the month/week/day within the year.
    try:
        from jhora.horoscope.dhasa.annual import mudda, patyayini
        annual = {}
        # Annual (varsha) dasha rows are ((lord1, lord2), (y,m,d,hr), dur). The lords are
        # planet ids (0=Sun..8=Ketu) for mudda/patyayini/varsha-vimsottari, or SIGN ids
        # (0=Aries..11=Pisces) for varsha-narayana; 'L' means lagna.
        def _decode_annual(rows, lord_type):
            if not isinstance(rows, (list, tuple)):
                return rows
            out_rows = []
            for row in rows:
                if not (isinstance(row, (list, tuple)) and len(row) >= 2 and isinstance(row[0], (list, tuple))):
                    out_rows.append(row)
                    continue
                lords = []
                for ld in row[0]:
                    if ld == "L":
                        lords.append("Lagna")
                    elif lord_type == "sign":
                        idx = int(ld) % 12
                        lords.append(RAASI_NAMES[idx])
                    else:
                        lords.append(_decode_planet_id(ld))
                st = row[1]
                start = _date_str(*st) if isinstance(st, (list, tuple)) else str(st)
                dur = row[2] if len(row) > 2 else None
                out_rows.append({"lords": lords, "start": start,
                                 "duration_years": round(float(dur), 3) if dur is not None else None})
            return out_rows
        # Anchor the varsha (solar-return) dasha year at the CURRENT annual year.
        try:
            yy = _jd_to_ymd(jd)[0] + years_elapsed - 1
            y_date = env["drik"].Date(yy, h.Date.month, h.Date.day)
        except Exception:
            y_date = None
        try:
            md = mudda.mudda_dhasa_bhukthi(jd, place, years=years_elapsed)
            annual["mudda"] = _decode_annual(md, "planet")
        except Exception as e:
            annual["mudda"] = str(e)
        try:
            # Patyayini needs a JD to anchor the year duration — use the current solar year jd.
            jy = env["utils"].julian_day_number(y_date, _tob(h)) if y_date else \
                 env["utils"].julian_day_number((h.Date.year + years_elapsed, h.Date.month, h.Date.day), _tob(h))
            pd = patyayini.get_dhasa_bhukthi(jy, place)
            annual["patyayini"] = _decode_annual(pd, "planet")
        except Exception as e:
            annual["patyayini"] = str(e)
        try:
            vv = mudda.varsha_vimsottari_dhasa_bhukthi(jd, place, years=years_elapsed)
            annual["varsha_vimsottari"] = _decode_annual(vv, "planet")
        except Exception as e:
            annual["varsha_vimsottari"] = str(e)
        try:
            from jhora.horoscope.dhasa.raasi import narayana as _nar
            y = y_date if y_date else env["drik"].Date(h.Date.year + 1, h.Date.month, h.Date.day)
            vn = _nar.varsha_narayana_dhasa_bhukthi(y, _tob(h), place)
            annual["varsha_narayana"] = _decode_annual(vn, "sign")
        except Exception as e:
            annual["varsha_narayana"] = str(e)
        out["annual_dashas"] = annual
    except Exception as e:
        out["annual_dashas"] = str(e)
    return out


# ---- Dasha families -------------------------------------------------------- #
# jd-based graha dashas: call fn(jd, place). Lord ids follow the source ordering.
JD_DASHAS = [
    ("vimsottari", "vimsottari", "get_vimsottari_dhasa_bhukthi"),
    ("ashtottari", "ashtottari", "get_ashtottari_dhasa_bhukthi"),
    ("yoga_vimsottari", "yoga_vimsottari", "get_dhasa_bhukthi"),
    ("aayu_amsayu", "aayu", "amsayu_dhasa_bhukthi"),
    ("aayu_nisargayu", "aayu", "nisargayu_dhasa_bhukthi"),
    ("aayu_pindayu", "aayu", "pindayu_dhasa_bhukthi"),
]
# (dob=Date, tob, place) graha / rasi dashas. Verified signatures only.
DOB_DASHAS = [
    ("yogini", "graha.yogini", "get_dhasa_bhukthi"),
    ("tithi_yogini", "graha.tithi_yogini", "get_dhasa_bhukthi"),
    ("naisargika", "graha.naisargika", "get_dhasa_bhukthi"),
    ("karaka", "graha.karaka", "get_dhasa_antardhasa"),
    ("tara", "graha.tara", "get_dhasa_bhukthi"),
    ("kaala", "graha.kaala", "get_dhasa_antardhasa"),
    ("narayana", "raasi.narayana", "narayana_dhasa_for_rasi_chart"),
    ("shoola", "raasi.shoola", "get_dhasa_bhukthi"),
    ("sudasa", "raasi.sudasa", "get_dhasa_bhukthi"),
    ("chara", "raasi.chara", "get_dhasa_antardhasa"),
    ("kalachakra", "raasi.kalachakra", "get_dhasa_bhukthi"),
    ("drig", "raasi.drig", "drig_dhasa"),
    ("kendradhi_rasi", "raasi.kendradhi_rasi", "kendradhi_rasi_dhasa"),
    # Conditional sama dashas — the Parashari rule says check these (via `applicability`)
    # when a specific combination holds and use them in place of / alongside Vimsottari.
    ("shodasottari", "graha.shodasottari", "get_dhasa_bhukthi"),
    ("dwadasottari", "graha.dwadasottari", "get_dhasa_bhukthi"),
    ("panchottari", "graha.panchottari", "get_dhasa_bhukthi"),
    ("satabdika", "graha.sataatbika", "get_dhasa_bhukthi"),
    ("shattrimsa_sama", "graha.shattrimsa_sama", "get_dhasa_bhukthi"),
    ("chaturashiti_sama", "graha.chathuraaseethi_sama", "get_dhasa_bhukthi"),
    ("shashti_sama", "graha.shastihayani", "get_dhasa_bhukthi"),
    ("dwissaptati_sama", "graha.dwisatpathi", "get_dhasa_bhukthi"),
    # Modern experimental: Ashtaka-varga / Rashmi point-strength dashas.
    ("ashtaka_varga", "graha.ashtaka_varga", "get_ashtaka_varga_dhasa_bhukthi"),
    ("rashmi", "graha.rashmi", "get_rashmi_dhasa_bhukthi"),
    # Additional rasi dashas (Jaimini) beyond chara/sudasa/shoola/drig/kendradhi/narayana.
    ("sthira", "raasi.sthira", "get_dhasa_antardhasa"),
    ("brahma", "raasi.brahma", "get_dhasa_antardhasa"),
    ("mandooka", "raasi.mandooka", "get_dhasa_antardhasa"),
    ("varnada", "raasi.varnada", "get_dhasa_antardhasa"),
    ("navamsa", "raasi.navamsa", "get_dhasa_antardhasa"),
    ("trikona", "raasi.trikona", "get_dhasa_antardhasa"),
    ("lagnamsaka", "raasi.lagnamsaka", "get_dhasa_antardhasa"),
]


def _import_dhasa(rel):
    return importlib.import_module("jhora.horoscope.dhasa." + rel)


# Full dasha catalog: (name, module-relative-path, fn, lord_type).
# lord_type tells what the raw integer/sign/other ids in each system's rows mean:
#   planet / sign / navamsa -> safely decoded to names;  karana/nakshatra/element/special -> kept raw.
DASHA_CATALOG = [
    # --- graha (planet) dashas ---
    ("vimsottari", "graha.vimsottari", "get_vimsottari_dhasa_bhukthi", "planet"),
    ("ashtottari", "graha.ashtottari", "get_ashtottari_dhasa_bhukthi", "planet"),
    ("yoga_vimsottari", "graha.yoga_vimsottari", "get_dhasa_bhukthi", "planet"),
    ("aayu_amsayu", "graha.aayu", "amsayu_dhasa_bhukthi", "planet"),
    ("aayu_nisargayu", "graha.aayu", "nisargayu_dhasa_bhukthi", "planet"),
    ("aayu_pindayu", "graha.aayu", "pindayu_dhasa_bhukthi", "planet"),
    ("yogini", "graha.yogini", "get_dhasa_bhukthi", "planet"),
    ("tithi_yogini", "graha.tithi_yogini", "get_dhasa_bhukthi", "planet"),
    ("naisargika", "graha.naisargika", "get_dhasa_bhukthi", "planet"),
    ("karaka", "graha.karaka", "get_dhasa_antardhasa", "planet"),
    ("tara", "graha.tara", "get_dhasa_bhukthi", "planet"),
    ("kaala", "graha.kaala", "get_dhasa_antardhasa", "planet"),
    ("buddhi_gathi", "graha.buddhi_gathi", "get_dhasa_bhukthi", "planet"),
    ("moola", "graha.moola", "get_dhasa_antardhasa", "planet"),
    ("ashtaka_varga", "graha.ashtaka_varga", "get_ashtaka_varga_dhasa_bhukthi", "planet"),
    ("rashmi", "graha.rashmi", "get_rashmi_dhasa_bhukthi", "planet"),
    # conditional sama dashas
    ("shodasottari", "graha.shodasottari", "get_dhasa_bhukthi", "planet"),
    ("dwadasottari", "graha.dwadasottari", "get_dhasa_bhukthi", "planet"),
    ("panchottari", "graha.panchottari", "get_dhasa_bhukthi", "planet"),
    ("satabdika", "graha.sataatbika", "get_dhasa_bhukthi", "planet"),
    ("shattrimsa_sama", "graha.shattrimsa_sama", "get_dhasa_bhukthi", "planet"),
    ("chaturashiti_sama", "graha.chathuraaseethi_sama", "get_dhasa_bhukthi", "planet"),
    ("shashti_sama", "graha.shastihayani", "get_dhasa_bhukthi", "planet"),
    ("dwissaptati_sama", "graha.dwisatpathi", "get_dhasa_bhukthi", "planet"),
    ("tithi_ashtottari", "graha.tithi_ashtottari", "get_dhasa_bhukthi", "planet"),
    ("saptharishi_nakshathra", "graha.saptharishi_nakshathra", "get_dhasa_bhukthi", "nakshatra"),
    ("karana_chathuraaseethi_sama", "graha.karana_chathuraaseethi_sama", "get_dhasa_bhukthi", "karana"),
    # --- rasi (sign) dashas ---
    ("narayana", "raasi.narayana", "narayana_dhasa_for_rasi_chart", "sign"),
    ("chara", "raasi.chara", "get_dhasa_antardhasa", "sign"),
    ("sudasa", "raasi.sudasa", "get_dhasa_bhukthi", "sign"),
    ("shoola", "raasi.shoola", "get_dhasa_bhukthi", "sign"),
    ("kalachakra", "raasi.kalachakra", "get_dhasa_bhukthi", "sign"),
    ("drig", "raasi.drig", "drig_dhasa", "sign"),
    ("kendradhi_rasi", "raasi.kendradhi_rasi", "kendradhi_rasi_dhasa", "sign"),
    # ROUND-5: the two kendradhi VARIANTS shipped in their own modules were missing from
    # the catalog (54 -> 56 systems). karaka-kendradhi starts from the karaka sign,
    # lagna-kendradhi from the lagna sign; both are sign-lord (rasi) dashas.
    ("karaka_kendraadhi", "raasi.karaka_kendraadhi", "get_karaka_kendradhi_rasi_bhukthi", "sign"),
    ("lagna_kendraadhi", "raasi.lagna_kendraadhi", "get_lagna_kendradhi_rasi_bhukthi", "sign"),
    ("sthira", "raasi.sthira", "get_dhasa_antardhasa", "sign"),
    ("brahma", "raasi.brahma", "get_dhasa_antardhasa", "sign"),
    ("mandooka", "raasi.mandooka", "get_dhasa_antardhasa", "sign"),
    ("varnada", "raasi.varnada", "get_dhasa_antardhasa", "sign"),
    ("navamsa", "raasi.navamsa", "get_dhasa_antardhasa", "sign"),
    ("trikona", "raasi.trikona", "get_dhasa_antardhasa", "sign"),
    ("lagnamsaka", "raasi.lagnamsaka", "get_dhasa_antardhasa", "sign"),
    ("chakra", "raasi.chakra", "get_dhasa_antardhasa", "sign"),
    ("chathurvidha_utthara", "raasi.chathurvidha_utthara", "get_dhasa_antardhasa", "sign"),
    ("padhanadhamsa", "raasi.padhanadhamsa", "get_dhasa_antardhasa", "sign"),
    ("paryaaya", "raasi.paryaaya", "get_dhasa_antardhasa", "sign"),
    ("sandhya", "raasi.sandhya", "get_dhasa_antardhasa", "sign"),
    ("tara_lagna", "raasi.tara_lagna", "get_dhasa_antardhasa", "sign"),
    ("yogardha", "raasi.yogardha", "get_dhasa_antardhasa", "sign"),
    ("raashiyanka", "raasi.raashiyanka", "get_dhasa_bhukthi", "sign"),
    ("niryaana", "raasi.niryaana", "get_dhasa_bhukthi", "sign"),
    ("nirayana_shoola", "raasi.nirayana", "nirayana_shoola_dhasa_bhukthi", "sign"),
    ("raasi_moola", "raasi.moola", "moola_dhasa", "sign"),
    # --- top-level special systems ---
    ("panchasvara", "panchasvara", "get_dhasa_bhukthi", "element"),
    ("sudharsana_chakra", "sudharsana_chakra", "get_dhasa_bhukthi", "special"),
]


def _dasha_arg_sets(fn, jd, place, dob, tob, planet_positions):
    """Return an ORDERED list of candidate arg-sets for a dasha function.

    Rather than guessing the signature from only the first parameter (which is fragile and
    version-sensitive, review §3), we introspect the full parameter list and try the most
    likely conventions in order — the known-good one first — so a wrong guess never silently
    yields a hidden error. `build_all_dashas` tries each until one succeeds.
    """
    try:
        import inspect
        params = list(inspect.signature(fn).parameters.keys())
    except Exception:
        params = []
    sets = []
    # (jd, place) pair convention
    if any(p in ("jd", "jd_at_dob", "jd_years", "jdut1") for p in params):
        sets.append((jd, place))
    # (planet_positions, dob, tob) via planet_positions
    if any(p in ("planet_positions", "drekkana", "navamsa", "h2p") for p in params):
        sets.append((planet_positions, dob, tob))
    # (dob, tob, place) — the most common graha/rasi dasha convention
    sets.append((dob, tob, place))
    # fallback full-width
    sets.append((jd, place, dob, tob, planet_positions))
    # de-dupe while preserving order. Key on the *idents* (not the objects) so unhashable
    # args (Date / Place) never raise TypeError.
    seen, out = set(), []
    for s in sets:
        k = tuple(id(x) for x in s)
        if k not in seen:
            seen.add(k)
            out.append(s)
    return out


# Parameter-name -> value mapping used to build an EXPLICIT call from the function's own
# signature (review §3: exact adapter per dasha, not generic positional combos). Any
# parameter whose name maps cleanly to a known input is populated; unknown optional params
# are left to their defaults. This yields ONE exact call instead of several guesses, so a
# function cannot accept a wrong-typed combo and return plausible-but-wrong data.
_DASHA_PARAM_MAP = {
    "jd": "jd", "jd_at_dob": "jd", "jdut1": "jd", "jdn": "jd", "jd_years": "jd",
    "place": "place", "place_as_tuple": "place",
    "planet_positions": "planet_positions", "drekkana": "planet_positions",
    "navamsa": "planet_positions", "h2p": "planet_positions", "rasi_position": "planet_positions",
    "dob": "dob", "birth_date": "dob", "date": "dob",
    "tob": "tob", "birth_time": "tob", "time_of_birth": "tob",
}


def _construct_dasha_args(fn, jd, place, dob, tob, planet_positions):
    """Build ONE arg-set for `fn` by matching its parameter names to known inputs.

    Returns a fully-keyword call (or a *args fallback if the signature is opaque). This is
    the "exact adapter" used when the catalog knows the function resolves; `_dasha_arg_sets`
    remains only as a last-resort fallback for system functions we cannot name-match.
    """
    try:
        import inspect
        want = list(inspect.signature(fn).parameters.keys())
        multi = inspect.signature(fn).parameters.get("**kwargs") is not None or \
                any(p.kind == inspect.Parameter.VAR_KEYWORD for p in inspect.signature(fn).parameters.values())
    except Exception:
        want, multi = [], False
    pool = {"jd": jd, "place": place, "planet_positions": planet_positions,
            "dob": dob, "tob": tob}
    kwargs = {}
    used = set()
    for pname in want:
        base = _DASHA_PARAM_MAP.get(pname)
        if base in pool:
            kwargs[pname] = pool[base]
            used.add(base)
    if kwargs:
        return kwargs if multi or True else list(kwargs.values())
    return None   # could not name-match any parameter -> caller falls back to _dasha_arg_sets


def _call_dasha(fn, jd, place, dob, tob, planet_positions, _resolution=None):
    """Call a dasha function with the exact, name-derived adapter first; only if that is
    impossible (opaque signature) fall back to the ordered positional trials. This prevents
    a wrong-typed positional combo from being silently accepted (review §3).

    REVIEW FIX (no silent wrong data): the path actually taken is reported via `_resolution`
    (a list we append one token to) rather than hidden. `build_all_dashas` surfaces it as a
    per-system `__resolution__` map, so a system whose call went through the generic
    positional fallback (the "can produce semantically wrong results without raising an
    error" case) is visibly flagged rather than indistinguishable from the exact adapter.
    """
    built = None
    try:
        built = _construct_dasha_args(fn, jd, place, dob, tob, planet_positions)
    except Exception:
        built = None
    if built is not None:
        try:
            with _quiet():
                r = fn(**built)
            if _resolution is not None:
                _resolution.append("exact_name_adapters")
            return r
        except Exception:
            pass   # fall through to the ordered positional attempts
    r = _try_arg_sets(fn, _dasha_arg_sets(fn, jd, place, dob, tob, planet_positions))
    if _resolution is not None:
        # ROUND-9 FIX (review Issue 4, CONFIRMED real): `_try_arg_sets` returns
        # {"__error__": ...} when EVERY positional trial failed. Tagging that as
        # "positional_fallback" made build_all_dashas' __status__ read
        # "unverified_fallback" — a broken computation disguised as a computed-but-
        # unverified one. Failures now resolve to the "error" token.
        if isinstance(r, dict) and "__error__" in r:
            _resolution.append("error")
        else:
            _resolution.append("positional_fallback")
    return r


# A per-run cache so the full dasha catalog is computed ONCE per chart, not once per
# section. `all_dashas`, `dasha_decoded` and `rasi_dasha` all read this.
_DASHA_CACHE = {}


def _reset_dasha_cache():
    _DASHA_CACHE.clear()


def _cached_all_dashas(env, h, jd, place, planet_positions, systems=None):
    key = ("all_dashas", jd, place.place if hasattr(place, "place") else str(place),
           tuple(sorted(map(str, systems) if systems else ["ALL"])))
    if key not in _DASHA_CACHE:
        _DASHA_CACHE[key] = build_all_dashas(env, h, jd, place, planet_positions, systems)
    return _DASHA_CACHE[key]


def build_all_dashas(env, h, jd, place, planet_positions=None, systems=None):
    """Compute the FULL PyJHora dasha catalog (raw timelines) for corroboration.

    Each system is a raw PyJHora array whose integer/sign/other ids are NOT planet
    names. To read names safely, use the companion `dasha_decoded` section, which
    tags each system's `lord_type` and decodes only where the encoding is known-safe.
    """
    out = {}
    if planet_positions is None:
        planet_positions = env["charts"].rasi_chart(jd, place)
    dob = env["drik"].Date(h.Date.year, h.Date.month, h.Date.day)
    tob = _tob(h)
    wanted = set(systems) if systems else None
    # Skip catalog systems that do not resolve in this build rather than computing an
    # error for them; report them cleanly (review §2.2).
    validated = set(_get_dasha_catalog_validation().get("catalog_validated", []))
    resolution = {}
    for name, module, fn_name, _lt in DASHA_CATALOG:
        if wanted and name not in wanted:
            continue
        if name not in validated:
            out[name] = {"status": "not_available_in_this_pyjhora_build",
                         "module": module, "function": fn_name}
            resolution[name] = "not_resolved_in_build"
            continue
        try:
            mod = _import_dhasa(module)
            fn = _resolve_dasha_fn(mod, fn_name)   # 5.0 renamed vimsottari's bhukthi fn
            if fn is None:
                fn = getattr(mod, fn_name)
            # Exact, name-derived adapter first (review §3: a generic positional combo may be
            # silently accepted and return a plausible-but-wrong timeline). Fall back to the
            # ordered positional trials only if the signature cannot be name-matched.
            _res = []
            out[name] = _call_dasha(fn, jd, place, dob, tob, planet_positions, _res)
            resolution[name] = _res[0] if _res else "unknown"
        except Exception as e:
            out[name] = {"__error__": str(e)}
            resolution[name] = "error"
    # REVIEW FIX: surface which adapter each system used, so a positional_fallback (the
    # "semantically wrong without raising" risk) is never hidden among exact adapters.
    out["__resolution__"] = resolution
    # ROUND-7 (review C5): a machine-readable STATUS per system. Anything not computed
    # through the exact name-derived adapter is explicitly `unverified_fallback` so
    # consumers can reject or caveat it without parsing resolution vocabulary.
    _status_vocab = {"exact_name_adapters": "verified_adapter",
                     "positional_fallback": "unverified_fallback",
                     "not_resolved_in_build": "not_available_in_this_pyjhora_build",
                     "error": "error", "unknown": "unknown"}
    out["__status__"] = {n: _status_vocab.get(r, r) for n, r in resolution.items()}
    return out


NAKSHATRA_NAMES = ["Ashwini", "Bharani", "Krittika", "Rohini", "Mrigashira", "Ardra",
    "Punarvasu", "Pushya", "Ashlesha", "Magha", "Purva_Phalguni", "Uttara_Phalguni",
    "Hasta", "Chitra", "Swati", "Vishakha", "Anuradha", "Jyeshtha", "Mula",
    "Purva_Ashadha", "Uttara_Ashadha", "Shravana", "Dhanishta", "Shatabhisha",
    "Purva_Bhadrapada", "Uttara_Bhadrapada", "Revati"]


# Per-system lord-ENCODING overrides (review §3: the catalog "planet/sign/navamsa/sakta"
# labels are too broad; a single shared mapping can mislabel). For each system we state the
# exact domain of its raw lord id so the decoder is correct rather than assumed:
#   planet(0..8), sign(0..11), navamsa_sign(0..11), nakshatra(0..26), element, special, karaka_pair
_DASHA_LORD_KIND = {
    # planet systems (default planet) — correct as-is.
    # nakshatra-based dasha: ids are nakshatra indices (0..26). ROUND-7 verified
    # empirically on a live chart (values 0..26 observed).
    "saptharishi_nakshathra": ("nakshatra", 27),
    # kalachakra period lords are RASI/sign ids (0..11) — ROUND-7 verified empirically
    # (max observed value 11 across the 81 rows; the old "navamsa" catalog label was a
    # mislabel, the decode was already sign-based and correct).
    "kalachakra": ("sign", 12),
    # karaka dasha lords are (role, planet_id) PAIRS, not bare planet ids.
    "karaka": ("karaka_pair", None),
    # element + special systems keep raw ids/names (no safe planet/sign mapping).
    "panchasvara": ("element", None),
    "sudharsana_chakra": ("special", None),
    "karana_chathuraaseethi_sama": ("karana", None),
}


def _dasha_lord_kind(name, catalog_lord_type):
    """Return the (domain, width) for a dasha system, honoring the per-system override."""
    kind = _DASHA_LORD_KIND.get(name)
    if kind:
        return kind
    widths = {"planet": 9, "sign": 12, "navamsa": 12, "nakshatra": 27}
    return (catalog_lord_type, widths.get(catalog_lord_type))


def _decode_dhasa_lord(name, lord_type, kind_width=None):
    """Return a NAME for a raw dasha lord id; return the raw id if not safely mappable.

    Range-checked (review §2.4): we only map ids within the valid range and leave any
    out-of-range value untouched, so an invalid id is never silently masked to a planet.
    `kind_width` optionally narrows/extends the allowed range per system.
    """
    try:
        idx = int(name)
    except Exception:
        return name
    if kind_width is not None and not (0 <= idx < kind_width):
        return name
    if lord_type == "planet":
        return PLANET_NAMES[idx] if 0 <= idx <= 8 else name
    if lord_type in ("sign", "navamsa", "navamsa_sign"):
        return RAASI_NAMES[idx] if 0 <= idx <= 11 else name
    if lord_type == "nakshatra":
        return NAKSHATRA_NAMES[idx] if 0 <= idx <= 26 else name
    # karana / element / special / karaka_pair -> no safe bare-id mapping; keep raw, don't guess.
    return name


def _flatten_chain(chain):
    out = []
    if isinstance(chain, (list, tuple)):
        for x in chain:
            if isinstance(x, (list, tuple)):
                out.extend(_flatten_chain(x))
            else:
                out.append(x)
    else:
        out.append(chain)
    return out


def _decode_chain_nested(chain, lord_type):
    """Decode a dasha lord chain keeping its NESTED hierarchy (not flattened).

    `chain` is the PyJHora nested lord structure, e.g. [maha, [antar, [pratyantar]]].
    The result mirrors the nesting so the AI can see mahadasha → antardasha → pratyantardasha
    explicitly instead of a flat list of names.
    """
    def dec(depth):
        if depth == "L":
            return "Lagna"
        # Some systems (e.g. karaka) use (role, planet_id) pairs — decode the id, keep the role.
        if isinstance(depth, (tuple, list)) and len(depth) == 2 and \
                isinstance(depth[1], (int, float)) and not isinstance(depth[1], bool) and \
                isinstance(depth[0], str) and depth[0] not in ("L",):
            return f"{depth[0]}({_decode_dhasa_lord(depth[1], lord_type)})"
        if isinstance(depth, (list, tuple)):
            return [dec(x) for x in depth]
        return _decode_dhasa_lord(depth, lord_type)
    return dec(chain)


def _dhasa_start(row):
    try:
        st = row[1] if len(row) > 1 else None
        if isinstance(st, (list, tuple)) and len(st) >= 3:
            frac_h = float(st[3]) if len(st) > 3 else 0.0
            return f"{int(st[0]):04d}-{int(st[1]):02d}-{int(st[2]):02d} {int(frac_h):02d}:{int((frac_h - int(frac_h)) * 60):02d}"
        return str(st)
    except Exception:
        return str(row[1]) if len(row) > 1 else ""


def build_dasha_decoded(env, h, jd, place, planet_positions=None, raw=None):
    """Decode every dasha system's raw lords into NAMES, tagging each lord_type.

    This is the AI-safe companion to `all_dashas`: it never leaves a planetary/sign id
    un-labelled, and for systems whose ids are NOT planets/signs (karana, nakshatra,
    element, special) it keeps the raw id and marks `decoded: raw` rather than guessing.
    """
    out = {}
    if raw is None:
        raw = _cached_all_dashas(env, h, jd, place, planet_positions)
    lt_map = {name: lt for name, _m, _f, lt in DASHA_CATALOG}
    for name, rows in sorted(raw.items()):
        lt = lt_map.get(name, "unknown")
        # resolve the exact per-system encoding (domain + width) for a correct decode
        kind, width = _dasha_lord_kind(name, lt)
        if isinstance(rows, dict) and "__error__" in rows:
            out[name] = {"status": "error", "lord_type": lt}
            continue
        if not isinstance(rows, (list, tuple)):
            continue
        decoded_rows = []
        for row in rows:
            if not isinstance(row, (list, tuple)) or len(row) < 2:
                continue
            ids = _flatten_chain(row[0])
            decoded = [_decode_dhasa_lord(i, kind, width) for i in ids]
            if kind in ("planet", "sign", "navamsa", "nakshatra"):
                # if any id failed to become a name, this type isn't safely mappable
                if not all(isinstance(d, str) and not str(d).isdigit() for d in decoded):
                    kind = "unknown"
                    decoded = ["?"] * len(ids)
            # Preserve the nested mahadasha→antardasha→… hierarchy for the AI.
            try:
                nested = _decode_chain_nested(row[0], kind)
            except Exception:
                nested = decoded
            dur = None
            if len(row) > 2:
                try:
                    dur = round(float(row[2]), 3)
                except Exception:
                    dur = None
            decoded_rows.append({"lords": decoded, "chain": nested,
                                 "depth": max(1, len(decoded)),
                                 "start": _dhasa_start(row), "duration_years": dur,
                                 "kind": kind})
        out[name] = {"lord_type": kind,
                     "decoded": "yes" if kind in ("planet", "sign", "navamsa", "nakshatra") else "raw",
                     "rows": decoded_rows}
    return out


def build_running_dashas(env, h, jd, place, jd_now=None, level_index=2):
    """Running (operative) dasha for EVERY system that exposes `get_running_dhasa_for_given_date`.

    Each system has a uniform `get_running_dhasa_for_given_date(jd_now, jd, place, dhasa_level_index)`.
    We call it and decode the operative lord chain per system's `lord_type` (planet/sign/navamsa/
    karana/element/special), preserving the nested mahadasha→antardasha→… hierarchy. The deepest
    level is the currently operating sub-period; each row gives its `start`/`end`.

    This makes the answer-space "running dasha + effective children for all systems" concrete:
    whatever system an astrologer wants to time with (Vimsottari, Yogini, Chara, Narayana,
    Shoola, Sudasa, Kalachakra, Ashtottari, Dwadasottari, Kaala, Tara, Karaka, Rashmi,
    Ashtaka-Varga, Panchasvara, Sudarshana-chakra, …) is now computed and decoded.
    """
    out = {}
    if jd_now is None:
        return {"note": "pass --now (YYYY-MM-DD) to compute running dashas"}
    done = 0
    attempted = 0
    skipped = []
    for name, module, _f, lt in DASHA_CATALOG:
        attempted += 1
        try:
            mod = _import_dhasa(module)
            fn = getattr(mod, "get_running_dhasa_for_given_date", None)
            if fn is None:
                skipped.append([name, "no get_running_dhasa_for_given_date"])
                continue
            # signature fallback (review §3.2): not every system accepts dhasa_level_index
            with _quiet():
                try:
                    rows = fn(jd_now, jd, place, dhasa_level_index=level_index)
                except TypeError:
                    try:
                        rows = fn(jd_now, jd, place)
                    except TypeError:
                        rows = fn(jd_now, jd)
            if not rows:
                skipped.append([name, "no rows"])
                continue
            decoded_rows = []
            if isinstance(rows, (list, tuple)):
                for row in rows:
                    if not isinstance(row, (list, tuple)) or not row:
                        continue
                    chain = row[0]
                    try:
                        lst = _decode_chain_nested(chain, lt)
                    except Exception:
                        lst = list(chain)
                    start = _tuple_iso(row[1]) if len(row) > 1 and isinstance(row[1], (list, tuple)) else None
                    end = _tuple_iso(row[2]) if len(row) > 2 and isinstance(row[2], (list, tuple)) else None
                    decoded_rows.append({
                        "chain": lst,
                        "deepest": lst[-1] if isinstance(lst, (list, tuple)) and lst else lst,
                        "start": start, "end": end,
                    })
            if decoded_rows:
                out[name] = {"lord_type": lt, "rows": decoded_rows}
                done += 1
            else:
                skipped.append([name, "rows decoded empty"])
        except Exception as e:
            skipped.append([name, str(e)[:120]])
    out["systems_computed"] = done
    out["systems_attempted"] = attempted
    out["systems_skipped"] = len(skipped)
    out["skip_reasons"] = skipped
    return out


def build_muhurta(env, h, jd, place):
    """Muhurta / daily-election data drawn from drik (open to the prompt)."""
    drik = env["drik"]
    out = {}
    def s(fn, *a, **k):
        try:
            with _quiet():
                return fn(*a, **k)
        except Exception as e:
            return {"__error__": str(e)}
    # All drik muhurta functions take BOTH (jd, place) — verified by introspection.
    # ROUND-5: the previous audit declared brahma/godhuli/vijaya/nishita/udhaya-lagna
    # "absent in this build" — that was a SPELLING artifact (the library spells them
    # brahma_muhurtha / godhuli_muhurtha / vijaya_muhurtha / nishita_kaala /
    # nishita_muhurtha / udhaya_lagna_muhurtha / tamil_jaamam). They exist and are now
    # exposed, together with muhurthas (all 30 day+night muhurtas), next_panchaka_days
    # and pushkara_yoga.
    pairs = ["abhijit_muhurta", "amrit_kaalam", "raahu_kaalam", "yamaganda_kaalam",
             "gulikai_kaalam", "durmuhurtam", "varjyam", "gauri_choghadiya",
             "shubha_hora", "panchaka_rahitha", "vidaal_yoga", "aadal_yoga",
             "anandhaadhi_yoga",
             "brahma_muhurtha", "godhuli_muhurtha", "sandhya_periods", "vijaya_muhurtha",
             "nishita_kaala", "nishita_muhurtha", "tamil_jaamam",
             "next_panchaka_days", "pushkara_yoga"]
    for name in pairs:
        fn = getattr(drik, name, None)
        if not fn:
            continue
        try:
            with _quiet():
                res = fn(jd, place)
            out[name] = res
        except Exception as e:
            out[name] = str(e)
    # pushkara_yoga decode: (0,'',±1)=none | (1,time,±1)=dwi | (2,time,±1)=tri pushkara
    try:
        _py = out.get("pushkara_yoga")
        if isinstance(_py, (list, tuple)) and not _py:
            out["pushkara_yoga"] = {"type": "none", "time": None, "direction": None}
        elif isinstance(_py, (list, tuple)) and len(_py) >= 2:
            _kind = {0: "none", 1: "dwi_pushkara", 2: "tri_pushkara"}.get(int(_py[0]), str(_py[0]))
            out["pushkara_yoga"] = {"type": _kind, "time": _py[1] if len(_py) > 1 else None,
                                    "direction": _py[2] if len(_py) > 2 else None}
    except Exception:
        pass
    # The 30 muhurthas of the day+night (15 each), decoded: name + auspicious flag + times.
    try:
        with _quiet():
            _mh = drik.muhurthas(jd, place)
        out["muhurthas_of_day"] = [
            {"name": nm, "auspicious_flag": fl,
             "start_time": (_hours_to_hhmm(tm[0]) if isinstance(tm, (list, tuple)) and tm else None),
             "end_time": (_hours_to_hhmm(tm[1]) if isinstance(tm, (list, tuple)) and len(tm) > 1 else None),
             "start_hours": (tm[0] if isinstance(tm, (list, tuple)) and tm else None),
             "end_hours": (tm[1] if isinstance(tm, (list, tuple)) and len(tm) > 1 else None)}
            for nm, fl, tm in _mh
        ]
        out["muhurthas_note"] = ("30 muhurtas: first 15 span sunrise->sunset (day), the next "
                                 "15 span sunset->sunrise (night). Times are LOCAL hours "
                                 "(night rows may exceed 24 = past midnight).")
    except Exception as e:
        out["muhurthas_of_day"] = str(e)
    # udhaya-lagna muhurtha: rising-sign windows from the previous ascendant entry.
    try:
        with _quiet():
            _ul = drik.udhaya_lagna_muhurtha(jd, place)
        out["udhaya_lagna_windows"] = [
            {"rising_sign_index": int(r) % 12, "rising_sign": RAASI_NAMES[int(r) % 12],
             "start_time": _hours_to_hhmm(t0), "end_time": _hours_to_hhmm(t1),
             "start_hours": t0, "end_hours": t1}
            for r, t0, t1 in _ul
        ]
    except Exception as e:
        out["udhaya_lagna_windows"] = str(e)
    # trikalam needs a keyword option ('raahu kaalam' | 'yamagandam' | 'gulikai').
    # Report ALL THREE options so the AI never has to guess one (review §5: only Rahu kaalam
    # was previously computed).
    out["trikalam"] = {}
    for opt in ("raahu kaalam", "yamagandam", "gulikai"):
        try:
            with _quiet():
                out["trikalam"][opt] = drik.trikalam(jd, place, option=opt)
        except Exception as e:
            out["trikalam"][opt] = str(e)
    # ROUND-5: drik1 (Kerala-tradition) extras — gauri chogadiya by drik1's own sunrise,
    # udhayadhi nazhikai (birth-time-to-sunrise in nazhikai) and drik1 day duration.
    try:
        drik1 = env.get("drik1")
        if drik1 is not None:
            d1 = {}
            try:
                with _quiet():
                    d1["gauri_chogadiya"] = drik1.gauri_chogadiya(jd, place)
            except Exception as e:
                d1["gauri_chogadiya"] = str(e)
            try:
                with _quiet():
                    _sr = drik.sunrise(jd, place)
                    _sunrise_hms = _sr[-2] if isinstance(_sr, (list, tuple)) and len(_sr) >= 2 else None
                    if _sunrise_hms:
                        _hh, _mm, _ss = (int(x) for x in str(_sunrise_hms).split(":")[:3])
                        d1["udhayadhi_nazhikai"] = drik1.udhayadhi_nazhikai(_tob(h), (_hh, _mm, _ss))
            except Exception as e:
                d1["udhayadhi_nazhikai"] = str(e)
            try:
                with _quiet():
                    d1["day_duration"] = drik1.day_duration(jd, place)
            except Exception as e:
                d1["day_duration"] = str(e)
            d1["note"] = ("drik1 = PyJHora's alternate (Kerala-tradition) panchanga engine. "
                          "udhayadhi_nazhikai = [time from sunrise to birth as 'H:M:S', "
                          "nazhikai value]; 1 nazhikai = 24 minutes.")
            out["drik1_extras"] = d1
    except Exception as e:
        out["drik1_extras"] = str(e)
    return out


def _festivals_for_day(vratha_mod, tithi=None, nakshatra=None):
    """Query `get_festival` for the birth panchanga criteria and union matching festivals.

    PyJHora's `get_festival` matches festival rows against a single criterion (AND), so a row
    can be keyed by tithi OR nakshatra; we query each separately and merge unique names.
    """
    merged = []
    seen = set()
    for kwargs in ({"tithi": tithi}, {"nakshatra": nakshatra}):
        try:
            with _quiet():
                rows = vratha_mod.get_festival(**kwargs)
            if not rows:
                continue
            for row in rows:
                nm = row.get("Festival_en") or row.get("Festival_name") or str(row)
                if nm not in seen:
                    seen.add(nm)
                    merged.append({"festival": nm, "criteria": {k: v for k, v in kwargs.items() if v is not None}})
        except Exception:
            continue
    return {"matching_festivals": merged,
            "note": "Empty list = no named festival falls on this exact tithi/nakshatra."}


# Optional (pyIslam-free) vratha / festival date finders over the birth solar year.
# ROUND-5: + manvaadhi, srartha (union finder), srartha_yoga, chandra_dharshan,
# moondraam_pirai — same (place, start, end) signature, verified against the build.
_VRATHA_FINDERS = [
    "sankranti_dates", "pournami_dates", "amavasya_dates", "ekadhashi_dates",
    "pradosham_dates", "shivarathri_dates", "durgashtami_dates", "vinayaka_chathurthi_dates",
    "sankatahara_chathurthi_dates", "sashti_dates", "kaalashtami_dates",
    "mahalaya_paksha_dates", "yugadhi_dates", "sathyanarayana_puja_dates", "ashtaka_dates",
    "manvaadhi_dates", "srartha_dates", "srartha_yoga_dates", "chandra_dharshan_dates",
    "moondraam_pirai_dates",
]


def _vratha_dates(vratha_mod, drik, h, place, max_each=8):
    """Run the pyIslam-free vratha/festival date-finders over the birth solar year.

    Each finder returns [(date_tuple, ...values, label), ...]; we compact to the ISO date +
    label (first `max_each`) so the section stays readable while still giving real windows.
    """
    year = h.Date.year
    start = drik.Date(year, 1, 1); end = drik.Date(year, 12, 31)
    out = {}
    for name in _VRATHA_FINDERS:
        fn = getattr(vratha_mod, name, None)
        if not fn:
            continue
        try:
            with _quiet():
                rows = fn(place, start, end)
            entries = []
            for row in rows or []:
                if not isinstance(row, (list, tuple)) or not row:
                    continue
                dat = row[0]
                if isinstance(dat, (list, tuple)) and len(dat) >= 3:
                    iso = f"{int(dat[0]):04d}-{int(dat[1]):02d}-{int(dat[2]):02d}"
                else:
                    iso = str(dat)
                label = row[-1] if isinstance(row, (list, tuple)) and len(row) > 1 else ""
                entries.append({"date": iso, "label": _strip_html(label)})
            out[name] = {"count": len(entries), "dates": entries[:max_each]}
        except Exception as e:
            out[name] = str(e)
    # ---- ROUND-5: janma-anchored date finders + tithi pravesha -----------------
    # Dates in the birth solar year when the birth TITHI / NAKSHATRA / YOGA recur
    # (tithi_dates / nakshathra_dates / yoga_dates need explicit index lists).
    try:
        birth_jd = None
        try:
            from jhora import utils as _u
            birth_jd = _u.julian_day_number((h.Date.year, h.Date.month, h.Date.day), _tob(h))
        except Exception:
            birth_jd = None
        if birth_jd is not None:
            with _quiet():
                _ti = int(drik.tithi(birth_jd, place)[0])
                _ni = int(drik.nakshatra(birth_jd, place)[0])
                _yi = int(drik.yogam(birth_jd, place)[0])
            for lab, fn, idx in (("janma_tithi_dates", getattr(vratha_mod, "tithi_dates", None), [_ti]),
                                 ("janma_nakshatra_dates", getattr(vratha_mod, "nakshathra_dates", None), [_ni]),
                                 ("janma_yoga_dates", getattr(vratha_mod, "yoga_dates", None), [_yi])):
                if not fn:
                    continue
                try:
                    with _quiet():
                        rows = fn(place, start, end, idx)
                    entries = []
                    for row in rows or []:
                        dat = row[0]
                        if isinstance(dat, (list, tuple)) and len(dat) >= 3:
                            iso = f"{int(dat[0]):04d}-{int(dat[1]):02d}-{int(dat[2]):02d}"
                        elif hasattr(dat, "year"):
                            iso = f"{int(dat.year):04d}-{int(dat.month):02d}-{int(dat.day):02d}"
                        else:
                            iso = str(dat)
                        label = row[-1] if len(row) > 1 else ""
                        entries.append({"date": iso, "label": _strip_html(label)})
                    out[lab] = {"index": idx[0], "count": len(entries),
                                "dates": entries[:max_each]}
                except Exception as e:
                    out[lab] = str(e)
        # tithi pravesha: the current-year date(s) with the SAME tithi + lunar month as
        # birth (the person's "tithi birthday"). year_number=None -> current year.
        # Library contract: [date, tithi_time, month_tithi_name] — but THIS build returns
        # numeric tithi_time (fractional hours, may exceed 24) and a numeric third field,
        # so time is formatted with the day-shift convention and a numeric label is kept
        # raw (flagged) instead of pretending to be a name.
        try:
            tp = getattr(vratha_mod, "tithi_pravesha", None)
            if tp:
                with _quiet():
                    rows = tp(drik.Date(h.Date.year, h.Date.month, h.Date.day), _tob(h), place)
                entries = []
                for row in rows or []:
                    dat, tim, lab = (list(row) + [None, None, None])[:3]
                    if isinstance(dat, (list, tuple)) and len(dat) >= 3:
                        iso = f"{int(dat[0]):04d}-{int(dat[1]):02d}-{int(dat[2]):02d}"
                    elif hasattr(dat, "year"):
                        iso = f"{int(dat.year):04d}-{int(dat.month):02d}-{int(dat.day):02d}"
                    else:
                        iso = str(dat)
                    ent = {"date": iso}
                    if isinstance(tim, (int, float)) and not isinstance(tim, bool):
                        ent["time"] = _hours_to_hhmm(tim)
                        ent["time_raw_hours"] = round(float(tim), 4)
                    elif tim is not None:
                        ent["time"] = str(tim)
                    if isinstance(lab, str):
                        ent["tithi_month_name"] = _strip_html(lab)
                    elif lab is not None:
                        ent["label_raw"] = (round(float(lab), 4)
                                            if isinstance(lab, (int, float)) and not isinstance(lab, bool)
                                            else str(lab))
                    entries.append(ent)
                out["tithi_pravesha_current_year"] = {
                    "count": len(entries),
                    "dates": entries,
                    "note": ("Birth-tithi anniversary dates in the current year (same tithi AND lunar "
                             "month as birth). If label_raw is numeric, this build returned no name "
                             "string — quote the date/time only."),
                }
        except Exception as e:
            out["tithi_pravesha_current_year"] = {"dates": [], "__error__": str(e)}
    except Exception:
        pass
    return out


def build_vratha(env, h, jd, place):
    """Vratha / festival anchors and panchaka (uses drik.panchaka where available)."""
    drik = env["drik"]
    out = {}
    def s(fn, *a, **k):
        try:
            with _quiet():
                return fn(*a, **k)
        except Exception as e:
            return {"__error__": str(e)}
    try:
        out["panchaka_days"] = s(drik.panchaka_rahitha, jd, place)
    except Exception as e:
        out["panchaka_days"] = str(e)
    try:
        out["shiva_vaasa"] = s(drik.shiva_vaasa, jd, place)
    except Exception as e:
        out["shiva_vaasa"] = str(e)
    try:
        out["agni_vaasa"] = s(drik.agni_vaasa, jd, place)
    except Exception as e:
        out["agni_vaasa"] = str(e)
    try:
        out["yogini_vaasa"] = s(drik.yogini_vaasa, jd, place)
    except Exception as e:
        out["yogini_vaasa"] = str(e)
    try:
        out["disha_shool"] = s(drik.disha_shool, jd, place)
    except Exception as e:
        out["disha_shool"] = str(e)
    # ---- Festival / vratha / pancha-paksha extras ----
    try:
        from jhora.panchanga import vratha as _vr
        # Daily festival listing needs the optional 'pyIslam' package (hijri calendar rows).
        # The aliased import is an availability GUARD: if it fails, the except writes the
        # documented not_computed note instead.
        try:
            import pyIslam as _pyi_day  # optional availability guard
            if _pyi_day is not None:
                out["festivals_of_day"] = s(_vr.get_festivals_of_the_day, jd, place)
        except Exception:
            out["festivals_of_day"] = \
                "not_computed: optional 'pyIslam' package not installed (needed for hijri rows)"
        # ROUND-5: range festival query (also pyIslam-dependent inside PyJHora) over the
        # solar year following birth.
        try:
            import pyIslam as _pyi_year  # optional availability guard
            if _pyi_year is not None:
                _d0 = drik.Date(h.Date.year, h.Date.month, h.Date.day)
                _d1 = drik.Date(h.Date.year + 1, h.Date.month, h.Date.day)
                with _quiet():
                    out["festivals_year_after_birth"] = _vr.get_festivals_between_the_dates(_d0, _d1, place)
        except Exception:
            out["festivals_year_after_birth"] = \
                "not_computed: optional 'pyIslam' package not installed (needed for hijri rows)"
        # Festivals for the day via panchanga criteria (numeric tithi + nakshatra indices).
        _ti = None; _ni = None
        with _quiet():
            _tr = drik.tithi(jd, place); _nr = drik.nakshatra(jd, place)
        if isinstance(_tr, (list, tuple)) and _tr:
            _ti = int(_tr[0])
        if isinstance(_nr, (list, tuple)) and _nr:
            _ni = int(_nr[0])
        out["festival_query"] = _festivals_for_day(_vr, tithi=_ti, nakshatra=_ni)
        # Vratha / festival dates for the solar year around birth (pyIslam-free).
        out["vratha_dates"] = _vratha_dates(_vr, drik, h, place)
    except Exception as e:
        out["festival_query"] = str(e)
    try:
        import jhora.panchanga.pancha_paksha as _pp
        dob_t = (h.Date.year, h.Date.month, h.Date.day)
        nak = None
        with _quiet():
            nak = drik.nakshatra(jd, place)
        pksh = 1
        with _quiet():
            pksh = _pp._get_paksha(jd, place)
        # _get_paksha returns an int here (verified); be defensive across versions
        # (it *could* return a tuple/list first-element, review §2.9).
        try:
            pksh = int(pksh)
        except Exception:
            if isinstance(pksh, (list, tuple)) and pksh:
                try:
                    pksh = int(pksh[0])
                except Exception:
                    pksh = 1
            else:
                pksh = 1
        bird = None
        if nak and isinstance(nak, (list, tuple)):
            try:
                bird = _pp._get_birth_bird_from_nakshathra(int(nak[0]), int(pksh))
            except Exception:
                bird = None
        bird = bird[0] if isinstance(bird, tuple) else bird
        out["pancha_paksha"] = s(_pp.construct_pancha_pakshi_information,
                                 dob_t, _tob(h), place, nakshathra_bird_index=bird)
    except Exception as e:
        out["pancha_paksha"] = str(e)
    # ROUND-16: vratha.special_vratha_dates — NEXT occurrence of each vratha type.
    # Round-14 rejected find_muhurtha_between_dates (does not exist). This API does.
    try:
        from jhora.panchanga import vratha as _vr16
        _start16 = drik.Date(h.Date.year, h.Date.month, h.Date.day)
        _types16 = [
            "pradosham", "sankranti", "amavasya", "pournami", "ekadhashi", "sashti",
            "sankatahara_chathurthi", "vinayaka_chathurthi", "shivarathri",
            "chandra_dharshan", "moondraam_pirai", "srartha", "ashtaka", "manvaadhi",
            "yugadhi", "mahalaya_paksha", "sathyanarayana_puja", "durgashtami",
            "kaalashtami",
        ]
        _nxt = {}
        for _vt in _types16:
            try:
                with _quiet():
                    _rows = _vr16.special_vratha_dates(place, _start16, None,
                                                       vratha_type=_vt)
                _ents = []
                for _row in (_rows or [])[:1]:
                    if not isinstance(_row, (list, tuple)) or not _row:
                        continue
                    _dat = _row[0]
                    if isinstance(_dat, (list, tuple)) and len(_dat) >= 3:
                        _iso = "%04d-%02d-%02d" % (int(_dat[0]), int(_dat[1]), int(_dat[2]))
                    elif hasattr(_dat, "year"):
                        _iso = "%04d-%02d-%02d" % (int(_dat.year), int(_dat.month), int(_dat.day))
                    else:
                        _iso = str(_dat)
                    _ent = {"date": _iso}
                    if (len(_row) > 2 and isinstance(_row[1], (int, float))
                            and isinstance(_row[2], (int, float))
                            and not isinstance(_row[1], bool)):
                        _ent.update(_span_times(_row[1], _row[2]))
                    elif (len(_row) > 1 and isinstance(_row[1], (int, float))
                          and not isinstance(_row[1], bool)):
                        _ent["time"] = _hours_to_hhmm(_row[1])
                    _lab = _row[-1] if len(_row) > 1 and isinstance(_row[-1], str) else None
                    if _lab:
                        _ent["label"] = _strip_html(_lab)
                    _ents.append(_ent)
                _nxt[_vt] = _ents[0] if _ents else None
            except Exception as e:
                _nxt[_vt] = {"__error__": str(e)}
        out["next_special_vratha"] = {
            "from_birth_date": "%04d-%02d-%02d" % (h.Date.year, h.Date.month, h.Date.day),
            "by_type": _nxt,
            "__note__": ("ROUND-16: vratha.special_vratha_dates(..., end_date=None) = the "
                         "NEXT occurrence of each vratha type after birth. Year-range "
                         "lists remain under vratha_dates (dedicated finders).")}
    except Exception as e:
        out["next_special_vratha"] = {"__error__": str(e)}
    return out


def build_eclipse(env, h, jd, place):
    # REVIEW FIX: `drik.next_solar_eclipse` / `drik.next_lunar_eclipse` are obsolete — they
    # return raw numeric JD lists (e.g. `[912, (2453646.99, …)]`), not dated eclipses. The
    # current public API is the `jhora.panchanga.eclipse` module (`ec.next_solar_eclipse` /
    # `next_lunar_eclipse`), which returns real dated + typed eclipses. We use `ec.*` for
    # next/prev and keep `drik.is_solar_eclipse` only for the "is it an eclipse now" flag
    # (there is no `drik.is_lunar_eclipse` in this build — verified absent).
    drik = env["drik"]
    out = {}
    def s(fn, *a, **k):
        try:
            with _quiet():
                return fn(*a, **k)
        except Exception as e:
            return {"__error__": str(e)}
    try:
        from jhora.panchanga import eclipse as ec
    except Exception as e:
        return {"__error__": str(e)}
    out["solar_eclipse"] = s(drik.is_solar_eclipse, jd, place)
    try:
        out["is_lunar_eclipse"] = s(drik.is_lunar_eclipse, jd, place)
    except Exception:
        out["is_lunar_eclipse"] = {"__error__": "drik.is_lunar_eclipse not available in this build"}
    out["next_solar_eclipse"] = s(ec.next_solar_eclipse, jd, place, show_maximum_eclipse_global_location=True)
    out["next_lunar_eclipse"] = s(ec.next_lunar_eclipse, jd, place, show_maximum_eclipse_global_location=True)
    out["prev_solar_eclipse"] = s(ec.next_solar_eclipse, jd, place, search_backward=True, show_maximum_eclipse_global_location=True)
    return out


def build_match(env, h, jd, place, planet_positions, other):
    """Marriage compatibility vs a partner's coordinates/dob (Ashtakoota porutham)."""
    try:
        from jhora.horoscope.match import compatibility as comp
    except Exception as e:
        return {"__error__": str(e)}
    out = {}
    try:
        date = dt.datetime.strptime(other["date"], "%Y-%m-%d").date()
        # PyJHora's Horoscope calls exit() (SystemExit, NOT an Exception) when the partner
        # has neither a place nor coordinates. Guard against that so a bare `--match-date`
        # reports a clean error instead of terminating the whole process (review §3).
        has_loc = bool(other.get("place") or
                       (other.get("lat") is not None and other.get("lon") is not None))
        if not has_loc:
            return {"__error__": "match requires --match-lat/--match-lon (or --match-place) for the partner"}
        if other.get("place"):
            # ROUND-10 hardening: same SystemExit guard as the coordinate branch — a
            # place string the geocoder cannot resolve must not kill the whole run.
            try:
                oh = env["Horoscope"](
                    place_with_country_code=other["place"],
                    date_in=env["drik"].Date(date.year, date.month, date.day),
                    birth_time=other["time"])
            except SystemExit:
                return {"__error__": ("partner Horoscope could not geocode --match-place "
                                      "'%s' (PyJHora calls exit()); pass partner "
                                      "--match-lat/--match-lon (and --match-tz) instead"
                                      % other.get("place"))}
            if other.get("tz") is not None:
                out["timezone_note"] = (
                    "--match-place was given: PyJHora geocodes the partner's timezone "
                    "from the place name, so an explicit --match-tz (%s) is NOT applied "
                    "in this mode (library behavior). Use --match-lat/--match-lon with "
                    "--match-tz for manual control." % other["tz"])
        else:
            # ROUND-9 hardening (review Issue 6): Horoscope treats a None timezone_offset
            # as incomplete location data and calls exit() — a SystemExit (BaseException)
            # that the section-wide `except Exception` would NOT catch, killing the whole
            # process. Guard the direct-call path so build_match can never terminate the run.
            # ROUND-10 FIX (review P1-3, CONFIRMED real): the partner tz used to default
            # to the native's --tz in main() — but --tz has an argparse default (5.5), so
            # the Round-9 fallback chain ALWAYS yielded 5.5 and every coordinate-mode
            # partner was silently forced to IST. New resolution order:
            #   1) explicit --match-tz
            #   2) partner coordinates → DST-aware offset via the library's own
            #      utils.get_place_timezone_offset(lat, lon, jd_local)
            #   3) the native's timezone (last resort) — every case disclosed.
            _ptz = other.get("tz")           # None unless --match-tz was given
            _ptz_src = "explicit --match-tz"
            if _ptz is None:
                try:
                    _pt = str(other.get("time") or "12:00:00")
                    _pjd_local = env["utils"].julian_day_number(
                        (date.year, date.month, date.day),
                        (int(_pt[:2]), int(_pt[3:5]), 0))
                    # ROUND-13 (review item 2): signature-adaptive binding via
                    # _place_tz_offset — on the pinned build this binds jd_local=
                    # exactly as before (audits 12d/13b); on builds naming the
                    # parameter `jd` (or 2-parameter legacy builds) it adapts
                    # instead of raising a TypeError that would silently fall back
                    # to the native's timezone.
                    _rz = _place_tz_offset(
                        env["utils"], other.get("lat"), other.get("lon"),
                        _pjd_local)
                    if _rz is not None:
                        _ptz = float(_rz)
                        _ptz_src = ("resolved from the partner's coordinates via the "
                                    "library's utils.get_place_timezone_offset "
                                    "(DST-aware at the partner's birth date)")
                except Exception:
                    _ptz = None
            if _ptz is None:
                _ptz = getattr(place, "timezone", None)
                if _ptz is None:
                    _ptz = getattr(h, "timezone_offset", None)
                _ptz_src = ("the native's timezone (last-resort fallback — the partner's "
                            "coordinates could not be resolved to a timezone)")
            other["tz"] = _ptz
            try:
                oh = env["Horoscope"](
                    latitude=other.get("lat"), longitude=other.get("lon"),
                    timezone_offset=_ptz,
                    date_in=env["drik"].Date(date.year, date.month, date.day),
                    birth_time=other["time"])
            except SystemExit:
                return {"__error__": ("partner Horoscope rejected the given location/timezone "
                                      "(PyJHora calls exit() — e.g. timezone_offset=None); "
                                      "pass an explicit --match-tz or --match-place")}
            if _ptz_src != "explicit --match-tz":
                out["timezone_note"] = (
                    "--match-tz was omitted; the partner timezone used was %s h — %s. "
                    "If this is wrong, re-run with an explicit --match-tz — Moon "
                    "nakshatra and all poruthams shift with it."
                    % (round(float(_ptz), 4) if _ptz is not None else None, _ptz_src))
        opp = env["charts"].rasi_chart(oh.julian_day, oh.Place)
        op = _planetary_longitude(opp)
        mp = _planetary_longitude(planet_positions)
        # ROUND-10 (review P2-6, ADOPTED): partner Manglik comparison — the identical
        # dosha.manglik calls/parameters used by the native's doshas section (reference
        # = lagna, 2nd house included, BV Raman exceptions toggled), run on the
        # partner's OWN rasi positions, plus the traditional mutual-neutralization
        # verdict (disclosed as tradition, not a library output).
        try:
            _dosha = env["dosha"]
            def _mang(parts):
                with _quiet():
                    r_exc = _dosha.manglik(parts, apply_exceptions=True)
                    r_raw = _dosha.manglik(parts, apply_exceptions=False)
                return {
                    "present": (bool(r_exc[0]) if isinstance(r_exc, (list, tuple)) else r_exc),
                    "present_before_exceptions": (bool(r_raw[0])
                                                  if isinstance(r_raw, (list, tuple)) else r_raw),
                    "exceptions_applied": (bool(r_exc[1]) if isinstance(r_exc, (list, tuple))
                                           and len(r_exc) > 1 else None),
                    "exception_indices": (list(r_exc[2]) if isinstance(r_exc, (list, tuple))
                                          and len(r_exc) > 2 else None),
                }
            _mn = _mang(planet_positions)
            _mpn = _mang(opp)
            _nf, _pf = _mn["present"], _mpn["present"]
            if _nf and _pf:
                _verdict = ("both partners are Manglik — traditionally treated as "
                            "mutually neutralizing (no dosha between them)")
            elif _nf is False and _pf is False:
                _verdict = "balanced — neither partner is Manglik"
            else:
                _side = "native" if _nf else "partner"
                _verdict = ("one-sided Manglik (the %s) — traditional texts advise "
                            "caution/remedies; compare present_before_exceptions and "
                            "exception_indices on both sides for mitigations" % _side)
            out["manglik_compatibility"] = {
                "native": _mn, "partner": _mpn, "verdict": _verdict,
                "convention": ("dosha.manglik(positions, apply_exceptions=True/False) — "
                               "identical to the native's doshas.manglik section; the "
                               "partner is evaluated on the partner's own rasi positions. "
                               "The mutual-neutralization verdict is the common "
                               "traditional reading, not a PyJHora output."),
            }
        except Exception as e:
            out["manglik_compatibility"] = {"__error__": str(e)}
        # ROUND-8 FIX (review P1-5, CONFIRMED): Ashtakoota is DIRECTIONAL — varna, dina/
        # tara, yoni, maitri etc. are evaluated bride→groom. The old code hardwired
        # native=male, partner=female; a female native got a reversed (wrong) reading.
        # --gender / --match-gender now decide which side feeds the boy_*/girl_* inputs.
        # Output keys boy_moon/girl_moon are KEPT (prompt compatibility) but each carries
        # an explicit `whose` label so nothing is ambiguous.
        native_gender = str(other.get("gender") or "male").lower()
        partner_gender = str(other.get("match_gender") or "female").lower()
        if native_gender == partner_gender:
            male_is_native = True
            _g_note = ("both sides given as '%s' — Ashtakoota is directional; the NATIVE "
                       "was used as the male/boy side by convention. Pass opposite "
                       "--gender/--match-gender values for the classical direction."
                       % native_gender)
        else:
            male_is_native = (native_gender == "male")
            _g_note = None
        boy_pp, girl_pp = (mp, op) if male_is_native else (op, mp)
        out["genders"] = {"native": native_gender, "partner": partner_gender,
                          "boy_side": "native" if male_is_native else "partner",
                          "girl_side": "partner" if male_is_native else "native"}
        if _g_note:
            out["genders"]["note"] = _g_note
        # nakshatra numbers are 1-based; index from the longitude
        boy_nak = _nakshatra_index(boy_pp["Moon"]) + 1
        girl_nak = _nakshatra_index(girl_pp["Moon"]) + 1
        boy_pad = _nakshatra_of_long(boy_pp["Moon"])[1]
        girl_pad = _nakshatra_of_long(girl_pp["Moon"])[1]
        boy_nak_name = _nakshatra_of_long(boy_pp["Moon"])[0]
        girl_nak_name = _nakshatra_of_long(girl_pp["Moon"])[0]
        out["boy_moon"] = {"nakshatra": boy_nak_name, "nakshatra_number": boy_nak,
                           "pada": boy_pad,
                           "whose": ("native (%s)" % native_gender) if male_is_native
                                    else ("partner (%s)" % partner_gender)}
        out["girl_moon"] = {"nakshatra": girl_nak_name, "nakshatra_number": girl_nak,
                            "pada": girl_pad,
                            "whose": ("partner (%s)" % partner_gender) if male_is_native
                                     else ("native (%s)" % native_gender)}
        try:
            # ROUND-7 / PyJHora 5.0: `method=None` now resolves to const.default_chart_type
            # and produces a BOOLEAN pass/fail score list instead of the classic numeric
            # 36-point Ashtakoota. We pin method='North' (the 4.8.7 default behavior) unless
            # the user asks otherwise via --match-method, and record which mode was used.
            _mm = env.get("_match_method") or "North"
            a = comp.Ashtakoota(boy_nakshatra_number=boy_nak, boy_paadham_number=boy_pad,
                                girl_nakshatra_number=girl_nak, girl_paadham_number=girl_pad,
                                method=_mm)
            # ROUND-11 (review P2-5): South now routes to the *_south predicates
            # below (previously a silent no-op — 5.0's compatibility_score() never
            # branches on method, and its porutham wrappers branch on CHART_STYLE
            # enums, not the 'South' string).
            _scale_txt = ("numeric 0..36 Ashtakoota points"
                          if str(_mm).lower() == "north" else
                          ("boolean Dasa-Koota (South/Tamil 10 poruthams); total = passes 0..10"
                           if str(_mm).lower() == "south" else
                           "method-dependent (5.0: non-North methods may return boolean pass/fail lists)"))
            out["match_method"] = {"method": _mm, "scale": _scale_txt}
            if str(_mm).lower() == "south":
                # ---- ROUND-11 (review P2-5): SOUTH / DASA-KOOTA (Tamil 10 poruthams)
                # The ten dedicated *_south predicates are called explicitly. The
                # library's own compatibility_score_south is BURIED inside a docstring
                # tomb in 5.0 (its def sits between two stray triple-quotes upstream —
                # hasattr(Ashtakoota, 'compatibility_score_south') is False), so the
                # total is computed wrapper-side as the sum of the ten booleans —
                # exactly the library's visible formula, max =
                # compatibility.max_compatibility_score_south (10). Varna and Naadi
                # are NOT part of Dasa Koota (the buried function hardcodes False).
                def _ps(fn):
                    try:
                        return bool(fn())
                    except Exception:
                        return "__error__"
                out["dasa_koota_breakdown"] = {
                    "dina": _ps(a.dina_porutham_south),
                    "gana": _ps(a.gana_porutham_south),
                    "mahendra": _ps(a.mahendra_porutham_south),
                    "sthree_dheerga": _ps(a.sthree_dheerga_porutham_south),
                    "yoni": _ps(a.yoni_porutham_south),
                    "raasi": _ps(a.raasi_porutham_south),
                    "raasi_adhipathi": _ps(a.raasi_adhipathi_porutham_south),
                    "vasiya": _ps(a.vasiya_porutham_south),
                    "rajju": _ps(a.rajju_porutham_south),
                    "vedha": _ps(a.vedha_porutham_south),
                }
                _dk_passes = sum(1 for _v in out["dasa_koota_breakdown"].values()
                                 if _v is True)
                out["dasa_koota_total"] = _dk_passes
                out["dasa_koota_max"] = int(getattr(
                    comp, "max_compatibility_score_south", 10))
                out["dasa_koota_note"] = (
                    "ROUND-11 (review P2-5): South-Indian / Tamil DASA-KOOTA "
                    "matching — ten BOOLEAN poruthams; dasa_koota_total = number "
                    "passed out of dasa_koota_max (10). The 36-point North Ashtakoota "
                    "scale does NOT apply; varna and naadi are excluded from this "
                    "system by design. dina_porutham_south counts from the BOY's "
                    "nakshatra (Tamil panchangam convention — direction-sensitive; "
                    "the engine keeps its boy/girl slot assignment). Common Tamil "
                    "practice weights rajju and dina most heavily; 7+/10 is "
                    "conventionally workable, 8+/10 good. Upstream quirk disclosed: "
                    "the library's compatibility_score_south is commented out inside "
                    "a docstring in 5.0, so the total is summed here from the ten "
                    "library predicates (the library's own formula).")
            else:
                # Each porutham returns (score, max_score). Record score+max for the AI.
                def _p(fn):
                    try:
                        r = fn()
                        return {"score": _clean(r[0]), "max": r[1]} if isinstance(r, (tuple, list)) and len(r) >= 2 else _clean(r)
                    except Exception:
                        return {"__error__": "n/a"}
                out["poruthams"] = {
                    "varna": _p(a.varna_porutham),
                    "vasiya": _p(a.vasiya_porutham),
                    "dina_tara": _p(a.dina_porutham),
                    "gana": _p(a.gana_porutham),
                    "yoni": _p(a.yoni_porutham),
                    "maitri": _p(a.maitri_porutham),
                    "raasi": _p(a.raasi_porutham),
                    "naadi": _p(a.naadi_porutham),
                }
                score = a.compatibility_score()
                # score list = [varna,vasiya,gana,dina,yoni,maitri,raasi,naadi,total,mahendra,vedha,rajju,sthree]
                if isinstance(score, (list, tuple)) and len(score) >= 9:
                    out["ashtakoota_total"] = float(score[8])
                    out["ashtakoota_max"] = 36
                    out["ashtakoota_breakdown"] = {
                        "varna": score[0], "vasiya": score[1], "gana": score[2],
                        "dina_tara": score[3], "yoni": score[4], "maitri": score[5],
                        "raasi": score[6], "naadi": score[7],
                    }
                    extra = len(score) >= 13
                    if extra:
                        out["special_poruthams"] = {
                            "mahendra": _clean(score[9]), "vedha": _clean(score[10]),
                            "rajju": _clean(score[11]), "sthree_dheerga": _clean(score[12]),
                        }
                else:
                    out["ashtakoota_score"] = score
        except Exception as e:
            out["ashtakoota_raw"] = str(e)
    except Exception as e:
        out["match"] = str(e)
    return out


def _clean(v):
    """Best-effort turn a porutham return value into a printable value."""
    if isinstance(v, (list, tuple)):
        return [str(x) for x in v]
    if isinstance(v, bool):
        return "Yes" if v else "No"
    if isinstance(v, (int, float)):
        return v
    if isinstance(v, str):
        return _strip_html(v)
    return str(v)


def _nakshatra_index(longitude):
    span = 360.0 / 27.0
    return int(longitude / span) % 27


def _varga_factors():
    """The divisional factors `vargas_all` covers.

    We START from PyJHora's own standard list `const.division_chart_factors` (which in this
    build is [1,2,3,4,5,6,7,8,9,10,11,12,16,20,24,27,30,40,45,60,81,108,144]) and append the
    extended factors we verified resolve at runtime via `charts.divisional_chart`'s custom
    fallback (D72 Shashtiamsa and D150 Nadiamsa — neither is in the standard list here).
    Each factor is labeled `standard` vs `extended` so the *comment* no longer over-claims
    that every listed factor is standard (review §3: "D72 is not in divisional_chart_functions").
    """
    std = []
    try:
        import jhora.const as _c
        std = [int(x) for x in getattr(_c, "division_chart_factors", []) if isinstance(x, (int, float, str))]
    except Exception:
        std = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 16, 20, 24, 27, 30, 40, 45, 60, 81, 108, 144]
    # ROUND-7 / PyJHora 5.0: D150 (Nadiamsa) is NOW STANDARD upstream (it appears in
    # const.division_chart_factors), so it is tagged `standard` automatically; only D72
    # remains an extended factor and resolves via the generic custom-fallback path.
    extended = [72, 150]        # dedup below keeps each factor once; tagging follows the
                                # live const.division_chart_factors of the installed build
    allf = list(dict.fromkeys([int(x) for x in std] + extended))
    std_set = set(int(x) for x in std)
    return allf, std_set


# _VARGAS_ALL = the full ordered factor list; _STANDARD_VARGAS = the standard subset.
_VARGAS_ALL, _STANDARD_VARGAS = _varga_factors()


def _dasha_catalog_verify():
    """Introspect the installed PyJHora and report which catalog module/function pairs
    actually resolve. This makes the catalog's correctness verifiable instead of assumed
    (it answers the recurring 'speculative dasha entries' question empirically)."""
    import importlib as _il
    verified = {}
    for name, module, fn_name, lt in DASHA_CATALOG:
        try:
            mod = _il.import_module("jhora.horoscope.dhasa." + module)
            has_fn = _resolve_dasha_fn(mod, fn_name) is not None
            has_run = hasattr(mod, "get_running_dhasa_for_given_date")
            verified[name] = {"module": module, "fn": fn_name,
                              "fn_ok": bool(has_fn), "running_ok": bool(has_run),
                              "lord_type": lt}
        except Exception as e:
            verified[name] = {"module": module, "fn": fn_name,
                              "fn_ok": False, "running_ok": False, "error": str(e)[:80]}
    ok = [n for n, v in verified.items() if v.get("fn_ok")]
    return {"total": len(verified), "fn_ok": len(ok),
            "catalog_validated": ok, "catalog_missing": [n for n, v in verified.items() if not v.get("fn_ok")]}


# Cache the introspection so `_features()` / `all_dashas` only verify the catalog ONCE per
# process instead of on every chart run (review §2.3).
_DASHA_CATALOG_VALIDATION = None


def _get_dasha_catalog_validation():
    global _DASHA_CATALOG_VALIDATION
    if _DASHA_CATALOG_VALIDATION is None:
        _DASHA_CATALOG_VALIDATION = _dasha_catalog_verify()
    return _DASHA_CATALOG_VALIDATION


def _features():
    """Machine-readable feature-availability summary (review §19)."""
    import importlib as _il
    # count systems with a running-dhasa API
    run = 0
    for name, module, _f, _lt in DASHA_CATALOG:
        try:
            if getattr(_il.import_module("jhora.horoscope.dhasa." + module),
                       "get_running_dhasa_for_given_date", None):
                run += 1
        except Exception:
            continue
    return {
        "sections": len([k for k in SECTIONS if k != "transits"]),
        "default_sections": [k for k, (_, on) in SECTIONS.items() if on],
        "full_sections": [k for k in SECTIONS],
        "dasha_systems": len(DASHA_CATALOG),
        "dasha_catalog_validated": _get_dasha_catalog_validation(),
        "dasha_systems_with_running": run,
        "divisional_charts": len(_VARGAS_ALL),
        "yogas_scanned_dynamically": "yogas_all" in SECTIONS,
        "festival_date_finders": len(_VRATHA_FINDERS),
        "requires_flag": {
            "transits": "--transit YYYY-MM-DD",
            "fine_dasha": "--fine-dasha 'START,END'",
            "match": "--match-date ... (partner DOB)",
            "running_dashas": "--now YYYY-MM-DD",
            "full_deep_tables": "--full (or --sections ...)",
            "experiments": "--experiments (EXPERIMENTAL research predictors; --experiments-deep "
                           "adds the heavy degree-locked scan; --marriage-window 'Y1,Y2' scopes years)",
            "alt_engines": "--alt-engines (Surya-Siddhanta crosschecks; also included by --full)",
            "prediction": "--prediction (bundled library prose + life-span bracket; also "
                          "included by --full; NOT part of the default fact-sheet run)",
            "prashna": "--prashna (wrapper horary; --prashna-number N --prashna-system kp249|nadi108|nadi150 --query-house 1..12)",
        },
        "optional_dependency": {
            "pyIslam": "only for hijri/month-based festival listing (festivals_of_day)",
            "geocoder/geopy": "only when passing --place / --match-place (place name)",
        },
        "round16_surface": {
            "vedic_weekday": "panchanga.vedic_weekday (sunrise-to-sunrise = vaara)",
            "karana_lord": "panchanga.karana.lord via utils.karana_lord",
            "nakshathra_lord": "panchanga.moon_nakshatra.library_lord via utils.nakshathra_lord",
            "sphuta_longitudes_varga": "sphutas.library_longitudes_by_varga D1/D9/D10",
            "saham_longitudes_varga": "sahams.__divisional__ D9/D10 (D1 named keys unchanged)",
            "special_lagna_varga": "special.special_lagna_longitudes_by_varga D9/D10",
            "next_special_vratha": "vratha.next_special_vratha (underscore keys)",
            "skipped": "Horoscope.get_ava_saha_yoga_* (HTML of S13/S14); yoga 700 aliases; named varga charts; *_old/*_raw; hijri/pyIslam; empty raja_yoga_bv_raman",
        },
    }


# --------------------------------------------------------------------------- #
# Deep subsystem builders (houses, strength components, ashtakavarga, tajaka,
# varga transits, eclipses, muhurta). Each is decoded where possible and
# guarded so a single failing API never stops the chart.
# --------------------------------------------------------------------------- #

def _house_forms(planet_positions):
    """Build the several PyJHora planet-position conventions the house/relationship
    functions expect, from the file's canonical `[[pid,[sign,deg]],['L',...]]` list.

    Returns a dict with:
      listform   -> `[[pid,(house0,lon_in_sign),...], ['L',(sign0,0)]]` (house_from_asc 0-based)
      sign_list  -> 12-entry list indexed by SIGN (Aries=0) of '/' joined planet ids
      house_list -> 12-entry list indexed by HOUSE-from-asc of '/' joined planet ids (+ 'L' at asc)
      asc_sign, ppl (name->abs longitude)
    """
    ppl = _planetary_longitude(planet_positions)
    asc_sign = _sign_of_long(ppl[LAGNA])
    listform = [["L", (asc_sign, 0.0)]]
    sign_list = ["" for _ in range(12)]
    house_list = ["" for _ in range(12)]
    for p in PLANET_ORDER:
        pid = _planet_id(p)
        if pid is None:
            continue
        sign = _sign_of_long(ppl[p])
        house = (sign - asc_sign) % 12
        lon = _deg_in_sign(ppl[p])
        listform.append([pid, (house, lon)])
        s = str(pid)
        sign_list[sign] = (sign_list[sign] + "/" + s).strip("/")
        house_list[house] = (house_list[house] + "/" + s).strip("/")
    # Lagna is placed in the sign-indexed list at the ascendant sign and in the
    # house-indexed list at the ascendant house (both PyJHora conventions).
    sign_list[asc_sign] = (sign_list[asc_sign] + "/" + "L").strip("/")
    house_list[0] = (house_list[0] + "/" + "L").strip("/") if house_list[0] else "L"
    return {"listform": listform, "sign_list": sign_list, "house_list": house_list,
            "asc_sign": asc_sign, "ppl": ppl}


def _house_list_of_planets(house_list):
    """Decode a '/' joined house_list of planet ids into a list of names (keeping 'Lagna')."""
    out = []
    for cell in house_list:
        names = []
        for token in str(cell).split("/"):
            token = token.strip()
            if not token:
                continue
            names.append("Lagna" if token == "L" else _decode_planet_id(int(token)))
        out.append(names)
    return out


def build_house_systems(env, h, jd, place, planet_positions):
    """House systems beyond whole-sign: bhava chart/houses, KP, stronger planet/rasi,
    order of planets & rasis by strength, relationship (natural/temp/compound), drishti
    (graha & rasi), rudra/brahma/maheshwara/trishoola/longevity, kendras/panaparas/apoklimas."""
    charts = env["charts"]
    house = env["house"]
    drik = env["drik"]
    out = {}
    F = _house_forms(planet_positions)
    sign_list = F["sign_list"]; house_list = F["house_list"]; asc_sign = F["asc_sign"]
    listform = F["listform"]

    def s(fn, *a, **k):
        try:
            with _quiet():
                return fn(*a, **k)
        except Exception as e:
            return {"__error__": str(e)}

    # --- bhava (equal/sign) chart + houses ---
    out["bhava_chart"] = s(charts.bhava_chart, jd, place)
    # bhava_houses returns {body-key -> RELATIVE HOUSE NUMBER (1..12) from the lagna}.
    # REVIEW R6 fix: the VALUES are house numbers, NOT planet ids — decode the KEYS.
    bhouses = s(charts.bhava_houses, jd, place)
    if isinstance(bhouses, dict):
        _bh = {}
        for k, v in bhouses.items():
            nm = "Lagna" if k == "L" else _decode_planet_id(int(k) if str(k).isdigit() else k)
            try:
                _bh[nm] = int(v)
            except Exception:
                _bh[nm] = v
        out["bhava_houses"] = _bh
        out["bhava_houses_note"] = ("body -> house number 1..12 exactly as charts.bhava_houses "
                                    "returns it (bhava-madhya convention; near boundaries this can "
                                    "differ from bhava_cusps_decoded occupancy — both are library "
                                    "outputs, faithfully passed through).")
    else:
        out["bhava_houses"] = bhouses
    # bhava_chart_houses gives cusps (degrees) per house
    _bo = env.get("_bhava_opts") or {}
    cusps = s(charts.bhava_chart_houses, jd, place,
              **({"bhava_starts_with_ascendant": True} if _bo.get("starts_with_asc") else {}))
    out["bhava_chart_houses"] = cusps

    # --- ROUND-7 (review tail: house-system selection, ascendant start vs middle,
    # bhava from Sun/Moon). PyJHora 5.0 `charts._bhaava_madhya_new` accepts ALL house
    # systems: Indian 1..5 (KN Rao / Parashari whole-sign / KP / BV Raman / nakshatra-pada
    # equal), 'O'/'S' Sripathi variants, and western letters (Placidus 'P', Koch 'K',
    # Campanus 'C', Regiomontanus 'R', Equal 'A'/'E', Alcabitus 'B', Vehlow 'V', etc. —
    # gated by const.include_western_house_systems, toggled just-in-time below).
    try:
        const_ = env["const"]
        _method = _bo.get("method")
        if _method is None:
            _method = getattr(const_, "bhaava_madhya_method", 1)
        else:
            try:
                _method = int(_method)
            except Exception:
                _method = str(_method)
        _ref = {"sun": 0, "moon": 1}.get(str(_bo.get("reference") or "").lower())
        _middle = not bool(_bo.get("asc_start"))
        _is_western = isinstance(_method, str)
        _prev_west = getattr(const_, "include_western_house_systems", False)
        if _is_western:
            const_.include_western_house_systems = True
        try:
            with _quiet():
                _rows = charts._bhaava_madhya_new(
                    jd=jd, place=place, bhava_madhya_method=_method,
                    reference_planet_for_ascendant=_ref,
                    ascendant_is_middle_of_house=_middle)
        finally:
            if _is_western:
                const_.include_western_house_systems = _prev_west
        _houses = []
        # item[0] is the house RASI index (bhava_chart convention — same trap fixed in
        # Round 6); the POSITION in the list is the house number counted from the
        # selected reference (lagna / Sun / Moon).
        for _pos, item in enumerate(_rows or []):
            try:
                b0, b1, b2 = (float(x) for x in item[1])
                _houses.append({
                    "house": _pos + 1,
                    "house_rasi": RAASI_NAMES[int(item[0]) % 12],
                    "begin": {"longitude": round(b0, 4), "sign": RAASI_NAMES[int(b0 // 30) % 12]},
                    "cusp": {"longitude": round(b1, 4), "sign": RAASI_NAMES[int(b1 // 30) % 12]},
                    "end": {"longitude": round(b2, 4), "sign": RAASI_NAMES[int(b2 // 30) % 12]},
                    "occupants": [("Lagna" if x == "L" else _decode_planet_id(x)) for x in item[2]]})
            except Exception:
                _houses.append(item)
        _label = None
        try:
            ahs = getattr(const_, "available_house_systems", None)
            ahs = ahs() if callable(ahs) else ahs      # 5.0: it became a lambda
            _label = dict(ahs).get(_method, dict(ahs).get(str(_method)))
        except Exception:
            pass
        out["bhava_method_selected"] = {
            "method": _method, "method_name": _label,
            "reference_planet_for_ascendant": (_bo.get("reference") or "lagna (default)"),
            "ascendant_is_middle_of_house": _middle,
            "houses": _houses,
            "note": ("Cusps from charts._bhaava_madhya_new for the SELECTED house system "
                     "(--bhava-method / --bhava-from / --bhava-asc-start). The rest of this "
                     "document remains WHOLE-SIGN; use this block only when the chosen "
                     "degree-based system is explicitly wanted."),
        }
        try:
            ahs2 = getattr(const_, "available_house_systems", None)
            ahs2 = ahs2() if callable(ahs2) else ahs2
            out["available_house_systems"] = ({str(k): v for k, v in dict(ahs2).items()}
                                              if isinstance(ahs2, dict) else ahs2)
        except Exception as e:
            out["available_house_systems"] = str(e)
    except Exception as e:
        out["bhava_method_selected"] = {"__error__": str(e)}

    # --- KP lords (need [[pid,(house>=1,lon)], ...]) ---
    kpin = [[pid, (hbi + 1, lon)] for pid, (hbi, lon) in listform if pid != "L"]
    try:
        kp = charts.get_KP_lords_from_planet_positions(kpin)
        out["KP_lords"] = kp
    except Exception as e:
        out["KP_lords"] = str(e)

    # --- stronger planet / rasi / order ---
    out["stronger_planet"] = s(house.stronger_planet_from_planet_positions, planet_positions)
    out["order_of_planets_by_strength"] = s(house.order_of_planets_by_strength, planet_positions)
    out["order_of_raasis_by_strength"] = s(house.order_of_raasis_by_strength, planet_positions)
    # stronger_rasi_from_planet_positions(planet_positions, rasi1, rasi2) compares exactly
    # two signs. Instead of a single misleading Aries-vs-Taurus entry, expose a full
    # ordered `stronger_rasi_rank` (by pairwise votes) + the count (review §3.3).
    try:
        votes = {i: 0 for i in range(12)}
        pairs = {}
        for i in range(12):
            for j in range(i + 1, 12):
                try:
                    with _quiet():
                        win = house.stronger_rasi_from_planet_positions(planet_positions, i, j)
                    if win is not None:
                        pairs[f"{RAASI_NAMES[i]}_vs_{RAASI_NAMES[j]}"] = RAASI_NAMES[int(win) % 12]
                        if isinstance(win, (int, float)):
                            votes[int(win) % 12] += 1
                except Exception:
                    continue
        out["stronger_rasi_pairs"] = pairs
        out["stronger_rasi_rank"] = [RAASI_NAMES[s] for s, _ in sorted(votes.items(), key=lambda kv: -kv[1])]
    except Exception as e:
        out["stronger_rasi_pairs"] = str(e)

    # --- relationship tables (natural / temporary / compound) decoded ---
    for lab, fn in (("natural_friends", house.natural_friends_of_planets),
                    ("natural_enemies", house.natural_enemies_of_planets),
                    ("natural_neutral", house.natural_neutral_of_planets)):
        try:
            r = fn(sign_list)
            if isinstance(r, dict):
                out[lab] = {_decode_planet_id(k): _decode_planet_id_list(v) for k, v in r.items()}
            else:
                out[lab] = r
        except Exception as e:
            out[lab] = str(e)
    try:
        out["temporary_friends"] = {_decode_planet_id(k): _decode_planet_id_list(v)
                                    for k, v in house._get_temporary_friends_of_planets(sign_list).items()}
    except Exception as e:
        out["temporary_friends"] = str(e)
    try:
        out["temporary_enemies"] = {_decode_planet_id(k): _decode_planet_id_list(v)
                                    for k, v in house._get_temporary_enemies_of_planets(sign_list).items()}
    except Exception as e:
        out["temporary_enemies"] = str(e)
    try:
        # REVIEW FIX: `_get_compound_relationships_of_planets` returns a 9x9 MATRIX
        # `p_compound[p][p1]` (planet ids both axes), each cell a compound-relationship
        # code: 4=Adhimitra(great friend) 3=Mitra 2=Neutral 1=Shatru 0=Adhishatru(great enemy).
        # The previous `{k: .. for k,v in .items()}` assumed a dict and raised
        # `'list' object has no attribute 'items'`. Decode the matrix to name-keyed rows.
        _CR_NAMES = {4: "Adhimitra(great friend)", 3: "Mitra(friend)",
                     2: "Neutral", 1: "Shatru(enemy)", 0: "Adhishatru(great enemy)"}
        _crm = house._get_compound_relationships_of_planets(sign_list)
        if isinstance(_crm, (list, tuple)) and _crm and isinstance(_crm[0], (list, tuple)):
            _comp = {}
            for p in range(len(_crm)):
                pn = _decode_planet_id(p)
                row = {}
                for p1 in range(len(_crm[p])):
                    row[_decode_planet_id(p1)] = _CR_NAMES.get(int(_crm[p][p1]), int(_crm[p][p1]))
                _comp[pn] = row
            out["compound_relations"] = _comp
        else:
            out["compound_relations"] = _crm
    except Exception as e:
        out["compound_relations"] = str(e)
    try:
        out["karakas"] = {"naisargika": [ _decode_planet_id(x) for x in house.naisargika_karakas()],
                          "chara": [_decode_planet_id(x) for x in house.chara_karakas(planet_positions)],
                          "sthira": [_decode_planet_id(x) for x in house.sthira_karakas(planet_positions)]}
    except Exception as e:
        out["karakas"] = str(e)

    # --- graha drishti + rasi drishti (sign-indexed lists) ---
    out["graha_drishti"] = {}
    for p in PLANET_ORDER:
        pid = _planet_id(p)
        if pid is None:
            continue
        r = s(house.graha_drishti_of_the_planet, sign_list, pid)
        out["graha_drishti"][p] = _decode_planet_id_list(r) if isinstance(r, (list, tuple)) else r
    rdris = s(house.raasi_drishti_from_chart, sign_list)
    # PyJHora's raasi_drishti_from_chart returns a 3-tuple (arp, ahp, app) whose THREE value
    # DOMAINS *differ* (review §3.current): keys are always the SOURCE sign (0..11), but the
    # values are:
    #   arp  (on-rasi):    SIGN ids 0..11
    #   ahp  (on-houses):  HOUSE ids 0..11 (0-based house-from-lagna, = sign_h - asc_house %12)
    #   app  (on-planets): PLANET ids 0..8
    # Decoding all three as sign names would mislabel houses and planets, so we decode each
    # with its own map: signs->names, houses->"H{n+1}", planets->names.
    def _dc_map(d):
        return {RAASI_NAMES[int(k)]: v for k, v in d.items()} if isinstance(d, dict) else d
    def _dc_sign(d):
        if isinstance(d, dict):
            return {RAASI_NAMES[int(k)]: [RAASI_NAMES[int(x) % 12] for x in v] for k, v in d.items()}
        return d
    def _dc_house(d):
        if isinstance(d, dict):
            # value is a 0-based house-from-lagna -> show as a 1-based "H#".
            return {RAASI_NAMES[int(k)]: [("H%d" % (int(x) % 12 + 1)) for x in v] for k, v in d.items()}
        return d
    def _dc_planet(d):
        if isinstance(d, dict):
            return {RAASI_NAMES[int(k)]: [_decode_planet_id(int(x)) for x in v] for k, v in d.items()}
        return d
    if isinstance(rdris, (tuple, list)) and len(rdris) == 3:
        out["raasi_drishti_on_rasi"] = _dc_sign(rdris[0])
        out["raasi_drishti_on_houses"] = _dc_house(rdris[1])
        out["raasi_drishti_on_planets"] = _dc_planet(rdris[2])
    elif isinstance(rdris, (tuple, list)) and len(rdris) == 2:
        out["raasi_drishti_on_rasi"] = _dc_sign(rdris[0])
        out["raasi_drishti_on_houses"] = _dc_house(rdris[1])
    else:
        out["raasi_drishti_from_chart"] = rdris
    out["aspected_houses_by_planet"] = {}
    for p in PLANET_ORDER:
        pid = _planet_id(p)
        if pid is None:
            continue
        r = s(house.aspected_houses_of_the_planet, sign_list, pid)
        out["aspected_houses_by_planet"][p] = r if not isinstance(r, (list, tuple)) \
            else [f"H{int(x)%12+1}" for x in r]

    # --- kendras / panaparas / apoklimas + strong-sign-of-planet + vargottama ---
    out["kendras"] = [((asc_sign + k) % 12) for k in (0, 3, 6, 9)]
    out["panaparas"] = [((asc_sign + k) % 12) for k in (1, 4, 7, 10)]
    out["apoklimas"] = [((asc_sign + k) % 12) for k in (2, 5, 8, 11)]
    out["kendra_signs"] = {i: RAASI_NAMES[s] for i, s in enumerate(out["kendras"])}
    # vargottama: planet whose D1 and D9 signs match
    try:
        d9 = charts.divisional_chart(jd, place, divisional_chart_factor=9)
        ppl9 = _planetary_longitude(d9)
        vt = [p for p in PLANET_ORDER if _sign_of_long(F["ppl"][p]) == _sign_of_long(ppl9[p])]
        out["vargottama"] = vt
    except Exception as e:
        out["vargottama"] = str(e)

    # --- Jaimini specials: rudra / brahma / maheshwara / trishoola / longevity ---
    out["rudra"] = s(house.rudra, planet_positions)
    out["brahma"] = s(house.brahma, planet_positions)
    out["maheshwara"] = s(house.maheshwara, _dob(h), _tob(h), place)
    out["trishoola_rasis"] = s(house.trishoola_rasis, planet_positions)
    out["longevity"] = s(house.longevity, _dob(h), _tob(h), place)
    # ROUND-17: const.longevity pair-class of two rasis (sign%3 movable/fixed/dual).
    # Values are 0/1/2 as the library table keys — do NOT invent Alpayu labels.
    try:
        ppl17 = F["ppl"]
        pairs = {}
        for lab, a, b in (("lagna_moon", LAGNA, "Moon"),
                          ("lagna_sun", LAGNA, "Sun"),
                          ("moon_sun", "Moon", "Sun")):
            try:
                r1 = _sign_of_long(ppl17[a]); r2 = _sign_of_long(ppl17[b])
                # const.longevity is keyed by movable/fixed/dual = rasi % 3 (only 0,1,2).
                with _quiet():
                    cls = house.longevity_of_pair(r1 % 3, r2 % 3)
                pairs[lab] = {"rasi_1": RAASI_NAMES[r1], "rasi_2": RAASI_NAMES[r2],
                              "class_index": cls,
                              "sign_types": (r1 % 3, r2 % 3)}
            except Exception as e:
                pairs[lab] = {"__error__": str(e)}
        out["longevity_of_sign_pairs"] = {
            "pairs": pairs,
            "__note__": ("ROUND-17: house.longevity_of_pair = lookup in const.longevity "
                         "by (rasi1, rasi2). Keys 0/1/2 are the library's own class "
                         "indices (sign-type pairing), NOT death predictions. Distinct "
                         "from house.longevity() above and the aayu section.")}
    except Exception as e:
        out["longevity_of_sign_pairs"] = {"__error__": str(e)}
    out["functional_benefic_lord_houses"] = s(house.functional_benefic_lord_houses, asc_sign)
    out["functional_malefic_lord_houses"] = s(house.functional_malefic_lord_houses, asc_sign)

    # --- houses occupied (decoded) + planet classification by house category ---
    out["planets_in_house_from_lagna"] = _house_list_of_planets(house_list)
    # The get_planets_in_* family AND is_yoga_kaaraka work in the RAASI-INDEX domain:
    # the canonical input is utils.get_planet_house_dictionary_from_planet_positions ->
    # {'L' + planet_id: raasi_index 0..11}, and the helpers test membership against the
    # RAASI sets of the ascendant's kendra/trikona/... (e.g. quadrants of raasi r are
    # [r, r+3, r+6, r+9] mod 12). REVIEW R6 fix: the previous 1-based house-from-lagna
    # dict silently dropped house-12 occupants from dushthanas/apoklimas (value 12 can
    # never match a rasi set that contains 0) and mis-anchored every category unless the
    # lagna happened to be Taurus. Use the library's canonical dict.
    p_to_h = env["utils"].get_planet_house_dictionary_from_planet_positions(planet_positions)
    _apk = getattr(house, "get_planets_in_apoklimas", None) or getattr(house, "get_planets_in_apklimas", None)
    _plc = getattr(house, "get_planets_in_quadrants", None) or getattr(house, "are_planets_in_quadrants", None)
    for lab, fn in (("in_quadrants", _plc),
                    ("in_trines", getattr(house, "get_planets_in_trines", None)),
                    ("in_panapharas", getattr(house, "get_planets_in_panapharas", None)),
                    ("in_apoklimas", _apk),
                    ("in_dushthanas", getattr(house, "get_planets_in_dushthanas", None)),
                    ("in_upachayas", getattr(house, "get_planets_in_upachayas", None)),
                    ("in_chathusras", getattr(house, "get_planets_in_chathusras", None))):
        if fn is None:
            continue
        try:
            r = fn(p_to_h)
            if isinstance(r, (list, tuple)):
                out[lab] = [("Lagna" if x == "L" else _decode_planet_id(x)) for x in r]
            else:
                out[lab] = r
        except Exception as e:
            out[lab] = str(e)
    # ROUND-7 (review tail): disclose the membership convention. These are PyJHora's own
    # category predicates over the RASI-indexed planet->house dictionary: house 1 = the
    # Lagna sign, so planets IN the lagna sign DO appear in in_quadrants (1,4,7,10) and
    # in_trines (1,5,9); the Lagna itself is listed as "Lagna" when the library includes it.
    out["category_membership_note"] = (
        "Category sets computed by PyJHora's house predicates over rasi-relative houses "
        "(1=quadrant/trine member): planets in the Lagna sign ARE included in in_quadrants "
        "and in_trines; 'Lagna' appears in the lists when the library returns the 'L' marker.")

    # --- bhava (equal-house) cusps, in house order ---
    # bhava_chart rows are [house_raasi_index, (start, cusp, end), [body ids]] — item[0] is
    # the RAASI of that house, NOT the house number (REVIEW R6 fix: the old code emitted
    # the rasi index as "house", so house 12 from a Taurus lagna appeared as "house": 0).
    # The house number is the row POSITION; the rasi is decoded to its name alongside.
    bc = s(charts.bhava_chart, jd, place)
    if isinstance(bc, (list, tuple)):
        out["bhava_cusps_decoded"] = []
        for _i, item in enumerate(bc):
            try:
                rasi, (s1, s2, s3), pids = item[0], item[1], item[2]
                out["bhava_cusps_decoded"].append({
                    "house": _i + 1,
                    "raasi": RAASI_NAMES[int(rasi) % 12],
                    "raasi_index": int(rasi) % 12,
                    "cusp_degrees": [round(float(x), 2) for x in (s1, s2, s3)],
                    "planets": [("Lagna" if x == "L" else _decode_planet_id(x)) for x in pids],
                })
            except Exception:
                out["bhava_cusps_decoded"].append(item)

    # ---- ROUND-5: aspect/association detail, bhava-madhya variants, misc house API ----
    def _dec_ids_mixed(v, kind):
        """Decode a list/'/'-string of ids of one domain (sign/planet/house)."""
        if v is None:
            return None
        items = v.split("/") if isinstance(v, str) and v else (list(v) if isinstance(v, (list, tuple)) else [v])
        outl = []
        for x in items:
            if isinstance(x, str) and not x.strip():
                continue
            try:
                xi = int(x)
            except Exception:
                outl.append(x)
                continue
            if kind == "sign":
                outl.append(RAASI_NAMES[xi % 12])
            elif kind == "planet":
                outl.append(_decode_planet_id(xi))
            elif kind == "house":
                outl.append("H%d" % (xi % 12 + 1))
            else:
                outl.append(xi)
        return outl

    # per-planet associations + two-way aspect detail
    out["planet_aspect_details"] = {}
    for p in PLANET_ORDER:
        pid = _planet_id(p)
        if pid is None:
            continue
        row = {}
        row["associations"] = _dec_ids_mixed(
            s(house.associations_of_the_planet, sign_list, planet_positions, pid), "planet") \
            if hasattr(house, "associations_of_the_planet") else None
        try:
            with _quiet():
                row["associations"] = _dec_ids_mixed(
                    house.associations_of_the_planet(house_to_planet_list=sign_list,
                                                     planet_positions=planet_positions,
                                                     planet=pid), "planet")
        except Exception as e:
            row["associations"] = str(e)
        for lab, fn, kind in (("planets_aspecting_it", getattr(house, "planets_aspecting_the_planet", None), "planet"),
                              ("raasis_aspecting_it", getattr(house, "raasis_aspecting_the_planet", None), "sign"),
                              ("houses_aspecting_it", getattr(house, "houses_aspecting_the_planet", None), "house"),
                              ("planets_it_aspects", getattr(house, "aspected_planets_of_the_planet", None), "planet"),
                              ("raasis_it_aspects", getattr(house, "aspected_rasis_of_the_planet", None), "sign")):
            if fn is None:
                continue
            try:
                with _quiet():
                    row[lab] = _dec_ids_mixed(fn(sign_list, pid), kind)
            except Exception as e:
                row[lab] = str(e)
        out["planet_aspect_details"][p] = row
    # per-sign aspect detail (both directions)
    out["raasi_aspect_details"] = {}
    for r in range(12):
        row = {}
        for lab, fn, kind in (("planets_aspecting_sign", getattr(house, "planets_aspecting_the_raasi", None), "planet"),
                              ("raasis_aspecting_sign", getattr(house, "raasis_aspecting_the_raasi", None), "sign"),
                              ("houses_aspecting_sign", getattr(house, "houses_aspecting_the_raasi", None), "house"),
                              ("planets_sign_aspects", getattr(house, "aspected_planets_of_the_raasi", None), "planet"),
                              ("houses_sign_aspects", getattr(house, "aspected_houses_of_the_raasi", None), "house"),
                              ("raasis_sign_aspects", getattr(house, "aspected_raasis_of_the_raasi", None), "sign"),
                              ("raasi_drishti_on_sign", getattr(house, "raasi_drishti_of_the_raasi", None), "sign")):
            if fn is None:
                continue
            try:
                with _quiet():
                    row[lab] = _dec_ids_mixed(fn(sign_list, r), kind)
            except Exception as e:
                row[lab] = str(e)
        out["raasi_aspect_details"][RAASI_NAMES[r]] = row
    # aspect-group families measured FROM THE LAGNA SIGN
    _lagna_groups = {}
    for lab, fn in (("trines", getattr(house, "trines_of_the_raasi", None)),
                    ("trikona_aspects", getattr(house, "trikona_aspects_of_the_raasi", None)),
                    ("chathusra_aspects", getattr(house, "chathusra_aspects_of_the_raasi", None)),
                    ("kendra_aspects", getattr(house, "kendra_aspects_of_the_raasi", None)),
                    ("upachaya_aspects", getattr(house, "upachaya_aspects_of_the_raasi", None)),
                    ("dushthana_aspects", getattr(house, "dushthana_aspects_of_the_raasi", None)),
                    ("quadrants", getattr(house, "quadrants_of_the_raasi", None)),
                    ("panapharas", getattr(house, "panapharas_of_the_raasi", None)),
                    ("apoklimas", getattr(house, "apoklimas_of_the_raasi", None)),
                    ("dushthanas", getattr(house, "dushthanas_of_the_raasi", None)),
                    ("upachayas", getattr(house, "upachayas_of_the_raasi", None)),
                    ("chathusras", getattr(house, "chathusras_of_the_raasi", None)),
                    ("aspected_kendras", getattr(house, "aspected_kendras_of_raasi", None))):
        if fn is None:
            continue
        try:
            with _quiet():
                _lagna_groups[lab] = _dec_ids_mixed(fn(asc_sign), "sign")
        except Exception as e:
            _lagna_groups[lab] = str(e)
    out["lagna_sign_aspect_groups"] = _lagna_groups
    # per-planet functional extras
    _misc = {}
    for p in PLANET_ORDER:
        pid = _planet_id(p)
        if pid is None:
            continue
        prow = {}
        try:
            _ph = p_to_h.get(pid)
            with _quiet():
                prow["is_yogakaraka"] = bool(house.is_yoga_kaaraka(asc_sign, pid, _ph))
        except Exception as e:
            prow["is_yogakaraka"] = str(e)
        try:
            with _quiet():
                prow["strong_signs"] = _dec_ids_mixed(house.strong_signs_of_planet(pid), "sign")
        except Exception as e:
            prow["strong_signs"] = str(e)
        _misc[p] = prow
    out["planet_functional_extras"] = _misc
    try:
        with _quiet():
            out["functional_neutral_lord_signs"] = _dec_ids_mixed(
                house.functional_neutral_lord_houses(asc_sign), "sign")
    except Exception as e:
        out["functional_neutral_lord_signs"] = str(e)
    try:
        with _quiet():
            _moon_sign = _sign_of_long(F["ppl"]["Moon"])
            _sr = house.stronger_rasi(sign_list, asc_sign, _moon_sign)
        out["stronger_rasi_lagna_vs_moon"] = {
            "lagna_sign": RAASI_NAMES[asc_sign], "moon_sign": RAASI_NAMES[_moon_sign],
            "stronger": (RAASI_NAMES[int(_sr) % 12] if isinstance(_sr, (int, float)) else _sr)}
    except Exception as e:
        out["stronger_rasi_lagna_vs_moon"] = str(e)
    try:
        with _quiet():
            out["lagna_sign_owner"] = _decode_planet_id(house.house_owner(sign_list, asc_sign))
    except Exception as e:
        out["lagna_sign_owner"] = str(e)
    try:
        with _quiet():
            _rj = house.rudra_from_jd_place(jd, place)
        # returns (rudra_sign, maheshwara?, trishoola signs) — decode defensively
        if isinstance(_rj, (list, tuple)) and len(_rj) >= 3:
            out["rudra_from_jd_place"] = {
                "rudra_sign": RAASI_NAMES[int(_rj[0]) % 12],
                "second_sign": RAASI_NAMES[int(_rj[1]) % 12],
                "trishoola_signs": [RAASI_NAMES[int(x) % 12] for x in _rj[2]]}
        else:
            out["rudra_from_jd_place"] = _rj
    except Exception as e:
        out["rudra_from_jd_place"] = str(e)
    try:
        with _quiet():
            out["maheshwara_from_planet_positions"] = _decode_planet_id(
                house.maheshwara_from_planet_positions(planet_positions))
    except Exception as e:
        out["maheshwara_from_planet_positions"] = str(e)
    # bhava-madhya (house midpoints) under the 4 PyJHora house doctrines
    def _dec_longs(v):
        try:
            return [{"house": i + 1, "longitude": round(float(x), 4),
                     "sign": RAASI_NAMES[int(float(x) // 30) % 12],
                     "degree_in_sign": round(float(x) % 30, 4)} for i, x in enumerate(v)]
        except Exception:
            return v
    _bmv = {}
    for lab, fn, kw in (("default", getattr(drik, "bhaava_madhya", None), {}),
                        ("swe_porphyry_P", getattr(drik, "bhaava_madhya_swe", None), {"house_code": "P"}),
                        ("kp", getattr(drik, "bhaava_madhya_kp", None), {}),
                        ("sripathi", getattr(drik, "bhaava_madhya_sripathi", None), {})):
        if fn is None:
            continue
        try:
            with _quiet():
                try:
                    _bmv[lab] = _dec_longs(fn(jd, place, **kw))
                except TypeError:
                    # ROUND-9 (review Enhancement 5, adapted): 5.0's bhaava_madhya_swe
                    # takes house_code= (verified against the installed source — the
                    # review's hsys-first ordering is the OLD 4.8.7 signature), but keep
                    # a cross-version kwarg translation so a future/older build whose
                    # signature flips still resolves instead of erroring the variant.
                    _alt = {"house_code": "hsys", "hsys": "house_code"}
                    _kw2 = {_alt.get(k, k): v for k, v in kw.items()}
                    if _kw2 == kw:
                        raise
                    with _quiet():
                        _bmv[lab] = _dec_longs(fn(jd, place, **_kw2))
        except Exception as e:
            _bmv[lab] = str(e)
    try:
        with _quiet():
            # PyJHora 5.0 REMOVED drik.bhaava_madhya_bv_raman; the same doctrine is
            # reachable via charts._bhaava_madhya_new(bhava_madhya_method=4).
            _bvfn = getattr(drik, "bhaava_madhya_bv_raman", None)
            if _bvfn is not None:
                _bvr = _bvfn(jd, place)
            else:
                _bvr = charts._bhaava_madhya_new(jd=jd, place=place, bhava_madhya_method=4)
        rows = []
        # NOTE: rows may carry the house RASI index in item[0] (5.0 _bhaava_madhya_new)
        # or the house number (old drik fn) — the POSITION in the list is always the
        # house order, so number houses by position and record item[0] as rasi.
        for _pos, item in enumerate(_bvr):
            try:
                (b0, b1, b2), pls = item[1], item[2]
                rows.append({"house": _pos + 1,
                             "house_rasi": RAASI_NAMES[int(item[0]) % 12],
                             "begin_middle_end_longitudes": [round(float(x), 4) for x in (b0, b1, b2)],
                             "occupants": [("Lagna" if x == "L" else _decode_planet_id(x)) for x in pls]})
            except Exception:
                rows.append(item)
        _bmv["bv_raman"] = rows
    except Exception as e:
        _bmv["bv_raman"] = str(e)
    _bmv["note"] = ("Bhava madhyas = house midpoints under 4 doctrines. The engine's "
                    "houses remain WHOLE-SIGN; these are the degree-based alternatives "
                    "PyJHora can compute (SWE-Porphyry / KP / Sripathi / BV Raman).")
    # ROUND-10 (review P2-1, ADOPTED): KP cuspal star/sub(-sub) lords for all 12 cusps.
    # PyJHora has no direct cusp-sub-lord API, but BOTH primitives are library ones:
    #   cusps  = drik.bhaava_madhya_kp(jd, place)  (Swiss Ephemeris Placidus per its
    #            docstring — the standard "Method-3" KP cusps; house1 cusp = ascendant,
    #            house10 cusp = MC)
    #   lords  = utils.kp_lords_for_longitude(label, abs_longitude, include_sign_lord,
    #            levels=5) — the library's iterative Vimshottari-proportional KP
    #            micro-lord calculator (fractions = const.KP_lord_fractions =
    #            years/120: Ke 7, Ve 20, Su 6, Mo 10, Ma 7, Ra 18, Ju 16, Sa 19, Me 17;
    #            sub starts from the star lord, pratyantara from the sub, etc.), which
    #            also yields the KP star number (1..249) of the cusp.
    # The wrapper only composes them per cusp — no KP math is reimplemented.
    try:
        _ukp = env["utils"]; _dkp = drik
        _kp_cusp_fn = getattr(_dkp, "bhaava_madhya_kp", None)
        _kp_lord_fn = getattr(_ukp, "kp_lords_for_longitude", None)
        if _kp_cusp_fn and _kp_lord_fn:
            with _quiet():
                _cusps = _kp_cusp_fn(jd, place)
            _rows = []
            for _i, _cl in enumerate(list(_cusps)[:12]):
                _cl = float(_cl) % 360.0
                _lab = "C%d" % (_i + 1)
                _res = _kp_lord_fn(_lab, _cl, include_sign_lord=True, levels=5)
                _v = _res[_lab] if isinstance(_res, dict) else _res
                # _v = [kp_star_number, sign_lord, star, sub, praty, sookshma, praana, deha]
                _rows.append({
                    "house": _i + 1,
                    "cusp_longitude": round(_cl, 4),
                    "cusp_sign": RAASI_NAMES[int(_cl // 30) % 12],
                    "cusp_degree_in_sign": round(_cl % 30, 4),
                    "kp_star_number_1_249": int(_v[0]),
                    "sign_lord": _planet_name(_v[1]),
                    "star_lord": _planet_name(_v[2]),
                    "sub_lord": _planet_name(_v[3]),
                    "pratyantara_lord": _planet_name(_v[4]),
                    "sookshma_lord": _planet_name(_v[5]),
                    "praana_lord": _planet_name(_v[6]),
                    "deha_lord": _planet_name(_v[7]) if len(_v) > 7 else None,
                })
            _bmv["kp_cuspal_lords"] = {
                "cusps": _rows,
                "__note__": ("Wrapper-composed from library primitives: cusps = "
                             "drik.bhaava_madhya_kp (Placidus per docstring; house 1 cusp "
                             "= ascendant, house 10 cusp = MC), lords = "
                             "utils.kp_lords_for_longitude (library KP micro-lord "
                             "calculator; Vimshottari-proportional sub/pratyantara/"
                             "sookshma/praana/deha from the star lord) + the KP star "
                             "number (1..249). Use for KP cuspal sub-lord analysis; the "
                             "engine's houses remain WHOLE-SIGN elsewhere."),
            }
        else:
            _bmv["kp_cuspal_lords"] = {
                "__error__": "drik.bhaava_madhya_kp or utils.kp_lords_for_longitude "
                             "absent in this build"}
    except Exception as e:
        _bmv["kp_cuspal_lords"] = {"__error__": str(e)}
    out["bhava_madhya_variants"] = _bmv
    # ---- ROUND-13 (review item 6, ADOPTED): KP four-fold house significators ----
    # Classical KP levels (priority A > B > C > D):
    #   A/level_1: planet sits in the STAR of an OCCUPANT of the house
    #   B/level_2: planet OCCUPIES the house
    #   C/level_3: planet sits in the STAR of the house's LORD
    #   D/level_4: planet IS the house's sign lord
    # Composed from engine/library primitives only (longitudes → nakshatra lords;
    # SIGN_LORDS; drik.bhaava_madhya_kp cusps for the KP frame). The review's
    # snippet referenced a `core` variable that does not exist in this builder —
    # the same data is rebuilt here from planet_positions. TWO frames ship: the
    # engine-convention WHOLE-SIGN frame (houses counted from the lagna sign, ==
    # core.planets[].house) and the orthodox KP CUSPAL frame (occupancy/lords from
    # the Placidus bhaava_madhya_kp cusps already exposed in kp_cuspal_lords).
    # Rahu/Ketu get levels A–C (they act through star lord + occupancy); they own
    # no houses in the 7-lord scheme — the Jaimini/KP co-lordship variant
    # (Ketu~Scorpio, Rahu~Aquarius) is disclosed, not applied.
    try:
        _lons13 = _planetary_longitude(planet_positions)
        _asc13 = _sign_of_long(_lons13[LAGNA])
        _star13 = {p: _nak_lord_of_long(_lons13[p]) for p in PLANET_ORDER}

        def _four_fold13(house_of_planet, lord_of_house):
            by_p = {p: {"level_1": [], "level_2": [], "level_3": [], "level_4": []}
                    for p in PLANET_ORDER}
            by_h = {hh: {"level_1": [], "level_2": [], "level_3": [], "level_4": []}
                    for hh in range(1, 13)}
            for p in PLANET_ORDER:
                sl = _star13.get(p)
                ph = house_of_planet.get(p)
                if ph:                                    # B/level_2 — occupant
                    by_p[p]["level_2"].append(ph)
                    by_h[ph]["level_2"].append(p)
                for hh in range(1, 13):                   # D/level_4 — owns house
                    if lord_of_house.get(hh) == p:
                        by_p[p]["level_4"].append(hh)
                        by_h[hh]["level_4"].append(p)
                if sl:                                    # A/level_1 + C/level_3
                    sl_h = house_of_planet.get(sl)
                    if sl_h:
                        by_p[p]["level_1"].append(sl_h)
                        by_h[sl_h]["level_1"].append(p)
                    for hh in range(1, 13):
                        if lord_of_house.get(hh) == sl:
                            by_p[p]["level_3"].append(hh)
                            by_h[hh]["level_3"].append(p)
            for p in by_p:
                for lv in by_p[p]:
                    by_p[p][lv] = sorted(set(by_p[p][lv]))
            for hh in by_h:
                for lv in by_h[hh]:
                    by_h[hh][lv] = sorted(set(by_h[hh][lv]),
                                          key=PLANET_ORDER.index)
            return {"by_planet": by_p,
                    "by_house": {str(hh): by_h[hh] for hh in range(1, 13)}}

        # Frame 1: whole-sign from the lagna (engine convention)
        _ws_houses13 = {p: _house_number(_asc13, _sign_of_long(_lons13[p]))
                        for p in PLANET_ORDER}
        _ws_lords13 = {hh: SIGN_LORDS[(_asc13 + hh - 1) % 12]
                       for hh in range(1, 13)}
        _ws_frame13 = _four_fold13(_ws_houses13, _ws_lords13)
        # Frame 2: KP cuspal (Placidus bhaava_madhya_kp — orthodox KP division)
        _kp_frame13 = None
        try:
            with _quiet():
                _cusps13 = [float(c) % 360.0
                            for c in list(drik.bhaava_madhya_kp(jd, place))[:12]]
            if len(_cusps13) == 12:
                def _cusp_house13(lon):
                    for i in range(12):
                        lo = _cusps13[i]
                        hi = _cusps13[(i + 1) % 12]
                        if lo <= hi:
                            if lo <= lon < hi:
                                return i + 1
                        elif lon >= lo or lon < hi:        # house wraps 0°
                            return i + 1
                    return None
                _kp_houses13 = {p: _cusp_house13(_lons13[p]) for p in PLANET_ORDER}
                _kp_lords13 = {hh: SIGN_LORDS[int(_cusps13[hh - 1] // 30) % 12]
                               for hh in range(1, 13)}
                _kp_frame13 = _four_fold13(_kp_houses13, _kp_lords13)
        except Exception:
            _kp_frame13 = None
        out["kp_significators"] = {
            "whole_sign_frame": _ws_frame13,
            "kp_cuspal_frame": (_kp_frame13 if _kp_frame13 is not None else
                                {"__error__": "drik.bhaava_madhya_kp unavailable"}),
            "convention": (
                "ROUND-13 (review item 6): KP four-fold significators, priority "
                "A > B > C > D — level_1/A: planet is in the STAR of an occupant "
                "of the house; level_2/B: planet OCCUPIES the house; level_3/C: "
                "planet is in the STAR of the house's lord; level_4/D: planet IS "
                "the house's sign lord. by_planet lists the houses each planet "
                "signifies; by_house lists the planets signifying each house "
                "(keys '1'..'12'). whole_sign_frame counts houses from the lagna "
                "sign (engine convention, == core.planets[].house); kp_cuspal_"
                "frame uses the Placidus drik.bhaava_madhya_kp cusps (orthodox "
                "KP) for occupancy and cusp-sign lords — the SAME cusps as "
                "bhava_madhya_variants.kp_cuspal_lords. Rahu/Ketu signify through "
                "star lord and occupancy (levels A–C); they own no houses in the "
                "7-lord scheme (the Jaimini/KP co-lordship variant Ketu~Scorpio / "
                "Rahu~Aquarius is disclosed, not applied). For KP event timing, "
                "intersect these significators with kp_cuspal_lords' star/sub "
                "lords — a house delivers through planets signifying it at the "
                "highest applicable level whose sub-lord also activates it."),
        }
    except Exception as e:
        out["kp_significators"] = {"__error__": str(e)}
    # functional benefics/malefics for this lagna + pachakadi + kendra ordering
    try:
        with _quiet():
            _bm = charts.benefics_and_malefics(jd, place)
        if isinstance(_bm, (list, tuple)) and len(_bm) == 2:
            out["functional_benefics_malefics"] = {
                "benefics": _dec_ids_mixed(_bm[0], "planet"),
                "malefics": _dec_ids_mixed(_bm[1], "planet"),
                "note": "charts.benefics_and_malefics(jd, place) — functional (lagna-based) lists."}
        else:
            out["functional_benefics_malefics"] = _bm
    except Exception as e:
        out["functional_benefics_malefics"] = str(e)
    try:
        with _quiet():
            _pk = charts.get_pachakadi_sambhandha(planet_positions)
        # raw shape: {planet_id: [relation_index, (other_planet_id, house_offset, table_char)]}
        # relation_index maps onto const.paachaadi_relations order.
        _rel_names = ["paachaka", "bodhaka", "kaaraka", "vedhaka"]
        _pkdec = {}
        for k, v in (_pk or {}).items():
            nm = _decode_planet_id(k)
            try:
                idx = int(v[0])
                ent = v[1]
                _pkdec[nm] = {
                    "relation": _rel_names[idx] if idx < len(_rel_names) else idx,
                    "relation_index": idx,
                    "with_planet": _decode_planet_id(ent[0]),
                    "house_offset_from_planet": int(ent[1]),
                    "table_char": (str(ent[2]) if ent[2] else None),
                }
            except Exception:
                _pkdec[nm] = v
        out["pachakadi_sambhandha"] = _pkdec
        out["pachakadi_sambhandha_note"] = (
            "Panchakadi relation satisfied at birth for each listed planet: relation is one of "
            "paachaka/bodhaka/kaaraka/vedhaka; with_planet sits house_offset_from_planet houses "
            "from the key planet; table_char 'E' is the library's own vedha-table marker.")
    except Exception as e:
        out["pachakadi_sambhandha"] = str(e)
    try:
        with _quiet():
            out["order_planets_from_kendras_of_lagna_sign"] = _dec_ids_mixed(
                charts.order_planets_from_kendras_of_raasi(planet_positions, asc_sign), "planet")
    except Exception as e:
        out["order_planets_from_kendras_of_lagna_sign"] = str(e)
    # ---- ROUND 5b: sign occupancy (house.planets_in_the_house) + relative-house tables
    # ---- (house.get_relative_house_of_planet) — houses-from-lagna and houses-from-moon ----
    try:
        utils = env["utils"]
        _c1d = utils.get_house_planet_list_from_planet_positions(planet_positions)
        pin = {}
        for r in range(12):
            try:
                ids = house.planets_in_the_house(r, chart_1d=_c1d, planet_positions=planet_positions)
                names = []
                for i in (ids or []):
                    if isinstance(i, str) and not str(i).isdigit():
                        names.append("Lagna" if str(i).upper() == "L" else str(i))
                    else:
                        ii = int(i)
                        names.append(PLANET_ORDER[ii] if 0 <= ii < len(PLANET_ORDER) else str(ii))
                pin[RAASI_NAMES[r]] = names
            except Exception as e:
                pin[RAASI_NAMES[r]] = "__error__: %s" % e
        out["planets_in_each_raasi"] = pin
    except Exception as e:
        out["planets_in_each_raasi"] = {"__error__": str(e)}
    try:
        sign_of = {}
        for row in planet_positions or []:
            try:
                sign_of[str(row[0])] = int(row[1][0])
            except Exception:
                continue
        h_of = {k: ((v - asc_sign) % 12) + 1 for k, v in sign_of.items()}
        moon_h = h_of.get("1")
        sun_h = h_of.get("0")   # ROUND-14: Surya-kundali frame (third Sudarshana wheel)
        rel = {}
        for p in PLANET_ORDER:
            pid = _planet_id(p)
            ph = h_of.get(str(pid)) if pid is not None else None
            if ph is None:
                continue
            rel[p] = {
                "house_from_lagna": ph,
                "relative_house_from_lagna": s(house.get_relative_house_of_planet, 1, ph),
                "relative_house_from_moon": (s(house.get_relative_house_of_planet, moon_h, ph)
                                             if moon_h is not None else None),
                "relative_house_from_sun": (s(house.get_relative_house_of_planet, sun_h, ph)
                                            if sun_h is not None else None),
            }
        if "L" in h_of:
            rel["Lagna"] = {
                "house_from_lagna": h_of["L"],
                "relative_house_from_moon": (s(house.get_relative_house_of_planet, moon_h, h_of["L"])
                                             if moon_h is not None else None),
                "relative_house_from_sun": (s(house.get_relative_house_of_planet, sun_h, h_of["L"])
                                            if sun_h is not None else None),
            }
        out["relative_houses"] = rel
    except Exception as e:
        out["relative_houses"] = {"__error__": str(e)}
    return out


def _as_int(v):
    """Coerce a value likely to be a numpy integer to a plain Python int (JSON-safe)."""
    try:
        if hasattr(v, "item"):
            return int(v.item())
        if isinstance(v, float) and float(v).is_integer():
            return int(v)
        return v
    except Exception:
        return v


def _normalize_int_grid(grid):
    """Recursively convert numpy ints to plain ints in a nested list."""
    if isinstance(grid, (list, tuple)):
        return [_normalize_int_grid(x) for x in grid]
    return _as_int(grid)


def _normalize_int_tuple(tup):
    """Normalize a tuple of arrays/lists (pindas) to plain ints."""
    if isinstance(tup, (list, tuple)):
        return [_normalize_int_tuple(x) for x in tup]
    return _as_int(tup)


def _try_arg_sets(fn, arg_sets):
    """Call `fn` with each candidate arg-set until one succeeds; return the result.
    The candidate order is the KNOWN-correct signature first, so robust fallback never
    overrides a working call (review §2.5)."""
    last = None
    for args in arg_sets:
        try:
            with _quiet():
                return fn(*args)
        except Exception as e:
            last = e
    return {"__error__": str(last)}


# Shadbala sub-components (all take (jd, place) — verified by probe).
_SHADBALA_COMPONENTS = [
    ("sthana_bala", "_sthana_bala"), ("dig_bala", "_dig_bala"),
    ("kaala_bala", "_kaala_bala"), ("drik_bala", "_drik_bala"),
    ("naisargika_bala", "_naisargika_bala"), ("cheshta_bala", "_cheshta_bala"),
    ("ana_bala", "_anayadya_bala"), ("uchcha_bala", "_uchcha_bala"),
    ("kendra_bala", "_kendra_bala"), ("sapthavargaja_bala", "_sapthavargaja_bala"),
    ("drekkana_bala", "_drekkana_bala"), ("navamsa_bala", "_navamsa_bala"),
    ("divaratri_bala", "_divaratri_bala"), ("nathonnata_bala", "_nathonnath_bala"),
    ("paksha_bala", "_paksha_bala"), ("tribhaga_bala", "_tribhaga_bala"),
    ("abda_bala", "_abda_bala"), ("masa_bala", "_masa_bala"),
    ("vaara_bala", "_vaara_bala"), ("hora_bala", "_hora_bala"),
    ("ayana_bala", "_ayana_bala"), ("yuddha_bala", "_yuddha_bala"),
    ("ishta_phala", "_ishta_phala"), ("subha_phala", "_subha_rashmi"),
]


def build_strength_full(env, h, jd, place, planet_positions):
    """Every Shadbala sub-component + the vargeeya/vimsopaka balas + shad_bala total.

    Each component is a list in planet order (Sun..Saturn) which we label; the
    special ones that need different inputs are guarded individually."""
    strength = env["strength"]
    charts = env["charts"]
    out = {}
    def s(fn, *a, **k):
        try:
            with _quiet():
                return fn(*a, **k)
        except Exception as e:
            return {"__error__": str(e)}
    # shad_bala total + its 6 primary components
    out["shad_bala"] = s(strength.shad_bala, jd, place)
    # ---- ROUND-12 (review F1) / ROUND-13 (item 3): decoded shad_bala payload ----
    # ROUND-13: the decode moved into the shared _decode_shadbala_summary helper so
    # the FAST `strengths` section and this deep section emit IDENTICAL summaries.
    out["shad_bala_summary"] = _decode_shadbala_summary(out.get("shad_bala"),
                                                        env["const"])
    # REVIEW FIX (correct per-component argument): the `_SHADBALA_COMPONENTS` list assumed
    # every sub-component takes `(jd, place)`, but that is wrong for several:
    #   _uchcha_bala / _kendra_bala        -> take (planet_positions,)
    #   _drekkana_bala / _navamsa_bala     -> take a p_to_h dict of the D3 / D9 chart
    # Each of these was previously recorded as a failure. We now build the exact arg for
    # each component so they actually compute. (Verified signatures by introspection.)
    #
    # ROUND-5 FIX (girl chart IndexError): `_drekkana_bala`/`_navamsa_bala` index
    # `const.house_strengths_of_planets[p][h]` — a 12-entry list per planet keyed by the
    # SIGN index (0..11) of the planet in the divisional chart, NOT a 1-based house-from-
    # lagna. PyJHora's own `pancha_vargeeya_bala` builds the arg as
    # `utils.get_planet_house_dictionary_from_planet_positions(divisional_chart(...))`
    # (= {planet_id: sign_index}). The previous 1-based house dict crashed with
    # "list index out of range" whenever a planet fell in the 12th house from the
    # divisional lagna (and mis-scored every other planet). We now use the library's
    # canonical construction and decode the {planet_id: score} result keys to NAMES.
    const = env["const"]
    utils = env["utils"]
    def _shadbala_args(fn_name):
        if fn_name in ("_uchcha_bala", "_kendra_bala"):
            return (planet_positions,)
        if fn_name in ("_drekkana_bala", "_navamsa_bala"):
            factor = 3 if fn_name == "_drekkana_bala" else 9
            try:
                # ROUND-8 (review P2-3): getattr with the classical default (Lagna + 9
                # grahas = 10 entries) instead of a hard private-attribute access.
                _n = getattr(const, "_pp_count_upto_ketu", 10)
                _dv = charts.divisional_chart(
                    jd, place, divisional_chart_factor=factor)[:_n]
                return (utils.get_planet_house_dictionary_from_planet_positions(_dv),)
            except Exception:
                return None
        return (jd, place)
    for lab, fn_name in _SHADBALA_COMPONENTS:
        fn = getattr(strength, fn_name, None)
        if not fn:
            continue
        args = _shadbala_args(fn_name)
        if args is None:
            out[lab] = {"__error__": f"{lab}: divisional chart unavailable; not computable"}
            continue
        try:
            r = fn(*args)
            if fn_name in ("_drekkana_bala", "_navamsa_bala") and isinstance(r, dict):
                r = {_decode_planet_id(k): v for k, v in r.items()}
            out[lab] = r
        except Exception as e:
            # ROUND-5: `_cheshta_bala`/`_subha_rashmi` previously ALWAYS failed here due to a
            # PyJHora-internal bug (old `_cheshta_bala` calls `drik.planet_list.index(p)` but
            # planet_list is a dict in this build). The compat patch installed by
            # `_isolated_pyjhora_config` delegates to the library's own maintained
            # `_cheshta_bala_new(use_epoch_table=True)` — the same code path `shad_bala`
            # uses internally — so both now compute and MATCH the composed total. This
            # fallback message remains only for future builds where even the new path fails.
            out[lab] = {"__error__": f"{lab}: {e} (not computable in this PyJHora build; "
                                    f"the composed shad_bala total is unaffected)"}
    # vargeeya balas
    for lab, fn in (("pancha_vargeeya", strength.pancha_vargeeya_bala),
                    ("dwadhasa_vargeeya", strength.dwadhasa_vargeeya_bala)):
        try:
            out[lab] = fn(jd, place)
        except Exception as e:
            out[lab] = str(e)
    # bhava bala
    out["bhava_bala"] = s(strength.bhava_bala, jd, place)
    out["bhava_drishti_bala"] = s(strength.bhava_drishti_bala, jd, place)
    out["harsha_bala"] = s(strength.harsha_bala, _dob(h), _tob(h), place)
    # vimsopaka balas + vaiseshikamsa. Verified: these take `(jd_at_dob, place)` (the
    # review's suggestion to try `(planet_positions,)` FIRST would break them, so we try
    # the correct signature first — but fall back robustly across PyJHora versions).
    for n in ["vimsopaka_shadvarga_of_planets", "vimsopaka_sapthavarga_of_planets",
              "vimsopaka_dasavarga_of_planets", "vimsopaka_shodhasavarga_of_planets",
              "vaiseshikamsa_shadvarga_of_planets", "vaiseshikamsa_sapthavarga_of_planets",
              "vaiseshikamsa_dhasavarga_of_planets", "vaiseshikamsa_shodhasavarga_of_planets"]:
        fn = getattr(charts, n, None)
        if fn:
            out[n.replace("_of_planets", "")] = _try_arg_sets(
                fn, [(jd, place), (planet_positions,), (jd, place, planet_positions)])
    # ---- ROUND-12 (review F1): kashta phala — the library has NO kashta function
    # and no dosha-rashmi; its _ishta_phala is a 0..60 dignity score (exalted 60,
    # moola-trikona 45, own sign 30, else compound-relation 22/15/8/4/2). The 0..60
    # complement convention (ishta + kashta = 60) matches modern shadbala-software
    # presentations and the review's formula; the classical BPHS rashmi-product
    # ishta/kashta doctrine (subha-rashmi of Moon/Mercury/Jupiter vs dosha-rashmi of
    # Sun/Mars/Saturn) is ABSENT from the library and was NOT reimplemented.
    try:
        _ip12 = out.get("ishta_phala")
        if isinstance(_ip12, (list, tuple)) and len(_ip12) == 7:
            out["kashta_phala_complement"] = [round(60.0 - float(v), 4)
                                              for v in _ip12]
            out["ishta_kashta_note"] = (
                "ROUND-12 (review F1): ishta_phala = the library's own 0..60 dignity "
                "score (higher = more benefic results in the planet's dashas and "
                "significant transits); kashta_phala_complement = 60 − ishta_phala, "
                "wrapper-defined on the LIBRARY's scale per the review's formula and "
                "modern software convention. The classical rashmi-product ishta/"
                "kashta doctrine is absent from PyJHora and was not reimplemented "
                "(disclosed, not invented). subha_phala (_subha_rashmi) is the "
                "library's separate auspicious-ray value.")
        else:
            out["kashta_phala_complement"] = {"__error__": "ishta_phala unavailable"}
    except Exception as e:
        out["kashta_phala_complement"] = {"__error__": str(e)}
    # ---- ROUND-12 (review F3): decoded twins for every raw 7-element list ----
    # Each <key>_by_planet maps the raw component list (Sun..Saturn — the library's
    # shadbala row order) to planet names. Raw lists stay untouched for parity.
    try:
        for _k12 in list(out.keys()):
            _v12 = out[_k12]
            if isinstance(_v12, (list, tuple)) and len(_v12) == 7 and all(
                    isinstance(x, (int, float)) and not isinstance(x, bool)
                    for x in _v12):
                out[_k12 + "_by_planet"] = {PLANET_NAMES[i]: round(float(x), 4)
                                            for i, x in enumerate(_v12)}
        out["by_planet_note"] = (
            "ROUND-12 (review F3): every raw 7-element component list in this "
            "section now has a decoded <key>_by_planet twin (row order Sun, Moon, "
            "Mars, Mercury, Jupiter, Venus, Saturn — the library's shadbala order, "
            "verified against shad_bala's own rows). Raw lists remain unchanged for "
            "backward parity; 12-element lists (bhava_bala etc.) are per-HOUSE and "
            "are deliberately NOT planet-twinned.")
    except Exception:
        pass
    return out


def build_ashtakavarga_deep(env, h, jd, place, planet_positions):
    """Full Ashtakavarga: binna + samudhaya + prastara, sodhana (trikona/ekadhipatya),
    sodhya pindas, and vimsopaka — using the correct house-indexed notation."""
    utils = env["utils"]
    ashtakavarga = env["ashtakavarga"]
    charts = env["charts"]
    out = {}
    def s(fn, *a, **k):
        try:
            with _quiet():
                return fn(*a, **k)
        except Exception as e:
            return {"__error__": str(e)}
    # Ashtakavarga is SIGN-based: use the raw sign-based list directly (review §2.4).
    h2p = utils.get_house_planet_list_from_planet_positions(planet_positions)
    out["houses_sign_indexed"] = h2p
    res = None
    try:
        res = ashtakavarga.get_ashtaka_varga(h2p)
        out["binna_ashtakavarga"] = res[0]
        out["samudhaya_ashtakavarga"] = res[1]
        out["prastara_ashtakavarga"] = res[2]
    except Exception as e:
        out["__error__"] = str(e)
    # ROUND-8 labelled views (same block as the FAST ashtakavarga section — decorative;
    # the raw arrays above remain authoritative).
    try:
        if res and len(res) > 1 and isinstance(res[1], (list, tuple)):
            _sav = [int(v) for v in res[1]]
            _lagna_sign = _sign_of_long(_planetary_longitude(planet_positions)[LAGNA])
            out["samudhaya_by_sign"] = {RAASI_NAMES[i]: _sav[i] for i in range(12)}
            out["samudhaya_by_house"] = {("H%d" % hh): _sav[(_lagna_sign + hh - 1) % 12]
                                         for hh in range(1, 13)}
            # ROUND-12 (review Bug-3, REAL — FIXED): row 7 of the BAV grid is the
            # LAGNA's own row (get_ashtaka_varga docstring: "0=Sun..7=Lagnam"), NOT
            # Rahu — Rahu/Ketu have no BAV rows. PLANET_NAMES[7] = "Rahu" mislabeled
            # it in both the FAST and deep builders; BAV_ROW_NAMES is now used.
            out["binna_by_sign"] = {
                (BAV_ROW_NAMES[pi] if pi < len(BAV_ROW_NAMES) else f"Row_{pi}"):
                    {RAASI_NAMES[i]: int(v) for i, v in enumerate(row)}
                for pi, row in enumerate(res[0])}
            out["bindu_labels_note"] = (
                "ROUND-12 (review Bug-3, FIXED): binna rows follow PyJHora's BAV "
                "order (get_ashtaka_varga docstring '0=Sun..7=Lagnam'): index 0 = "
                "Sun, then Moon, Mars, Mercury, Jupiter, Venus, Saturn, and index 7 "
                "= LAGNA — the ascendant's own BAV row. The previous label used "
                "PLANET_NAMES[7] = 'Rahu', mislabeling the Lagna row (Rahu/Ketu have "
                "NO BAV rows). samudhaya index 0 = Aries. *_by_house uses whole-sign "
                "houses counted from the lagna sign.")
    except Exception:
        pass  # labels are decorative; the raw arrays above remain authoritative
    # sodhana (trikona / ekadhipatya) + sodhya pindas. These take the BINNA grid and the
    # sign-indexed h2p (not the h2p for sodhana input) — see their signatures (review §2.6):
    #   _trikona_sodhana(binna)                          -> reduced binna
    #   _ekadhipatya_sodhana(trikona_reduced, chart_1d)  -> sodhita binna
    #   sodhaya_pindas(binna, chart_1d)                  -> (raasi, graha, sodhya) pindas
    binna = res[0] if res else None
    trikona = None
    if binna is not None:
        # NOTE: `_trikona_sodhana`/`sodhaya_pindas` shallow-copy and MUTATE the binna rows
        # in place, so we hand them a deep copy to keep `binna_ashtakavarga` pristine.
        try:
            trikona = ashtakavarga._trikona_sodhana(copy.deepcopy(binna))
            out["sodhana_trikona"] = _normalize_int_grid(trikona)
        except Exception as e:
            out["sodhana_trikona"] = str(e)
        try:
            out["sodhana_ekadhipatya"] = _normalize_int_grid(
                ashtakavarga._ekadhipatya_sodhana(trikona, h2p))
        except Exception as e:
            out["sodhana_ekadhipatya"] = str(e)
        try:
            out["sodhya_pindas"] = _normalize_int_tuple(
                ashtakavarga.sodhaya_pindas(copy.deepcopy(binna), h2p))
            # ROUND-12 (review F4): decode the three pinda arrays. Library docstring:
            # each array is PER-PLANET, 0=Sun..6=Saturn (7 values each — not the 12/8
            # shapes the review's snippet assumed; its proposed code also keyed all
            # three arrays off the same variable). Verified: shodhya = raasi + graha.
            _sp12 = out["sodhya_pindas"]
            if isinstance(_sp12, (list, tuple)) and len(_sp12) == 3 and all(
                    isinstance(a, (list, tuple)) and len(a) == 7 for a in _sp12):
                _n7 = PLANET_NAMES[:7]
                out["sodhya_pindas_decoded"] = {
                    "raasi_pindas": {_n7[i]: int(v) for i, v in enumerate(_sp12[0])},
                    "graha_pindas": {_n7[i]: int(v) for i, v in enumerate(_sp12[1])},
                    "shodhya_pindas": {_n7[i]: int(v) for i, v in enumerate(_sp12[2])},
                    "__note__": (
                        "ROUND-12 (review F4): sodhaya_pindas returns (raasi, graha, "
                        "shodhya) pindas — each 7 values indexed 0=Sun..6=Saturn per "
                        "the library docstring; shodhya = raasi + graha elementwise "
                        "(verified). Pindas classically feed pindayu longevity and "
                        "transit-strength readings. Rasimana-multiplier variants "
                        "(PVR book Virgo=6 vs alternate Virgo=5) follow the library's "
                        "const.ashtakavarga_rasimana_multipliers — disclosed in "
                        "sodhaya_pindas' own docstring. Raw arrays remain in "
                        "sodhya_pindas."),
                }
        except Exception as e:
            out["sodhya_pindas"] = str(e)
    # ROUND-7 (review C7): provenance note for the sodhana chain. Upstream issue #52
    # (diagonal-only ekadhipatya reduction; Sun/Moon charts never reduced) affected
    # 4.8.7 — verified FIXED in the 5.0 dev line ("V4.8.9 - Changes per Issue #52"
    # marker present in ashtakavarga.py); 5.0 also added `rasimana_option` to
    # sodhaya_pindas (default = corrected Virgo multiplier per secondary sources).
    out["__upstream_note__"] = (
        "sodhana_ekadhipatya/sodhya_pindas: upstream issue naturalstupid/PyJHora#52 "
        "(4.8.7: only each planet's own sign-pair reduced, Sun & Moon skipped) is FIXED in "
        "the PyJHora 5.0 dev line this build targets — verified present in the installed "
        "ashtakavarga source. If you run this wrapper against stock 4.8.7, treat these two "
        "outputs as affected by #52.")
    # vimsopaka shadvarga/shodasavarga as the quantitative strength arm
    for n in ["vimsopaka_shadvarga_of_planets", "vimsopaka_shodhasavarga_of_planets"]:
        fn = getattr(charts, n, None)
        if fn:
            try:
                out[n.replace("_of_planets", "")] = fn(jd, place)
            except Exception as e:
                out[n.replace("_of_planets", "")] = str(e)
    return out


def _house_list_from_rows(rows):
    """From `[[pid,[sign,deg]],...]` build the two PyJHora lists:
    (sign_indexed_list, house_from_asc_indexed_list), both with 'L' placed."""
    sign_list = ["" for _ in range(12)]
    house_list = ["" for _ in range(12)]
    asc_sign = None
    for pid, val in rows:
        if pid == "L":
            asc_sign = int(val[0])
    if asc_sign is None:
        asc_sign = 0
    for pid, val in rows:
        sign = int(val[0])
        house = (sign - asc_sign) % 12
        tok = "L" if pid == "L" else str(pid)
        sign_list[sign] = (sign_list[sign] + "/" + tok).strip("/")
        house_list[house] = (house_list[house] + "/" + tok).strip("/")
    return sign_list, house_list


def _tajaka_months_elapsed(jd_now, h):
    """Fallback month WITHIN the current Tajaka annual (solar-return) year, 1..12.

    This is a calendar-based approximation used ONLY when the exact annual-chart start JD
    cannot be obtained. The authoritative path is `_tajaka_month_from_annual_start` (review:
    "Tajaka month logic remains calendar-month based rather than actual solar-return JD
    based"), which anchors to the real solar-return instant. We keep this only as a runtime
    fallback; `build_tajaka_deep` prefers the JD-anchored value.

    The solar return recurs on the birth month/day, so the return month itself is month 1 of
    the NEW annual year (0->1, not 0->12).
    """
    try:
        own_y = _jd_to_ymd(jd_now)[0]
        own_m = _jd_to_ymd(jd_now)[1]
        tot = (own_y - int(h.Date.year)) * 12 + (own_m - int(h.Date.month))
        return ((tot % 12) + 1) if tot >= 0 else 1
    except Exception:
        return 1


def _tajaka_month_from_annual_start(jd_now, annual_start, jd, place, years_elapsed, env):
    """The Tajaka month (1..12) measured from the ACTUAL solar-return (varsha) start JD.

    REVIEW FIX: previously the month was derived from the calendar month alone
    (`own_year-own_month` vs birth), which ignores that the solar return happens at a precise
    instant (birth month/day) and that a Tajaka *month* is a 1/12 division of the solar year
    (~30.44 days). Now we:
      1. get the current annual chart's start `[y,m,d] (+ 'hh:mm:ss')` via
         `tajaka.annual_chart(jd, place, years=years_elapsed)[1]`,
      2. convert it to a JD,
      3. count how many 1/12 solar-year slices have elapsed from that start to `jd_now`,
         and report 1..12.
    If any step fails (or there is no `jd_now`), we return None so the caller falls back.
    """
    if jd_now is None:
        return None
    try:
        if not isinstance(annual_start, (list, tuple)) or not annual_start:
            return None
        date_part = annual_start[0]
        if not isinstance(date_part, (list, tuple)) or len(date_part) < 3:
            return None
        y, m, d = int(date_part[0]), int(date_part[1]), int(date_part[2])
        hm = annual_start[1] if len(annual_start) > 1 else "00:00:00"
        if isinstance(hm, str):
            parts = [int(x) for x in hm.split(":")[:3] + ["0", "0", "0"][:max(0, 3 - hm.count(":"))]]
            parts = parts + [0] * (3 - len(parts))
            hr, mn, sc = parts[0], parts[1], parts[2]
        else:
            hr, mn, sc = 0, 0, 0
        start_jd = env["utils"].julian_day_number((y, m, d), (hr, mn, int(sc)))
        solar_year_days = 365.2425
        slice_days = solar_year_days / 12.0
        month = int((jd_now - start_jd) // slice_days) + 1
        return max(1, min(12, month))
    except Exception:
        return None


def _tajaka_years_elapsed(jd_now, h):
    """Tajaka year NUMBER (1 = the birth/solar-return year) as of `jd_now`.

    `years` passed to `tajaka.annual_chart(jd, place, years=N)` is the index of the annual
    chart, which increments each time the solar return (birth month/day) passes. A simple
    `current_year - birth_year` (the previous fix) ignores the birthday boundary — a date
    BEFORE this year's birthday belongs to the annual chart that started LAST year. We check
    whether the birthday has passed (month/day comparison) before incrementing.
    """
    try:
        own_y, own_m, own_d = _jd_to_ymd(jd_now)
        by, bm, bd = int(h.Date.year), int(h.Date.month), int(h.Date.day)
        base = own_y - by
        # only count the current year's return if this year's birthday has already passed
        if (own_m, own_d) < (bm, bd):
            base -= 1
        return max(0, base) + 1
    except Exception:
        # fall back to the (approximate) calendar-year difference
        try:
            return max(1, _jd_to_ymd(jd_now)[0] - int(h.Date.year))
        except Exception:
            return 1


def build_tajaka_deep(env, h, jd, place, planet_positions, jd_now=None, annual_year=None,
                      tajaka_month=None):
    """Deeper Tajaka: monthly & sixty-hour charts, Lord of the Month, Muntha for the
    month, and all Tajaka Yogas (ithasala, eesarpha, nakta, yamaya, manahoo, kamboola,
    radda, duhphali-kutta, ishkavala, induvara) plus Tajaka aspects.

    `tajaka_month` (1..12) explicitly selects the month; otherwise it is derived from
    `jd_now`. It MUST be resolved before the monthly chart / Lord of the Month are
    computed so the label and the chart always agree (review §2.1)."""
    tajaka = env["tajaka"]
    tajaka_yoga = env["tajaka_yoga"]
    out = {}
    # ROUND-8 FIX (review P0-4, CONFIRMED): previously this function ABORTED when the
    # tajaka_yoga module was unavailable — discarding the monthly chart, sixty-hour
    # chart, varsha/maasa pravesh, Lord of the Month, Tajaka aspects and deeptamsa
    # details, none of which depend on tajaka_yoga (only the yoga-pair block below
    # does). Compute everything; guard ONLY the yogas.
    def s(fn, *a, **k):
        try:
            with _quiet():
                return fn(*a, **k)
        except Exception as e:
            return {"__error__": str(e)}
    # resolve the annual year (same boundary-aware logic as build_tajaka; review §3)
    years_elapsed = 1
    if annual_year is not None:
        try:
            years_elapsed = max(1, int(annual_year))
        except Exception:
            years_elapsed = 1
    elif jd_now is not None:
        years_elapsed = _tajaka_years_elapsed(jd_now, h)
    out["years_elapsed"] = years_elapsed

    # The ANNUAL (varsha) chart: Tajaka is annual astrology, so aspects/yogas/kendras
    # must be read from the solar-return chart, NOT the natal D1 chart (review §2.6).
    annual_pp = None
    annual_jd = None
    try:
        ac = tajaka.annual_chart(jd, place, years=years_elapsed)
        # ac = ([['L',(sign,deg)], [pid,(sign,deg)], ...], [(y,m,d), 'HH:MM:SS'])
        if isinstance(ac, (tuple, list)) and ac and isinstance(ac[0], (list, tuple)):
            annual_pp = ac[0]
        # PyJHora 5.0: some tajaka-yoga helpers now want (jd, place) instead of the
        # annual positions — derive the ANNUAL chart's julian day from ac[1].
        try:
            if isinstance(ac, (tuple, list)) and len(ac) > 1 and isinstance(ac[1], (list, tuple)) \
                    and len(ac[1]) == 2:
                (ay, am, ad_), atime = ac[1][0], ac[1][1]
                _parts = [int(float(x)) for x in str(atime).replace(":", " ").split()][:3]
                while len(_parts) < 3:
                    _parts.append(0)
                annual_jd = env["utils"].julian_day_number(
                    (int(ay), int(am), int(ad_)), tuple(_parts))
        except Exception:
            annual_jd = None
    except Exception:
        annual_pp = None
    out["annual_chart_used"] = annual_pp is not None
    if annual_pp is None:
        annual_pp = planet_positions   # graceful fallback (still decoded, flagged below)
        out["__annual_warning__"] = "annual_chart unavailable; Tajaka values use natal positions"

    # monthly chart + Lord of the Month for the current annual-month. The month is resolved
    # HERE (from the explicit --tajaka-month, else derived) so the label, monthly_chart,
    # lord_of_the_month and sixty_hour_chart all use the SAME month (review §2.1).
    #
    # REVIEW FIX: the auto-derived month is now anchored to the ACTUAL solar-return start JD
    # (via `_tajaka_month_from_annual_start`, using `annual_chart(...)[1]`), not the calendar
    # month. `_tajaka_months_elapsed` remains only as a last-resort fallback.
    if tajaka_month is not None:
        try:
            months_elapsed = max(1, min(12, int(tajaka_month)))
        except Exception:
            months_elapsed = None
    else:
        months_elapsed = None
    if months_elapsed is None:
        _m_from_jd = None
        try:
            with _quiet():
                _ac = tajaka.annual_chart(jd, place, years=years_elapsed)
            annual_start = _ac[1] if isinstance(_ac, (tuple, list)) and len(_ac) > 1 else None
            _m_from_jd = _tajaka_month_from_annual_start(jd_now, annual_start, jd, place,
                                                         years_elapsed, env)
        except Exception:
            _m_from_jd = None
        if _m_from_jd is not None:
            months_elapsed = max(1, min(12, int(_m_from_jd)))
        else:
            months_elapsed = max(1, min(12, int(_tajaka_months_elapsed(jd_now, h))))
    out["months_elapsed"] = months_elapsed
    out["month_anchor"] = ("solar-return JD" if months_elapsed == _m_from_jd else "explicit/fallback") \
        if tajaka_month is None else "explicit --tajaka-month"
    out["monthly_chart"] = s(tajaka.monthly_chart, jd, place, years=years_elapsed, months=months_elapsed)
    out["lord_of_the_month"] = s(tajaka.lord_of_the_month, jd, place, years_elapsed, months_elapsed)
    # sixty-hour chart (configurable count via optional arg, default current)
    shc = int(env.get("_sixty_hour_count", 1) or 1)
    out["sixty_hour_chart"] = s(tajaka.sixty_hour_chart, jd, place, years=years_elapsed,
                                months=months_elapsed, sixty_hour_count=shc)
    out["varsha_pravesh"] = s(tajaka.varsha_pravesh, jd, place, years=years_elapsed)
    out["maasa_pravesh"] = s(tajaka.maasa_pravesh, jd, place, years=years_elapsed, months=months_elapsed)

    # Annual ascendant sign for kendras/panaparas/apoklimas
    def _asc_sign(ppp):
        for pid, val in ppp:
            if pid == "L":
                return int(val[0])
        return 0
    annual_asc = _asc_sign(annual_pp)
    out["annual_ascendant_sign"] = RAASI_NAMES[annual_asc] if 0 <= annual_asc < 12 else annual_asc
    out["kendras_panaparas_apoklimas"] = {
        "kendras": [((annual_asc + k) % 12) for k in (0, 3, 6, 9)],
        "panaparas": [((annual_asc + k) % 12) for k in (1, 4, 7, 10)],
        "apoklimas": [((annual_asc + k) % 12) for k in (2, 5, 8, 11)],
    }

    # Tajaka aspects of each ANNUAL-chart planet — aspects_of_the_planet(sign_list, planet)
    hpl, _hl = _house_list_from_rows(annual_pp)
    out["tajaka_aspects"] = {}
    for p in PLANET_ORDER:
        pid = _planet_id(p)
        if pid is None:
            continue
        r = s(tajaka.aspects_of_the_planet, hpl, pid)
        if isinstance(r, (tuple, list)) and len(r) == 2:
            out["tajaka_aspects"][p] = {
                "aspected_planets": _decode_planet_id_list(r[1]) if isinstance(r[1], (list, tuple)) else r[1],
                "aspected_houses": [("H%d" % (int(_h) % 12 + 1)) for _h in (r[0] or [])],
            }
        else:
            out["tajaka_aspects"][p] = r

    # Tajaka Yogas (evaluate on the ANNUAL chart positions, not natal) — ROUND-8: this
    # is the ONLY block that needs the tajaka_yoga module (review P0-4). When it is
    # absent, emit a typed note for the yogas and CONTINUE with everything else
    # (monthly/60-hour charts, praveshas, aspects, deeptamsa, ROUND-5 tables).
    if tajaka_yoga is None:
        out["tajaka_yogas"] = {"__error__": "tajaka_yoga module not available in this "
                                            "PyJHora build (the rest of tajaka_deep is "
                                            "unaffected)"}
    else:
        yg = {}
        for name, fn in (("ithasala_pairs", tajaka_yoga.get_ithasala_yoga_planet_pairs),
                         ("eesarpha_pairs", tajaka_yoga.get_eesarpha_yoga_planet_pairs),
                         ("kamboola_pairs", tajaka_yoga.get_kamboola_yoga_planet_pairs),
                         ("gairi_kamboola_pairs", tajaka_yoga.get_gairi_kamboola_yoga_planet_pairs),
                         ("radda_pairs", tajaka_yoga.get_radda_yoga_planet_pairs),
                         ("manahoo_pairs", tajaka_yoga.get_manahoo_yoga_planet_pairs),
                         ("khallasara_pairs", tajaka_yoga.get_khallasara_yoga_planet_pairs),
                         ("duhphali_kutta_pairs", tajaka_yoga.get_duhphali_kutta_yoga_planet_pairs),
                         ("nakta_triples", tajaka_yoga.get_nakta_yoga_planet_triples),
                         ("yamaya_triples", tajaka_yoga.get_yamaya_yoga_planet_triples)):
            # PyJHora 5.0: get_duhphali_kutta_yoga_planet_pairs takes (jd, place) and derives
            # positions itself; the rest still take the annual positions. Try both shapes.
            if name == "duhphali_kutta_pairs":
                yg[name] = _try_arg_sets(fn, [(annual_jd or jd, place), (annual_pp,)])
                continue
            try:
                with _quiet():
                    yg[name] = fn(annual_pp)
            except Exception as e:
                yg[name] = str(e)
        # planet_to_house_dict for ishkavala / induvara: {pid: house(1..12), 'L': asc_house}
        try:
            appn = _planetary_longitude(annual_pp)
            htp = {p: ((_sign_of_long(appn.get(p, 0)) - annual_asc) % 12 + 1) for p in PLANET_ORDER if p in appn}
            htp["L"] = 1
            yg["ishkavala"] = s(tajaka_yoga.ishkavala_yoga, htp)
            yg["induvara"] = s(tajaka_yoga.induvara_yoga, htp)
        except Exception as e:
            yg["ishkavala"] = str(e)
        out["tajaka_yogas"] = yg
    out["tajaka_uses"] = "annual (varsha) chart positions"

    # ---- ROUND 5: tajaka rasi-aspect tables, ithasala/deeptamsa verification,
    # ---- benefic/malefic planet-on-house aspect map, approximate annual chart ----
    def _sign_names(sl):
        try:
            return [RAASI_NAMES[int(x) % 12] for x in (sl or [])]
        except Exception:
            return sl
    def _pl_ids(pl):
        # tajaka aspect helpers return chart_1d-style string ids ('8', '1/6' split parts, 'L')
        res_l = []
        for x in (pl or []):
            if isinstance(x, str) and x.strip().upper() == "L":
                res_l.append("Lagna")
            elif isinstance(x, str) and x.strip().isdigit():
                res_l.append(_decode_planet_id(int(x)))
            else:
                res_l.append(_decode_planet_id(x))
        return res_l
    _typed_fns = [
        ("trinal", getattr(tajaka, "trinal_aspects_of_the_raasi", None)),
        ("sextile", getattr(tajaka, "sextile_aspects_of_the_raasi", None)),
        ("square", getattr(tajaka, "square_aspects_of_the_raasi", None)),
        ("opposition", getattr(tajaka, "opposition_aspects_of_the_raasi", None)),
        ("conjunction", getattr(tajaka, "conjunction_aspects_of_the_raasi", None)),
        ("benefic", getattr(tajaka, "benefic_aspects_of_the_raasi", None)),
        ("malefic", getattr(tajaka, "malefic_aspects_of_the_raasi", None)),
        ("neutral", getattr(tajaka, "neutral_aspects_of_the_raasi", None)),
        ("semi_sextile", getattr(tajaka, "semi_sextile_aspects_of_the_raasi", None)),
    ]
    try:
        # NOTE: the aggregate aspects_of_the_raasi (and its conjunction component) die with
        # "X is not in list" whenever the queried sign holds no planet — a known library
        # fragility. We therefore call each typed helper individually, guarded per call.
        det = {}
        for r in range(12):
            row = {}
            for lbl, fn in _typed_fns:
                if fn is None:
                    continue
                try:
                    res = fn(hpl, r)
                    if isinstance(res, (tuple, list)) and len(res) == 2:
                        row[lbl] = {
                            "raasis": _sign_names(res[0]),
                            "planets": _pl_ids(res[1]),
                        }
                    else:
                        row[lbl] = res
                except Exception as e:
                    row[lbl] = ("none (sign has no planet — library conjunction-lookup "
                                "limitation: %s)" % e) if "not in list" in str(e) else "__error__: %s" % e
            det[RAASI_NAMES[r]] = row
        out["tajaka_raasi_aspect_details"] = det
    except Exception as e:
        out["tajaka_raasi_aspect_details"] = {"__error__": str(e)}
    try:
        typed = {}
        for _lbl, _fn in _typed_fns:
            if _fn is None:
                continue
            try:
                _r = _fn(hpl, annual_asc)
                if isinstance(_r, (tuple, list)) and len(_r) == 2:
                    typed[_lbl] = {"raasis": _sign_names(_r[0]),
                                   "planets": _pl_ids(_r[1])}
                else:
                    typed[_lbl] = _sign_names(_r)
            except Exception as _e:
                typed[_lbl] = "__error__: %s" % _e
        out["tajaka_lagna_sign_aspect_breakdown"] = typed
    except Exception as e:
        out["tajaka_lagna_sign_aspect_breakdown"] = {"__error__": str(e)}
    try:
        pairs = []
        _ip = yg.get("ithasala_pairs")
        if isinstance(_ip, (list, tuple)):
            for _pr in _ip:
                if isinstance(_pr, (list, tuple)) and len(_pr) == 2:
                    pairs.append(("ithasala", int(_pr[0]), int(_pr[1])))
        if not pairs:
            pairs = [("reference:Sun-Moon", 0, 1), ("reference:Jupiter-Venus", 4, 5)]
        ith = {}
        _ith_types = {1: "Varthamaana ithasala", 2: "Bhavishya ithasala", 3: "Poorna ithasala"}
        for _tag, a, b in pairs[:8]:
            nm = "%s-%s" % (PLANET_ORDER[a], PLANET_ORDER[b]) if max(a, b) < len(PLANET_ORDER) else "%d-%d" % (a, b)
            key = nm if _tag == "ithasala" else _tag
            try:
                within = tajaka.both_planets_within_their_deeptamsa(annual_pp, a, b)
                if isinstance(within, (tuple, list)) and len(within) == 2:
                    within = {"within_deeptamsa": bool(within[0]),
                              "ithasala_type": _ith_types.get(int(within[1]) if str(within[1]).isdigit() else None, within[1])}
            except Exception as _e:
                within = "__error__: %s" % _e
            try:
                appr = tajaka.both_planets_approaching(annual_pp, a, b)
            except Exception as _e:
                appr = "__error__: %s" % _e
            try:
                # NOTE: planets_have_aspects takes the chart_1d list FIRST (hpl), then the two ids
                have = tajaka.planets_have_aspects(hpl, a, b)
            except Exception as _e:
                have = "__error__: %s" % _e
            ith[key] = {
                "pair": [PLANET_ORDER[a], PLANET_ORDER[b]],
                "from_ithasala_yoga_list": (_tag == "ithasala"),
                "planets_have_aspects": have,
                "both_approaching": appr,
                "within_deeptamsa": within,
            }
        out["ithasala_deeptamsa_details"] = ith
    except Exception as e:
        out["ithasala_deeptamsa_details"] = {"__error__": str(e)}
    try:
        hmap = {}
        for p, pid in (("Jupiter", 4), ("Saturn", 6), ("Mars", 2), ("Venus", 5), ("Mercury", 3)):
            row = {}
            for hh in (1, 2, 5, 7, 9, 10, 11):
                try:
                    _b = tajaka.planet_has_benefic_aspect_on_house(hpl, pid, hh)
                except Exception as _e:
                    _b = str(_e)
                try:
                    _m = tajaka.planet_has_malefic_aspect_on_house(hpl, pid, hh)
                except Exception as _e:
                    _m = str(_e)
                row["H%d" % hh] = {"benefic_aspect": _b, "malefic_aspect": _m}
            hmap[p] = row
        out["planet_aspect_on_houses_tajaka"] = hmap
    except Exception as e:
        out["planet_aspect_on_houses_tajaka"] = {"__error__": str(e)}
    try:
        # annual_chart_approximate(dob, tob, place, ...) — NOT (jd, place): it internally
        # walks the Table-71 weekday drift from the birth date/time.
        _r = s(tajaka.annual_chart_approximate, _dob(h), _tob(h), place, years=years_elapsed)
        if isinstance(_r, dict) and str(_r.get("__error__", "")) in ("0", "'0'"):
            # the Table-71 lookup has no entry for this years_elapsed (KeyError 0) — a
            # library data-range limit, not a crash: say so and point at the exact chart.
            out["annual_chart_approximate"] = {
                "__note__": ("Table-71 weekday-drift approximation has no entry for %s completed "
                             "year(s) (library raises KeyError 0 for this age); the exact "
                             "solar-return annual_chart above is authoritative." % years_elapsed)}
        elif isinstance(_r, (list, tuple)) and _r and not isinstance(_r, dict) and "__error__" not in str(_r[0])[:12]:
            _rows = _r[0] if isinstance(_r[0], (list, tuple)) else _r
            dec = {}
            for item in _rows or []:
                try:
                    key, (sgn, deg) = item[0], item[1]
                    nm = "Lagna" if key == "L" else _decode_planet_id(key)
                    dec[nm] = {"sign": RAASI_NAMES[int(sgn) % 12], "degree_in_sign": round(float(deg), 4)}
                except Exception:
                    continue
            _extra = None
            for _x in _r[1:]:
                if isinstance(_x, (int, float)) and not isinstance(_x, bool):
                    _extra = _x
                    break
            out["annual_chart_approximate"] = {
                "positions": dec, "returned_jd_or_count": _extra,
                "note": ("Table-71 weekday-drift APPROXIMATION of the annual (varsha) chart — "
                         "crosscheck only; the exact solar-return annual_chart above is authoritative."),
            }
        else:
            out["annual_chart_approximate"] = _r
    except Exception as e:
        out["annual_chart_approximate"] = {"__error__": str(e)}
    return out


def build_varga_transits(env, h, jd, place, transit_jd, natal_planet_positions, division=None):
    """Transits measured in a DIVISIONAL chart (or the mixed D1+D9) — next/prev entry
    dates and conjunctions in the divisional frame, plus the divisional positions."""
    charts = env["charts"]
    out = {}
    def s(fn, *a, **k):
        try:
            with _quiet():
                return fn(*a, **k)
        except Exception as e:
            return {"__error__": str(e)}
    division = int(division) if division else 9
    try:
        tr = charts.divisional_chart(transit_jd, place, divisional_chart_factor=division)
        tpl = _planetary_longitude(tr)
        # Divisional houses AND the NATAL positions must both come from the DIVISIONAL
        # chart, never the D1 chart (review §2.10 + §3: the natal *divisional* sign of a
        # planet is NOT its D1 sign — e.g. Moon D1 Gemini vs D9 Sagittarius). We compute
        # the natal divisional chart once and reuse it for both the lagna and the planets.
        try:
            ndiv = charts.divisional_chart(jd, place, divisional_chart_factor=division)
            ndiv_lon = _planetary_longitude(ndiv)
            div_asc_sign = _sign_of_long(ndiv_lon[LAGNA])
        except Exception:
            ndiv_lon = None
            div_asc_sign = None
        rows = []
        for p in PLANET_ORDER:
            if p not in tpl:
                continue
            # natal divisional sign/house (fall back to D1 only if the divisional chart failed)
            if ndiv_lon is not None and p in ndiv_lon:
                ns = _sign_of_long(ndiv_lon[p])
            else:
                npl = _planetary_longitude(natal_planet_positions)
                ns = _sign_of_long(npl[p]) if p in npl else None
            if ns is None:
                continue
            _asc = div_asc_sign if div_asc_sign is not None else _sign_of_long(
                _planetary_longitude(natal_planet_positions)[LAGNA])
            rows.append({"planet": p,
                         "transit_D%d_sign" % division: RAASI_NAMES[_sign_of_long(tpl[p])],
                         "transit_house_from_D%d_lagna" % division: _house_number(_asc, _sign_of_long(tpl[p])),
                         "natal_D%d_sign" % division: RAASI_NAMES[ns],
                         "natal_D%d_house" % division: _house_number(_asc, ns)})
        out["divisional_" + str(division)] = {"sign": "D%d" % division, "transits": rows}
    except Exception as e:
        out["divisional_" + str(division)] = str(e)
    # entry dates in the divisional chart (if that API exists)
    for direction in (1, -1):
        dirdot = "next" if direction == 1 else "previous"
        for p in PLANET_ORDER:
            pid = _planet_id(p)
            if pid is None:
                continue
            fn = getattr(charts, dirdot + "_planet_entry_date_divisional_chart", None)
            if fn:
                try:
                    # ROUND-9 (review Enhancement 2): decode the raw [jd, divisional-
                    # longitude] pair into an ISO date + sign, consistent with
                    # next_conjunctions_divisional (the engine's decode-everything policy).
                    _ev = fn(transit_jd, place, pid, divisional_chart_factor=division)
                    out.setdefault(dirdot + "_entry_dates", {})[p] = _decode_entry_pair(_ev)
                except Exception as e:
                    out.setdefault(dirdot + "_entry_dates", {})[p] = str(e)
    # ---- ROUND 5: next conjunctions of the classic trigger pairs measured in the
    # ---- DIVISIONAL frame (charts.next_conjunction_of_planet_pair_divisional_chart) ----
    try:
        _fnc = getattr(charts, "next_conjunction_of_planet_pair_divisional_chart", None)
        if _fnc:
            conj = {}
            for lbl, a, b in (("Jupiter-Saturn", 4, 6), ("Jupiter-Venus", 4, 5), ("Sun-Moon", 0, 1)):
                try:
                    with _quiet():
                        r = _fnc(transit_jd, place, a, b, divisional_chart_factor=division)
                    if isinstance(r, (tuple, list)) and r and isinstance(r[0], (int, float)):
                        try:
                            from jhora import utils as _u2
                            _y, _m, _d, _fh = _u2.jd_to_gregorian(r[0])
                            _ds = "%04d-%02d-%02d %s" % (int(_y), int(_m), int(_d), _hours_to_hhmm(_fh))
                        except Exception:
                            _y, _m, _d = _jd_to_ymd(r[0])
                            _ds = "%04d-%02d-%02d" % (_y, _m, _d)
                        conj[lbl] = {
                            "date": _ds,
                            "planet1_longitude": round(float(r[1]), 4) if len(r) > 1 else None,
                            "planet2_longitude": round(float(r[2]), 4) if len(r) > 2 else None,
                        }
                    else:
                        conj[lbl] = r
                except Exception as e:
                    conj[lbl] = "__error__: %s" % e
            out["next_conjunctions_divisional"] = conj
    except Exception as e:
        out["next_conjunctions_divisional"] = {"__error__": str(e)}
    return out


def build_eclipse_deep(env, h, jd, place):
    """Full eclipse surface: solar/lunar, next/prev, type + global location + timing."""
    from jhora.panchanga import eclipse as ec
    out = {}
    def s(fn, *a, **k):
        try:
            with _quiet():
                return fn(*a, **k)
        except Exception as e:
            return {"__error__": str(e)}
    # PyJHora's eclipse functions now access `place.elevation`, so they need the Place
    # OBJECT (not a (name,lat,lon,tz) tuple — a tuple has no .elevation; verified it raises
    # AttributeError). Pass `place` directly (review §3: "accesses place.elevation").
    out["is_solar_eclipse"] = s(env["drik"].is_solar_eclipse, jd, place)
    _ile = getattr(env["drik"], "is_lunar_eclipse", None)
    if _ile:
        out["is_lunar_eclipse"] = s(_ile, jd, place)
    # global max-location eclipses (next/prev, solar and lunar) — pass the Place object
    try:
        out["next_solar_eclipse_global"] = list(ec.next_solar_eclipse(jd, place, show_maximum_eclipse_global_location=True))
    except Exception as e:
        out["next_solar_eclipse_global"] = str(e)
    try:
        out["prev_solar_eclipse_global"] = list(ec.next_solar_eclipse(jd, place, search_backward=True, show_maximum_eclipse_global_location=True))
    except Exception as e:
        out["prev_solar_eclipse_global"] = str(e)
    try:
        out["next_lunar_eclipse_global"] = list(ec.next_lunar_eclipse(jd, place, show_maximum_eclipse_global_location=True))
    except Exception as e:
        out["next_lunar_eclipse_global"] = str(e)
    try:
        out["prev_lunar_eclipse_global"] = list(ec.next_lunar_eclipse(jd, place, search_backward=True, show_maximum_eclipse_global_location=True))
    except Exception as e:
        out["prev_lunar_eclipse_global"] = str(e)
    return out


# --------------------------------------------------------------------------- #
# ROUND 5 — new sections: bundled prediction texts, alternative engines,
# experimental research predictors.
# --------------------------------------------------------------------------- #
def _html_to_text(x):
    """PyJHora's bundled prediction resources are HTML fragments (<html><b>..<br>..).
    Convert to clean plain text so the JSON carries prose, not markup."""
    if not isinstance(x, str):
        return x
    import re as _re
    t = _re.sub(r"(?i)<br\s*/?>", "\n", x)
    t = _re.sub(r"(?i)</?b>", "", t)
    t = _re.sub(r"<[^>]+>", "", t)
    for a, b in (("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"), ("&nbsp;", " "),
                 ("&#39;", "'"), ("&quot;", '"'), ("&mdash;", "—"), ("&ndash;", "–")):
        t = t.replace(a, b)
    return "\n".join(l.strip() for l in t.splitlines()).strip()


def _patch_life_span_range(lon_mod):
    """PyJHora's prediction.longevity.life_span_range crashes under Python 3 when
    all three aayu votes agree: `counter.keys()[0]` (dict_keys is not subscriptable).
    Primary fix — minimal documented source-level surgery: re-exec the function with that
    ONE expression changed to `list(counter.keys())[0]`, applied through the compat-patch
    manager so the original is restored at run exit. No-op if the build is already fixed.

    ROUND-8 (review P2-4): `inspect.getsource` fails on .pyc-only / zip-bundled installs
    (OSError/TypeError). In that case fall back to a FAITHFUL py3 reimplementation of the
    same three-vote algorithm (amsa/pinda/hora-lagna aayu groups, majority vote, same
    tie-breaks) — verified equal to the source-patched result on the reference charts."""
    import inspect
    try:
        src = inspect.getsource(lon_mod.life_span_range).replace("\r", "")
    except Exception:
        src = None
    if src is not None:
        if "counter.keys()[0]" not in src:
            return  # upstream already fixed — no-op
        try:
            fixed_src = src.replace("counter.keys()[0]", "list(counter.keys())[0]")
            ns = dict(vars(lon_mod))
            exec(compile(fixed_src, "<life_span_range_py3_fix>", "exec"), ns)
            _apply_compat_patch(lon_mod, "life_span_range", ns["life_span_range"],
                                reason="life_span_range crashes under py3 when all three aayu "
                                       "votes agree (counter.keys()[0] not subscriptable). 4.8.7 "
                                       "broken; 5.0-dev fixed ONLY the two-vote branch (max(...)) — "
                                       "the three-vote branch still carries the py2 expression, so "
                                       "this patch remains required (auto-no-ops once removed "
                                       "upstream)")
            return
        except Exception:
            pass  # fall through to the getsource-free reimplementation
    # ---- getsource-free fallback: faithful py3 reimplementation (ROUND-8) ----
    try:
        _ns = vars(lon_mod)
        _charts, _house, _drik, _const = _ns["charts"], _ns["house"], _ns["drik"], _ns["const"]
        import collections as _collections

        def _get_aayu(sign1, sign2):
            if sign1 in _const.fixed_signs and sign2 in _const.fixed_signs:
                return 0
            elif sign1 in _const.movable_signs and sign2 in _const.movable_signs:
                return 2
            elif sign1 in _const.dual_signs and sign2 in _const.dual_signs:
                return 1
            elif ((sign1 in _const.fixed_signs and sign2 in _const.movable_signs) or
                  (sign1 in _const.movable_signs and sign2 in _const.fixed_signs)):
                return 1
            elif ((sign1 in _const.dual_signs and sign2 in _const.movable_signs) or
                  (sign1 in _const.movable_signs and sign2 in _const.dual_signs)):
                return 0
            elif ((sign1 in _const.fixed_signs and sign2 in _const.dual_signs) or
                  (sign1 in _const.dual_signs and sign2 in _const.fixed_signs)):
                return 2
            return None

        def life_span_range_py3(jd, place):
            planet_positions = _charts.rasi_chart(jd, place)
            asc_house = planet_positions[0][1][0]
            eigth_house = (asc_house + 7) % 12
            moon_house = planet_positions[2][1][0]
            lagna_lord = _house.house_owner_from_planet_positions(planet_positions, asc_house)
            lagna_lord_house = planet_positions[lagna_lord + 1][1][0]
            eighth_lord = _house.house_owner_from_planet_positions(planet_positions, eigth_house)
            eighth_lord_house = planet_positions[eighth_lord + 1][1][0]
            hora_lagna = _drik.hora_lagna(jd, place)[0]
            _aayu_group = [_get_aayu(lagna_lord_house, eighth_lord_house),
                           _get_aayu(asc_house, moon_house),
                           _get_aayu(asc_house, hora_lagna)]
            counter = _collections.Counter(_aayu_group)
            if len(counter) == 1:            # all three same
                return list(counter.keys())[0]   # the py3-safe form of the upstream bug
            elif len(counter) == 2:          # two are same
                return max(counter, key=counter.get)
            else:                            # all 3 different
                ret = _aayu_group[-1]
                if moon_house == asc_house or moon_house == (asc_house + 6) % 12:
                    ret = _aayu_group[1]
                return ret

        _apply_compat_patch(lon_mod, "life_span_range", life_span_range_py3,
                            reason="life_span_range py2-only three-vote branch "
                                   "(counter.keys()[0] not subscriptable); "
                                   "inspect.getsource is unavailable in this install, so a "
                                   "faithful py3 reimplementation of the same algorithm "
                                   "replaces it (same aayu votes + tie-breaks)")
    except Exception:
        pass  # keep original behavior; a failure would surface as __error__ as usual


def build_chakras(env, h, jd, place, planet_positions):
    """ROUND-14 (review "missing chakras" claim - PARTIALLY TRUE, ADOPTED AS DATA):
    PyJHora's chakra implementations live in jhora/ui/chakra.py as PyQt6 widgets
    (not importable headless - PyQt6 is not installed). This section ports the
    COMPUTATIONAL DATA + placement logic of the two chakras that have real content
    in this build, with sources cited: Kota Chakra (const.kota_chakra_star_
    placement_from_birth_star + kota_paala_lord_for_star_paadha tables and the
    KotaChakra widget logic) and Sarvatobhadra Chakra (Sarvatobadra._grid_label_
    values 9x9 grid + drik.nakshatra_pada planet-cell logic). REJECTED with
    evidence (audits 14a/14e): Kurma chakra has ZERO computational content in this
    build; the Sudarshana "composite chart" IS the sudharsana_chakra DASHA (already
    in the 56-system catalog) and its three reference frames are now data-complete
    via house_systems.relative_houses (from lagna / moon / sun). DISCLOSED:
    PyJHora computes NO vedha (obstruction) relationships for the SBC anywhere in
    the library - vedha logic exists only in compatibility.py porutham - so the
    grid + placements ship as data for classical SBC reading; vedha rules remain
    the interpreter's doctrine."""
    drik = env["drik"]; utils = env["utils"]; const = env["const"]
    house = env["house"]
    out = {}

    def s(fn, *a, **k):
        try:
            with _quiet():
                return fn(*a, **k)
        except Exception as e:
            return {"__error__": str(e)}

    lons = _planetary_longitude(planet_positions)
    bodies = PLANET_ORDER + [LAGNA]

    # ------------------- Kota Chakra -------------------
    try:
        moon_lon = lons["Moon"]
        nak, pada, _rem = drik.nakshatra_pada(moon_lon)
        nak = int(nak); pada = int(pada)
        circle_idx = list(const.abhijit_order_of_stars)   # 28 stars, Abhijit after UttaraShada
        circle_full = [NAKSHATRAS[i] for i in circle_idx]
        circle_short = [utils.NAKSHATRA_SHORT_LIST[i] for i in circle_idx]
        zone_names = ["Baahya (%s)" % utils.resource_strings.get("baahya_str", "Exterior"),
                      "Praakaara (%s)" % utils.resource_strings.get("praakaara_str", "Wall"),
                      "Durgantara (%s)" % utils.resource_strings.get("durgantara_str", "Interior"),
                      "Sthambha (%s)" % utils.resource_strings.get("sthamba_str", "Pillar")]
        rings = []
        for ri, row in enumerate(const.kota_chakra_star_placement_from_birth_star):
            idxs = [(nak - 1 + (e - 1)) % 28 for e in row]
            rings.append({"ring": ri + 1, "zone": zone_names[ri],
                          "positions_from_birth_star": [int(e) for e in row],
                          "stars": [circle_full[i] for i in idxs],
                          "stars_short": [circle_short[i] for i in idxs]})
        planets_by_zone = {z["zone"]: [] for z in rings}
        body_zones = {}
        for nm in bodies:
            try:
                pnak, ppada, _r = drik.nakshatra_pada(lons[nm])
                pshort = utils.NAKSHATRA_SHORT_LIST[int(pnak) - 1]
                found = next((z["zone"] for z in rings if pshort in z["stars_short"]), None)
                body_zones[nm] = {"nakshatra": NAKSHATRAS[int(pnak) - 1],
                                  "nakshatra_index": int(pnak), "pada": int(ppada),
                                  "zone": found}
                if found:
                    planets_by_zone[found].append(nm)
            except Exception as e:
                body_zones[nm] = {"__error__": str(e)}
        moon_sign = _sign_of_long(moon_lon)
        _ho = getattr(house, "house_owner_from_planet_positions", None)
        kota_lord = None
        if _ho is not None:
            r = s(_ho, planet_positions, moon_sign)
            if isinstance(r, int):
                kota_lord = _planet_name(r)
        if kota_lord is None:
            kota_lord = SIGN_LORDS[moon_sign]
        paala_id = const.kota_paala_lord_for_star_paadha[nak - 1][pada - 1]
        out["kota_chakra"] = {
            "birth_star": {"nakshatra": NAKSHATRAS[nak - 1], "nakshatra_index": nak,
                           "pada": pada},
            "star_circle_order": circle_full,
            "rings_outer_to_inner": rings,
            "planets_by_zone": planets_by_zone,
            "body_zones": body_zones,
            "kota_lord": kota_lord,
            "kota_paala": _planet_name(int(paala_id)),
            "__note__": ("DATA PORT of jhora/ui/chakra.py KotaChakra + jhora/const.py tables "
                         "(kota_chakra_star_placement_from_birth_star: 4 rings, outer-to-inner = "
                         "Baahya/Praakaara/Durgantara/Sthambha, star positions counted from the "
                         "birth (Moon) star through the 28-star circle with Abhijit inserted "
                         "after UttaraShada; kota_paala_lord_for_star_paadha: protector by birth "
                         "star + pada). Zone semantics per the library's own captions: Exterior / "
                         "Wall / Interior / Pillar. Classical reading (outer rings = distant/mild "
                         "effects, inner = immediate/intense) is interpretive doctrine, NOT "
                         "computed by the library."),
        }
    except Exception as e:
        out["kota_chakra"] = {"__error__": str(e)}

    # ------------------- Sarvatobhadra Chakra -------------------
    try:
        grid_raw = [
            ["ii", 23, 24, 25, 26, 27, 1, 2, "a"],
            [22, "rii", "g", "s", "d", "ch", "l", "u", 3],
            [28, "kh", "ai", 11, 12, 1, "lu", "a", 4],
            [21, "j", 10, "ah", "Rikita<br>Fri", "o", 2, "v", 5],
            [20, "bh", 9, "Jaya<br>Thu", "Pooma<br>Sat", "Nanda<br>Sun<br>Tue", 3, "k", 6],
            [19, "y", 8, "am", "Bhadra<br>Mon<br>Wed", "au", 4, "h", 7],
            [18, "n", "e", 7, 6, 5, "luu", "d", 8],
            [17, "ri", "t", "r", "p", "t~", "m", "uu", 9],
            ["i", 16, 15, 14, 13, 12, 11, 10, "aa"],
        ]
        cells = []
        star_cell = {}
        for i in range(9):
            for j in range(9):
                v = grid_raw[i][j]
                border = (i in (0, 8)) or (j in (0, 8))
                if isinstance(v, int) and border:
                    cells.append({"row": i, "col": j, "type": "nakshatra",
                                  "value": NAKSHATRAS[v - 1], "index": int(v)})
                    star_cell[int(v)] = (i, j)
                elif isinstance(v, int):
                    cells.append({"row": i, "col": j, "type": "rasi",
                                  "value": RAASI_NAMES[v - 1], "index": int(v)})
                else:
                    txt = str(v).replace("<br>", " ")
                    kind = ("tithi_group_vara" if any(t in txt for t in
                            ("Nanda", "Bhadra", "Jaya", "Rikita", "Pooma"))
                            else "syllable")
                    cells.append({"row": i, "col": j, "type": kind, "value": txt})
        sbc_bodies = {}
        for nm in bodies:
            try:
                pnak, ppada, _r = drik.nakshatra_pada(lons[nm])
                rc = star_cell.get(int(pnak))
                sbc_bodies[nm] = {"nakshatra": NAKSHATRAS[int(pnak) - 1],
                                  "nakshatra_index": int(pnak), "pada": int(ppada),
                                  "grid_row": rc[0] if rc else None,
                                  "grid_col": rc[1] if rc else None}
            except Exception as e:
                sbc_bodies[nm] = {"__error__": str(e)}
        tithi_cell = None
        try:
            ti = int(s(drik.tithi, jd, place)[0])
            grp = ["Nanda", "Bhadra", "Jaya", "Rikita", "Pooma"][(ti - 1) % 5]
            hit = next((c for c in cells if c["type"] == "tithi_group_vara"
                        and c["value"].startswith(grp)), None)
            tithi_cell = {"tithi_index": ti, "group": grp,
                          "grid_row": hit["row"] if hit else None,
                          "grid_col": hit["col"] if hit else None,
                          "cell_label": hit["value"] if hit else None}
        except Exception:
            tithi_cell = None
        out["sarvatobhadra_chakra"] = {
            "grid_9x9": grid_raw,
            "cells": cells,
            "bodies": sbc_bodies,
            "birth_tithi_group_cell": tithi_cell,
            "__note__": ("DATA PORT of jhora/ui/chakra.py Sarvatobadra._grid_label_values "
                         "(hardcoded 9x9): border integers = nakshatras 1-28 (Abhijit included), "
                         "interior integers = rasis 1-12, strings = vowel/consonant syllables "
                         "plus the five tithi-group cells with their weekday pairings (Nanda "
                         "Sun/Tue, Bhadra Mon/Wed, Jaya Thu, Rikita Fri, Pooma Sat); planet cells "
                         "resolve via drik.nakshatra_pada exactly as the widget does. DISCLOSURE: "
                         "PyJHora computes NO vedha (obstruction/penetration) relationships for "
                         "this chakra anywhere in the library - the review's 'vedha penetrations' "
                         "are NOT a missing extraction; the vedha rules are classical doctrine the "
                         "interpreter applies to this grid data."),
        }
    except Exception as e:
        out["sarvatobhadra_chakra"] = {"__error__": str(e)}
    return out


def build_aayu(env, h, jd, place, planet_positions):
    """ROUND-14 (review item 6 - harana reduction tables: TRUE, ADOPTED): the
    intermediate longevity mathematics behind PyJHora's aayu dashas. The TIMELINES
    (pindayu / nisargayu / amsayu) are already in the 56-system catalog; this
    section exposes the TABLES those timelines are built from: per-planet base-year
    tables, the four harana (reduction) factor tables, bharana (amsayu-only
    increase factors), composed finals under both methods, the aayur-type
    determination, lagna longevity and the public longevity() total. Source:
    jhora.horoscope.dhasa.graha.aayu - PRIVATE functions, called only after the
    library's own initializer _get_global_constants(jd, place) (bhava houses + BV
    Raman benefics/malefics), exactly as get_dhasa_antardhasa does. DISCLOSURES
    (audit 14b-4): (1) the module docstring carries a V4.8.9-era "DO NOT USE THIS
    YET - NOT FULLY IMPLEMENTED YET" warning and _krurodaya_harana a TODO - yet
    these very functions drive the catalog aayu dashas that pass 56/56 validation,
    and every final_total + lagna_longevity reproduces the corresponding
    all_dashas.aayu_* mahadasha-duration sum (parity audited); (2) method 1 =
    Santhanam (BPHS lineage), method 2 = Varahamihira (the library default; the
    catalog amsayu timeline uses method 2); (3) longevity values are
    classical-doctrine strength indices - NEVER death predictions."""
    aayu = env.get("aayu")
    out = {}
    if aayu is None:
        return {"__error__": "jhora.horoscope.dhasa.graha.aayu unavailable in this build"}
    const = env["const"]

    def s(fn, *a, **k):
        try:
            with _quiet():
                return fn(*a, **k)
        except Exception as e:
            return {"__error__": str(e)}

    def dec(dct):
        if not isinstance(dct, dict):
            return dct
        res = {}
        for k, v in dct.items():
            if k == const._ascendant_symbol:
                nm = LAGNA
            elif isinstance(k, int) and 0 <= k <= 6:
                nm = _planet_name(k)
            else:
                continue   # nodes (7/8) can appear in raw factor dicts but are not consumed
            res[nm] = round(float(v), 4) if isinstance(v, (int, float)) else v
        return res

    try:
        pp = planet_positions[:const._pp_count_upto_ketu]
        with _quiet():
            aayu._get_global_constants(jd, place)   # sets subha/asubha_grahas + bhava_houses globals
        factors = {
            "astangata_combustion": dec(s(aayu._astangata_harana, pp)),
            "shatru_kshetra_enemy_sign": dec(s(aayu._shatru_kshetra_harana, pp)),
            "chakrapata_above_horizon": dec(s(aayu._chakrapata_harana, pp)),
            "krurodaya_malefic_rising": dec(s(aayu._krurodaya_harana, pp)),
        }
        try:
            if "method" in inspect.signature(aayu._chakrapata_harana).parameters:
                factors["chakrapata_method1_santhanam"] = dec(
                    s(aayu._chakrapata_harana, pp, method=1))
        except Exception:
            pass
        out["harana_factors"] = factors
        out["bharana_factors_amsayu_only"] = dec(s(aayu._bharana, pp))

        systems = {}
        for nm, fn in (("pindayu", aayu._pindayu), ("nisargayu", aayu._nisargayu)):
            base = s(fn, pp, False)
            fin = s(fn, pp, True)
            fin1 = s(fn, pp, True, 1)
            entry = {"base_years_by_planet": dec(base),
                     "final_years_by_planet": dec(fin),
                     "final_years_by_planet_method1_santhanam": dec(fin1)}
            if isinstance(base, dict):
                entry["base_total_years"] = round(sum(float(v) for v in base.values()), 4)
            if isinstance(fin, dict):
                entry["final_total_years"] = round(sum(float(v) for v in fin.values()), 4)
            if isinstance(fin1, dict):
                entry["final_total_years_method1_santhanam"] = round(
                    sum(float(v) for v in fin1.values()), 4)
            systems[nm] = entry
        a_base1 = s(aayu._amsayu, pp, False)
        a_base2 = s(aayu._amsayu, pp, False, 2)
        a_fin1 = s(aayu._amsayu, pp, True)
        a_fin2 = s(aayu._amsayu, pp, True, 2)
        systems["amsayu"] = {
            "base_years_by_planet_method1_santhanam": dec(a_base1),
            "base_years_by_planet_method2_varahamihira": dec(a_base2),
            "final_years_by_planet_method2_varahamihira": dec(a_fin2),
            "final_years_by_planet_method1_santhanam": dec(a_fin1),
            "final_total_years_method2_varahamihira": (
                round(sum(float(v) for v in a_fin2.values()), 4)
                if isinstance(a_fin2, dict) else None),
            "final_total_years_method1_santhanam": (
                round(sum(float(v) for v in a_fin1.values()), 4)
                if isinstance(a_fin1, dict) else None),
            "composition": ("final = base x bharana (increase: x3 retrograde/exalted/svakshetra, "
                            "x2 sva-navamsa/sva-drekkana/vargottama) x harana-min"),
        }
        out["systems"] = systems

        at = s(aayu._get_aayur_type, pp)
        _atmap = {"L": "Amsayu (Lagna)", 0: "Pindayu (Sun)", 1: "Nisargayu (Moon)"}
        out["aayur_type"] = {
            "raw": at, "decoded": _atmap.get(at, str(at)),
            "determined_by": "strongest of Lagna / Sun / Moon (library _stronger_of_lagna_sun_moon)",
        }
        lag = s(aayu._lagna_longevity, jd, place)
        lag_s = s(aayu._lagna_longevity_santhanam, jd, place)
        out["lagna_longevity_years"] = round(float(lag), 4) if isinstance(lag, (int, float)) else lag
        out["lagna_longevity_years_santhanam"] = (
            round(float(lag_s), 4) if isinstance(lag_s, (int, float)) else lag_s)
        pub = s(aayu.longevity, jd, place)
        if isinstance(pub, tuple) and len(pub) == 2 and isinstance(pub[0], (int, float)):
            out["longevity_public"] = {
                "total_years": round(float(pub[0]), 4),
                "aayur_type_raw": pub[1],
                "aayur_type": _atmap.get(pub[1], str(pub[1])),
                "composition": ("sum of the auto-selected aayu system's mahadasha durations "
                                "(including the lagna period)"),
            }
        else:
            out["longevity_public"] = pub
        out["catalog_parity"] = ("Per system: final_total_years + lagna_longevity_years equals the "
                                 "sum of all_dashas.aayu_<system> mahadasha durations (amsayu uses "
                                 "the method-2 total) - parity audited within 0.05 years.")
        out["__note__"] = ("PRIVATE-API extraction from jhora.horoscope.dhasa.graha.aayu after the "
                           "library's own _get_global_constants initializer; the module carries a "
                           "'DO NOT USE THIS YET - NOT FULLY IMPLEMENTED YET' docstring warning and "
                           "_krurodaya_harana a TODO - the same functions nevertheless drive the "
                           "validated 56/56 catalog aayu dashas. Harana composition per "
                           "_apply_harana: per planet the final factor = the MINIMUM across the "
                           "four haranas (Santhanam rule - only the strongest reduction applies, "
                           "they do not multiply). Rahu/Ketu keys can appear in raw factor dicts "
                           "but are never consumed. Longevity values are classical-doctrine "
                           "strength indices, NOT death predictions. Methods: 1 = Santhanam (BPHS lineage), "
                           "2 = Varahamihira (the library default; the catalog amsayu timeline uses "
                           "method 2).")
    except Exception as e:
        out["__error__"] = str(e)
    return out


def build_prediction(env, h, jd, place, planet_positions):
    """PyJHora's bundled prediction TEXT resources + the Parashari life-span category.

    IMPORTANT SEMANTICS: `bundled_texts.*` are PRE-WRITTEN interpretive prose shipped with
    the library (sources: 'How to judge a horoscope' — B.V. Raman; ChinmayaAstrolger.com;
    planet-in-house and house-lord-placement compendiums). They are NOT computed from this
    chart beyond selecting the blocks for the Janma Rasi / placements — quote them as
    library reference text, never as engine-derived facts.
    `life_span_range` IS computed (prediction.longevity.life_span_range): a coarse
    Parashari longevity bracket (0=Alpayu, 1=Madhyayu, 2=Poornayu) — never a date."""
    out = {}
    gen = env.get("prediction_general")
    lon = env.get("prediction_longevity")
    if gen is None and lon is None:
        out["__error__"] = "prediction modules not available in this PyJHora build"
        return out
    if gen is not None:
        try:
            with _quiet():
                r = gen.get_prediction_details(jd, place)
            keymap = {"Janma rasi_1": "janma_rasi_bv_raman_text",
                      "Janma rasi_2": "janma_rasi_chinmaya_text",
                      "Planets": "planets_in_houses_texts",
                      "Houses": "house_lords_placement_texts"}
            texts = {}
            for k, v in (r or {}).items():
                texts[keymap.get(k, k)] = _html_to_text(v)
            out["bundled_texts"] = texts
            out["bundled_texts_note"] = (
                "Pre-written reference prose bundled with PyJHora (BV Raman / ChinmayaAstrolger / "
                "planet-in-house & house-lord compendiums), selected by this chart's Janma Rasi and "
                "placements. Library text, not a computed claim.")
        except Exception as e:
            out["bundled_texts"] = {"__error__": str(e)}
    if lon is not None:
        _patch_life_span_range(lon)
        try:
            idx = int(lon.life_span_range(jd, place))
            names = {0: "Alpayu (short life)", 1: "Madhyayu (middle life)", 2: "Poornayu (full life)"}
            out["life_span_range"] = {"index": idx, "category": names.get(idx, "unknown")}
            # ROUND-7 (review tail): clearer, less fatalistic ALIAS — identical value.
            out["longevity_bracket"] = out["life_span_range"]
        except Exception as e:
            out["life_span_range"] = {"__error__": str(e)}
        try:
            out["longevity_reference_constants"] = {
                "nakshathra_year": getattr(lon, "nakshathra_year", None),
                "one_year_days": getattr(lon, "one_year_days", None),
                "turns_of_moon": getattr(lon, "turns_of_moon", None),
                "jcd_year_factor": getattr(lon, "jcd_year_factor", None),
            }
        except Exception:
            pass
    return out


def build_alt_engines(env, h, jd, place, planet_positions):
    """ALTERNATIVE computation engines shipped with PyJHora, exposed for CROSSCHECKS only.

    The Surya-Siddhanta module carries its author's own header: 'WORK STILL IN PROGRESS —
    NOT WORKING FOR SOME PLANETS YET'. The Drik (Swiss Ephemeris) values elsewhere in this
    document are authoritative; every ss.* value here is experimental context. Entry points
    that are internally broken in this build are REPORTED (not silently dropped)."""
    out = {}
    ss = env.get("surya_sidhantha")
    drik = env["drik"]
    if ss is None:
        out["__error__"] = "surya_sidhantha module not available in this PyJHora build"
        return out
    out["__label__"] = ("Surya-Siddhanta crosscheck values (module self-labelled work-in-progress). "
                        "Drik values elsewhere in this document are authoritative.")
    # ss.kali_ahargana calls drik.vaara(jd) WITHOUT the `place` arg this build requires →
    # bind a temporary vaara shim to this birth place, restored immediately after the call.
    _orig_vaara = getattr(drik, "vaara", None)
    try:
        if callable(_orig_vaara):
            drik.vaara = lambda j, p=None, show_vedic_day=True: \
                _orig_vaara(j, p if p is not None else place, show_vedic_day)
        try:
            out["kali_ahargana_surya_siddhanta"] = int(ss.kali_ahargana(jd))
        except Exception as e:
            out["kali_ahargana_surya_siddhanta"] = {"__error__": str(e)}
    finally:
        if _orig_vaara is not None:
            drik.vaara = _orig_vaara
    try:
        out["ahargana_khanda_khaadyaka"] = round(float(ss.ahargana_khanda_khaadyaka(jd)), 4)
    except Exception as e:
        out["ahargana_khanda_khaadyaka"] = {"__error__": str(e)}
    try:
        r = ss.sunrise_set(jd, place)
        out["sunrise_set_surya_siddhanta"] = {
            "sunrise_hours": round(float(r[0]), 4), "sunrise_hms": r[1],
            "sunset_hours": round(float(r[2]), 4), "sunset_hms": r[3],
            "note": ("Local solar-time approximation from the SS sun-declination semidiurnal arc; "
                     "no equation of time / longitude correction. Compare with the Drik "
                     "sunrise/sunset in panchanga."),
        }
    except Exception as e:
        out["sunrise_set_surya_siddhanta"] = {"__error__": str(e)}
    # SS ascendants need a sun longitude; feed the primary Drik sidereal solar longitude.
    # NOTE: the author labels BOTH ascendant() and ascendant_new() "TODO NOT WORKING STILL
    # UNDER TESTING"; ascendant_new returns a value in this build, ascendant() dies with a
    # tuple/real TypeError — so only the _new variant is attempted and the old one is listed
    # under unavailable_in_this_build.
    try:
        sun_long = drik.solar_longitude(jd)
        if isinstance(sun_long, (list, tuple)):
            sun_long = sun_long[0]
        fn = getattr(ss, "ascendant_new", None)
        if fn is not None:
            try:
                r = fn(jd, place, sun_long)
                out["ascendant_surya_siddhanta_new"] = {
                    "sign_index": int(r[0]), "sign": RAASI_NAMES[int(r[0]) % 12],
                    "degree_in_sign": round(float(r[1]), 4),
                    "note": ("EXPERIMENTAL — module author labels it 'NOT WORKING STILL UNDER "
                             "TESTING'; may differ materially from the Drik Lagna in core.* — "
                             "never substitute it for the authoritative ascendant."),
                }
            except Exception as e:
                out["ascendant_surya_siddhanta_new"] = {"__error__": str(e)}
    except Exception as e:
        out["ascendant_surya_siddhanta_new"] = {"__error__": str(e)}
    out["unavailable_in_this_build"] = {
        "planet_positions": "broken in 4.8.7: drik.planet_list is a dict here, so ss code calling .index() on it fails",
        "tithi": "depends on ss.planet_positions (broken)",
        "nakshatra": "depends on ss.planet_positions (broken)",
        "bhujantara_correction": "depends on drik.planet_list (broken)",
        "solar_month_and_date": "expects a subscriptable panchanga_date; unusable in this build",
        "ascendant_old": "author-labelled 'NOT WORKING STILL UNDER TESTING'; raises a tuple/real TypeError in this build",
        "pradosham_dates": "use vratha.pradosham_dates instead (exposed in the vratha section)",
    }
    return out


def _exp_json(v):
    """Recursively JSON-sanitize jhora.experiments output (datetime/date/tuple/numpy → plain
    JSON values; anything exotic → str). Experiment modules return rich Python objects that
    would otherwise hit the serialization masker."""
    import datetime as _dt
    if isinstance(v, dict):
        return {str(k): _exp_json(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_exp_json(x) for x in v]
    if isinstance(v, _dt.datetime):
        return v.strftime("%Y-%m-%d %H:%M")
    if isinstance(v, _dt.date):
        return v.strftime("%Y-%m-%d")
    if isinstance(v, bool) or v is None or isinstance(v, str):
        return v
    if isinstance(v, int):
        return int(v)
    if isinstance(v, float):
        return round(v, 4)
    if hasattr(v, "item"):
        try:
            return _exp_json(v.item())
        except Exception:
            pass
    return str(v)


def _expand_md_ad_segments_shim(vdb_info):
    """Adapter for jhora.experiments.marriage*: those modules were written against the OLD
    get_vimsottari_dhasa_bhukthi row format (md, ad, 'YYYY-MM-DD ...') but PyJHora 4.8.7
    returns (lords_chain, (Y, M, D, fractional_hours), duration_years) — hence their stock
    expander dies with "'float' object has no attribute 'split'". This shim rebuilds the
    [{'md','ad','pd','start','end'}, ...] segment list the scorers expect. Verified to
    produce sane scored windows with marriage2/marriage5."""
    import datetime as _dt
    segs = []
    if not vdb_info:
        return segs
    starts = []
    for row in vdb_info:
        try:
            lords = row[0]
            md = int(lords[0]) if len(lords) > 0 else None
            ad = int(lords[1]) if len(lords) > 1 else None
            y, m, d, fh = row[1]
            starts.append((md, ad,
                           _dt.datetime(int(y), int(m), int(d)) + _dt.timedelta(hours=float(fh))))
        except Exception:
            continue
    starts.sort(key=lambda t: t[2])
    for i, (md, ad, st) in enumerate(starts):
        en = (starts[i + 1][2] - _dt.timedelta(seconds=1) if i < len(starts) - 1
              else _dt.datetime(9999, 12, 31, 23, 59, 59))
        segs.append({"md": md, "ad": ad, "pd": None, "start": st, "end": en})
    return segs


def build_experiments(env, h, jd, place, planet_positions, marriage_window=None, deep=False):
    """EXPERIMENTAL research-grade predictors from jhora.experiments (opt-in: --experiments).

    Contents: PVR-style marriage-window scorers (marriage5 primary, marriage2 variant),
    Jupiter+Saturn double-transit scans and sensitive-point scans. These are the library
    author's research heuristics — NOT classical Parashari/Jaimini doctrine. Every consumer
    must treat them as hypotheses requiring corroboration from the decoded dasha + transits
    sections. The heavy degree-locked scan runs only with deep=True (--experiments-deep)."""
    out = {}
    out["__label__"] = ("EXPERIMENTAL research predictors (jhora.experiments). NOT classical doctrine — "
                        "hypotheses only; corroborate against dasha + transits before quoting.")
    # ROUND-8 (review P2-1): explicit section-level status when the upstream package is
    # gone (PyJHora 5.0 removed jhora.experiments) — the per-key typed notes stay, but
    # consumers can now branch on one machine-readable field.
    if not any(env.get(k) for k in ("exp_marriage5", "exp_marriage2",
                                    "exp_double_transit", "exp_sensitive_point")):
        out["status"] = "deprecated_upstream"
        out["note"] = ("jhora.experiments was REMOVED in the PyJHora 5.0 dev line; every "
                       "predictor below carries a typed not-available marker. Marriage/"
                       "timing conclusions rest on the classical sections (Vimsottari + "
                       "Narayana + transits, which include the double-transit crosschecks).")
    yb = h.Date.year
    w0 = w1 = None
    if marriage_window:
        try:
            w0, w1 = int(marriage_window[0]), int(marriage_window[1])
        except Exception:
            w0 = w1 = None
    if w0 and w1:
        age_range = (max(15, min(w0, w1) - yb), min(80, max(w0, w1) - yb))
    else:
        age_range = (20, 35)
    out["marriage_age_range_used"] = list(age_range)
    out["marriage_calendar_window"] = [w0, w1] if (w0 and w1) else "default (from age range)"
    # Shim the marriage modules' dasha-segment expander (restored by the compat-patch
    # manager when the isolated run exits — same discipline as cheshta_bala).
    for mk in ("exp_marriage5", "exp_marriage2"):
        mod = env.get(mk)
        if mod is not None and hasattr(mod, "_expand_to_md_ad_segments"):
            _apply_compat_patch(mod, "_expand_to_md_ad_segments", _expand_md_ad_segments_shim,
                                reason="experiments.marriage* scorers expect the OLD "
                                       "(md, ad, 'YYYY-MM-DD..') row format; 4.8.7/5.0 return "
                                       "(lords_chain, (Y,M,D,fh), dur_years) — shim rebuilds "
                                       "the segment list (NOTE: jhora.experiments was REMOVED "
                                       "in 5.0; patch applies only when the package exists)")

    def _dec_windows(rows):
        res = []
        for w in rows or []:
            if not isinstance(w, dict):
                res.append(_exp_json(w))
                continue
            row = {}
            ww = w.get("window")
            try:
                row["year"] = w.get("year")
                row["month"] = w.get("month")
                if isinstance(ww, (tuple, list)) and len(ww) == 2:
                    row["window_start"] = ww[0].strftime("%Y-%m-%d") if hasattr(ww[0], "strftime") else str(ww[0])
                    row["window_end"] = ww[1].strftime("%Y-%m-%d") if hasattr(ww[1], "strftime") else str(ww[1])
                row["score"] = w.get("score")
                row["candidate_id"] = w.get("candidate_id")
                row["reasons"] = _exp_json(w.get("reasons"))
                if "_year_ev" in w:
                    row["year_evidence"] = _exp_json(w.get("_year_ev"))
            except Exception:
                row = _exp_json(w)
            res.append(row)
        return res

    m5 = env.get("exp_marriage5")
    if m5 is not None:
        try:
            with _quiet():
                rows = m5.predict_marriage_windows_from_jd_place(jd, place, start_year=w0, end_year=w1)
            out["marriage5_windows"] = _dec_windows(rows)
            out["marriage5_note"] = ("Top-K scored candidate months (Vimsottari MD/AD gating + ithasala orb + "
                                     "Jupiter transit + Sudarsana-chakra year support + varshaphala triggers). "
                                     "reasons.md_ad_pd_names is already decoded; score is a research heuristic.")
        except Exception as e:
            out["marriage5_windows"] = {"__error__": str(e)}
    else:
        out["marriage5_windows"] = {"__error__": "jhora.experiments.marriage5 not available"}
    m2 = env.get("exp_marriage2")
    if m2 is not None:
        try:
            with _quiet():
                rows = m2.predict_marriage_windows_from_jd_place(jd, place, start_year=w0, end_year=w1)
            out["marriage2_windows_variant"] = _dec_windows(rows)
        except Exception as e:
            out["marriage2_windows_variant"] = {"__error__": str(e)}
    else:
        out["marriage2_windows_variant"] = {
            "__error__": "jhora.experiments.marriage2 not available "
                         "(the jhora.experiments package was REMOVED in PyJHora 5.0)"}

    dtm = env.get("exp_double_transit")
    if dtm is not None:
        try:
            with _quiet():
                rows = dtm.double_transit_marriage_windows(jd, place, marriage_age_range=age_range,
                                                           step_days=30.0)
            dec = []
            for r in rows or []:
                rr = dict(r) if isinstance(r, dict) else {"raw": r}
                d = rr.get("date")
                if isinstance(d, (tuple, list)) and len(d) >= 3:
                    rr["date"] = "%04d-%02d-%02d%s" % (
                        int(d[0]), int(d[1]), int(d[2]),
                        (" " + _hours_to_hhmm(d[3])) if len(d) > 3 else "")
                for k in ("sat_sign", "jup_sign"):
                    if isinstance(rr.get(k), int):
                        rr[k + "_name"] = RAASI_NAMES[int(rr[k]) % 12]
                dec.append(_exp_json(rr))
            out["double_transit_windows"] = dec
            out["double_transit_note"] = (
                "Jupiter+Saturn double-transit sampling every 30 days over ages %d-%d. "
                "*_targets are HOUSE numbers (from Lagna); ok/lagna_ok/chandra_ok are the "
                "module's own boolean gates." % age_range)
        except Exception as e:
            out["double_transit_windows"] = {"__error__": str(e)}
        if deep:
            try:
                with _quiet():
                    rows = dtm.double_transit_marriage_windows_degree_locked(
                        jd, place, marriage_age_range=age_range)
                out["double_transit_degree_locked"] = _exp_json(rows)
                out["double_transit_degree_locked_note"] = (
                    "Degree-locked variant (±3° orb, pair windows) — slow scan (~15s+); "
                    "empty list = no degree-locked window found in range.")
            except Exception as e:
                out["double_transit_degree_locked"] = {"__error__": str(e)}
    else:
        out["double_transit_windows"] = {
            "__error__": "jhora.experiments.double_transit not available "
                         "(the jhora.experiments package was REMOVED in PyJHora 5.0)"}

    spm = env.get("exp_sensitive_point")
    if spm is not None:
        for nm, fn in (("scan_dt_perfect_fit", getattr(spm, "scan_dt_perfect_fit_windows", None)),
                       ("scan_part3_with_7th_target",
                        getattr(spm, "scan_part3_with_7th_target_windows", None))):
            if fn is None:
                continue
            try:
                with _quiet():
                    rows = fn(jd, place, marriage_age_range=age_range, print_diagnostics=False)
                dec = []
                for r in rows or []:
                    if not isinstance(r, dict):
                        dec.append(_exp_json(r))
                        continue
                    rr = _exp_json(r)
                    for k in ("start_jd", "end_jd"):
                        v = r.get(k)
                        if isinstance(v, (tuple, list)) and len(v) >= 3:
                            rr[k] = "%04d-%02d-%02d%s" % (
                                int(v[0]), int(v[1]), int(v[2]),
                                (" " + _hours_to_hhmm(v[3])) if len(v) > 3 else "")
                    smp = r.get("sample")
                    if isinstance(smp, dict) and "sev_lord_id" in smp and isinstance(rr.get("sample"), dict):
                        try:
                            rr["sample"]["sev_lord"] = PLANET_ORDER[int(smp["sev_lord_id"]) % 9]
                        except Exception:
                            pass
                    dec.append(rr)
                out[nm] = dec
            except Exception as e:
                out[nm] = {"__error__": str(e)}
    else:
        for nm in ("scan_dt_perfect_fit", "scan_part3_with_7th_target"):
            out[nm] = {"__error__": "jhora.experiments.sensitive_point not available "
                                    "(the jhora.experiments package was REMOVED in PyJHora 5.0)"}
    return out



def build_prashna(env, h, jd, place, planet_positions, number=None, system="kp249",
                  query_house=None):
    """ROUND-15 WRAPPER Prashna / horary.

    PyJHora has NO Ashtamangala/Tambula module (Round-14 library scan is still true
    for those English spellings). It DOES ship number-to-lagna helpers under the
    spelling `prasna` (utils.get_prasna_lagna_KP_249_*, get_prasna_lagna_108_*,
    get_prasna_lagna_nadi_* plus const.prasna_kp_249_dict — 249 entries). This
    section:
      * treats the supplied datetime as the QUESTION MOMENT (same D1 chart);
      * extracts the eight classical Ashtamangala *limbs as FACTS* from panchanga
        + lagna/hora (not a yes/no oracle);
      * maps an optional querent number through the library KP-249 / 108 / nadi-150
        tables to a prasna lagna;
      * reports Trisphuta / Chatur / Pancha sphuta of the question moment;
      * lists occupants/lord of an optional query house.

    HARD RULE: this is a FACT SHEET. It never invents a yes/no answer.
    """
    drik = env["drik"]; utils = env["utils"]; const = env["const"]
    sphuta = env.get("sphuta")
    out = {
        "__note__": ("ROUND-15 wrapper Prashna. Question moment = the chart datetime. "
                     "Library helpers used: utils.get_prasna_lagna_KP_249_for_rasi_chart, "
                     "get_prasna_lagna_108_for_rasi_chart, get_prasna_lagna_nadi_for_rasi_chart, "
                     "const.prasna_kp_249_dict, sphuta.tri/chatur/pancha_sphuta. "
                     "There is still no Ashtamangala/Tambula *oracle* in PyJHora — limbs "
                     "below are decoded panchanga facts, not a binary answer."),
        "question_moment": {
            "date": "%04d-%02d-%02d" % (h.Date.year, h.Date.month, h.Date.day),
            "time": str(getattr(h, "birth_time", None)),
            "julian_day_local": jd,
        },
    }
    ppl = _planetary_longitude(planet_positions)
    asc_sign = _sign_of_long(ppl[LAGNA])
    out["prasna_lagna_from_chart"] = {
        "sign_index": asc_sign, "sign": RAASI_NAMES[asc_sign],
        "longitude": round(ppl[LAGNA], 4),
        "nakshatra": _nakshatra_of_long(ppl[LAGNA])[0],
        "nakshatra_pada": _nakshatra_of_long(ppl[LAGNA])[1],
        "note": "Ascendant at the question moment (the horary lagna).",
    }

    # ---- Ashtamangala eight limbs as FACTS (not an oracle) ----
    limbs = {}
    try:
        t = drik.tithi(jd, place)
        tn, tp = _tithi_name(t[0])
        limbs["tithi"] = {"index": t[0], "name": tn, "paksha": tp}
        if len(t) > 2:
            limbs["tithi"].update(_span_times(t[1], t[2]))
    except Exception as e:
        limbs["tithi"] = {"__error__": str(e)}
    try:
        n = drik.nakshatra(jd, place)
        limbs["nakshatra"] = {"index": n[0], "name": NAKSHATRAS[(int(n[0]) - 1) % 27],
                              "pada": n[1] if len(n) > 1 else None}
    except Exception as e:
        limbs["nakshatra"] = {"__error__": str(e)}
    try:
        y = drik.yogam(jd, place)
        limbs["yoga"] = {"index": y[0], "name": YOGA_NAMES[(int(y[0]) - 1) % 27]}
    except Exception as e:
        limbs["yoga"] = {"__error__": str(e)}
    try:
        k = drik.karana(jd, place)
        limbs["karana"] = {"index": k[0], "name": _karana_name(k[0])}
    except Exception as e:
        limbs["karana"] = {"__error__": str(e)}
    try:
        w = drik.vaara(jd, place)
        limbs["vara"] = {"index": w, "name": WEEKDAY_NAMES[int(w) % 7]}
    except Exception as e:
        limbs["vara"] = {"__error__": str(e)}
    limbs["lagna"] = dict(out["prasna_lagna_from_chart"])
    try:
        # janma hora of the question moment — reuse the same Chaldean walk as panchanga
        VARA_LORDS = ["Sun", "Moon", "Mars", "Mercury", "Jupiter", "Venus", "Saturn"]
        CHALDAEAN = ["Sun", "Venus", "Mercury", "Moon", "Saturn", "Jupiter", "Mars"]
        def _jd_of(x):
            if isinstance(x, (int, float)) and not isinstance(x, bool) and 2000000 < float(x) < 3000000:
                return float(x)
            if isinstance(x, (list, tuple)):
                for it in x:
                    v = _jd_of(it)
                    if v is not None:
                        return v
            return None
        sr_jd = _jd_of(drik.sunrise(jd, place))
        w_idx = int(drik.vaara(jd, place)) % 7
        hrs = (jd - sr_jd) * 24.0 if sr_jd is not None else None
        if hrs is not None and hrs < 0:
            w_idx = (w_idx - 1) % 7
            sr_jd = _jd_of(drik.sunrise(jd - 1, place))
            hrs = (jd - sr_jd) * 24.0 if sr_jd is not None else None
        if hrs is not None:
            k = int(hrs) % 24
            start = CHALDAEAN.index(VARA_LORDS[w_idx])
            limbs["hora"] = {"lord": CHALDAEAN[(start + k) % 7], "hora_index_of_day": k,
                             "hours_since_sunrise": round(hrs, 4)}
        else:
            limbs["hora"] = {"__error__": "sunrise JD unavailable"}
    except Exception as e:
        limbs["hora"] = {"__error__": str(e)}
    try:
        moon_sign = _sign_of_long(ppl["Moon"])
        limbs["chandra_rasi"] = {"sign_index": moon_sign, "sign": RAASI_NAMES[moon_sign],
                                 "house_from_lagna": _house_number(asc_sign, moon_sign)}
    except Exception as e:
        limbs["chandra_rasi"] = {"__error__": str(e)}
    out["ashtamangala_limbs"] = {
        "limbs": limbs,
        "__note__": ("Classical Kerala Ashtamangala Prashna uses eight lots (tithi, "
                     "nakshatra, yoga, karana, vara, lagna, hora, chandra-rasi among "
                     "the common limbs). These are FACTS of the question moment. The "
                     "library has no tambula/coin-toss / yes-no reducer — do NOT invent one."),
    }

    # ---- Number Prasna (library tables) ----
    sys_name = (system or "kp249").lower().replace("-", "").replace("_", "")
    num_block = {"system_requested": sys_name, "number": number}
    if number is None:
        num_block["__note__"] = ("No --prashna-number given. Pass 1..249 (KP), 1..108 (nadi-108) "
                                 "or 1..1800 (nadi-150) to resolve a number-prasna lagna.")
    else:
        try:
            n = int(number)
            if sys_name in ("kp249", "kp", "249"):
                if n < 1 or n > 249:
                    num_block["__error__"] = "KP-249 number must be 1..249 (got %s)" % n
                else:
                    row = const.prasna_kp_249_dict[n]
                    rasi, nak, sd, ed = int(row[0]), int(row[1]), float(row[2]), float(row[3])
                    lib_rasi = int(utils.get_prasna_lagna_KP_249_for_rasi_chart(n))
                    num_block.update({
                        "system": "KP-249 (const.prasna_kp_249_dict)",
                        "number": n,
                        "rasi_index": lib_rasi, "sign": RAASI_NAMES[lib_rasi % 12],
                        "nakshatra_index": nak,
                        "nakshatra": NAKSHATRAS[nak % 27],
                        "span_in_sign_deg": [round(sd, 6), round(ed, 6)],
                        "sign_lord": _decode_planet_id(row[4]) if len(row) > 4 else None,
                        "star_lord": _decode_planet_id(row[5]) if len(row) > 5 else None,
                        "sub_lord": _decode_planet_id(row[6]) if len(row) > 6 else None,
                        "house_from_chart_lagna": _house_number(asc_sign, lib_rasi % 12),
                    })
            elif sys_name in ("nadi108", "108", "nadi"):
                if n < 1 or n > 108:
                    num_block["__error__"] = "nadi-108 number must be 1..108 (got %s)" % n
                else:
                    # Library: kp_no//9 — off-by-one at 9,18,...,108 (108 -> 12).
                    lib_rasi = int(utils.get_prasna_lagna_108_for_rasi_chart(n))
                    corr = (n - 1) // 9   # 1..9 = Aries ... 100..108 = Pisces
                    num_block.update({
                        "system": "nadi-108 (utils.get_prasna_lagna_108_for_rasi_chart)",
                        "number": n,
                        "library_rasi_index": lib_rasi,
                        "library_sign": (RAASI_NAMES[lib_rasi] if 0 <= lib_rasi <= 11
                                         else "OUT_OF_RANGE_%s" % lib_rasi),
                        "corrected_rasi_index": corr,
                        "corrected_sign": RAASI_NAMES[corr % 12],
                        "correction_note": ("library formula is number//9, which maps 108->12 "
                                            "(invalid) and 9->1 (Taurus) instead of (n-1)//9. "
                                            "Cite corrected_sign; library_sign is the raw helper."),
                        "navamsa_index": int(utils.get_prasna_lagna_108_for_navamsa(n)),
                        "house_from_chart_lagna": _house_number(asc_sign, corr % 12),
                    })
            elif sys_name in ("nadi150", "150", "nadiamsa"):
                if n < 1 or n > 1800:
                    num_block["__error__"] = "nadi-150 number must be 1..1800 (got %s)" % n
                else:
                    lib_rasi = int(utils.get_prasna_lagna_nadi_for_rasi_chart(n))
                    corr = (n - 1) // 150
                    num_block.update({
                        "system": "nadi-150 (utils.get_prasna_lagna_nadi_for_rasi_chart)",
                        "number": n,
                        "library_rasi_index": lib_rasi,
                        "library_sign": (RAASI_NAMES[lib_rasi] if 0 <= lib_rasi <= 11
                                         else "OUT_OF_RANGE_%s" % lib_rasi),
                        "corrected_rasi_index": corr % 12,
                        "corrected_sign": RAASI_NAMES[corr % 12],
                        "correction_note": "library uses number//150; corrected uses (n-1)//150.",
                        "house_from_chart_lagna": _house_number(asc_sign, corr % 12),
                    })
            else:
                num_block["__error__"] = "unknown --prashna-system %r (use kp249|nadi108|nadi150)" % system
        except Exception as e:
            num_block["__error__"] = str(e)
    out["number_prasna"] = num_block

    # ---- Trisphuta of the question moment ----
    dob = drik.Date(h.Date.year, h.Date.month, h.Date.day)
    tob = _tob(h)
    sph = {}
    if sphuta is not None:
        for name in ("tri_sphuta", "chatur_sphuta", "pancha_sphuta"):
            fn = getattr(sphuta, name, None)
            if not fn:
                continue
            try:
                with _quiet():
                    res = fn(dob, tob, place)
                sph[name] = ({k: _strip_html(v) for k, v in res.items()}
                             if isinstance(res, dict) else _decode_sphuta(res)
                             if "_decode_sphuta" in globals() else res)
            except Exception as e:
                sph[name] = {"__error__": str(e)}
    else:
        sph["__error__"] = "sphuta module unavailable"
    out["sphutas_of_question"] = sph

    # ---- Query house facts ----
    qh = 7 if query_house is None else int(query_house)
    if qh < 1 or qh > 12:
        out["query_house"] = {"__error__": "query house must be 1..12"}
    else:
        sign = (asc_sign + qh - 1) % 12
        occ = [p for p in PLANET_ORDER if _house_number(asc_sign, _sign_of_long(ppl[p])) == qh]
        lord = SIGN_LORDS[sign]
        lord_house = None
        try:
            lord_house = _house_number(asc_sign, _sign_of_long(ppl[lord]))
        except Exception:
            pass
        out["query_house"] = {
            "house": qh,
            "sign": RAASI_NAMES[sign],
            "sign_lord": lord,
            "occupants": occ,
            "lord_occupies_house": lord_house,
            "defaulted_to_7th": query_house is None,
            "__note__": ("Default query house = 7 (the classical 'question/other party' "
                         "bhava) when --query-house is omitted. Occupants and lord are "
                         "whole-sign from the question-moment lagna."),
        }
    return out


def build_verdicts(doc):
    """ROUND-15 FAST ledger: every combination is present | absent | unknown.

    Stops two AIs from disagreeing on the same JSON: one treating a missing
    yogas_all key as absent, the other as unevaluated; one treating a list as
    present, the other as 'not a boolean'. Readers MUST cite verdicts.by_name[X]
    and MUST NOT infer presence from a missing key.
    """
    by_name = {}
    sources = []

    def put(name, val, source):
        key = str(name)
        v = _verdict_from_value(val)
        v["source"] = source
        by_name[key] = v
        sources.append(source)

    yo = doc.get("yogas") or {}
    if isinstance(yo, dict):
        for k, val in yo.items():
            if k.startswith("__") or k in ("computed_yogas", "__capability__"):
                continue
            put(k, val, "yogas")
        comp = yo.get("computed_yogas") or {}
        if isinstance(comp, dict):
            for k, val in comp.items():
                if k.startswith("__"):
                    continue
                put(k, val, "yogas.computed_yogas")

    ya = doc.get("yogas_all") or {}
    if isinstance(ya, dict):
        pres = ya.get("yogas_present") or {}
        if isinstance(pres, dict):
            for k, val in pres.items():
                put(k, val if val is not True else True, "yogas_all.yogas_present")
        for k in ya.get("yogas_absent") or []:
            if k not in by_name:
                put(k, False, "yogas_all.yogas_absent")

    ds = doc.get("doshas") or {}
    if isinstance(ds, dict):
        checked = ds.get("doshas_checked") or {}
        if isinstance(checked, dict):
            for k, val in checked.items():
                put(k, val, "doshas.doshas_checked")
        for typed in ("manglik", "kala_sarpa", "pitru_dosha", "shrapit", "kalathra", "ganda_moola"):
            if typed in ds:
                put(typed, ds[typed], "doshas." + typed)

    ry = doc.get("raja_yogas") or {}
    if isinstance(ry, dict):
        for i in (1, 2, 3):
            k = "other_raja_yoga_%d" % i
            if k in ry:
                put(k, ry[k], "raja_yogas")

    present = sorted(k for k, v in by_name.items() if v.get("present") is True)
    absent = sorted(k for k, v in by_name.items() if v.get("present") is False)
    unknown = sorted(k for k, v in by_name.items() if v.get("present") is None)
    return {
        "by_name": by_name,
        "present": present,
        "absent": absent,
        "unknown": unknown,
        "counts": {"present": len(present), "absent": len(absent), "unknown": len(unknown),
                   "total": len(by_name)},
        "__note__": ("ROUND-15: THE ONLY allowed source for 'is combination X present?'. "
                     "present=true/false/null. A name missing from by_name was NOT evaluated "
                     "(do not call it absent). yogas_all.yogas_present is a PRESENT-only "
                     "subset — use yogas_absent / this ledger for negatives. Cite "
                     "verdicts.by_name[<name>].present, never guess from a list vs bool."),
    }


# ---- Section registry ------------------------------------------------------ #
# name -> (builder, is_default_on)
SECTIONS = {
    "core":        (lambda h, jd, place, pp, env: build_core(env, h, jd, place, pp), True),
    "panchanga":   (lambda h, jd, place, pp, env: build_panchanga(env, h, jd, place), True),
    "planet_flags":(lambda h, jd, place, pp, env: build_planet_flags(env, h, jd, place, pp), True),
    "house_vars":  (lambda h, jd, place, pp, env: build_house_variables(env, h, jd, place, pp), True),
    "doshas":      (lambda h, jd, place, pp, env: build_doshas(env, h, jd, place, pp), True),
    "karakas":     (lambda h, jd, place, pp, env: build_karakas(env, h, jd, place, pp), True),
    "vargas":      (lambda h, jd, place, pp, env: build_vargas(env, h, jd, place, pp, [2, 3, 7, 9, 10, 12]), True),
    "special":     (lambda h, jd, place, pp, env: build_special(env, h, jd, place), True),
    "dasha":       (lambda h, jd, place, pp, env: build_dhasa(env, h, jd, place, pp, None), True),
    "applicability": (lambda h, jd, place, pp, env: build_applicability(env, h, jd, place), True),
    "narayana":    (lambda h, jd, place, pp, env: build_narayana(env, h, jd, place, pp, None), False),
    "rasi_dasha":  (lambda h, jd, place, pp, env: build_rasi_dasha_interpretation(env, h, jd, place, pp), False),
    "sphutas":     (lambda h, jd, place, pp, env: build_sphutas(env, h, jd, place), False),
    "sahams":      (lambda h, jd, place, pp, env: build_sahams(env, h, jd, place, pp), False),
    "arudhas":     (lambda h, jd, place, pp, env: build_arudhas_and_specials(env, h, jd, place, pp), False),
    "strengths":   (lambda h, jd, place, pp, env: build_strengths(env, h, jd, place, pp), False),
    "ashtakavarga":(lambda h, jd, place, pp, env: build_ashtakavarga(env, h, jd, place, pp), False),
    # ROUND-9 (review Enhancement 4): curated yogas now run by DEFAULT (they are cheap —
    # single-pass predicates — and Pancha Mahapurusha / Kemadruma / Gajakesari belong in
    # every base fact sheet), and the default varga set includes D2 (Hora — wealth) and
    # D3 (Drekkana — siblings/vitality), completing the classical Shadvarga core.
    "yogas":       (lambda h, jd, place, pp, env: build_yogas(env, h, jd, place, pp), True),
    "yogas_all":   (lambda h, jd, place, pp, env: build_yogas_all(env, h, jd, place, pp), False),
    "raja_yogas":  (lambda h, jd, place, pp, env: build_raja_yogas(env, h, jd, place, pp), False),
    "tajaka":      (lambda h, jd, place, pp, env: build_tajaka(env, h, jd, place), False),
    "vargas_all":  (lambda h, jd, place, pp, env: build_vargas(env, h, jd, place, pp, _VARGAS_ALL, label_varga_factors=True), False),
    "all_dashas":  (lambda h, jd, place, pp, env: build_all_dashas(env, h, jd, place, pp), False),
    "dasha_decoded": (lambda h, jd, place, pp, env: build_dasha_decoded(env, h, jd, place, pp), False),
    "running_dashas": (lambda h, jd, place, pp, env: build_running_dashas(env, h, jd, place, None), False),
    "muhurta":     (lambda h, jd, place, pp, env: build_muhurta(env, h, jd, place), False),
    "vratha":      (lambda h, jd, place, pp, env: build_vratha(env, h, jd, place), False),
    "eclipses":    (lambda h, jd, place, pp, env: build_eclipse_deep(env, h, jd, place), False),
    "house_systems": (lambda h, jd, place, pp, env: build_house_systems(env, h, jd, place, pp), False),
    "strength_full": (lambda h, jd, place, pp, env: build_strength_full(env, h, jd, place, pp), False),
    "ashtakavarga_deep": (lambda h, jd, place, pp, env: build_ashtakavarga_deep(env, h, jd, place, pp), False),
    "tajaka_deep": (lambda h, jd, place, pp, env: build_tajaka_deep(env, h, jd, place, pp, None), False),
    "varga_transits": (lambda h, jd, place, pp, env: {"__note__": "requires --transit YYYY-MM-DD"}, False),
    "prediction":  (lambda h, jd, place, pp, env: build_prediction(env, h, jd, place, pp), False),
    "chakras":     (lambda h, jd, place, pp, env: build_chakras(env, h, jd, place, pp), True),
    "aayu":        (lambda h, jd, place, pp, env: build_aayu(env, h, jd, place, pp), False),
    "alt_engines": (lambda h, jd, place, pp, env: build_alt_engines(env, h, jd, place, pp), False),
    "experiments": (lambda h, jd, place, pp, env: {"__note__": "requires --experiments flag"}, False),
    "prashna":     (lambda h, jd, place, pp, env: build_prashna(env, h, jd, place, pp), False),
}


# ROUND-19 — plan-first deep readings. FAST remains a smoke/default fact sheet;
# a real question uses --query-profile (see query_playbooks.md).
QUERY_PROFILE_NAMES = (
    "deep", "marriage", "career", "timing", "health", "children",
    "money", "muhurta", "prashna", "compatibility",
)
# Full natal sheet minus experiments. prediction / alt_engines stay flag-gated.
QUERY_PROFILE_FULL = frozenset({
    "deep", "marriage", "career", "timing", "health", "children",
    "money", "compatibility",
})
QUERY_PROFILE_NARROW = {
    "muhurta": [
        "core", "panchanga", "muhurta", "vratha", "planet_flags",
        "house_vars", "special", "yogas", "applicability",
    ],
    "prashna": [
        "core", "panchanga", "prashna", "planet_flags",
        "house_vars", "doshas", "yogas", "karakas", "special",
    ],
}


def sections_for_query_profile(profile):
    """Return SECTIONS keys for --query-profile. Unknown names raise ValueError."""
    name = (profile or "").strip().lower()
    if name not in QUERY_PROFILE_NAMES:
        raise ValueError(
            "unknown --query-profile %r (choose from %s)"
            % (profile, ", ".join(QUERY_PROFILE_NAMES))
        )
    if name in QUERY_PROFILE_NARROW:
        return list(QUERY_PROFILE_NARROW[name])
    return [k for k in SECTIONS.keys() if k != "experiments"]


def main():
    ap = argparse.ArgumentParser(description="PyJHora computation engine -> expert JSON")
    ap.add_argument("--name", default="Native")
    ap.add_argument("--lat", type=float, default=12.9141)
    ap.add_argument("--lon", type=float, default=74.8560)
    ap.add_argument("--tz", type=float, default=5.5)
    ap.add_argument("--place", default=None, help="place name instead of coords (Nominatim)")
    ap.add_argument("--date", default="1990-05-15", help="YYYY-MM-DD")
    ap.add_argument("--time", default="10:30:00", help="HH:MM:SS local")
    ap.add_argument("--ayanamsa", default="LAHIRI")
    ap.add_argument("--mean-nodes", action="store_true", help="use Mean Rahu/Ketu")
    ap.add_argument("--out", default="chart.json")
    ap.add_argument("--full", action="store_true", help="compute + dump deep tables")
    ap.add_argument("--fast-full", action="store_true",
                    help="a curated --full that includes the high-value deep sections but "
                         "skips the heaviest experimental ones (yogas_all, varga_transits, "
                         "running_dashas) for faster runs")
    ap.add_argument("--now", default=None,
                    help="reference 'current' date YYYY-MM-DD for running dashas + Tajaka "
                         "anchoring. ROUND-8 (review P3-7): defaults to TODAY when omitted "
                         "— running dashas are now always computed. Pass an explicit date "
                         "for reproducible artifacts.")
    ap.add_argument("--transit", default=None,
                    help="YYYY-MM-DD to also compute transits against the natal chart")
    ap.add_argument("--sections", default=None,
                    help="comma-separated section names to compute (default: the standard set). "
                         "Use --list-sections to see all.")
    ap.add_argument("--query-profile", default=None, choices=list(QUERY_PROFILE_NAMES),
                    help="ROUND-19 plan-first deep set for a question type. "
                         "deep/marriage/career/timing/health/children/money/compatibility "
                         "= full natal sheet minus experiments (always-deep; not FAST). "
                         "muhurta/prashna = focused election/horary sets. "
                         "Implies --transit at --now when --transit is omitted. "
                         "Overridden by explicit --sections. See query_playbooks.md.")
    ap.add_argument("--list-sections", action="store_true",
                    help="print the available sections and exit")
    ap.add_argument("--match-date", default=None, help="partner DOB YYYY-MM-DD for compatibility")
    ap.add_argument("--match-time", default=None, help="partner birth time HH:MM:SS")
    ap.add_argument("--match-lat", type=float, default=None)
    ap.add_argument("--match-lon", type=float, default=None)
    ap.add_argument("--match-tz", type=float, default=None,
                    help="partner timezone offset (e.g. 5.5). ROUND-10: when omitted the partner "
                         "tz is resolved FROM THE PARTNER'S OWN COORDINATES (library "
                         "utils.get_place_timezone_offset, DST-aware at the partner birth date), "
                         "NOT defaulted to the native's --tz; the native tz is only a last-resort "
                         "fallback. Whatever was used is disclosed via match.timezone_note. With "
                         "--match-place the library geocodes the tz and this flag is ignored.")
    ap.add_argument("--match-place", default=None, help="partner place name instead of coords")
    ap.add_argument("--transit-conjunctions", action="store_true",
                    help="also compute all planet-pair next-conjunction dates (36 searches; default off)")
    ap.add_argument("--transit-no-events", action="store_true",
                    help="skip the per-planet retrograde/stationary/entry event-date search (default: computed)")
    ap.add_argument("--fine-dasha", default=None,
                    help="emit a fine sub-period calendar (L3 pratyantar/L4 sukshma) for an ISO window "
                         "'YYYY-MM-DD,YYYY-MM-DD'; uses --now as the anchor. --max-depth controls depth.")
    ap.add_argument("--max-depth", type=int, default=4, help="fine-dasha recursion depth (2..6)")
    ap.add_argument("--narayana-depth", type=int, default=3,
                    help="Narayana running-chain depth (1..6); uses --now as the anchor")
    ap.add_argument("--running-depth", type=int, default=3,
                    help="dhasa_level_index passed to each running-dasha system. ROUND-8 "
                         "(review P3-7): default raised 2→3 so the default chain reaches "
                         "Mahadasha→Antardasha→Pratyantar for timing work")
    ap.add_argument("--annual-year", type=int, default=None,
                    help="explicit Tajaka annual-year number (>=1); else derived from --now")
    ap.add_argument("--varga-division", type=int, default=9,
                    help="divisional chart factor for varga_transits (default D9)")
    ap.add_argument("--tajaka-month", type=int, default=None,
                    help="Tajaka month within the annual year (1..12); else derived from --now")
    ap.add_argument("--sixty-hour-count", type=int, default=1,
                    help="sixty-hour subdivision count for the Tajaka sixty-hour chart (default 1)")
    ap.add_argument("--arudha-base", type=int, default=0,
                    help="Arudha counting convention (0/1/2) passed to bhava_arudha_longitudes (default 0)")
    ap.add_argument("--experiments", action="store_true",
                    help="run EXPERIMENTAL research predictors (marriage-window scorers, "
                         "double-transit + sensitive-point scans; adds ~10s)")
    ap.add_argument("--experiments-deep", action="store_true",
                    help="also run the heavy degree-locked double-transit scan (~15s+); "
                         "implies --experiments")
    ap.add_argument("--marriage-window", default=None,
                    help="calendar-year range 'Y1,Y2' for the marriage experiments "
                         "(default: derived from ages 20-35)")
    ap.add_argument("--alt-engines", action="store_true",
                    help="include the Surya-Siddhanta alternative-engine crosscheck section "
                         "(also included automatically by --full)")
    ap.add_argument("--prediction", action="store_true",
                    help="include the prediction section (PyJHora's bundled interpretive prose "
                         "+ the computed life-span bracket) in a non-full run; --full always "
                         "includes it. Off by default: the default run is a pure fact sheet.")
    ap.add_argument("--dasha-year-duration", type=int, default=None, choices=range(1, 9),
                    metavar="1..8",
                    help="const.DHASA_YEAR_DURATION for year-length-sensitive computations: "
                         "1=mean sidereal, 2=true sidereal (library default), 3=mean tropical, "
                         "4=true tropical, 5=savana, 6=mean lunar, 7=true lunar, 8=Gregorian")
    # --- ROUND-7 flags (review C3 + tails: explicit Vimsottari variations, house-system
    # selection, stock vs patched mode, update check) ---
    ap.add_argument("--vimsottari-star-position", type=int, default=None,
                    help="Vimsottari star_position_from_moon (library default 1)")
    ap.add_argument("--vimsottari-tribhagi", action="store_true",
                    help="Vimsottari use_tribhagi_variation=True")
    ap.add_argument("--vimsottari-rasi-bhukthi", action="store_true",
                    help="Vimsottari use_rasi_bhukthi_variation=True")
    ap.add_argument("--vimsottari-seed-star", type=int, default=None,
                    help="Vimsottari seed_star (library default 3)")
    ap.add_argument("--vimsottari-chart-method", type=int, default=None,
                    help="Vimsottari chart_method (library default 1)")
    ap.add_argument("--match-method", default=None,
                    help="Ashtakoota method; default 'North' = classic numeric 0..36 scoring. "
                         "PyJHora 5.0: method=None falls back to const.default_chart_type and "
                         "yields BOOLEAN pass/fail lists — we pin North unless overridden.")
    ap.add_argument("--gender", default="male", choices=("male", "female"),
                    help="gender of the NATIVE (chart subject). ROUND-8: Ashtakoota "
                         "matching is directional (bride→groom); the male side feeds the "
                         "boy_* inputs. Also passed to gender-aware yoga predicates "
                         "(mahabhagya). Default: male.")
    ap.add_argument("--match-gender", default="female", choices=("male", "female"),
                    help="gender of the PARTNER given via --match-* (default: female). "
                         "Same-gender pairs are computed with the native on the boy side "
                         "and labelled in match.genders.note.")
    ap.add_argument("--bhava-method", default=None,
                    help="house system for the bhava_method_selected block: 1=KN Rao, "
                         "2=Parashari whole-sign, 3=KP, 4=BV Raman, 5=nakshatra-pada equal, "
                         "O/S=Sripathi variants, or western letters (P=Placidus, K=Koch, "
                         "C=Campanus, R=Regiomontanus, A/E=Equal, B=Alcabitus, V=Vehlow, ...) "
                         "— western letters auto-enable const.include_western_house_systems "
                         "for this run only")
    ap.add_argument("--bhava-from", default=None, choices=["sun", "moon"],
                    help="reference planet for the ascendant when computing bhava cusps "
                         "(PyJHora 5.0 reference_planet_for_ascendant; default = Lagna)")
    ap.add_argument("--bhava-asc-start", action="store_true",
                    help="treat the ascendant as the START of house 1 instead of its middle "
                         "(ascendant_is_middle_of_house=False)")
    ap.add_argument("--bhava-starts-with-ascendant", action="store_true",
                    help="pass bhava_starts_with_ascendant=True to bhava_chart_houses")
    ap.add_argument("--stock", action="store_true",
                    help="install NO wrapper compatibility patches (pure upstream behavior; "
                         "paths broken upstream surface as guarded __error__ fields) — "
                         "__meta__.engine_mode records which mode produced the document")
    ap.add_argument("--no-update-check", action="store_true",
                    help="skip the small PyPI/GitHub PyJHora update check performed at start")
    ap.add_argument("--prashna", action="store_true",
                    help="ROUND-15: include wrapper Prashna/horary. Datetime = question moment. Facts only.")
    ap.add_argument("--prashna-number", type=int, default=None,
                    help="querent number 1..249 (KP) / 1..108 / 1..1800. Implies --prashna.")
    ap.add_argument("--prashna-system", default="kp249",
                    help="number-prasna table: kp249 | nadi108 | nadi150")
    ap.add_argument("--query-house", type=int, default=None,
                    help="Prashna query house 1..12 (default 7). Implies --prashna.")
    args = ap.parse_args()
    if getattr(args, "prashna_number", None) is not None or getattr(args, "query_house", None) is not None:
        args.prashna = True
    if args.experiments_deep:
        args.experiments = True
    # ROUND-8 (review P3-7): default the reference date to TODAY when --now is omitted so
    # running dashas / Tajaka anchoring are ALWAYS computed (previously they were skipped
    # entirely). The defaulting is disclosed in __meta__.
    _now_defaulted = args.now is None
    if _now_defaulted:
        args.now = dt.date.today().isoformat()

    if args.list_sections:
        for k, (_, on) in SECTIONS.items():
            print(f"{'[on]' if on else '[off]'} {k}")
        return

    _reset_dasha_cache()

    env = _imports()
    drik = env["drik"]; utils = env["utils"]
    Horoscope = env["Horoscope"]; charts = env["charts"]

    # --- ROUND-7 (user request): small update check on EVERY run ----------------
    # Non-fatal, stderr-only notices; the result is also recorded in __meta__.
    _installed_version = _pyjhora_version(env)
    _upd = None
    if not args.no_update_check:
        try:
            _upd = check_for_updates(_installed_version)
            if _upd.get("update_available"):
                for _it in _upd["newer_than_installed"]:
                    print(f"[update-check] newer PyJHora available: {_it['version']} "
                          f"({_it['source']}); installed {_installed_version}. Re-validate "
                          "this wrapper (test_coverage.py) before upgrading.", file=sys.stderr)
            else:
                print(f"[update-check] PyJHora {_installed_version} — no newer release seen "
                      f"(pypi={_upd.get('pypi_latest')}, github_master="
                      f"{_upd.get('github_master_version')}).", file=sys.stderr)
        except Exception as _e:      # never fatal
            _upd = {"installed": _installed_version, "__error__": repr(_e)[:140]}

    # ROUND-7 (review C2/C3): explicit dasha-year-duration ENUM + vimsottari variations.
    _dyd_enum = None
    if args.dasha_year_duration:
        try:
            _dyd_enum = env["const"].DHASA_YEAR_DURATION(int(args.dasha_year_duration))
        except Exception:
            _dyd_enum = None
    _vims_opts = {"star_position_from_moon": args.vimsottari_star_position,
                  "use_tribhagi_variation": True if args.vimsottari_tribhagi else None,
                  "use_rasi_bhukthi_variation": True if args.vimsottari_rasi_bhukthi else None,
                  "seed_star": args.vimsottari_seed_star,
                  "chart_method": args.vimsottari_chart_method}
    env["_match_method"] = args.match_method
    # ROUND-8: native gender, PyJHora convention 0=male, 1=female — feeds gender-aware
    # yoga predicates (mahabhagya #61) via the yogas_all pool and the match directionality.
    env["_gender"] = 0 if str(args.gender).lower() == "male" else 1
    env["_bhava_opts"] = {"method": args.bhava_method, "reference": args.bhava_from,
                          "asc_start": args.bhava_asc_start,
                          "starts_with_asc": args.bhava_starts_with_ascendant}

    # --- configure engine (Rahu/Ketu true or mean nodes) -------------------
    # PyJHora keeps its ayanamsa/nodes as GLOBAL state on the drik module and jhora.const
    # (set_planet_list / set_ayanamsa_mode mutate module globals). We run the whole chart
    # inside `_isolated_pyjhora_config` so the prior global state is restored on ANY exit —
    # a concurrent-request-safe, side-effect-free run (review §3: "mutated and never isolated").
    with _isolated_pyjhora_config(drik, env["const"],
                                  {"set_rahu_ketu_as_true_nodes": False if args.mean_nodes else True},
                                  args.ayanamsa,
                                  dasha_year_duration=args.dasha_year_duration,
                                  install_patches=not args.stock):
        date = dt.datetime.strptime(args.date, "%Y-%m-%d").date()
        if args.place:
            h = Horoscope(place_with_country_code=args.place,
                          date_in=drik.Date(date.year, date.month, date.day),
                          birth_time=args.time)
        else:
            h = Horoscope(latitude=args.lat, longitude=args.lon, timezone_offset=args.tz,
                          date_in=drik.Date(date.year, date.month, date.day),
                          birth_time=args.time)
        jd = h.julian_day
        place = h.Place
    
        planet_positions = charts.rasi_chart(jd, place)
    
        doc = {
            "__meta__": {
                "engine": "PyJHora",
                "version": _pyjhora_version(env),
                "target_release_line": ("GitHub dev line (4.9.3-dev -> 5.0); PyPI stable lags "
                                        "behind — this wrapper is validated against the dev line"),
                "method": "Drik (modern ephemeris), sidereal",
                "ayanamsa": args.ayanamsa.upper(),
                "query_profile": getattr(args, "query_profile", None),
                "nodes": "True" if not args.mean_nodes else "Mean",
                "engine_mode": ("stock_pyjhora (no wrapper compat patches: --stock)" if args.stock
                                else "wrapper_patched (see compatibility_patches)"),
                "update_check": (_upd if _upd is not None else "skipped (--no-update-check)"),
                # filled in AFTER all sections compute (some patches install mid-run):
                "compatibility_patches": [],
                "optional_dependencies": {
                    "pyIslam": bool(importlib.util.find_spec("pyislam")
                                    or importlib.util.find_spec("pyIslam")),
                },
                "experiments_package": (
                    "present" if importlib.util.find_spec("jhora.experiments") else
                    "REMOVED upstream in the PyJHora 5.0 dev line — the experiments section "
                    "reports not-available entries instead of scorers"),
                # ROUND-7 (review C7/#52,#57,#59,#61,#62 disclosure demand): the FIVE upstream
                # issues cited by the external review, each VERIFIED this round against the
                # installed 5.0-dev source and given an explicit status.
                "upstream_issue_status": {
                    "note": ("Statuses verified against the installed PyJHora build during the "
                             "Round-7 audit (source markers / signatures / behavior). 'fixed' "
                             "means present in the 5.0 dev line this wrapper targets; the same "
                             "outputs are AFFECTED if you downgrade to stock 4.8.7."),
                    "naturalstupid/PyJHora#52": {
                        "title": "_ekadhipatya_sodhana reduced only diagonal pairs; Sun/Moon skipped",
                        "affects": ["ashtakavarga_deep.sodhana_ekadhipatya", "ashtakavarga_deep.sodhya_pindas"],
                        "status_in_4_8_7": "broken", "status_in_target": "fixed ('V4.8.9 - Changes per Issue #52' marker in ashtakavarga.py)"},
                    "naturalstupid/PyJHora#57": {
                        "title": "special_thaara inconsistent Lagna offsets + Abhijit ordering",
                        "affects": ["panchanga.special_thaara_from_moon", "panchanga.special_thaara_from_lagna"],
                        "status_in_4_8_7": "broken", "status_in_target": "fixed in the dev line per maintainer; 5.0 also generalized the base to any planet/Lagna"},
                    "naturalstupid/PyJHora#59": {
                        "title": "Chara Dasha (K.N. Rao) antardasha order not rotated per mahadasha",
                        "affects": ["all_dashas.chara", "dasha_decoded.chara"],
                        "status_in_4_8_7": "broken", "status_in_target": "fixed (dedicated _antardhasa_order_knrao in raasi/chara.py)"},
                    "naturalstupid/PyJHora#61": {
                        "title": "Mahabhagya yoga: divisional factor misread as gender by the dispatcher",
                        "affects": ["yogas_all (mahabhagya entries)"],
                        "status_in_4_8_7": "broken", "status_in_target": "fixed (signature now (jd, place, divisional_chart_factor=1, gender=0))"},
                    "naturalstupid/PyJHora#62": {
                        "title": "Dhasa year-duration config strings ignored (silent sidereal fallback)",
                        "affects": ["dasha.dasha_year_duration_effective", "all dasha timelines"],
                        "status_in_4_8_7": "broken", "status_in_target": "fixed; this engine ALSO passes the enum explicitly and records the effective day count"},
                },
                # Bugs THIS wrapper discovered in the 5.0 dev line during the Round-7
                # migration (not yet reported upstream), with the mitigation applied:
                "wrapper_discovered_5_0_dev_bugs": [
                    {"where": "drik.previous_ascendant_entry_date",
                     "bug": "forwards increment_days= to next_ascendant_entry_date, which dropped that kwarg (TypeError on every call)",
                     "mitigation": "compat patch: kwarg stripped, direction=-1 forwarded (auto-no-ops once fixed upstream)"},
                    {"where": "transit.tajaka_yoga.get_duhphali_kutta_yoga_planet_pairs",
                     "bug": "module references a global 'charts' it never imports (NameError)",
                     "mitigation": "compat patch: charts module injected (restored after run)"},
                    {"where": "prediction.longevity.life_span_range",
                     "bug": "the three-vote-tie branch still uses the py2-only counter.keys()[0]; 5.0 modernized only the two-vote branch",
                     "mitigation": "compat patch: documented source transform (auto-no-ops once fixed upstream)"},
                    {"where": "chart.yoga predicates (balya_dhana, matrunasa, matrudeerghayur, jarajaputra)",
                     "bug": "public *_from_planet_positions wrappers forward kwargs their internal _calculation functions do not accept / reference undefined globals",
                     "mitigation": "NONE — surfaced honestly in yogas_all.error_examples; do not trust these specific yogas on 5.0-dev"},
                ],
                "dasha_year_duration": (int(args.dasha_year_duration) if args.dasha_year_duration
                                        else "library default (TRUE_SIDEREAL_YEAR)"),
                "now_reference_date": args.now,
                "now_defaulted_to_today": _now_defaulted,
                "native_gender": args.gender,
                "birth": {"name": args.name, "date": args.date, "time": args.time,
                          "lat": args.lat, "lon": args.lon, "tz": args.tz},
                # REVIEW R6 fix: with --place the CLI lat/lon/tz above are the *defaults*,
                # not the geocoded values actually used — report the Horoscope's resolved
                # Place object fields explicitly.
                "place_resolved": {"name": getattr(place, "name", "") or (args.place or ""),
                                   "lat": place.latitude, "lon": place.longitude,
                                   "tz": place.timezone,
                                   "elevation": getattr(place, "elevation", 0.0)},
                # `h.julian_day` is the LOCAL-clock JD (PyJHora feeds local birth time to
                # swe.julday; every drik call in this document uses THAT convention).
                # REVIEW R6 fix: `h.julian_utc` is the DATE-ONLY (midnight) JD — it is NOT
                # the UT birth instant. True UT JD = local JD - tz/24 (utils.julian_day_utc).
                "julian_day_local": jd,
                "julian_day_ut": env["utils"].julian_day_utc(jd, place),
                "julian_day_date_only": getattr(h, "julian_utc", None),
                "sections_computed": (args.sections.split(",") if args.sections
                                      else ("ALL" if args.full else "default")),
                "jana_path": os.path.dirname(__file__),
            },
            "__legend__": {
                "sign_index": "0=Aries ... 11=Pisces (matches absolute_longitude // 30).",
                "planet_names": "core.planets[].name, planet_flags.*, house_vars.*, and karakas.* are all STRING planet names (Sun..Ketu). Numeric ids appear ONLY inside raw PyJHora arrays (all_dashas.*, sphutas).",
                "house_numbers": "Houses are whole-sign: house N = the sign (lagna_sign_index + N - 1) mod 12. Always 1=the sign containing the Ascendant.",
                "nakshatra": "core.planets[].nakshatra and panchanga.moon_nakshatra.name are NAME strings (already decoded). nakshatra_pada is 1..4.",
                "dignity": "Exalted | Own_sign | In_other_sign | Debilitated (relative to sign placement only; combustion/retrogression are in planet_flags).",
                "panchanga_decode": "tithi/yoga/karana/moon_nakshatra give BOTH a raw index and a decoded NAME. Use the NAME.",
                "raw_dashas": "all_dashas.* use raw integer lord/sign ids — NOT planet names. dasha.* is the decoded, authoritative timing source.",
                "match_decode": "match.ashtakoota_total is out of 36; poruthams* give {score,max} per koota; special_poruthams are Yes/No flags. All decoded. ROUND-11: --match-method South replaces the North fields with dasa_koota_breakdown (10 boolean poruthams) + dasa_koota_total out of dasa_koota_max=10 (Tamil Dasa-Koota; varna/naadi excluded).",
                "round11_fields": "ROUND-11 additions: planet_flags.combustion_detail (standard BPHS orbs + cazimi + library-mismatch flags — planet_flags.combust stays the raw library verdict), planet_flags.gandanta (water->fire junction zones for lagna + 9 grahas), panchanga.dagdha_rasis (the tithi's burnt signs, disputed variants disclosed, planets sitting in them), transits[].kakshya (3d45m sub-sign lord + natal BAV bindu + favorable flag) with transits.kakshya_transits_note, sphutas.yogi_avayogi_planets (yogi/duplicate-yogi/avayogi derivations with the library-point crosscheck).",
                "round12_fields": "ROUND-12 additions: core.planets[].navamsa_sign/_index + core.lagna.navamsa_sign/_index (inline D9 sign of the same longitude, audited == divisional_chart(9)); ashtakavarga(.binna_by_sign) + ashtakavarga_deep row 7 now correctly labeled 'Lagna' (was mislabeled 'Rahu' — FIXED; Rahu/Ketu have no BAV rows); strength_full.shad_bala_summary (the library's 9-row shad_bala decoded: 6 components + totals + rupas + required-minimum ratios + meets_required_minimum, with the Sun-minimum source dispute disclosed) and .kashta_phala_complement (60 − ishta on the library's 0..60 scale) plus <key>_by_planet decoded twins for every raw 7-element list; ashtakavarga_deep.sodhya_pindas_decoded (raasi/graha/shodhya pindas per Sun..Saturn; shodhya = raasi + graha); dasha.yogini_decoded (the 8 yogini names/lords/durations + decoded timeline + current period at --now).",
                "round13_fields": "ROUND-13 additions: strengths.shad_bala_summary (the FAST path now carries the SAME decoded 9-row shad_bala summary as strength_full — one shared decoder), arudhas_by_varga.D10 + arudhas_by_varga_note (Dasamsa arudhas: A10 Rajyapada career-status, A11 Labhapada income), doshas.manglik_multi_reference (Kuja dosha from lagna AND Moon AND Venus via the library's own manglik_reference_planet parameter; per-frame Mars house, exceptions applied/raw, lagna-house union variant disclosed), house_systems.kp_significators (KP four-fold A/B/C/D significators by planet and by house in BOTH the whole-sign and the orthodox KP-cuspal frames), _navamsa_sign_of_long mod-360 wrap (defensive no-op — the review's 360°=Pisces claim was wrong) and signature-adaptive partner-timezone binding (_place_tz_offset: jd_local/jd/positional/2-param across dev builds).",
                "round14_fields": "ROUND-14 additions: chakras (NEW FAST SECTION - Kota Chakra DATA PORT: 28-star circle with Abhijit after UttaraShada, four rings Baahya/Praakaara/Durgantara/Sthambha resolved from the Moon's birth star + pada, per-body zone placement, kota lord and kota paala; Sarvatobhadra Chakra DATA PORT: the full 9x9 grid decoded into nakshatra/rasi/syllable/tithi-group cells, body cell positions and the birth tithi-group cell - both ported from the PyQt6-locked jhora/ui/chakra.py widgets with sources cited, and PyJHora computes NO SBC vedha penetrations anywhere, disclosed) | aayu (NEW DEEP SECTION - the harana reduction TABLES behind the catalog aayu dashas: base-year tables for pindayu/nisargayu/amsayu, the four harana factor tables (astangata combustion, shatru-kshetra enemy-sign, chakrapata above-horizon, krurodaya malefic-rising) plus bharana increases, Santhanam (method 1) and Varahamihira (method 2) variants, aayur-type determination, lagna longevity, the public longevity() total and a catalog-parity note; PRIVATE-API extraction with the module's own 'NOT FULLY IMPLEMENTED YET' docstring disclosed - values are classical strength indices, NEVER death predictions) | house_systems.relative_houses.relative_house_from_sun (Surya-kundali frame completing the Sudarshana lagna/moon/sun triad) | FOUR review claims REJECTED with audit evidence (14a): chart renderers (no jhora.chart_renderer module exists - rendering is PyQt6 UI only), Prashna/horary (zero matches library-wide), a muhurta range-scanner (no find_muhurtha_between_dates - every muhurta function is single-jd), and deity/gemstone remedy constants (absent from const; tithi_deities holds 16 Nitya Devis, a different doctrine).",
                "round15_fields": "ROUND-15: negative-hour tithi clocks (-Nd); decoded special_tithis/karaka_tithi/lunar_month/samvatsara/vedic_date/chandrabalam/thaaraabalam; yogas_all.yogas_absent; FAST verdicts ledger; wrapper prashna via library prasna KP-249/108/nadi. Round-14 REJECT of an Ashtamangala oracle still holds.",
                "round16_fields": "ROUND-16: remaining drik/charts/vratha surface. panchanga.vedic_weekday (sunrise-to-sunrise, same as vaara; civil_weekday differs pre-sunrise), vedic_time_equal_day_night (60 ghati + 30 muhurta), days_in_tamil_month, daily_planet_speed (9 grahas, signed), next/previous lunar_year + solar_year (Mesha sankranti, NOT the natal solar return) + solar_month, next_same_tithi (EXPERIMENTAL — library TODO), nava_thaara_for_all_planets / special_thaara_for_all_planets (0-based naks), next_planet_nakshatra_pravesha (retrograde bodies may yield a past JD, flagged unreliable_past_date), karana.lord (utils.karana_lord) + moon_nakshatra.library_lord (utils.nakshathra_lord). sphutas.library_longitudes_by_varga D1/D9/D10 (S1..S14 = utils._sphutas; Horoscope.get_ava_saha_yoga_* skipped as HTML of S13/S14). sahams.__divisional__ D9/D10 (36 lots; D1 named keys unchanged). special.special_lagna_longitudes_by_varga D9/D10 (BL..IL). vratha.next_special_vratha (underscore keys only — spaces return None). tattva-element lords still omitted.",
                "round17_fields": "ROUND-17 remaining non-UI computational APIs: core.planets[].kp + lagna.kp (KP-249 sub/star lords via utils.get_KP_details_from_planet_longitude), library_navamsa (drik.navamsa_from_long, audited == navamsa_sign), panchanga.tamil_solar_month_date_from_jd, sree_lagna_from_moon_asc_longitudes, nava_thaara_for_D9_chart; special.varnada_lagnas V1..V12 at D1/D9/D10 + mixed_charts D9xD12/D1xD10 + varnada mixed D1xD9; arudhas.bhava_arudhas_signs A1..A12; house_vars.strongest_among_sun_to_saturn; house_systems.in_chathusras + longevity_of_sign_pairs (const.longevity class 0/1/2 on rasi%3, NOT death); transits.previous_conjunctions + next_D9_sign_entry; sphutas/sahams mixed D1xD9. STILL NOT WRAPPED (not missing facts): yoga 700 *_from_jd_place aliases (yogas_all executes the predicates), named varga chart aliases (vargas_all has 25), Horoscope HTML getters (UI), *_old/*_raw, drik1 legacy, hijri/pyIslam, empty raja_yoga_bv_raman, get_amsa_resources (UI strings).",
                "round18_fields": "ROUND-18: special_thaara_for_D9_chart; arudha/special-lagna/special-planet mixed D1xD9; previous_D9_sign_entry; strongest_kendra_sign; next_conjunctions Rahu-Ketu skipped (was a NoneType crash string) and remaining pairs decoded to dates like previous_conjunctions.",
                "prediction_texts": "prediction.bundled_texts.* are PRE-WRITTEN prose resources shipped with PyJHora (B.V. Raman / ChinmayaAstrolger compendiums) selected by this chart's Janma Rasi and placements — quote them as library reference text, NOT as computed facts.",
                "life_span_range": "prediction.life_span_range is the coarse Parashari longevity BRACKET (Alpayu/Madhyayu/Poornayu) — never a date, never a death prediction.",
                "experiments": "experiments.* are EXPERIMENTAL research predictors (marriage-window scorers, double-transit, sensitive-point scans) — hypotheses only; corroborate against dasha + transits before quoting.",
                "alt_engines": "alt_engines.* are Surya-Siddhanta crosscheck values from a module its author labels work-in-progress; the Drik values elsewhere in this document are authoritative.",
                "error_fields": "Any value equal to \"__error__\" (or a str describing an exception) was not computable; a {\"__non_serializable__\": {type, repr}} object marks a value the serializer could not represent natively. Acknowledge and skip all of them.",
            },
            "__available__": _features(),
            "__notes__": {
                "whole_sign_houses": "House numbers are whole-sign relative to the Lagna sign (1=the sign containing the Ascendant).",
                "dasha_lords": "The `dasha.vimsottari_timeline` and `dasha.running_vimsottari` are DECODED (lord names + dates). "
                               "The `all_dashas.*` timelines are raw PyJHora arrays whose integer lord/sign ids are NOT "
                               "planet names; use them to CORROBORATE the decoded dasha, never to quote a lord that is not "
                               "in a decoded/verified timeline.",
                "unknown_values": "Any key with value \"__error__\" was not computable by the engine; acknowledge and skip, do not guess.",
            },
        }
    
        # --- choose sections ----------------------------------------------------
        # --fast-full: the high-value deep set minus the most expensive/experimental ones
        FAST_FULL_EXCLUDE = {"yogas_all", "varga_transits", "running_dashas"}
        if args.sections:
            requested = [s.strip() for s in args.sections.split(",") if s.strip()]
        elif getattr(args, "query_profile", None):
            requested = sections_for_query_profile(args.query_profile)
            if args.query_profile == "prashna":
                args.prashna = True
            # Deep readings always include gochara at --now unless the caller
            # already named a transit day.
            if not args.transit:
                args.transit = args.now
        elif args.full:
            requested = [k for k, (_, on) in SECTIONS.items()]
        elif args.fast_full:
            requested = [k for k, (_, on) in SECTIONS.items() if k not in FAST_FULL_EXCLUDE]
        else:
            requested = [k for k, (_, on) in SECTIONS.items() if on]
        # experiments is strictly opt-in (--experiments); alt_engines and prediction via
        # their flags (or any --full-style selection). REVIEW R6: prediction ships bundled
        # INTERPRETIVE prose, so it is no longer part of the default fact-sheet run.
        if not args.experiments:
            requested = [k for k in requested if k != "experiments"]
        if args.alt_engines and "alt_engines" not in requested:
            requested.append("alt_engines")
        if args.prediction and "prediction" not in requested:
            requested.append("prediction")
        if getattr(args, "prashna", False) and "prashna" not in requested:
            requested.append("prashna")
        # calendar-year window for the marriage experiments ('Y1,Y2')
        _mw = None
        if args.marriage_window:
            try:
                _mw = [int(x.strip()) for x in args.marriage_window.split(",") if x.strip()]
                if len(_mw) != 2:
                    _mw = None
            except Exception:
                _mw = None
        # transits is special (needs a --transit date); add only if computed.
        transits_present = None
        _transit_jd = None
        if args.transit:
            td = dt.datetime.strptime(args.transit, "%Y-%m-%d").date()
            try:
                tjd = utils.julian_day_number((td.year, td.month, td.day), (12, 0, 0))
                _transit_jd = tjd
                transits_present = build_transits(env, h, jd, place, planet_positions, tjd,
                                                  compute_conjunctions=args.transit_conjunctions,
                                                  compute_events=not args.transit_no_events)
            except Exception as e:
                transits_present = {"__error__": str(e)}
    
        # running dasha needs jd_now
        jd_now = None
        if args.now:
            nowd = dt.datetime.strptime(args.now, "%Y-%m-%d").date()
            try:
                jd_now = utils.julian_day_number((nowd.year, nowd.month, nowd.day), (12, 0, 0))
            except Exception:
                jd_now = None
    
        for key in requested:
            builder = SECTIONS.get(key, (None, None))[0]
            if builder is None:
                continue
            try:
                if key == "dasha":
                    doc["dasha"] = build_dhasa(env, h, jd, place, planet_positions, jd_now,
                                               vims_opts=_vims_opts,
                                               dasha_duration_type=_dyd_enum,
                                               running_depth=args.running_depth)
                elif key == "narayana":
                    doc["narayana"] = build_narayana(env, h, jd, place, planet_positions, jd_now,
                                                     max_level=args.narayana_depth)
                elif key == "tajaka":
                    doc["tajaka"] = build_tajaka(env, h, jd, place, jd_now, annual_year=args.annual_year)
                elif key == "tajaka_deep":
                    env["_sixty_hour_count"] = args.sixty_hour_count
                    doc["tajaka_deep"] = build_tajaka_deep(env, h, jd, place, planet_positions, jd_now,
                                                           annual_year=args.annual_year,
                                                           tajaka_month=args.tajaka_month)
                elif key == "running_dashas":
                    doc["running_dashas"] = build_running_dashas(env, h, jd, place, jd_now,
                                                                 level_index=args.running_depth)
                elif key == "arudhas":
                    doc[key] = build_arudhas_and_specials(env, h, jd, place, planet_positions,
                                                         arudha_base=args.arudha_base)
                elif key == "varga_transits":
                    if transits_present is not None:
                        doc["varga_transits"] = build_varga_transits(env, h, jd, place, _transit_jd,
                                                                     planet_positions,
                                                                     division=args.varga_division)
                    else:
                        doc["varga_transits"] = {"__note__": "requires --transit YYYY-MM-DD"}
                elif key == "prashna":
                    doc["prashna"] = build_prashna(
                        env, h, jd, place, planet_positions,
                        number=getattr(args, "prashna_number", None),
                        system=getattr(args, "prashna_system", "kp249"),
                        query_house=getattr(args, "query_house", None))
                elif key == "experiments":
                    if args.experiments:
                        doc["experiments"] = build_experiments(env, h, jd, place, planet_positions,
                                                               marriage_window=_mw,
                                                               deep=args.experiments_deep)
                    else:
                        doc["experiments"] = {"__note__": "requires --experiments flag"}
                else:
                    doc[key] = builder(h, jd, place, planet_positions, env)
            except Exception as e:
                doc[key] = {"__error__": str(e)}
    
        if transits_present is not None:
            doc["transits"] = transits_present
    
        # --- fine dasha sub-period calendar for a window -------------------------
        if args.fine_dasha:
            win = [w.strip() for w in args.fine_dasha.split(",") if w.strip()]
            if len(win) == 2:
                try:
                    doc["fine_dasha"] = build_fine_dhasa(
                        env, h, jd, place, jd_now=jd_now,
                        window_start=win[0], window_end=win[1],
                        max_depth=args.max_depth, step_days=0.5 if args.max_depth >= 5 else 1.0)
                except Exception as e:
                    doc["fine_dasha"] = {"__error__": str(e)}
    
        # --- marriage compatibility (if partner details given) ------------------
        if args.match_date:
            doc["match"] = build_match(
                env, h, jd, place, planet_positions,
                {"date": args.match_date, "time": args.match_time or "12:00:00",
                 "lat": args.match_lat, "lon": args.match_lon,
                 # ROUND-10 FIX (review P1-3, CONFIRMED): pass --match-tz through AS-IS
                 # (None when omitted). The old chain fell back to args.tz — but --tz has
                 # an argparse default (5.5), so the partner in coordinate mode ALWAYS
                 # got IST. build_match now resolves an omitted tz from the partner's
                 # coordinates (library utils.get_place_timezone_offset, DST-aware),
                 # with the native tz as last resort, and discloses the source via
                 # match.timezone_note.
                 "tz": args.match_tz,
                 "place": args.match_place,
                 "gender": args.gender, "match_gender": args.match_gender})

        # ROUND-7 (review C8): snapshot the installed compat patches INSIDE the isolated
        # context — `_restore_compat_patches()` empties the registry when the context
        # exits, so this must be captured before serialization. Empty list under --stock.
        try:
            doc["verdicts"] = build_verdicts(doc)
        except Exception as e:
            doc["verdicts"] = {"__error__": str(e)}
        doc["__meta__"]["compatibility_patches"] = _compat_patch_report()
    
    # ---- structured error manifest (review §14) -------------------------------
    # Surface every failed value as a plain structured record {section, path, error}
    # instead of leaving exception string fragments littered inside the data. This is
    # non-breaking (it ADDS a top-level __errors__ list) but gives consumers a formal,
    # machine-readable error envelope rather than raw exception strings.
    # REVIEW FIX: the manifest previously ONLY recorded failures stored as `{"__error__": ..}`
    # dicts. Many builders instead store a bare `str(e)` as the field value (e.g. `out["x"]
    # = str(e)`), which slipped through the manifest entirely. We now also detect plain-string
    # values that unambiguously look like a Python exception string (the conservative markers
    # that only appear in `str(e)` output) and tag them `plain_string_error`. We deliberately
    # EXCLUDE ambiguous markers ("not_computed:", "requires --", "missing") because those are
    # informative notes, not failures, and must not mark a clean chart as erroring.
    _err_manifest = []
    def _collect_errors(o, path, current_section):
        if isinstance(o, dict):
            for k, v in o.items():
                sp = f"{path}.{k}" if path else str(k)
                # keep the top-level SECTION name (the caller scopes each section);
                # an __error__ nested one level down still belongs to that section.
                sec = current_section
                if k == "__error__":
                    _err_manifest.append({"section": sec, "path": sp,
                                         "error": str(v)[:160], "code": "computation_failed"})
                else:
                    _collect_errors(v, sp, sec)
        elif isinstance(o, (list, tuple)):
            for i, v in enumerate(o):
                _collect_errors(v, f"{path}[{i}]", current_section)
        elif isinstance(o, str) and o and any(m in o for m in _ERR_STRING_MARKERS):
            # a bare string value that is really an error (nothing after it in the dict)
            _err_manifest.append({"section": current_section, "path": path,
                                  "error": o[:160], "code": "plain_string_error"})
    for _sec in list(doc.keys()):
        if _sec.startswith("__"):
            continue
        _collect_errors(doc[_sec], "", _sec)
    _err_seen = set(); _uniq = []
    for e in _err_manifest:
        k = (e["section"], e["path"], e["error"])
        if k not in _err_seen:
            _err_seen.add(k); _uniq.append(e)
    doc["__errors__"] = _uniq
    # also record the per-section envelope {status, error_count} for the whole run.
    doc["__status__"] = {}
    for _sec in [k for k in doc.keys() if not k.startswith("__")]:
        _n = sum(1 for e in _uniq if e["section"] == _sec)
        doc["__status__"][_sec] = {"status": ("ok" if _n == 0 else "error"),
                                   "errors": _n}

    # Serialize with a *visible* fallback rather than silently masking every non-JSON
    # value with str() (review §3: `json.dump(default=str)` hides real failures). We use a
    # custom `default` that records which objects had to be stringified, then surface that
    # list in __meta__ so a masking regression is visible — while still emitting valid JSON.
    #
    # REVIEW FIX: a dry-run `json.dumps` captures the mask list ONCE (with `_counting`
    # true), then the doc's `serialization_masked_count` is set and the SAME doc is
    # written once. Previously the JSON was written TWICE, so the `default` ran twice and
    # every non-serializable value was appended to `_masked` twice (count doubled). Now the
    # count is exactly the number of distinct values stringified in one serialization.
    _masked = []
    _counting = [True]
    def _json_default(obj):
        # ROUND-8 FIX (review P0-2): coerce valid non-JSON-native types (numpy scalars/
        # arrays, date/datetime, sets) to natives FIRST via the module-level _json_coerce;
        # only genuinely unrepresentable objects are masked (and counted).
        v, ok = _json_coerce(obj)
        if ok:
            return v
        if _counting[0]:
            _masked.append(repr(obj)[:80])
        # REVIEW R6 fix: a bare str(obj) can masquerade as computed data. Emit a TAGGED
        # placeholder so a consumer can never mistake a stringified failure for a value.
        return {"__non_serializable__": {"type": type(obj).__name__, "repr": repr(obj)[:120]}}
    _mask_ui: dict = doc.setdefault("__meta__", {})
    # dry run (counts masks exactly once; does not write to disk or mutate doc)
    json.dumps(doc, default=_json_default)
    _counting[0] = False
    _mask_ui["serialization_masked_count"] = len(_masked)
    _mask_ui["serialization_masked_samples"] = _masked[:5]
    # single write (no further counting; the doc now carries the correct count)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2, default=_json_default)
    print(f"Wrote {args.out}  (sections: {', '.join(doc.keys())})"
          + (f"; {len(_masked)} value(s) stringified (see __meta__.serialization_masked_count)"
             if _masked else ""))


if __name__ == "__main__":
    main()
