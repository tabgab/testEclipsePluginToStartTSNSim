#!/usr/bin/env bash
#
# Launch tsntool using its virtual environment. This is the entry point the
# OMNeT++ IDE "External Tools" configuration calls. Auto-detects OMNETPP_ROOT
# when the tool lives inside the OMNeT++ tree (i.e. <omnetpp>/tsn-tool/).
#
HERE="$(cd "$(dirname "$0")" && pwd)"
VENV="$HERE/.venv"

if [ -d "$VENV" ]; then
  # shellcheck disable=SC1091
  source "$VENV/bin/activate"
fi

if [ -z "$OMNETPP_ROOT" ] && [ -f "$HERE/../setenv" ] && [ -x "$HERE/../bin/opp_run" ]; then
  OMNETPP_ROOT="$(cd "$HERE/.." && pwd)"
  export OMNETPP_ROOT
fi

exec python -m tsntool "$@"
