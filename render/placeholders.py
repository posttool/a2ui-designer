"""Deterministic labeled SVG placeholder images for synthetic samples."""

from __future__ import annotations

import hashlib
import html

# Muted, photographic-feeling pairs (top, bottom) for gradients.
PALETTES = [
    ("#c9d6df", "#52616b"),
    ("#e8d5b7", "#8a6a4f"),
    ("#cfe0c3", "#5b7553"),
    ("#f0c9b6", "#9a5b4b"),
    ("#d4cbe5", "#5d5479"),
    ("#bfd8d2", "#3f6c66"),
    ("#e6e2c3", "#7a7350"),
    ("#d9c2c2", "#7a4f55"),
    ("#c3d3e6", "#45607d"),
    ("#e4d0e0", "#7d5470"),
]


def svg(slug: str, width: int, height: int) -> str:
  width = max(16, min(int(width), 4000))
  height = max(16, min(int(height), 4000))
  h = int(hashlib.sha1(slug.encode("utf-8")).hexdigest(), 16)
  top, bottom = PALETTES[h % len(PALETTES)]
  label = html.escape(slug.replace("-", " ").replace("_", " ")[:60])
  fs = max(10, min(28, int(min(width, height) / 9)))
  # A few soft shapes so images read as "photos" in screenshots.
  cx1, cy1 = (h >> 8) % 100, (h >> 16) % 100
  cx2, cy2 = (h >> 24) % 100, (h >> 32) % 100
  show_label = width >= 80 and height >= 48
  text = (
      f'<text x="50%" y="{height - fs * 0.9:.0f}" text-anchor="middle" '
      'font-family="Google Sans, Roboto, Arial, sans-serif" '
      f'font-size="{fs}" fill="#ffffff" fill-opacity="0.92">{label}</text>'
      if show_label
      else ""
  )
  return (
      f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" '
      f'height="{height}" viewBox="0 0 {width} {height}">'
      '<defs><linearGradient id="g" x1="0" y1="0" x2="0" y2="1">'
      f'<stop offset="0" stop-color="{top}"/><stop offset="1" '
      f'stop-color="{bottom}"/></linearGradient></defs>'
      '<rect width="100%" height="100%" fill="url(#g)"/>'
      f'<circle cx="{cx1}%" cy="{cy1}%" r="{min(width, height) * 0.35:.0f}" '
      'fill="#ffffff" fill-opacity="0.16"/>'
      f'<circle cx="{cx2}%" cy="{cy2}%" r="{min(width, height) * 0.22:.0f}" '
      'fill="#000000" fill-opacity="0.08"/>'
      f"{text}</svg>"
  )


def parse_size(spec: str) -> tuple[int, int]:
  spec = (
      spec.lower()
      .removesuffix(".svg")
      .removesuffix(".png")
      .removesuffix(".jpg")
  )
  try:
    w, h = spec.split("x", 1)
    return int(w), int(h)
  except ValueError:
    return 800, 600
