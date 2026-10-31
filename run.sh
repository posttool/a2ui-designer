#!/bin/bash
# Runs the Design Constitution Builder server (stdlib Python 3.13).
#   ./run.sh                      # port 8810, data in ~/.design_constitution_builder
#   ./run.sh --port 8811 --data_dir /tmp/dcb --no_browser
#   ./run.sh --https              # TLS (self-signed) so the browser allows the
#                                 # microphone for voice in teaching sessions
cd "$(dirname "$0")" || exit 1
exec python3 -B -m server.main "$@"
