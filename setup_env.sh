#!/usr/bin/env bash
#
# Create the tool's Python virtual environment and install dependencies.
# Tries the system Python first (3.14 on this machine); if PySide6 has no wheel
# for it, falls back to Python 3.13.
#
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"
VENV="$HERE/.venv"

pick_python() {
  for py in python3.14 python3.13 python3.12 python3.11 python3; do
    command -v "$py" >/dev/null 2>&1 && { echo "$py"; return; }
  done
  echo "python3"
}

create_and_install() {
  local py="$1"
  echo ">> creating venv with $py"
  rm -rf "$VENV"
  "$py" -m venv "$VENV"
  # shellcheck disable=SC1091
  source "$VENV/bin/activate"
  python -m pip install -U pip wheel >/dev/null
  python -m pip install -e ".[gui,analysis,dev]"
}

PY="$(pick_python)"
if create_and_install "$PY"; then
  echo ">> environment ready ($PY)"
else
  echo ">> install failed on $PY (likely no PySide6 wheel); falling back to python3.13"
  if command -v python3.13 >/dev/null 2>&1; then
    create_and_install python3.13
    echo ">> environment ready (python3.13 fallback)"
  else
    echo "!! python3.13 not found; install it (brew install python@3.13) and re-run"
    exit 1
  fi
fi

echo
echo "Activate with:  source $VENV/bin/activate"
echo "Then try:       tsntool env   |   tsntool list   |   tsntool gui"
