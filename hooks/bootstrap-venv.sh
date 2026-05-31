#!/usr/bin/env bash
# Bootstrap the plugin's Python venv from pyproject.toml.
#
# Invocation contract (set by Claude Code plugin system):
#   CLAUDE_PLUGIN_ROOT  — absolute path to this plugin's install directory
#   CLAUDE_PLUGIN_DATA  — per-user data directory for this plugin
#
# Algorithm:
#   1. Compute a manifest fingerprint from pyproject.toml.
#   2. Compare against the last-seen fingerprint in CLAUDE_PLUGIN_DATA.
#   3. If unchanged → skip (fast path, no output).
#   4. If changed (or absent) → recreate the venv with uv, install deps,
#      save the new fingerprint.
#
# Requires: uv (https://github.com/astral-sh/uv) on PATH.
# Falls back gracefully if uv is not available (prints a warning, exits 0).

set -euo pipefail

PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT}"
PLUGIN_DATA="${CLAUDE_PLUGIN_DATA}"
MANIFEST="${PLUGIN_ROOT}/pyproject.toml"
STAMP_FILE="${PLUGIN_DATA}/.venv-manifest-hash"
VENV_DIR="${PLUGIN_DATA}/venv"

# ------------------------------------------------------------------
# Guard: require uv
# ------------------------------------------------------------------
if ! command -v uv >/dev/null 2>&1; then
    echo "[claude-github-tools] WARNING: 'uv' not found on PATH." \
         "Skipping venv bootstrap." >&2
    exit 0
fi

# ------------------------------------------------------------------
# Guard: require pyproject.toml
# ------------------------------------------------------------------
if [ ! -f "${MANIFEST}" ]; then
    echo "[claude-github-tools] WARNING: pyproject.toml not found at" \
         "${MANIFEST}. Skipping venv bootstrap." >&2
    exit 0
fi

# ------------------------------------------------------------------
# Compute fingerprint of the manifest
# ------------------------------------------------------------------
# Use Python's hashlib rather than platform-specific sha256sum so this
# works on both macOS (shasum) and Linux (sha256sum) and Git Bash on
# Windows (no sha256sum by default).
CURRENT_HASH=$(python -c "
import hashlib, sys
data = open(sys.argv[1], 'rb').read()
print(hashlib.sha256(data).hexdigest())
" "${MANIFEST}" 2>/dev/null) || {
    echo "[claude-github-tools] WARNING: Could not hash ${MANIFEST}." \
         "Skipping venv bootstrap." >&2
    exit 0
}

# ------------------------------------------------------------------
# Fast path: fingerprint unchanged → venv is current
# ------------------------------------------------------------------
if [ -f "${STAMP_FILE}" ] && [ "$(cat "${STAMP_FILE}")" = "${CURRENT_HASH}" ]; then
    # Venv is up to date; nothing to do.
    exit 0
fi

# ------------------------------------------------------------------
# Slow path: create/recreate the venv and install deps
# ------------------------------------------------------------------
echo "[claude-github-tools] Bootstrapping Python venv in ${VENV_DIR} ..."

# Ensure the data directory exists.
mkdir -p "${PLUGIN_DATA}"

# Remove any stale venv so we get a clean install.
if [ -d "${VENV_DIR}" ]; then
    rm -rf "${VENV_DIR}"
fi

# Create the venv.
uv venv "${VENV_DIR}"

# Install the plugin package and its dev extras so scripts can be run.
# --no-cache keeps the install hermetic (avoids stale wheel cache issues).
uv pip install \
    --python "${VENV_DIR}" \
    --no-cache \
    -e "${PLUGIN_ROOT}[dev]"

# Persist the new fingerprint so the next session skips this step.
printf '%s' "${CURRENT_HASH}" > "${STAMP_FILE}"

echo "[claude-github-tools] Venv bootstrap complete."
