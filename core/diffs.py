"""Line diffs for constitution revisions (difflib)."""

from __future__ import annotations

import difflib
from typing import Any


def diff(
    a: str, b: str, a_name: str = "a", b_name: str = "b"
) -> dict[str, Any]:
  a_lines = (a or "").splitlines()
  b_lines = (b or "").splitlines()
  unified = "\n".join(
      difflib.unified_diff(
          a_lines, b_lines, fromfile=a_name, tofile=b_name, lineterm=""
      )
  )
  rows: list[dict[str, Any]] = []
  added = removed = 0
  sm = difflib.SequenceMatcher(None, a_lines, b_lines, autojunk=False)
  for tag, i1, i2, j1, j2 in sm.get_opcodes():
    if tag == "equal":
      for k in range(i2 - i1):
        rows.append({
            "type": "equal",
            "a_line": i1 + k + 1,
            "b_line": j1 + k + 1,
            "a": a_lines[i1 + k],
            "b": b_lines[j1 + k],
        })
    elif tag == "delete":
      removed += i2 - i1
      for k in range(i1, i2):
        rows.append({
            "type": "delete",
            "a_line": k + 1,
            "b_line": None,
            "a": a_lines[k],
            "b": "",
        })
    elif tag == "insert":
      added += j2 - j1
      for k in range(j1, j2):
        rows.append({
            "type": "insert",
            "a_line": None,
            "b_line": k + 1,
            "a": "",
            "b": b_lines[k],
        })
    else:  # replace
      removed += i2 - i1
      added += j2 - j1
      n = max(i2 - i1, j2 - j1)
      for k in range(n):
        ai, bj = i1 + k, j1 + k
        rows.append({
            "type": "replace",
            "a_line": ai + 1 if ai < i2 else None,
            "b_line": bj + 1 if bj < j2 else None,
            "a": a_lines[ai] if ai < i2 else "",
            "b": b_lines[bj] if bj < j2 else "",
        })
  return {
      "unified": unified,
      "rows": rows,
      "stats": {"added": added, "removed": removed},
  }
