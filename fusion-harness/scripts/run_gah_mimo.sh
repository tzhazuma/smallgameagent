#!/bin/bash
# Run game-agent-harness with our mimo-v2.5 planner (harness_http adapter)
# instead of Codex — fair-comparison configuration. All else unchanged.
set -e
source /home/azuma/Downloads/smallgameagent/.env
export PLAYABLE_PLANNER_PROVIDER=harness_http
export PLAYABLE_PLANNER_ENDPOINT=http://127.0.0.1:9100/plan
export PLAYABLE_PLANNER_MODEL=mimo-v2.5
export NODE_USE_ENV_PROXY=1
cd /tmp/gah-latest
node ./src/cli.mjs autonomous --game-id "$1" --cognition-mode no_vlm_codex_cli
