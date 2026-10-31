"""Dev-mode sign-in: LDAP typed by the user → HMAC-signed session cookie.

`--auth_mode iap` trusts the `X-Goog-Authenticated-User-Email` header set by
Identity-Aware Proxy (for a later Cloud Run deployment).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time

COOKIE = "dcb_session"
TTL = 30 * 24 * 3600


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


def iap_ldap(header_value: str | None) -> str | None:
  if not header_value:
    return None
  email = header_value.split(":", 1)[-1]
  if not email.endswith("@google.com"):
    return None
  return email.split("@", 1)[0].lower()
