#!/bin/bash
# Runs the A2UI Designer server (stdlib Python >= 3.10).
#   ./run.sh                      # port 8100, data in ~/.a2ui_designer
#   ./run.sh --port 8101 --data_dir /tmp/a2uid --no_browser
#   ./run.sh --https              # TLS (self-signed) so the browser allows the
#                                 # microphone for voice in teaching sessions
cd "$(dirname "$0")" || exit 1
exec python3 -B -m server.main "$@"
