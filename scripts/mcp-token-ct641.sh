#!/bin/bash
# Create (or replace) an API bearer token inside LXC 641 and print it ONCE. Only its sha256 is stored.
# Usage: scripts/mcp-token-ct641.sh [NAME] [mcp|user]      (default: hermes mcp)
# The service reads tokens at start-up: restart it afterwards (systemctl --user restart juicr.service in CT 641).
NAME="${1:-hermes}"; ROLE="${2:-mcp}"
echo "API token for '$NAME' (role $ROLE) - shown once; only its hash is stored in the CT:"
ssh -i ~/.ssh/proxispace_root root@10.138.6.1 \
  "pct exec 641 -- runuser -u juicr -- bash -c 'cd ~/app && JUICR_DATA=/home/juicr/.juicr ~/venv/bin/python -m juicr token $NAME $ROLE'"
echo
echo "MCP URL: http://10.138.6.41:8810/mcp   header: Authorization: Bearer <token above>"
