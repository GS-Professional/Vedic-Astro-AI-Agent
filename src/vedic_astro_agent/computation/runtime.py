"""One-time PyJHora runtime preparation: language resources, no-network flags, ephemeris.

PyJHora reads localisation resource strings that stay empty until ``utils.set_language``
runs, and its swisseph path is bound per thread. Skipping any of this turns into obscure
KeyErrors or wildly wrong longitudes later, so every computation funnels through
``initialize()`` + ``bind_thread()`` below.
"""
from __future__ import annotations

import os
import threading

_init_lock = threading.Lock()
_initialised = False
_thread_state = threading.local()


class ComputationRuntimeError(RuntimeError):
    """The computation layer cannot run (missing ephemeris, bad install...)."""


class EphemerisMissingError(ComputationRuntimeError):
    """Swiss Ephemeris *.se1 files are absent."""


def _ephe_dir() -> str:
    """Directory holding the Swiss Ephemeris files.

    ``VEDIC_EPHE_DIR`` overrides the packaged location so deployments can keep the
    ~105MB of data outside the venv (see setup_ephe.sh).
    """
    override = os.environ.get("VEDIC_EPHE_DIR")
    if override:
        return os.path.abspath(override)
    from jhora import const

    return const._ephe_path


def check_ephemeris() -> None:
    """Fail early, with an actionable message, if the *.se1 files are missing.

    The PyJHora wheel does not include them, so a fresh install has an empty ephe
    directory; without this check every tool fails separately with an opaque error.
    """
    ephe_dir = _ephe_dir()
    has_se1 = os.path.isdir(ephe_dir) and any(
        f.endswith(".se1") for f in os.listdir(ephe_dir)
    )
    if not has_se1:
        raise EphemerisMissingError(
            f"Swiss Ephemeris data files (*.se1) not found in {ephe_dir!r}.\n"
            "They are not included in the PyJHora wheel. Run ./setup_ephe.sh in the "
            "repository root (or set VEDIC_EPHE_DIR to a directory containing them)."
        )


def initialize() -> None:
    """Prepare the PyJHora runtime. Idempotent; safe to call from any service."""
    global _initialised
    if not _initialised:
        with _init_lock:
            if not _initialised:
                # jhora prints "added to system path" chatter from some module imports;
                # an agent's output must stay clean, so the first import is muted.
                import contextlib
                import io

                with contextlib.redirect_stdout(io.StringIO()):
                    from jhora import const, utils

                    utils.set_language(const._DEFAULT_LANGUAGE)
                    utils.get_resource_lists()

                    # Pre-import every jhora submodule this package touches. Their
                    # __init__ files print "added to system path" chatter on first import;
                    # doing it here (muted) keeps later lazy imports silent cache hits.
                    import jhora.horoscope.chart.ashtakavarga  # noqa: F401
                    import jhora.horoscope.chart.charts  # noqa: F401
                    import jhora.horoscope.chart.dosha  # noqa: F401
                    import jhora.horoscope.chart.raja_yoga  # noqa: F401
                    import jhora.horoscope.chart.strength  # noqa: F401
                    import jhora.horoscope.chart.yoga  # noqa: F401
                    import jhora.horoscope.dhasa.graha.ashtottari  # noqa: F401
                    import jhora.horoscope.dhasa.graha.vimsottari  # noqa: F401
                    import jhora.horoscope.dhasa.graha.yogini  # noqa: F401
                    import jhora.horoscope.match.compatibility  # noqa: F401
                    import jhora.panchanga.drik  # noqa: F401

                # Callers always supply explicit coordinates; never let PyJHora do
                # network-backed geocoding or elevation lookups mid-computation.
                const.use_internet_for_location_check = False
                const.get_place_elevation_from_internet = False

                check_ephemeris()
                _initialised = True

    bind_thread()


def bind_thread() -> None:
    """Point swisseph at the ephemeris directory for the calling thread.

    pyswisseph keeps its state block per-thread, so the ``swe.set_ephe_path`` that
    ``jhora.const`` runs at import configures only the importing thread. Any worker
    thread must re-bind or it computes against the compiled-in default path — with no
    ephemeris that silently produces wrong longitudes.
    """
    if getattr(_thread_state, "bound", False):
        return

    import swisseph as swe

    swe.set_ephe_path(_ephe_dir())
    _thread_state.bound = True
