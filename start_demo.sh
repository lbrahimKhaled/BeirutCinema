#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "$0")"
if [[ ! -x .venv/bin/python ]]; then
  python3 -m venv .venv
fi
if ! .venv/bin/python -c 'import fastmcp, requests, bs4, playwright' 2>/dev/null; then
  .venv/bin/python -m pip install -r requirements.txt
fi
if [[ "${1:-}" == chat ]] && ! .venv/bin/python -c 'import agents, litellm, dotenv' 2>/dev/null; then
  .venv/bin/python -m pip install -r requirements-agent.txt
fi
exec .venv/bin/python client.py "$@"
