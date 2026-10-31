"""Auth modes: `--auth_mode local` (default) and `--auth_mode password`."""

import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fixtures  # noqa: E402

PKG = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PKG not in sys.path:
  sys.path.insert(0, PKG)

from server import auth as auth_mod  # noqa: E402
from server import main as server_main  # noqa: E402


class LocalModeTest(unittest.TestCase):

  def setUp(self):
    self.s = fixtures.LiveServer(auth_mode="local")

  def tearDown(self):
    self.s.close()

  def test_no_login_needed_and_implicit_admin(self):
    st, body = self.s.call("GET", "/api/me")
    self.assertEqual(st, 200, body)
    self.assertEqual(body["auth_mode"], "local")
    self.assertTrue(body["version"])
    self.assertEqual(body["user"]["ldap"], auth_mod.LOCAL_USER)
    self.assertTrue(body["user"]["is_admin"])
    st, body = self.s.call("GET", "/api/projects")
    self.assertEqual(st, 200, body)
    st, body = self.s.call("GET", "/api/admin/overview")
    self.assertEqual(st, 200, body)
    self.assertNotIn("bugs", body["totals"])
    st, body = self.s.call(
        "POST", "/api/projects", {"name": "Local project"}
    )
    self.assertEqual(st, 201, body)
    self.assertEqual(body["project"]["owner_ldap"], auth_mod.LOCAL_USER)

  def test_login_endpoint_returns_local_user(self):
    st, body = self.s.call("POST", "/api/login", {"password": "whatever"})
    self.assertEqual(st, 200, body)
    self.assertEqual(body["user"]["ldap"], auth_mod.LOCAL_USER)

  def test_bug_routes_removed(self):
    st, _ = self.s.call("GET", "/api/bugs")
    self.assertIn(st, (404, 405))


class PasswordModeTest(unittest.TestCase):

  def setUp(self):
    self.s = fixtures.LiveServer(auth_mode="password", admins=("boss",))

  def tearDown(self):
    self.s.close()

  def test_requires_password(self):
    st, body = self.s.call("GET", "/api/me")
    self.assertEqual(st, 200)
    self.assertIsNone(body["user"])
    self.assertEqual(body["auth_mode"], "password")
    st, _ = self.s.call("GET", "/api/projects")
    self.assertEqual(st, 401)
    st, _ = self.s.call("POST", "/api/login", {"username": "alice"})
    self.assertEqual(st, 401)
    st, _ = self.s.login("alice", password="nope")
    self.assertEqual(st, 401)
    st, body = self.s.login("alice")
    self.assertEqual(st, 200, body)
    self.assertEqual(body["user"]["ldap"], "alice")
    self.assertFalse(body["user"]["is_admin"])
    st, _ = self.s.call("GET", "/api/projects")
    self.assertEqual(st, 200)
    st, _ = self.s.call("GET", "/api/admin/overview")
    self.assertEqual(st, 403)

  def test_username_optional_and_admins_file(self):
    st, body = self.s.call(
        "POST", "/api/login", {"password": fixtures.TEST_PASSWORD}
    )
    self.assertEqual(st, 200, body)
    self.assertEqual(body["user"]["ldap"], auth_mod.DEFAULT_PASSWORD_USER)
    st, body = self.s.login("boss")
    self.assertTrue(body["user"]["is_admin"])
    st, _ = self.s.call("POST", "/api/logout")
    self.assertEqual(st, 200)
    self.s.jar.clear()
    st, _ = self.s.call("GET", "/api/projects")
    self.assertEqual(st, 401)


class ArgsTest(unittest.TestCase):

  def test_defaults(self):
    with mock.patch.dict(os.environ, {}, clear=False):
      os.environ.pop("A2UID_PASSWORD", None)
      args = server_main.parse_args([])
    self.assertEqual(args.auth_mode, "local")
    self.assertEqual(args.port, 8100)
    self.assertIsNone(args.admins)
    self.assertTrue(args.data_dir.endswith(".a2ui_designer"))

  def test_password_mode_needs_secret(self):
    with mock.patch.dict(os.environ, {}, clear=False):
      os.environ.pop("A2UID_PASSWORD", None)
      with self.assertRaises(SystemExit), mock.patch("sys.stderr"):
        server_main.parse_args(["--auth_mode", "password"])

  def test_password_from_env_and_file(self):
    with mock.patch.dict(os.environ, {"A2UID_PASSWORD": "envpw"}):
      self.assertEqual(server_main.load_password(None), "envpw")
      server_main.parse_args(["--auth_mode", "password"])
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as fh:
      fh.write("filepw\n")
    try:
      with mock.patch.dict(os.environ, {"A2UID_PASSWORD": "envpw"}):
        self.assertEqual(server_main.load_password(fh.name), "filepw")
    finally:
      os.unlink(fh.name)

  def test_iap_mode_removed(self):
    with self.assertRaises(SystemExit), mock.patch("sys.stderr"):
      server_main.parse_args(["--auth_mode", "iap"])

  def test_check_password(self):
    self.assertTrue(auth_mod.check_password("s3cret", "s3cret"))
    self.assertFalse(auth_mod.check_password("s3cret", "S3cret"))
    self.assertFalse(auth_mod.check_password(None, ""))
    self.assertFalse(hasattr(auth_mod, "iap_ldap"))


if __name__ == "__main__":
  unittest.main()
