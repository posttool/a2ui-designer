#!/bin/bash
# Runs the A2UI Designer server (stdlib Python >= 3.10 + npm renderer build).
#   ./run.sh                      # port 8100, data in ~/.a2ui_designer
#   ./run.sh --port 8101 --data_dir /tmp/a2uid --no_browser
#   ./run.sh --https              # TLS (self-signed) so the browser allows the
#                                 # microphone for voice in teaching sessions
set -e
cd "$(dirname "$0")" || exit 1
if [ ! -d "node_modules/@a2ui/web_core" ] || [ ! -d "node_modules/@a2ui/lit" ]; then
  echo "Installing npm dependencies (@a2ui/web_core, @a2ui/lit, esbuild)..."
  npm install
fi
if [ ! -f "web/vendor/a2ui.bundle.js" ]; then
  echo "Building A2UI renderer bundle (web/vendor/a2ui.bundle.js)..."
  npm run build
fi
exec python3 -B -m server.main "$@"
