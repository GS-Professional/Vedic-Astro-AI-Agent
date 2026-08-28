#!/bin/sh
# setup_ephe.sh — Download the Swiss Ephemeris data files PyJHora needs.
#
# The PyJHora wheel does not ship the ~105MB of .se1 ephemeris files, so without them
# every planetary position calculation fails. This script pulls just that directory
# from the PyJHora repository at the tag matching the pinned PyJHora version.
#
# The approach (sparse, blobless checkout of the ephe directory) is the same one used
# by https://github.com/chinmay-sh/pyjhora-mcp (setup-ephe.sh).
#
# Usage:
#   ./setup_ephe.sh                # downloads to ./ephe/
#   ./setup_ephe.sh /custom/path   # downloads to a custom path
#   PYJHORA_TAG=V4.9.x ./setup_ephe.sh   # pin a different PyJHora tag

set -eu

# Keep in step with the PyJHora pin in pyproject.toml.
PYJHORA_TAG="${PYJHORA_TAG:-V4.8.7}"
REPO_URL="https://github.com/naturalstupid/pyjhora"

# Resolve the target now: the script cd's into the temp checkout below, so a relative
# path must be made absolute first or the files land in the wrong place.
case "${1:-$(dirname "$0")/ephe}" in
    /*) EPHE_DIR="${1:-$(dirname "$0")/ephe}" ;;
    *)  EPHE_DIR="$(pwd)/${1:-$(dirname "$0")/ephe}" ;;
esac

# /var/tmp rather than /tmp: /tmp is often a size-limited tmpfs.
TMP_DIR=$(mktemp -d -p /var/tmp 2>/dev/null || mktemp -d)
cleanup() { rm -rf "$TMP_DIR"; }
trap cleanup EXIT

if ! command -v git >/dev/null 2>&1; then
    echo "Error: git is required. Debian/Ubuntu: apt-get install git" >&2
    exit 1
fi

echo ">>> Downloading PyJHora ephemeris data files ($PYJHORA_TAG)..."
echo "    Target directory: $EPHE_DIR"

git clone --no-checkout --depth 1 --filter=blob:none --branch "$PYJHORA_TAG" \
    "$REPO_URL.git" "$TMP_DIR/pyjhora"

cd "$TMP_DIR/pyjhora"
git sparse-checkout init --cone
git sparse-checkout set "src/jhora/data/ephe"
git checkout

SRC_DIR="$TMP_DIR/pyjhora/src/jhora/data/ephe"
ephe_count=$(find "$SRC_DIR" -maxdepth 1 -type f | wc -l)
if [ "$ephe_count" -eq 0 ]; then
    echo "Error: no ephemeris files were found in the downloaded repository." >&2
    exit 1
fi

mkdir -p "$EPHE_DIR"
cp "$SRC_DIR"/* "$EPHE_DIR/"

echo ""
echo ">>> Done. $(find "$EPHE_DIR" -maxdepth 1 -type f | wc -l) files in: $EPHE_DIR"
echo ""
echo "Next steps:"
echo "  If $EPHE_DIR is not the packaged jhora/data/ephe directory, export it:"
echo "    export VEDIC_EPHE_DIR=$EPHE_DIR"
echo "  Then try:  veda ask \"Give me an overview of my chart\" --dob 1996-12-07 --tob 10:30 --place \"Chennai, IN\""
