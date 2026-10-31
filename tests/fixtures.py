"""Shared test fixtures: scripted LLM handlers, fake browser, live server."""

from __future__ import annotations

import base64
import http.cookiejar
import json
import os
import shutil
import sys
import tempfile
import threading
import urllib.error
import urllib.request
import warnings

# Tests never reach Wikimedia Commons for sample photos.
os.environ["DCB_WEB_IMAGES"] = "0"

# Worker threads keep thread-local sqlite connections until exit.
warnings.filterwarnings("ignore", category=ResourceWarning)

PKG = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PKG not in sys.path:
  sys.path.insert(0, PKG)

from llm.scripted import ScriptedLlm  # noqa: E402
from server import main as server_main  # noqa: E402
from server.http import make_server  # noqa: E402

# 1×1 transparent PNG
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA"
    "60e6kgAAAABJRU5ErkJggg=="
)

GOOD_A2UI = {
    "messages": [
        {
            "version": "v0.9",
            "createSurface": {
                "surfaceId": "main",
                "catalogId": (
                    "https://a2ui.org/specification/v0_9/catalogs/basic/catalog.json"
                ),
            },
        },
        {
            "version": "v0.9",
            "updateComponents": {
                "surfaceId": "main",
                "components": [
                    {"id": "root", "component": "Card", "child": "col"},
                    {
                        "id": "col",
                        "component": "Column",
                        "children": ["t", "b"],
                    },
                    {
                        "id": "t",
                        "component": "Text",
                        "text": {"path": "/title"},
                        "variant": "h2",
                    },
                    {
                        "id": "b",
                        "component": "Button",
                        "child": "bl",
                        "action": {"event": {"name": "ok"}},
                    },
                    {"id": "bl", "component": "Text", "text": "OK"},
                ],
            },
        },
        {
            "version": "v0.9",
            "updateDataModel": {
                "surfaceId": "main",
                "path": "/",
                "value": {"title": "Hello"},
            },
        },
    ]
}

BAD_A2UI = {
    "messages": [{
        "version": "v0.9",
        "updateComponents": {
            "surfaceId": "main",
            "components": [
                {"id": "root", "component": "Carousel", "items": []}
            ],
        },
    }]
}

JUDGE = {
    "overall_score": 6,
    "verdict": "warn",
    "summary": "Okay.",
    "findings": [{
        "rule": "Lead with the decision",
        "status": "violated",
        "evidence": "Decision is last",
        "severity": "high",
    }],
    "strengths": ["clear"],
    "issues": ["buried action"],
    "suggested_constitution_changes": [
        "Put the decision the user must make first."
    ],
    "likely_unfixable_by_constitution": False,
    "unfixable_reason": "",
}


def synth_handler(system, parts):
  del system
  text = parts[0]
  n = int(text.split("Create exactly ")[1].split(" ")[0])
  brief = "Designer's brief:" in text
  return {
      "samples": [
          {
              **(
                  {
                      "category": "Household logistics",
                      "subcategory": "Dinner for guests",
                      "subcategory_description": "Hosting dinner at home.",
                  }
                  if brief
                  else {}
              ),
              "title": f"Sample {i}",
              "intent": "Check meds",
              "context": {
                  "surface": "card",
                  "user_state": "",
                  "role": "caregiver",
                  "locale": "en-US",
                  "time_of_day": "morning",
                  "urgency": "routine",
              },
              "size_class": "s",
              "state": "content",
              "edge_cases": [],
              "tags": ["t"],
              "payload_json": json.dumps(
                  {"title": f"Item {i}", "items": [1, 2]}
              ),
          }
          for i in range(n)
      ]
  }


def make_llm() -> ScriptedLlm:
  llm = ScriptedLlm()
  llm.handlers["generate"] = lambda s, p: GOOD_A2UI
  llm.handlers["repair"] = lambda s, p: GOOD_A2UI
  llm.handlers["judge"] = lambda s, p: JUDGE
  llm.handlers["analyze_import"] = lambda s, p: {
      "summary_md": "",
      "extractions": [],
      "questions": [],
  }
  llm.handlers["synthesize"] = synth_handler
  llm.handlers["revise"] = lambda s, p: {
      "reply_md": "Here is a tighter rule.",
      "proposal": {
          "has_proposal": True,
          "constitution_md": (
              "## Lead with the decision\n\n- Put the decision first.\n"
          ),
          "change_summary": "Add decision-first rule",
          "changed_sections": ["Lead with the decision"],
      },
      "out_of_scope": [
          {"rule": "28dp radius", "why": "styling", "suggested_home": "theme"}
      ],
  }
  llm.handlers["scope_check"] = lambda s, p: {
      "summary": "One styling rule.",
      "items": [{
          "rule_excerpt": "28dp radius",
          "why": "Visual styling",
          "suggested_home": "theme",
          "suggested_theme_change": {"token": "radius_card", "value": "28"},
      }],
  }
  llm.handlers["wizard_select_samples"] = lambda s, p: {
      "selected_sample_ids": [],
      "needs_synthesis": False,
      "rationale": "Use round-robin starter samples.",
  }
  return llm


