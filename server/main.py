"""Design Constitution Builder server entrypoint.

From the repository root:
python3 -B -m server.main --port 8100 --host 0.0.0.0
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
from render.browser_pool import BrowserPool  # pylint: disable=g-import-not-at-top,wrong-import-position
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


def load_password(password_file: str | None) -> str:
  """Shared secret for --auth_mode password: env A2UID_PASSWORD or a file."""
  pw = os.environ.get("A2UID_PASSWORD", "")
  if password_file:
    with open(os.path.expanduser(password_file), encoding="utf-8") as fh:
      pw = fh.read().strip()
  if not pw:
    raise ValueError(
        "--auth_mode password needs --password_file or $A2UID_PASSWORD"
    )
  return pw


def build(
    args: argparse.Namespace,
    llm=None,
    pool=None,
):
  store = Store(args.data_dir)
  secret = load_secret(args.data_dir)
  llm = llm or GeminiClient(default_model=args.model)
  auth_mode = getattr(args, "auth_mode", "local")
  svc = Service(store, llm, load_admins(getattr(args, "admins", None)))
  if pool is None and not args.no_browser:
    pool = BrowserPool(tabs=args.tabs)
  base = args.render_base_url or f"http://127.0.0.1:{args.port}"
  runs = RunManager(
      svc,
      pool,
      base,
      secret,
      workers=args.workers,
  )
  runs.recover()
  return (
      App(
          svc,
          runs,
          secret,
          auth_mode=auth_mode,
          password=load_password(getattr(args, "password_file", None))
          if auth_mode == "password"
          else None,
      ),
      pool,
  )


def parse_args(argv=None) -> argparse.Namespace:
  ap = argparse.ArgumentParser(description=__doc__)
  ap.add_argument("--port", type=int, default=8100)
  ap.add_argument("--host", default="0.0.0.0")
  ap.add_argument(
      "--data_dir", default=os.path.expanduser("~/.a2ui_designer")
  )
  ap.add_argument(
      "--admins",
      default=None,
      help=(
          "Optional path to a JSON list of admin usernames (password mode)."
          " In local mode the implicit user is always an admin."
      ),
  )
  ap.add_argument(
      "--auth_mode",
      choices=["local", "password"],
      default="local",
      help=(
          "local: no login, a single implicit admin user 'local'. password:"
          " shared-secret login (--password_file or $A2UID_PASSWORD)."
      ),
  )
  ap.add_argument(
      "--password_file",
      default=None,
      help="File holding the shared password for --auth_mode password.",
  )
  ap.add_argument("--model", default="gemini-3.8-flash")
  ap.add_argument("--workers", type=int, default=8)
  ap.add_argument("--tabs", type=int, default=3)
  ap.add_argument(
      "--no_browser",
      action="store_true",
      help="Skip screenshots (no headless Chrome).",
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
  args = ap.parse_args(argv)
  if args.auth_mode == "password":
    try:
      load_password(args.password_file)
    except ValueError as e:
      ap.error(str(e))
  return args


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


if __name__ == "__main__":
  main()
