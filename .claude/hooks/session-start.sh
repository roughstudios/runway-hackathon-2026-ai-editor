#!/bin/bash
set -euo pipefail

# Only run in Claude Code remote (web) sessions
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

# ── Remote Desktop ────────────────────────────────────────────────────────────
# Install a lightweight window manager if not present
if ! command -v fluxbox &>/dev/null; then
  apt-get install -y -q fluxbox xterm >/dev/null 2>&1
fi

# Start a virtual display (Xvfb) on :99 if not already running
DISPLAY_NUM=99
if ! pgrep -f "Xvfb :${DISPLAY_NUM}" >/dev/null 2>&1; then
  Xvfb ":${DISPLAY_NUM}" -screen 0 1280x800x24 -ac &
  sleep 1
fi

export DISPLAY=":${DISPLAY_NUM}"

# Start fluxbox window manager if not already running
if ! DISPLAY=":${DISPLAY_NUM}" pgrep -f fluxbox >/dev/null 2>&1; then
  DISPLAY=":${DISPLAY_NUM}" fluxbox &>/dev/null &
  sleep 1
fi

# Persist DISPLAY for the session
echo "export DISPLAY=:${DISPLAY_NUM}" >> "${CLAUDE_ENV_FILE:-/dev/null}"

# ── Python Dependencies ───────────────────────────────────────────────────────
cd "${CLAUDE_PROJECT_DIR}"

pip install -e ".[dev]" -q --disable-pip-version-check
