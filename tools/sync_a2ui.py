#!/usr/bin/env python3
"""Refreshes the vendored upstream A2UI files under seeds/a2ui/.

The files are copied VERBATIM from github.com/a2ui-project/a2ui at a pinned
ref and recorded (with sha256 checksums) in seeds/a2ui/UPSTREAM.json;
tests/seeds_test.py fails if any vendored file is edited by hand.

Usage:
  python3 tools/sync_a2ui.py --ref <commit-sha|tag>
  python3 tools/sync_a2ui.py --ref <sha> --from_dir /path/to/a2ui/checkout
  python3 tools/sync_a2ui.py --check     # verify checksums only

Stdlib only. Directory listings use the GitHub contents API; with
--from_dir the listing comes from the local checkout instead.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import urllib.request

REPO = "https://github.com/a2ui-project/a2ui"
RAW = "https://raw.githubusercontent.com/a2ui-project/a2ui/{ref}/{path}"
API = "https://api.github.com/repos/a2ui-project/a2ui/contents/{path}?ref={ref}"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEST = os.path.join(ROOT, "seeds", "a2ui")
MANIFEST = os.path.join(DEST, "UPSTREAM.json")

# (directory, suffix) globs and single files that are vendored.
FILES = [
    "LICENSE",
    "catalogs/basic/v1/catalog.json",
    "catalogs/basic/v1/basic_catalog_implementation_guide.md",
    "specification/v1_0/docs/a2ui_protocol.md",
    "specification/v1_0/test/testing_catalog.json",
]
DIRS = [
    ("catalogs/basic/v1/examples", ".json"),
    ("specification/v1_0/json", ".json"),
    ("specification/v1_0/test/cases", ".json"),
]


def sha256_file(path: str) -> str:
  h = hashlib.sha256()
  with open(path, "rb") as fh:
    for chunk in iter(lambda: fh.read(65536), b""):
      h.update(chunk)
  return h.hexdigest()


def _get(url: str) -> bytes:
  req = urllib.request.Request(url, headers={"User-Agent": "a2ui-designer-sync"})
  with urllib.request.urlopen(req, timeout=60) as resp:
    return resp.read()


def list_files(ref: str, from_dir: str | None) -> list[str]:
  out = list(FILES)
  for d, suffix in DIRS:
    if from_dir:
      names = sorted(os.listdir(os.path.join(from_dir, d)))
    else:
      items = json.loads(_get(API.format(path=d, ref=ref)))
      names = sorted(i["name"] for i in items if i.get("type") == "file")
    out.extend(f"{d}/{n}" for n in names if n.endswith(suffix))
  return out


def fetch(path: str, ref: str, from_dir: str | None) -> bytes:
  if from_dir:
    with open(os.path.join(from_dir, path), "rb") as fh:
      return fh.read()
  return _get(RAW.format(ref=ref, path=path))


def check() -> int:
  with open(MANIFEST, encoding="utf-8") as fh:
    manifest = json.load(fh)
  bad = []
  for rel, digest in manifest["files"].items():
    p = os.path.join(DEST, rel)
    if not os.path.exists(p) or sha256_file(p) != digest:
      bad.append(rel)
  for rel in bad:
    print(f"MISMATCH {rel}", file=sys.stderr)
  print(f"{len(manifest['files']) - len(bad)}/{len(manifest['files'])} ok")
  return 1 if bad else 0


def sync(ref: str, from_dir: str | None) -> int:
  paths = list_files(ref, from_dir)
  staging = DEST + ".tmp"
  shutil.rmtree(staging, ignore_errors=True)
  files: dict[str, str] = {}
  for rel in paths:
    data = fetch(rel, ref, from_dir)
    p = os.path.join(staging, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as fh:
      fh.write(data)
    files[rel] = hashlib.sha256(data).hexdigest()
    print(f"  {rel} ({len(data)} bytes)")
  manifest = {
      "repo": REPO,
      "commit": ref,
      "note": (
          "Verbatim copies; do not edit. Refresh with"
          " `python3 tools/sync_a2ui.py --ref <sha|tag>`."
      ),
      "files": dict(sorted(files.items())),
  }
  with open(os.path.join(staging, "UPSTREAM.json"), "w", encoding="utf-8") as fh:
    json.dump(manifest, fh, indent=2)
    fh.write("\n")
  shutil.rmtree(DEST, ignore_errors=True)
  os.replace(staging, DEST)
  print(f"synced {len(files)} files from {REPO}@{ref}")
  return 0


def main() -> int:
  ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
  ap.add_argument("--ref", help="upstream commit sha or tag")
  ap.add_argument(
      "--from_dir", help="copy from a local a2ui checkout instead of GitHub"
  )
  ap.add_argument("--check", action="store_true", help="verify checksums")
  args = ap.parse_args()
  if args.check:
    return check()
  if not args.ref:
    ap.error("--ref is required (or --check)")
  return sync(args.ref, args.from_dir)


if __name__ == "__main__":
  sys.exit(main())
