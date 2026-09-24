#!/usr/bin/env bash
# ForgeOne — canonical environment launcher
#
# Source this before any ForgeOne command that downloads, installs or runs
# tooling, so every child process resolves caches and models under
# $FORGEONE_HOME/storage/ instead of a global location.
#
#   source scripts/forgeone-env.sh
#
# Deliberately does NOT touch ~/.zshrc, ~/.bashrc, HOME, system Python, macOS
# swap or any unrelated application setting. It only sets variables for the
# current shell and its children.
#
# Usage in a script:
#   set -a; . scripts/forgeone-env.sh; set +a

# Resolve the repository root from this file's location (no personal paths).
FORGEONE_HOME="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/.." && pwd -P)"
export FORGEONE_HOME

FORGEONE_STORAGE="$FORGEONE_HOME/storage"
export FORGEONE_STORAGE

# --- Hugging Face ----------------------------------------------------------
export HF_HOME="$FORGEONE_STORAGE/cache/huggingface"
export HF_HUB_CACHE="$HF_HOME/hub"
export HF_XET_CACHE="$FORGEONE_STORAGE/cache/xet"
export HF_ASSETS_CACHE="$FORGEONE_STORAGE/cache/assets"

# --- uv --------------------------------------------------------------------
export UV_CACHE_DIR="$FORGEONE_STORAGE/cache/uv"
export UV_PYTHON_INSTALL_DIR="$FORGEONE_STORAGE/tools/python"
export UV_PYTHON_BIN_DIR="$FORGEONE_STORAGE/tools/bin"
export UV_TOOL_DIR="$FORGEONE_STORAGE/tools/uv-tools"

# --- pip -------------------------------------------------------------------
export PIP_CACHE_DIR="$FORGEONE_STORAGE/cache/pip"

# --- scratch ---------------------------------------------------------------
export TMPDIR="$FORGEONE_STORAGE/tmp"

# --- future milestone (NOT installed by this task) -------------------------
export XINFERENCE_HOME="$FORGEONE_STORAGE/runtimes/xinference"

# --- convenience -----------------------------------------------------------
export FORGEONE_UV="$FORGEONE_STORAGE/tools/uv/uv"
export FORGEONE_MLX_ENV="$FORGEONE_STORAGE/bakeoff/model-venv"
export FORGEONE_OPENHANDS_ENV="$FORGEONE_STORAGE/bakeoff/openhands-venv"

# Keep the directories present; harmless if they already exist.
mkdir -p "$HF_HUB_CACHE" "$HF_XET_CACHE" "$HF_ASSETS_CACHE" \
         "$UV_CACHE_DIR" "$PIP_CACHE_DIR" "$TMPDIR" \
         "$FORGEONE_STORAGE/models" "$FORGEONE_STORAGE/runs" \
         "$FORGEONE_STORAGE/logs" "$FORGEONE_STORAGE/secrets" 2>/dev/null || true

# A tiny helper so callers can confirm the policy took effect.
forgeone_env_report() {
  echo "FORGEONE_HOME            = $FORGEONE_HOME"
  echo "HF_HOME                  = $HF_HOME"
  echo "HF_HUB_CACHE             = $HF_HUB_CACHE"
  echo "UV_CACHE_DIR             = $UV_CACHE_DIR"
  echo "PIP_CACHE_DIR            = $PIP_CACHE_DIR"
  echo "TMPDIR                   = $TMPDIR"
  echo "XINFERENCE_HOME          = $XINFERENCE_HOME (not installed)"
}
