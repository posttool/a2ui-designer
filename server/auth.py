"""Sign-in helpers.

`--auth_mode local` (default): no sign-in; every request is the implicit admin
user `local`.

`--auth_mode password`: a shared secret (`--password_file` or
`$A2UID_PASSWORD`) unlocks the app; the optional username is used as the
display/owner name and stored in an HMAC-signed session cookie.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time

COOKIE = "dcb_session"
TTL = 30 * 24 * 3600
LOCAL_USER = "local"
DEFAULT_PASSWORD_USER = "user"


def _candidate_secrets(secret: bytes) -> list[bytes]:
  lines = [s.strip() for s in secret.splitlines() if s.strip()]
  return lines or [secret]


def sign(secret: bytes, ldap: str) -> str:
  primary = _candidate_secrets(secret)[0]
  exp = str(int(time.time()) + TTL)
  payload = f"{ldap}|{exp}"
  mac = hmac.new(primary, payload.encode(), hashlib.sha256).hexdigest()[:32]
  return (
      base64.urlsafe_b64encode(f"{payload}|{mac}".encode()).decode().rstrip("=")
  )


def verify(secret: bytes, token: str | None) -> str | None:
  if not token:
    return None
  try:
    pad = "=" * (-len(token) % 4)
    ldap, exp, mac = base64.urlsafe_b64decode(token + pad).decode().split("|")
    exp_ts = int(exp)
  except (ValueError, UnicodeDecodeError):
    return None
  if exp_ts < time.time():
    return None
  payload = f"{ldap}|{exp}".encode()
  for cand in _candidate_secrets(secret):
    want = hmac.new(cand, payload, hashlib.sha256).hexdigest()[:32]
    if hmac.compare_digest(want, mac):
      return ldap
  return None


def cookie_header(token: str, clear: bool = False) -> str:
  if clear:
    return f"{COOKIE}=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax"
  return f"{COOKIE}={token}; Path=/; Max-Age={TTL}; HttpOnly; SameSite=Lax"


def check_password(expected: str | None, given: str) -> bool:
  if not expected:
    return False
  return hmac.compare_digest(expected.encode(), (given or "").encode())
