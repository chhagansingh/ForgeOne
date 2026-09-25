#!/usr/bin/env bash
# FORGE-003 — headless protected K2 smoke launcher
#
# Runs from a PLAIN macOS Terminal, independently of any IDE.
# It never starts an unprotected llama-server: all model access goes through
# the ForgeOne ProtectedGateway and Resource Controller.
#
#   ./scripts/run_k2_headless_smoke.sh --check      preflight only, no weights
#   ./scripts/run_k2_headless_smoke.sh --execute    ONE bounded session
#
# No automatic retry. No scheduled retry. The watchdog is a separate process.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
FORGEONE_HOME="$(cd "$SCRIPT_DIR/.." && pwd -P)"
export FORGEONE_HOME

# shellcheck source=forgeone-env.sh
. "$FORGEONE_HOME/scripts/forgeone-env.sh"

PY="$FORGEONE_STORAGE/bakeoff/model-venv/bin/python"
SESSION="$FORGEONE_HOME/scripts/k2_headless_session.py"

MODE="${1:-}"
case "$MODE" in
  --check|--execute) ;;
  *) echo "usage: $0 {--check|--execute}" >&2; exit 64 ;;
esac

# --- storage containment: every path below must resolve inside storage/ -----
check_inside() {
  local p="$1"
  local rp root
  rp="$(cd "$(dirname "$p")" 2>/dev/null && pwd -P)/$(basename "$p")"
  root="$(cd "$FORGEONE_STORAGE" && pwd -P)"
  case "$rp" in "$root"/*) return 0 ;; *) echo "BLOCKED: $rp escapes $root" >&2; return 1 ;; esac
}
check_inside "$PY" || exit 2
check_inside "$FORGEONE_STORAGE/runtimes/k2-llama/build/bin/llama-server" || exit 2

echo "ForgeOne headless K2 launcher"
echo "  FORGEONE_HOME : $FORGEONE_HOME"
echo "  mode          : $MODE"
echo "  python        : $PY"
echo

if [ ! -x "$PY" ]; then echo "BLOCKED: python missing at $PY" >&2; exit 2; fi
if [ ! -f "$SESSION" ]; then echo "BLOCKED: session script missing" >&2; exit 2; fi

export PYTHONPATH="$FORGEONE_HOME${PYTHONPATH:+:$PYTHONPATH}"
exec "$PY" -u "$SESSION" "$MODE"