class FakePool:

  def __init__(self):
    self.calls = []

  def screenshot(self, url, width, height, out_path, scale=2.0, timeout=25.0):
    del scale, timeout
    self.calls.append(url)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "wb") as fh:
      fh.write(PNG)
    return {
        "height": 300,
        "width": width,
        "overflowX": False,
        "unsupported": [],
        "missingImages": 0,
        "errors": [],
    }

  def close(self):
    pass


class FakeAndroidWorker:

  def __init__(self):
    self.calls = []

  def status(self):
    return {"available": True, "warm": True, "port": 18811}

  def render_job(
      self,
      *,
      job_id,
      renderer,
      a2ui,
      width,
      height,
      theme,
      output_path,
      thumb_path,
  ):
    del a2ui, theme
    self.calls.append((job_id, renderer))
    for p in (output_path, thumb_path):
      os.makedirs(os.path.dirname(p), exist_ok=True)
      with open(p, "wb") as fh:
        fh.write(PNG)
    meta = {
        "renderer": renderer,
        "engine": f"Fake {renderer}",
        "width": width,
        "height": height,
        "latency_ms": 12,
    }
    if renderer == "android_remote_compose":
      meta.update({
          "rc_byte_size": 1024,
          "rc_op_count": 18,
          "rc_stats": ["TextData: 4", "DrawRoundRect: 3"],
          "rc_hierarchy": "RootLayoutComponent\n  ColumnLayout\n",
      })
    return {
        "id": job_id,
        "renderer": renderer,
        "ok": True,
        "output_path": output_path,
        "thumb_path": thumb_path,
        "meta": meta,
    }

  def close(self):
    pass


class FakeIosRunner:

  def __init__(self):
    self.calls = []

  def status(self):
    return {"available": True, "running": False}

  def render_batch(self, jobs):
    out = {}
    for j in jobs:
      jid = str(j["id"])
      self.calls.append(jid)
      for p in (j["output_path"], j["thumb_path"]):
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as fh:
          fh.write(PNG)
      out[jid] = {
          "id": jid,
          "renderer": "ios_swiftui",
          "ok": True,
          "output_path": j["output_path"],
          "thumb_path": j["thumb_path"],
          "meta": {
              "renderer": "ios_swiftui",
              "engine": "Fake iOS SwiftUI",
              "width": int(j.get("width") or 390) * 2,
              "height": 600,
              "latency_ms": 40,
          },
      }
    return out


class LiveServer:
  """In-process server on a random port with a cookie-aware client."""

  def __init__(
      self,
      llm=None,
      pool=None,
      android_worker=None,
      ios_runner=None,
      admins=("admin1",),
  ):
    self.dir = tempfile.mkdtemp(prefix="dcb_test_")
    admins_path = os.path.join(self.dir, "admins.json")
    with open(admins_path, "w", encoding="utf-8") as fh:
      json.dump(list(admins), fh)
    self.llm = llm or make_llm()
    self.pool = pool or FakePool()
    self.android_worker = android_worker or FakeAndroidWorker()
    self.ios_runner = ios_runner or FakeIosRunner()
    args = server_main.parse_args([
        "--port",
        "0",
        "--data_dir",
        self.dir,
        "--admins",
        admins_path,
        "--workers",
        "4",
    ])
    self.app, _ = server_main.build(
        args,
        llm=self.llm,
        pool=self.pool,
        android_worker=self.android_worker,
        ios_runner=self.ios_runner,
    )
    # Tests never reach Figma; tests that need components override these.
    self.app.builder.figma_scan = lambda token, url: {"components": []}
    self.app.builder.figma_thumbs = lambda token, key, ids: {}
    self.app.refs.figma_frames = lambda token, url: {
        "file": {"key": "K", "name": "Test file", "url": url},
        "pages": [],
        "preselect": [],
    }
    self.app.refs.figma_source_files = lambda token, src: {
        "source": src,
        "files": [],
    }
    self.srv = make_server(self.app, "127.0.0.1", 0)
    self.port = self.srv.server_address[1]
    self.app.runs.base_url = f"http://127.0.0.1:{self.port}"
    self.thread = threading.Thread(target=self.srv.serve_forever, daemon=True)
    self.thread.start()
    self.jar = http.cookiejar.CookieJar()
    self.opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(self.jar)
    )

  def url(self, path):
    return f"http://127.0.0.1:{self.port}{path}"

  def call(self, method, path, body=None, raw=False):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        self.url(path),
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
      with self.opener.open(req, timeout=30) as r:
        payload = r.read()
        return r.status, (payload if raw else json.loads(payload or b"{}"))
    except urllib.error.HTTPError as e:
      payload = e.read()
      try:
        return e.code, json.loads(payload)
      except ValueError:
        return e.code, payload

  def login(self, ldap):
    self.jar.clear()
    return self.call("POST", "/api/login", {"ldap": ldap})

  def close(self):
    self.srv.shutdown()
    self.srv.server_close()
    self.app.runs.executor.shutdown(wait=True, cancel_futures=True)
    shutil.rmtree(self.dir, ignore_errors=True)
