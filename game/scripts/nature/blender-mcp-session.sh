#!/bin/sh
# Run one headless Claude Code session whose only tools are the Blender MCP tools (mcp__blender__*), against a
# running Blender with the MCP add-on (mcp-for-blender) listening on localhost:9876. The MCP client config lives
# outside the repository (user/local scope only), by default next to the raw sources:
#   {"mcpServers":{"blender":{"command":"uvx","args":["mcp-for-blender@2.2.6"],
#     "env":{"DISABLE_TELEMETRY":"true","BLENDER_HOST":"localhost","BLENDER_PORT":"9876"}}}}
# Usage: scripts/nature/blender-mcp-session.sh scripts/nature/fence-kit.prompt.md OUTPUT.txt
set -eu
config=${BLENDER_MCP_CONFIG:-$HOME/turku3d/data/assets-src/nature/blender/session/mcp.json}
prompt=$(cat "$1")
out="$(cd "$(dirname "$2")" && pwd)/$(basename "$2")"
cd "$(dirname "$config")"
timeout 2400 claude -p "$prompt" --mcp-config "$config" --strict-mcp-config \
  --allowedTools "mcp__blender__*" --model opus --output-format text > "$out" 2>&1
