import http.server
import json
import os
import shutil
import socket
import tempfile
import threading
import time
import unittest
import zipfile

from core.a2ui import validator
from exports import package
from render import cdp


class PackageStandaloneTest(unittest.TestCase):
  """Builds a package, unzips it, serves it via HTTP, and tests demo.html."""

  def setUp(self):
    self.temp_dir = tempfile.mkdtemp(prefix="a2ui_pkg_test_")
    self.addCleanup(shutil.rmtree, self.temp_dir)
    
    # We need a fixture project with web_template component, a composition, template, tokens, asset
    # To keep it simple, we can just mock the parts that `package_files` needs,
    # or create a dummy store/svc with minimal data.
    # Actually, we can use a mock store and svc.
    self.pid = "test-pkg"
    
    class MockStore:
      def __init__(self, d): self.d = d
      def query(self, sql, params):
        if "custom_components" in sql: return self.d.get("comps", [])
        if "templates" in sql: return self.d.get("templates", [])
        if "guidelines" in sql: return self.d.get("guidelines", [])
        if "imagery_styles" in sql: return self.d.get("imagery_styles", [])
        return []
      def file_path(self, *parts):
        return os.path.join(self.d["basedir"], *parts)

    from core.a2ui import catalog as catalog_mod
    class MockSvc:
      def __init__(self, cat): self.cat = cat
      def catalog_for(self, p): return self.cat
      def current_revision(self, pid): return {"number": 1, "text_md": "const"}

    self.basedir = tempfile.mkdtemp(prefix="a2ui_pkg_assets_")
    self.addCleanup(shutil.rmtree, self.basedir)
    os.makedirs(os.path.join(self.basedir, "gen"))
    with open(os.path.join(self.basedir, "gen", "test_asset.png"), "wb") as f:
      f.write(b"fake_png")

    self.store = MockStore({
        "basedir": self.basedir,
        "comps": [
            {
                "id": "c1", "project_id": self.pid, "name": "WebComp", "status": "active",
                "classification": "custom",
                "definition": {
                    "name": "WebComp",
                    "description": "A web component.",
                    "props": {"properties": {"title": {"type": "string"}}},
                    "web": {"template": "<div>{{title}} <img src=\"/api/gen/test_asset.png\"></div>", "css": "div { color: red; }"},
                    "sample": {"props": {"title": "Hello"}}
                }
            },
            {
                "id": "c2", "project_id": self.pid, "name": "CompComp", "status": "active",
                "classification": "custom",
                "definition": {
                    "name": "CompComp",
                    "description": "A composition component.",
                    "props": {"properties": {"title": {"type": "string"}}},
                    "composition": {"kind": "RECTANGLE", "name": "r1"},
                    "web": {"template": "<div>{{title}}</div>", "css": ""},
                    "sample": {"props": {"title": "World"}}
                }
            }
        ],
        "templates": [
            {"id": "t1", "project_id": self.pid, "name": "MyTemplate", "status": "active"}
        ],
        "imagery_styles": []
    })
    self.svc = MockSvc(catalog_mod.build_catalog({
        "catalog_id": "test-cat-id",
        "custom_components": {
            "WebComp": self.store.d["comps"][0]["definition"],
            "CompComp": self.store.d["comps"][1]["definition"],
        }
    }))
    self.project = {
        "id": self.pid,
        "name": "Test Project",
        "settings": {
            "theme": {"primary": "#ff0000"}
        }
    }

  def _free_port(self):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
      s.bind(('', 0))
      return s.getsockname()[1]

  def test_package_standalone(self):
    # 1. Build package
    filename, zip_bytes = package.package_zip(self.store, self.svc, self.project)
    
    # 2. Unzip to temp dir
    zip_path = os.path.join(self.temp_dir, filename)
    with open(zip_path, "wb") as f:
      f.write(zip_bytes)
    
    extract_dir = os.path.join(self.temp_dir, "extracted")
    with zipfile.ZipFile(zip_path, "r") as z:
      z.extractall(extract_dir)
    
    # The root dir is the only folder in extract_dir
    root_dir = os.path.join(extract_dir, filename.replace(".zip", ""))
    
    # 3. Assert no string matches in any text file
    import re
    bad_regex = re.compile(r"/api/|/gen/(img|svg)|/img/[a-z0-9-]+/\d+x\d+|dcb\.local|_ldap|localhost|127\.0\.0\.1")
    for root, _, files in os.walk(root_dir):
      for name in files:
        if name.endswith((".json", ".html", ".md", ".css", ".txt")):
          path = os.path.join(root, name)
          with open(path, "r", encoding="utf-8") as f:
            content = f.read()
            m = bad_regex.search(content)
            if m:
              self.fail(f"Found forbidden string '{m.group(0)}' in {path}")
              
    # 4. Validate every examples/*.json
    examples_dir = os.path.join(root_dir, "catalog", "examples")
    if os.path.exists(examples_dir):
      for name in os.listdir(examples_dir):
        if name.endswith(".json"):
          with open(os.path.join(examples_dir, name)) as f:
            ex = json.load(f)
            self.assertTrue(ex.get("valid"), f"Example {name} is not valid: {ex}")
            # You could also call validator here, but it's already validated in package.py
            
    # 5. Serve with http.server
    port = self._free_port()
    handler = http.server.SimpleHTTPRequestHandler
    httpd = http.server.HTTPServer(('127.0.0.1', port), handler)
    
    # Override directory for handler
    def serve():
      # Change working directory for the thread so simple HTTP server serves from root_dir
      os.chdir(root_dir)
      httpd.serve_forever()
      
    t = threading.Thread(target=serve, daemon=True)
    t.start()
    
    # Let server start
    time.sleep(1)
    
    # 6. Load demo.html in headless Chrome
    browser = None
    try:
      browser = cdp.Browser(width=1200, height=800)
      url = f"http://127.0.0.1:{port}/demo.html"
      browser.page.navigate(url)
      
      # wait for __demo.done == true
      for _ in range(50):
        done = browser.page.evaluate("window.__demo && window.__demo.done")
        if done:
          break
        time.sleep(0.1)
        
      self.assertTrue(browser.page.evaluate("window.__demo.done"), "Demo did not finish loading")
      
      results = browser.page.evaluate("window.__demo.results")
      self.assertIsNotNone(results)
      self.assertTrue(len(results) > 0, "No results in demo")
      for res in results:
        meta = res.get("meta") or {}
        errors = meta.get("errors") or []
        self.assertEqual(len(errors), 0, f"Example {res['name']} mounted with errors: {errors}")
        
      # Screenshot it
      screenshot_path = os.path.join(self.temp_dir, "demo_http.png")
      browser.page.screenshot(screenshot_path)
      print(f"HTTP Screenshot saved to {screenshot_path}")

      # 7. Load demo.html via file://
      file_url = f"file://{os.path.abspath(os.path.join(root_dir, 'demo.html'))}"
      browser.page.navigate(file_url)
      
      for _ in range(50):
        done = browser.page.evaluate("window.__demo && window.__demo.done")
        if done:
          break
        time.sleep(0.1)
        
      self.assertTrue(browser.page.evaluate("window.__demo.done"), "File Demo did not finish loading")
      
      mode = browser.page.evaluate("window.__demo.mode")
      if mode != "offline":
          error = browser.page.evaluate("window.__demo.error")
          self.fail(f"Demo did not fall back to offline loader: mode={mode}, error={error}")
      
      results_file = browser.page.evaluate("window.__demo.results")
      self.assertTrue(len(results_file) > 0, "No results in file demo")
      for res in results_file:
        meta = res.get("meta") or {}
        errors = meta.get("errors") or []
        self.assertEqual(len(errors), 0, f"File Example {res['name']} mounted with errors: {errors}")

      file_screenshot_path = os.path.join(self.temp_dir, "demo_file.png")
      browser.page.screenshot(file_screenshot_path)
      print(f"File Screenshot saved to {file_screenshot_path}")
      
    finally:
      if browser:
        browser.close()
      httpd.shutdown()
      httpd.server_close()
      t.join(timeout=2)


if __name__ == "__main__":
  unittest.main()
