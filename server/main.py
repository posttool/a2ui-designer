"""Design Constitution Builder server entrypoint.

cd google3/experimental/users/dkaram/design_constitution_builder
python3 -B -m server.main --port 8810 --host 0.0.0.0
"""

from __future__ import annotations

import argparse
import os
import secrets
import signal
import socket
import ssl
import subprocess
import sys
import threading

PKG = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PKG not in sys.path:
  sys.path.insert(0, PKG)

from core.service import Service, load_admins  # pylint: disable=g-import-not-at-top,wrong-import-position
from core.storage import Store  # pylint: disable=g-import-not-at-top,wrong-import-position
from llm.gemini import GeminiClient  # pylint: disable=g-import-not-at-top,wrong-import-position
from render.android_worker import AndroidRenderWorker  # pylint: disable=g-import-not-at-top,wrong-import-position
from render.browser_pool import BrowserPool  # pylint: disable=g-import-not-at-top,wrong-import-position
from render.cloudsims_runner import CloudSimsIosRunner  # pylint: disable=g-import-not-at-top,wrong-import-position
from render.ios_runner import IosSimulatorRunner  # pylint: disable=g-import-not-at-top,wrong-import-position
from server.app import App  # pylint: disable=g-import-not-at-top,wrong-import-position
from server.http import make_server  # pylint: disable=g-import-not-at-top,wrong-import-position
from sim.runs import RunManager  # pylint: disable=g-import-not-at-top,wrong-import-position


def load_secret(data_dir: str) -> bytes:
  p = os.path.join(data_dir, "secret.key")
  if not os.path.exists(p):
    os.makedirs(data_dir, exist_ok=True)
    with open(p, "w", encoding="utf-8") as fh:
      fh.write(secrets.token_hex(32))
    os.chmod(p, 0o600)
  with open(p, encoding="utf-8") as fh:
    return fh.read().strip().encode()


def build(
    args: argparse.Namespace,
    llm=None,
    pool=None,
    android_worker=None,
    ios_runner=None,
):
  store = Store(args.data_dir)
  secret = load_secret(args.data_dir)
  llm = llm or GeminiClient(default_model=args.model)
  svc = Service(store, llm, load_admins(args.admins))
  if pool is None and not args.no_browser:
    pool = BrowserPool(tabs=args.tabs)
    if android_worker is None:
      android_worker = AndroidRenderWorker()
      android_worker.warm_up_async()
    if ios_runner is None:
      cold = IosSimulatorRunner()
      if getattr(args, "ios_mode", "cloudsims") == "cloudsims":
        ios_runner = CloudSimsIosRunner(args.data_dir, fallback=cold)
        ios_runner.warm_up_async()
      else:
        ios_runner = cold
  base = args.render_base_url or f"http://127.0.0.1:{args.port}"
  runs = RunManager(
      svc,
      pool,
      base,
      secret,
      workers=args.workers,
      android_worker=android_worker,
      ios_runner=ios_runner,
  )
  runs.recover()
  return (
      App(
          svc,
          runs,
          secret,
          auth_mode=args.auth_mode,
          bug_component_id=getattr(args, "bug_component_id", None),
          issues_cli=getattr(args, "issues_cli", None),
      ),
      pool,
  )


def parse_args(argv=None) -> argparse.Namespace:
  ap = argparse.ArgumentParser(description=__doc__)
  ap.add_argument("--port", type=int, default=8810)
  ap.add_argument("--host", default="0.0.0.0")
  ap.add_argument(
      "--data_dir", default=os.path.expanduser("~/.design_constitution_builder")
  )
  ap.add_argument("--admins", default=os.path.join(PKG, "admins.json"))
  ap.add_argument("--auth_mode", choices=["dev", "iap"], default="dev")
  ap.add_argument("--model", default="gemini-3.8-flash")
  ap.add_argument("--workers", type=int, default=8)
  ap.add_argument("--tabs", type=int, default=3)
  ap.add_argument(
      "--no_browser",
      action="store_true",
      help="Skip screenshots (no headless Chrome / Android / iOS renderers).",
  )
  ap.add_argument(
      "--render_base_url",
      default="",
      help="Base URL headless Chrome uses to reach this server.",
  )
  ap.add_argument(
      "--https",
      action="store_true",
      help=(
          "Serve over TLS with a self-signed certificate (generated once into"
          " <data_dir>/tls/). Browsers only enable the microphone (voice in"
          " teaching sessions) on https:// or localhost."
      ),
  )
  ap.add_argument(
      "--ios_mode",
      choices=["cloudsims", "blaze"],
      default="cloudsims",
      help=(
          "iOS SwiftUI renderer: warm CloudSims simulator (falls back to blaze)"
          " or cold per-batch Forge-on-Mac blaze test."
      ),
  )
  ap.add_argument(
      "--bug_component_id",
      type=int,
      default=None,
      help=(
          "Buganizer component ID for 'Report a bug' (defaults to"
          " DCB_BUG_COMPONENT_ID or 1629228 AIR > Gen UX)."
      ),
  )
  ap.add_argument(
      "--issues_cli",
      default=None,
      help="Path to the Buganizer issues CLI binary.",
  )
  return ap.parse_args(argv)


def ensure_tls_cert(data_dir: str) -> tuple[str, str]:
  """Self-signed cert for --https (openssl CLI, generated once)."""
  d = os.path.join(data_dir, "tls")
  crt, key = os.path.join(d, "cert.pem"), os.path.join(d, "key.pem")
  if not (os.path.exists(crt) and os.path.exists(key)):
    os.makedirs(d, exist_ok=True)
    host = socket.getfqdn() or "localhost"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-keyout",
            key,
            "-out",
            crt,
            "-days",
            "825",
            "-subj",
            f"/CN={host}",
            "-addext",
            f"subjectAltName=DNS:{host},DNS:localhost,IP:127.0.0.1",
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    os.chmod(key, 0o600)
  return crt, key


def main(argv=None) -> None:
  args = parse_args(argv)
  app, pool = build(args)
  srv = make_server(app, args.host, args.port)
  scheme = "http"
  if args.https:
    crt, key = ensure_tls_cert(args.data_dir)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(crt, key)
    srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    scheme = "https"
    # Headless Chrome renders through this server too; it is started with
    # --ignore-certificate-errors.
    if not args.render_base_url:
      app.runs.base_url = f"https://127.0.0.1:{args.port}"
  print(
      f"Design Constitution Builder on {scheme}://{args.host}:{args.port}/ "
      f"(data: {args.data_dir})",
      flush=True,
  )

  def stop(*_):
    threading.Thread(target=srv.shutdown, daemon=True).start()

  signal.signal(signal.SIGTERM, stop)
  try:
    srv.serve_forever()
  except KeyboardInterrupt:
    pass
  finally:
    if pool:
      pool.close()
    if app.runs.android_worker:
      app.runs.android_worker.close()
    if hasattr(app.runs.ios_runner, "close"):
      app.runs.ios_runner.close()


if __name__ == "__main__":
  main()
