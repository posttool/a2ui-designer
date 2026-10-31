"""Material Color Utilities (HCT, CAM16, TonalPalette, Contrast, Blend, DynamicScheme).

Pure-stdlib Python port of
https://github.com/material-foundation/material-color-utilities
used to compute complementary Material 3 color tokens when an import from
Stitch,
Figma, or screenshots only specifies a subset of the canonical color tokens.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import re
from typing import Any

# ---------------------------------------------------------------------------
# Color & Math Utilities (utils/utils.cc, quantize/lab.cc)
# ---------------------------------------------------------------------------

WHITE_POINT_D65 = (95.047, 100.0, 108.883)

SRGB_TO_XYZ = (
    (0.41233895, 0.35762064, 0.18051042),
    (0.2126, 0.7152, 0.0722),
    (0.01932141, 0.11916382, 0.95034478),
)

_HEX_RE = re.compile(r"^#?([0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")


def signum(num: float) -> int:
  if num < 0:
    return -1
  elif num == 0:
    return 0
  return 1


def lerp(start: float, stop: float, amount: float) -> float:
  return (1.0 - amount) * start + amount * stop


def clamp_int(minimum: int, maximum: int, value: int) -> int:
  return max(minimum, min(maximum, int(value)))


def clamp_double(minimum: float, maximum: float, value: float) -> float:
  return max(minimum, min(maximum, float(value)))


def sanitize_degrees_int(degrees: int) -> int:
  degrees = degrees % 360
  if degrees < 0:
    degrees += 360
  return degrees


def sanitize_degrees_double(degrees: float) -> float:
  degrees = math.fmod(degrees, 360.0)
  if degrees < 0:
    degrees += 360.0
  return degrees


def difference_degrees(a: float, b: float) -> float:
  return 180.0 - abs(abs(a - b) - 180.0)


def rotation_direction(from_deg: float, to_deg: float) -> float:
  increasing_difference = sanitize_degrees_double(to_deg - from_deg)
  return 1.0 if increasing_difference <= 180.0 else -1.0


def matrix_multiply(
    row: tuple[float, float, float],
    matrix: tuple[
        tuple[float, float, float],
        tuple[float, float, float],
        tuple[float, float, float],
    ],
) -> tuple[float, float, float]:
  a = row[0] * matrix[0][0] + row[1] * matrix[0][1] + row[2] * matrix[0][2]
  b = row[0] * matrix[1][0] + row[1] * matrix[1][1] + row[2] * matrix[1][2]
  c = row[0] * matrix[2][0] + row[1] * matrix[2][1] + row[2] * matrix[2][2]
  return (a, b, c)


def argb_from_rgb(red: int, green: int, blue: int) -> int:
  return (
      (0xFF << 24)
      | ((red & 0xFF) << 16)
      | ((green & 0xFF) << 8)
      | (blue & 0xFF)
  )


def red_from_argb(argb: int) -> int:
  return (argb >> 16) & 0xFF


def green_from_argb(argb: int) -> int:
  return (argb >> 8) & 0xFF


def blue_from_argb(argb: int) -> int:
  return argb & 0xFF


def hex_from_argb(argb: int) -> str:
  return (
      f"#{red_from_argb(argb):02X}"
      f"{green_from_argb(argb):02X}"
      f"{blue_from_argb(argb):02X}"
  )


def argb_from_hex(hex_str: str) -> int:
  if not isinstance(hex_str, str):
    raise ValueError(f"Expected hex string, got {hex_str!r}")
  s = hex_str.strip()
  m = _HEX_RE.match(s)
  if not m:
    raise ValueError(f"Invalid hex color: {hex_str!r}")
  digits = m.group(1)
  if len(digits) == 3:
    digits = "".join(ch * 2 for ch in digits)
  elif len(digits) == 8:
    digits = digits[:6]
  r = int(digits[0:2], 16)
  g = int(digits[2:4], 16)
  b = int(digits[4:6], 16)
  return argb_from_rgb(r, g, b)


def is_valid_hex(val: Any) -> bool:
  if not isinstance(val, str):
    return False
  return bool(_HEX_RE.match(val.strip()))


def linearized(rgb_component: int) -> float:
  normalized = rgb_component / 255.0
  if normalized <= 0.040449936:
    return normalized / 12.92 * 100.0
  return math.pow((normalized + 0.055) / 1.055, 2.4) * 100.0


def delinearized(rgb_component: float) -> int:
  normalized = rgb_component / 100.0
  if normalized <= 0.0031308:
    delinearized_val = normalized * 12.92
  else:
    delinearized_val = 1.055 * math.pow(normalized, 1.0 / 2.4) - 0.055
  return clamp_int(0, 255, round(delinearized_val * 255.0))


def argb_from_linrgb(linrgb: tuple[float, float, float]) -> int:
  r = delinearized(linrgb[0])
  g = delinearized(linrgb[1])
  b = delinearized(linrgb[2])
  return argb_from_rgb(r, g, b)


def y_from_lstar(lstar: float) -> float:
  ke = 8.0
  if lstar > ke:
    cube_root = (lstar + 16.0) / 116.0
    cube = cube_root * cube_root * cube_root
    return cube * 100.0
  return lstar / (24389.0 / 27.0) * 100.0


def lstar_from_y(y: float) -> float:
  y = y / 100.0
  e = 216.0 / 24389.0
  if y <= e:
    return (24389.0 / 27.0) * y
  y_intermediate = math.pow(y, 1.0 / 3.0)
  return 116.0 * y_intermediate - 16.0


def lstar_from_argb(argb: int) -> float:
  r = red_from_argb(argb)
  g = green_from_argb(argb)
  b = blue_from_argb(argb)
  y = 0.2126 * linearized(r) + 0.7152 * linearized(g) + 0.0722 * linearized(b)
  return lstar_from_y(y)


def _lab_f(t: float) -> float:
  e = 216.0 / 24389.0
  kappa = 24389.0 / 27.0
  if t > e:
    return math.pow(t, 1.0 / 3.0)
  return (kappa * t + 16.0) / 116.0


def lab_from_int(argb: int) -> tuple[float, float, float]:
  lin_r = linearized(red_from_argb(argb))
  lin_g = linearized(green_from_argb(argb))
  lin_b = linearized(blue_from_argb(argb))
  xyz = matrix_multiply((lin_r, lin_g, lin_b), SRGB_TO_XYZ)
  fx = _lab_f(xyz[0] / WHITE_POINT_D65[0])
  fy = _lab_f(xyz[1] / WHITE_POINT_D65[1])
  fz = _lab_f(xyz[2] / WHITE_POINT_D65[2])
  l = 116.0 * fy - 16.0
  a = 500.0 * (fx - fy)
  b = 200.0 * (fy - fz)
  return (l, a, b)


# ---------------------------------------------------------------------------
# ViewingConditions (cam/viewing_conditions.cc)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ViewingConditions:
  adapting_luminance: float
  background_lstar: float
  surround: float
  discounting_illuminant: bool
  background_y_to_white_point_y: float
  aw: float
  nbb: float
  ncb: float
  c: float
  nc: float
  drgb_inverse: tuple[float, float, float]
  rgb_d: tuple[float, float, float]
  fl: float
  fl_root: float
  z: float

  @staticmethod
  def create(
      white_point: tuple[float, float, float] = WHITE_POINT_D65,
      adapting_luminance: float = -1.0,
      background_lstar: float = 50.0,
      surround: float = 2.0,
      discounting_illuminant: bool = False,
  ) -> ViewingConditions:
    if adapting_luminance <= 0.0:
      adapting_luminance = 200.0 / math.pi * y_from_lstar(50.0) / 100.0
    background_lstar = max(0.1, background_lstar)
    xyz = white_point
    r_w = xyz[0] * 0.401288 + xyz[1] * 0.650173 + xyz[2] * -0.051461
    g_w = xyz[0] * -0.250268 + xyz[1] * 1.204414 + xyz[2] * 0.045854
    b_w = xyz[0] * -0.002079 + xyz[1] * 0.048952 + xyz[2] * 0.953127
    f = 0.8 + (surround / 10.0)
    c = (
        lerp(0.59, 0.69, (f - 0.9) * 10.0)
        if f >= 0.9
        else lerp(0.525, 0.59, (f - 0.8) * 10.0)
    )
    d = (
        1.0
        if discounting_illuminant
        else f
        * (1.0 - ((1.0 / 3.6) * math.exp((-adapting_luminance - 42.0) / 92.0)))
    )
    d = clamp_double(0.0, 1.0, d)
    nc = f
    rgb_d = (
        d * (100.0 / r_w) + 1.0 - d,
        d * (100.0 / g_w) + 1.0 - d,
        d * (100.0 / b_w) + 1.0 - d,
    )
    k = 1.0 / (5.0 * adapting_luminance + 1.0)
    k4 = k * k * k * k
    k4_f = 1.0 - k4
    fl = (k4 * adapting_luminance) + (
        0.1 * k4_f * k4_f * math.pow(5.0 * adapting_luminance, 1.0 / 3.0)
    )
    n = y_from_lstar(background_lstar) / white_point[1]
    z = 1.48 + math.sqrt(n)
    nbb = 0.725 / math.pow(n, 0.2)
    ncb = nbb
    rgb_a_factors = (
        math.pow(fl * rgb_d[0] * r_w / 100.0, 0.42),
        math.pow(fl * rgb_d[1] * g_w / 100.0, 0.42),
        math.pow(fl * rgb_d[2] * b_w / 100.0, 0.42),
    )
    rgb_a = (
        (400.0 * rgb_a_factors[0]) / (rgb_a_factors[0] + 27.13),
        (400.0 * rgb_a_factors[1]) / (rgb_a_factors[1] + 27.13),
        (400.0 * rgb_a_factors[2]) / (rgb_a_factors[2] + 27.13),
    )
    aw = (2.0 * rgb_a[0] + rgb_a[1] + 0.05 * rgb_a[2]) * nbb
    return ViewingConditions(
        adapting_luminance=adapting_luminance,
        background_lstar=background_lstar,
        surround=surround,
        discounting_illuminant=discounting_illuminant,
        background_y_to_white_point_y=n,
        aw=aw,
        nbb=nbb,
        ncb=ncb,
        c=c,
        nc=nc,
        drgb_inverse=(0.0, 0.0, 0.0),
        rgb_d=rgb_d,
        fl=fl,
        fl_root=math.pow(fl, 0.25),
        z=z,
    )


DEFAULT_VIEWING_CONDITIONS = ViewingConditions.create()


# ---------------------------------------------------------------------------
# CAM16 (cam/cam.cc)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Cam16:
  hue: float
  chroma: float
  j: float
  q: float
  m: float
  s: float
  jstar: float
  astar: float
  bstar: float

  @staticmethod
  def from_int(
      argb: int,
      viewing_conditions: ViewingConditions = DEFAULT_VIEWING_CONDITIONS,
  ) -> Cam16:
    red_l = linearized(red_from_argb(argb))
    green_l = linearized(green_from_argb(argb))
    blue_l = linearized(blue_from_argb(argb))
    x = 0.41233895 * red_l + 0.35762064 * green_l + 0.18051042 * blue_l
    y = 0.2126 * red_l + 0.7152 * green_l + 0.0722 * blue_l
    z = 0.01932141 * red_l + 0.11916382 * green_l + 0.95034478 * blue_l
    return Cam16.from_xyz(x, y, z, viewing_conditions)

  @staticmethod
  def from_xyz(
      x: float,
      y: float,
      z: float,
      viewing_conditions: ViewingConditions = DEFAULT_VIEWING_CONDITIONS,
  ) -> Cam16:
    r_c = 0.401288 * x + 0.650173 * y - 0.051461 * z
    g_c = -0.250268 * x + 1.204414 * y + 0.045854 * z
    b_c = -0.002079 * x + 0.048952 * y + 0.953127 * z

    r_d = viewing_conditions.rgb_d[0] * r_c
    g_d = viewing_conditions.rgb_d[1] * g_c
    b_d = viewing_conditions.rgb_d[2] * b_c

    r_af = math.pow(viewing_conditions.fl * abs(r_d) / 100.0, 0.42)
    g_af = math.pow(viewing_conditions.fl * abs(g_d) / 100.0, 0.42)
    b_af = math.pow(viewing_conditions.fl * abs(b_d) / 100.0, 0.42)

    r_a = signum(r_d) * 400.0 * r_af / (r_af + 27.13)
    g_a = signum(g_d) * 400.0 * g_af / (g_af + 27.13)
    b_a = signum(b_d) * 400.0 * b_af / (b_af + 27.13)

    a = (11.0 * r_a + -12.0 * g_a + b_a) / 11.0
    b = (r_a + g_a - 2.0 * b_a) / 9.0
    u = (20.0 * r_a + 20.0 * g_a + 21.0 * b_a) / 20.0
    p2 = (40.0 * r_a + 20.0 * g_a + b_a) / 20.0

    atan2_val = math.atan2(b, a)
    atan_degrees = atan2_val * 180.0 / math.pi
    hue = sanitize_degrees_double(atan_degrees)
    hue_radians = hue * math.pi / 180.0

    ac = p2 * viewing_conditions.nbb
    j = 100.0 * math.pow(
        ac / viewing_conditions.aw,
        viewing_conditions.c * viewing_conditions.z,
    )
    q = (
        (4.0 / viewing_conditions.c)
        * math.sqrt(j / 100.0)
        * (viewing_conditions.aw + 4.0)
        * viewing_conditions.fl_root
    )
    hue_prime = hue + 360.0 if hue < 20.14 else hue
    e_hue = 0.25 * (math.cos(hue_prime * math.pi / 180.0 + 2.0) + 3.8)
    p1 = 50000.0 / 13.0 * e_hue * viewing_conditions.nc * viewing_conditions.ncb
    t = p1 * math.hypot(a, b) / (u + 0.305)
    alpha = math.pow(
        1.64 - math.pow(0.29, viewing_conditions.background_y_to_white_point_y),
        0.73,
    ) * math.pow(t, 0.9)
    c = alpha * math.sqrt(j / 100.0)
    m = c * viewing_conditions.fl_root
    s = 50.0 * math.sqrt(
        (alpha * viewing_conditions.c) / (viewing_conditions.aw + 4.0)
    )
    jstar = (1.0 + 100.0 * 0.007) * j / (1.0 + 0.007 * j)
    mstar = 1.0 / 0.0228 * math.log1p(0.0228 * m)
    astar = mstar * math.cos(hue_radians)
    bstar = mstar * math.sin(hue_radians)

    return Cam16(
        hue=hue,
        chroma=c,
        j=j,
        q=q,
        m=m,
        s=s,
        jstar=jstar,
        astar=astar,
        bstar=bstar,
    )

  @staticmethod
  def from_jch(
      j: float,
      c: float,
      h: float,
      viewing_conditions: ViewingConditions = DEFAULT_VIEWING_CONDITIONS,
  ) -> Cam16:
    q = (
        (4.0 / viewing_conditions.c)
        * math.sqrt(j / 100.0)
        * (viewing_conditions.aw + 4.0)
        * viewing_conditions.fl_root
    )
    m = c * viewing_conditions.fl_root
    alpha = 0.0 if j == 0.0 else c / math.sqrt(j / 100.0)
    s = 50.0 * math.sqrt(
        (alpha * viewing_conditions.c) / (viewing_conditions.aw + 4.0)
    )
    hue_radians = h * math.pi / 180.0
    jstar = (1.0 + 100.0 * 0.007) * j / (1.0 + 0.007 * j)
    mstar = 1.0 / 0.0228 * math.log1p(0.0228 * m)
    astar = mstar * math.cos(hue_radians)
    bstar = mstar * math.sin(hue_radians)
    return Cam16(
        hue=h,
        chroma=c,
        j=j,
        q=q,
        m=m,
        s=s,
        jstar=jstar,
        astar=astar,
        bstar=bstar,
    )

  @staticmethod
  def from_ucs(
      jstar: float,
      astar: float,
      bstar: float,
      viewing_conditions: ViewingConditions = DEFAULT_VIEWING_CONDITIONS,
  ) -> Cam16:
    m = math.hypot(astar, bstar)
    m2 = math.expm1(m * 0.0228) / 0.0228
    c = m2 / viewing_conditions.fl_root
    h = math.atan2(bstar, astar) * (180.0 / math.pi)
    if h < 0.0:
      h += 360.0
    j = jstar / (1.0 - (jstar - 100.0) * 0.007)
    return Cam16.from_jch(j, c, h, viewing_conditions)

  def distance(self, other: Cam16) -> float:
    d_j = self.jstar - other.jstar
    d_a = self.astar - other.astar
    d_b = self.bstar - other.bstar
    d_e_prime = math.sqrt(d_j * d_j + d_a * d_a + d_b * d_b)
    return 1.41 * math.pow(d_e_prime, 0.63)

  def to_int(
      self,
      viewing_conditions: ViewingConditions = DEFAULT_VIEWING_CONDITIONS,
  ) -> int:
    alpha = (
        0.0
        if (self.chroma == 0.0 or self.j == 0.0)
        else self.chroma / math.sqrt(self.j / 100.0)
    )
    t = math.pow(
        alpha
        / math.pow(
            1.64
            - math.pow(0.29, viewing_conditions.background_y_to_white_point_y),
            0.73,
        ),
        1.0 / 0.9,
    )
    h_rad = self.hue * math.pi / 180.0
    e_hue = 0.25 * (math.cos(h_rad + 2.0) + 3.8)
    ac = viewing_conditions.aw * math.pow(
        self.j / 100.0,
        1.0 / viewing_conditions.c / viewing_conditions.z,
    )
    p1 = (
        e_hue
        * (50000.0 / 13.0)
        * viewing_conditions.nc
        * viewing_conditions.ncb
    )
    p2 = ac / viewing_conditions.nbb
    h_sin = math.sin(h_rad)
    h_cos = math.cos(h_rad)
    gamma = (
        23.0
        * (p2 + 0.305)
        * t
        / (23.0 * p1 + 11.0 * t * h_cos + 108.0 * t * h_sin)
    )
    a = gamma * h_cos
    b = gamma * h_sin
    r_a = (460.0 * p2 + 451.0 * a + 288.0 * b) / 1403.0
    g_a = (460.0 * p2 - 891.0 * a - 261.0 * b) / 1403.0
    b_a = (460.0 * p2 - 220.0 * a - 6300.0 * b) / 1403.0

    r_c_base = max(0.0, (27.13 * abs(r_a)) / (400.0 - abs(r_a)))
    r_c = (
        signum(r_a)
        * (100.0 / viewing_conditions.fl)
        * math.pow(r_c_base, 1.0 / 0.42)
    )
    g_c_base = max(0.0, (27.13 * abs(g_a)) / (400.0 - abs(g_a)))
    g_c = (
        signum(g_a)
        * (100.0 / viewing_conditions.fl)
        * math.pow(g_c_base, 1.0 / 0.42)
    )
    b_c_base = max(0.0, (27.13 * abs(b_a)) / (400.0 - abs(b_a)))
    b_c = (
        signum(b_a)
        * (100.0 / viewing_conditions.fl)
        * math.pow(b_c_base, 1.0 / 0.42)
    )
    r_f = r_c / viewing_conditions.rgb_d[0]
    g_f = g_c / viewing_conditions.rgb_d[1]
    b_f = b_c / viewing_conditions.rgb_d[2]

    x = 1.86206786 * r_f - 1.01125463 * g_f + 0.14918677 * b_f
    y = 0.38752654 * r_f + 0.62144744 * g_f - 0.00897398 * b_f
    z = -0.01584150 * r_f - 0.03412294 * g_f + 1.04996444 * b_f

    r_l = 3.2404542 * x - 1.5371385 * y - 0.4985314 * z
    g_l = -0.9692660 * x + 1.8760108 * y + 0.0415560 * z
    b_l = 0.0556434 * x - 0.2040259 * y + 1.0572252 * z

    return argb_from_linrgb((r_l, g_l, b_l))


# ---------------------------------------------------------------------------
# HCT Solver (cam/hct_solver.cc)
# ---------------------------------------------------------------------------

_SCALED_DISCOUNT_FROM_LINRGB = (
    (
        0.001200833568784504,
        0.002389694492170889,
        0.0002795742885861124,
    ),
    (
        0.0005891086651375999,
        0.0029785502573438758,
        0.0003270666104008398,
    ),
    (
        0.00010146692491640572,
        0.0005364214359186694,
        0.0032979401770712076,
    ),
)

_LINRGB_FROM_SCALED_DISCOUNT = (
    (
        1373.2198709594231,
        -1100.4251190754821,
        -7.278681089101213,
    ),
    (
        -271.815969077903,
        559.6580465940733,
        -32.46047482791194,
    ),
    (
        1.9622899599665666,
        -57.173814538844006,
        308.7233197812385,
    ),
)

_CRITICAL_PLANES = (
    0.015176349177441876,
    0.045529047532325624,
    0.07588174588720938,
    0.10623444424209313,
    0.13658714259697685,
    0.16693984095186062,
    0.19729253930674434,
    0.2276452376616281,
    0.2579979360165119,
    0.28835063437139563,
    0.3188300904430532,
    0.350925934958123,
    0.3848314933096426,
    0.42057480301049466,
    0.458183274052838,
    0.4976837250274023,
    0.5391024159806381,
    0.5824650784040898,
    0.6277969426914107,
    0.6751227633498623,
    0.7244668422128921,
    0.775853049866786,
    0.829304845476233,
    0.8848452951698498,
    0.942497089126609,
    1.0022825574869039,
    1.0642236851973577,
    1.1283421258858297,
    1.1946592148522128,
    1.2631959812511864,
    1.3339731595349034,
    1.407011200216447,
    1.4823302800086415,
    1.5599503113873272,
    1.6398909516233677,
    1.7221716113234105,
    1.8068114625156377,
    1.8938294463134073,
    1.9832442801866852,
    2.075074464868551,
    2.1693382909216234,
    2.2660538449872063,
    2.36523901573795,
    2.4669114995532007,
    2.5710888059345764,
    2.6777882626779785,
    2.7870270208169257,
    2.898822059350997,
    3.0131901897720907,
    3.1301480604002863,
    3.2497121605402226,
    3.3718988244681087,
    3.4967242352587946,
    3.624204428461639,
    3.754355295633311,
    3.887192587735158,
    4.022731918402185,
    4.160988767090289,
    4.301978482107941,
    4.445716283538092,
    4.592217266055746,
    4.741496401646282,
    4.893568542229298,
    5.048448422192488,
    5.20615066083972,
    5.3666897647573375,
    5.5300801301023865,
    5.696336044816294,
    5.865471690767354,
    6.037501145825082,
    6.212438385869475,
    6.390297286737924,
    6.571091626112461,
    6.7548350853498045,
    6.941541251256611,
    7.131223617812143,
    7.323895587840543,
    7.5195704746346665,
    7.7182615035334345,
    7.919981813454504,
    8.124744458384042,
    8.332562408825165,
    8.543448553206703,
    8.757415699253682,
    8.974476575321063,
    9.194643831691977,
    9.417930041841839,
    9.644347703663403,
    9.873909240696694,
    10.106627003236781,
    10.342513269534024,
    10.58158024687427,
    10.8238400726681,
    11.069304815507364,
    11.317986476196008,
    11.569896988756009,
    11.825048221409341,
    12.083451977536606,
    12.345119996613247,
    12.610063955123938,
    12.878295467445942,
    13.149826086772048,
    13.42466730586372,
    13.702830557985108,
    13.984327217668513,
    14.269168601521828,
    14.55736596900856,
    14.848930523210871,
    15.143873411576273,
    15.44220572664832,
    15.743938506781891,
    16.04908273684337,
    16.35764934889634,
    16.66964922287304,
    16.985093187232053,
    17.30399201960269,
    17.62635644741625,
    17.95219714852476,
    18.281524751807332,
    18.614349837764564,
    18.95068293910138,
    19.290534541298456,
    19.633915083172692,
    19.98083495742689,
    20.331304511189067,
    20.685334046541502,
    21.042933821039977,
    21.404114048223256,
    21.76888489811322,
    22.137256497705877,
    22.50923893145328,
    22.884842241736916,
    23.264076429332462,
    23.6469514538663,
    24.033477234264016,
    24.42366364919083,
    24.817520537484558,
    25.21505769858089,
    25.61628489293138,
    26.021211842414342,
    26.429848230738664,
    26.842203703840827,
    27.258287870275353,
    27.678110301598522,
    28.10168053274597,
    28.529008062403893,
    28.96010235337422,
    29.39497283293396,
    29.83362889318845,
    30.276079891419332,
    30.722335150426627,
    31.172403958865512,
    31.62629557157785,
    32.08401920991837,
    32.54558406207592,
    33.010999283389665,
    33.4802739966603,
    33.953417292456834,
    34.430438229418264,
    34.911345834551085,
    35.39614910352207,
    35.88485700094671,
    36.37747846067349,
    36.87402238606382,
    37.37449765026789,
    37.87891309649659,
    38.38727753828926,
    38.89959975977785,
    39.41588851594697,
    39.93615253289054,
    40.460400508064545,
    40.98864111053629,
    41.520882981230194,
    42.05713473317016,
    42.597404951718396,
    43.141702194811224,
    43.6900349931913,
    44.24241185063697,
    44.798841244188324,
    45.35933162437017,
    45.92389141541209,
    46.49252901546552,
    47.065252796817916,
    47.64207110610409,
    48.22299226451468,
    48.808024568002054,
    49.3971762874833,
    49.9904556690408,
    50.587870934119984,
    51.189430279724725,
    51.79514187861014,
    52.40501387947288,
    53.0190544071392,
    53.637271562750364,
    54.259673423945976,
    54.88626804504493,
    55.517063457223934,
    56.15206766869424,
    56.79128866487574,
    57.43473440856916,
    58.08241284012621,
    58.734331877617365,
    59.39049941699807,
    60.05092333227251,
    60.715611475655585,
    61.38457167773311,
    62.057811747619894,
    62.7353394731159,
    63.417162620860914,
    64.10328893648692,
    64.79372614476921,
    65.48848194977529,
    66.18756403501224,
    66.89098006357258,
    67.59873767827808,
    68.31084450182222,
    69.02730813691093,
    69.74813616640164,
    70.47333615344107,
    71.20291564160104,
    71.93688215501312,
    72.67524319850172,
    73.41800625771542,
    74.16517879925733,
    74.9167682708136,
    75.67278210128072,
    76.43322770089146,
    77.1981124613393,
    77.96744375590167,
    78.74122893956174,
    79.51947534912904,
    80.30219030335869,
    81.08938110306934,
    81.88105503125999,
    82.67721935322541,
    83.4778813166706,
    84.28304815182372,
    85.09272707154808,
    85.90692527145302,
    86.72564993000343,
    87.54890820862819,
    88.3767072518277,
    89.2090541872801,
    90.04595612594655,
    90.88742016217518,
    91.73345337380438,
    92.58406282226491,
    93.43925555268066,
    94.29903859396902,
    95.16341895893969,
    96.03240364439274,
    96.9059996312159,
    97.78421388448044,
    98.6670533535366,
    99.55452497210776,
)


def _true_delinearized(rgb_component: float) -> float:
  normalized = rgb_component / 100.0
  if normalized <= 0.0031308:
    delinearized_val = normalized * 12.92
  else:
    delinearized_val = 1.055 * math.pow(normalized, 1.0 / 2.4) - 0.055
  return delinearized_val * 255.0


def _chromatic_adaptation(component: float) -> float:
  af = math.pow(abs(component), 0.42)
  return signum(component) * 400.0 * af / (af + 27.13)


def _inverse_chromatic_adaptation(adapted: float) -> float:
  adapted_abs = abs(adapted)
  base = max(0.0, 27.13 * adapted_abs / (400.0 - adapted_abs))
  return signum(adapted) * math.pow(base, 1.0 / 0.42)


def _are_bounded(vec: tuple[float, float, float]) -> bool:
  return (
      0.0 <= vec[0] <= 100.0
      and 0.0 <= vec[1] <= 100.0
      and 0.0 <= vec[2] <= 100.0
  )


def _hue_of(linrgb: tuple[float, float, float]) -> float:
  scaled_discount = matrix_multiply(linrgb, _SCALED_DISCOUNT_FROM_LINRGB)
  r_a = _chromatic_adaptation(scaled_discount[0])
  g_a = _chromatic_adaptation(scaled_discount[1])
  b_a = _chromatic_adaptation(scaled_discount[2])
  a = (11.0 * r_a + -12.0 * g_a + b_a) / 11.0
  b = (r_a + g_a - 2.0 * b_a) / 9.0
  return sanitize_degrees_double(math.atan2(b, a) * 180.0 / math.pi)


def _intercept(source: float, mid: float, target: float) -> float:
  return (mid - source) / (target - source)


def _lerp_point(
    source: tuple[float, float, float],
    t: float,
    target: tuple[float, float, float],
) -> tuple[float, float, float]:
  return (
      source[0] + (target[0] - source[0]) * t,
      source[1] + (target[1] - source[1]) * t,
      source[2] + (target[2] - source[2]) * t,
  )


def _set_coordinate(
    source: tuple[float, float, float],
    coordinate: float,
    target: tuple[float, float, float],
    axis: int,
) -> tuple[float, float, float]:
  t = _intercept(source[axis], coordinate, target[axis])
  return _lerp_point(source, t, target)


def _critical_plane_below(x: float) -> int:
  return int(math.floor(x - 0.5))


def _critical_plane_above(x: float) -> int:
  return int(math.ceil(x - 0.5))


def _are_in_cyclic_order(a: float, b: float, c: float) -> bool:
  delta_a_b = sanitize_degrees_double(b - a)
  delta_a_c = sanitize_degrees_double(c - a)
  return delta_a_b < delta_a_c


def _bisect_to_segment(
    y: float,
    target_hue: float,
) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
  left = (-1.0, -1.0, -1.0)
  right = left
  left_hue = 0.0
  right_hue = 0.0
  initialized = False
  uncut = True
  for n in range(12):
    mid = _nth_vertex(y, n)
    if mid[0] < 0:
      continue
    mid_hue = _hue_of(mid)
    if not initialized:
      left = mid
      right = mid
      left_hue = mid_hue
      right_hue = mid_hue
      initialized = True
      continue
    if uncut or _are_in_cyclic_order(left_hue, mid_hue, right_hue):
      uncut = False
      if _are_in_cyclic_order(left_hue, target_hue, mid_hue):
        right = mid
        right_hue = mid_hue
      else:
        left = mid
        left_hue = mid_hue
  return (left, right)


def _nth_vertex(y: float, n: int) -> tuple[float, float, float]:
  k_r = 0.2126
  k_g = 0.7152
  k_b = 0.0722
  coord_a = 0.0 if (n % 4 <= 1) else 100.0
  coord_b = 0.0 if (n % 2 == 0) else 100.0
  if n < 4:
    g = coord_a
    b = coord_b
    r = (y - g * k_g - b * k_b) / k_r
    return (r, g, b) if (0.0 <= r <= 100.0) else (-1.0, -1.0, -1.0)
  elif n < 8:
    b = coord_a
    r = coord_b
    g = (y - r * k_r - b * k_b) / k_g
    return (r, g, b) if (0.0 <= g <= 100.0) else (-1.0, -1.0, -1.0)
  else:
    r = coord_a
    g = coord_b
    b = (y - r * k_r - g * k_g) / k_b
    return (r, g, b) if (0.0 <= b <= 100.0) else (-1.0, -1.0, -1.0)


def _bisect_to_limit(y: float, target_hue: float) -> tuple[float, float, float]:
  left, right = _bisect_to_segment(y, target_hue)
  left_hue = _hue_of(left)
  for axis in range(3):
    if left[axis] != right[axis]:
      l_plane = -1
      r_plane = 255
      if left[axis] < right[axis]:
        l_plane = _critical_plane_below(_true_delinearized(left[axis]))
        r_plane = _critical_plane_above(_true_delinearized(right[axis]))
      else:
        l_plane = _critical_plane_above(_true_delinearized(left[axis]))
        r_plane = _critical_plane_below(_true_delinearized(right[axis]))
      for _ in range(8):
        if abs(r_plane - l_plane) <= 1:
          break
        m_plane = int(math.floor((l_plane + r_plane) / 2.0))
        mid_plane_coord = _CRITICAL_PLANES[m_plane]
        mid = _set_coordinate(left, mid_plane_coord, right, axis)
        mid_hue = _hue_of(mid)
        if _are_in_cyclic_order(left_hue, target_hue, mid_hue):
          right = mid
          r_plane = m_plane
        else:
          left = mid
          left_hue = mid_hue
          l_plane = m_plane
  return _lerp_point(left, 0.5, right)


def _find_result_by_j(hue_radians: float, chroma: float, y: float) -> int:
  j = math.sqrt(y) * 11.0
  viewing_conditions = DEFAULT_VIEWING_CONDITIONS
  t_inner_coeff = 1.0 / math.pow(
      1.64 - math.pow(0.29, viewing_conditions.background_y_to_white_point_y),
      0.73,
  )
  e_hue = 0.25 * (math.cos(hue_radians + 2.0) + 3.8)
  p1 = e_hue * (50000.0 / 13.0) * viewing_conditions.nc * viewing_conditions.ncb
  h_sin = math.sin(hue_radians)
  h_cos = math.cos(hue_radians)
  for iteration_round in range(5):
    j_normalized = j / 100.0
    alpha = (
        0.0 if (chroma == 0.0 or j == 0.0) else chroma / math.sqrt(j_normalized)
    )
    t = math.pow(alpha * t_inner_coeff, 1.0 / 0.9)
    ac = viewing_conditions.aw * math.pow(
        j_normalized,
        1.0 / viewing_conditions.c / viewing_conditions.z,
    )
    p2 = ac / viewing_conditions.nbb
    gamma = (
        23.0
        * (p2 + 0.305)
        * t
        / (23.0 * p1 + 11.0 * t * h_cos + 108.0 * t * h_sin)
    )
    a = gamma * h_cos
    b = gamma * h_sin
    r_a = (460.0 * p2 + 451.0 * a + 288.0 * b) / 1403.0
    g_a = (460.0 * p2 - 891.0 * a - 261.0 * b) / 1403.0
    b_a = (460.0 * p2 - 220.0 * a - 6300.0 * b) / 1403.0
    r_c_scaled = _inverse_chromatic_adaptation(r_a)
    g_c_scaled = _inverse_chromatic_adaptation(g_a)
    b_c_scaled = _inverse_chromatic_adaptation(b_a)
    linrgb = matrix_multiply(
        (r_c_scaled, g_c_scaled, b_c_scaled),
        _LINRGB_FROM_SCALED_DISCOUNT,
    )
    if linrgb[0] < 0 or linrgb[1] < 0 or linrgb[2] < 0:
      return 0
    k_r = 0.2126
    k_g = 0.7152
    k_b = 0.0722
    fnj = k_r * linrgb[0] + k_g * linrgb[1] + k_b * linrgb[2]
    if fnj <= 0:
      return 0
    if iteration_round == 4 or abs(fnj - y) < 0.002:
      if linrgb[0] > 100.01 or linrgb[1] > 100.01 or linrgb[2] > 100.01:
        return 0
      return argb_from_linrgb(linrgb)
    j = j - (fnj - y) * j / (2.0 * fnj)
  return 0


def solve_to_int(hue_degrees: float, chroma: float, lstar: float) -> int:
  if chroma < 0.0001 or lstar < 0.0001 or lstar > 99.9999:
    return argb_from_lstar(lstar)
  hue_degrees = sanitize_degrees_double(hue_degrees)
  hue_radians = hue_degrees / 180.0 * math.pi
  y = y_from_lstar(lstar)
  exact_answer = _find_result_by_j(hue_radians, chroma, y)
  if exact_answer != 0:
    return exact_answer
  linrgb = _bisect_to_limit(y, hue_degrees)
  return argb_from_linrgb(linrgb)


def argb_from_lstar(lstar: float) -> int:
  y = y_from_lstar(lstar)
  component = delinearized(y)
  return argb_from_rgb(component, component, component)


# ---------------------------------------------------------------------------
# HCT (cam/hct.h, cam/hct.cc)
# ---------------------------------------------------------------------------


class Hct:
  """HCT: Hue, Chroma, Tone color system that accounts for viewing conditions."""

  def __init__(self, argb: int):
    self._argb = argb
    cam = Cam16.from_int(argb)
    self._hue = cam.hue
    self._chroma = cam.chroma
    self._tone = lstar_from_argb(argb)

  @staticmethod
  def from_int(argb: int) -> Hct:
    return Hct(argb)

  @staticmethod
  def from_hct(hue: float, chroma: float, tone: float) -> Hct:
    return Hct(solve_to_int(hue, chroma, tone))

  @staticmethod
  def from_hex(hex_str: str) -> Hct:
    return Hct(argb_from_hex(hex_str))

  @property
  def hue(self) -> float:
    return self._hue

  @property
  def chroma(self) -> float:
    return self._chroma

  @property
  def tone(self) -> float:
    return self._tone

  def to_int(self) -> int:
    return self._argb

  def to_hex(self) -> str:
    return hex_from_argb(self._argb)

  def set_hue(self, new_hue: float) -> None:
    self._set_internal_state(solve_to_int(new_hue, self._chroma, self._tone))

  def set_chroma(self, new_chroma: float) -> None:
    self._set_internal_state(solve_to_int(self._hue, new_chroma, self._tone))

  def set_tone(self, new_tone: float) -> None:
    self._set_internal_state(solve_to_int(self._hue, self._chroma, new_tone))

  def _set_internal_state(self, argb: int) -> None:
    self._argb = argb
    cam = Cam16.from_int(argb)
    self._hue = cam.hue
    self._chroma = cam.chroma
    self._tone = lstar_from_argb(argb)

  def __repr__(self) -> str:
    return (
        f"Hct(hue={self._hue:.1f}, chroma={self._chroma:.1f},"
        f" tone={self._tone:.1f}, hex={self.to_hex()})"
    )


# ---------------------------------------------------------------------------
# TonalPalette & KeyColor (palettes/tones.cc)
# ---------------------------------------------------------------------------


class KeyColor:
  """Creates a key color from a hue and chroma that maximizes chroma near tone 50."""

  def __init__(self, hue: float, requested_chroma: float):
    self._hue = hue
    self._requested_chroma = requested_chroma
    self._chroma_cache: dict[int, float] = {}

  def create(self) -> Hct:
    pivot_tone = 50
    tone_step_size = 1
    epsilon = 0.01
    lower_tone = 0
    upper_tone = 100
    while lower_tone < upper_tone:
      mid_tone = int((lower_tone + upper_tone) / 2)
      is_ascending = self._max_chroma(mid_tone) < self._max_chroma(
          mid_tone + tone_step_size
      )
      sufficient_chroma = (
          self._max_chroma(mid_tone) >= self._requested_chroma - epsilon
      )
      if sufficient_chroma:
        if abs(lower_tone - pivot_tone) < abs(upper_tone - pivot_tone):
          upper_tone = mid_tone
        else:
          if lower_tone == mid_tone:
            return Hct.from_hct(
                self._hue, self._requested_chroma, float(lower_tone)
            )
          lower_tone = mid_tone
      else:
        if is_ascending:
          lower_tone = mid_tone + tone_step_size
        else:
          upper_tone = mid_tone
    return Hct.from_hct(self._hue, self._requested_chroma, float(lower_tone))

  def _max_chroma(self, tone: int) -> float:
    if tone not in self._chroma_cache:
      self._chroma_cache[tone] = Hct.from_hct(
          self._hue, 200.0, float(tone)
      ).chroma
    return self._chroma_cache[tone]


class TonalPalette:
  """A convenience class for retrieving colors that are constant in hue and chroma,

  but vary in tone (0..100).
  """

  def __init__(self, hue: float, chroma: float, key_color: Hct):
    self._hue = hue
    self._chroma = chroma
    self._key_color = key_color
    self._cache: dict[float, int] = {}

  @staticmethod
  def from_int(argb: int) -> TonalPalette:
    hct = Hct.from_int(argb)
    return TonalPalette.from_hct(hct)

  @staticmethod
  def from_hex(hex_str: str) -> TonalPalette:
    return TonalPalette.from_int(argb_from_hex(hex_str))

  @staticmethod
  def from_hct(hct: Hct) -> TonalPalette:
    return TonalPalette(hct.hue, hct.chroma, hct)

  @staticmethod
  def from_hue_and_chroma(hue: float, chroma: float) -> TonalPalette:
    key_color = KeyColor(hue, chroma).create()
    return TonalPalette(hue, chroma, key_color)

  @property
  def hue(self) -> float:
    return self._hue

  @property
  def chroma(self) -> float:
    return self._chroma

  @property
  def key_color(self) -> Hct:
    return self._key_color

  def get(self, tone: float) -> int:
    tone = clamp_double(0.0, 100.0, float(tone))
    key = round(tone, 2)
    if key in self._cache:
      return self._cache[key]
    argb = solve_to_int(self._hue, self._chroma, tone)
    self._cache[key] = argb
    return argb

  def tone(self, tone: float) -> int:
    return self.get(tone)

  def get_hct(self, tone: float) -> Hct:
    return Hct.from_hct(
        self._hue, self._chroma, clamp_double(0.0, 100.0, float(tone))
    )

  def get_hex(self, tone: float) -> str:
    return hex_from_argb(self.get(tone))


# ---------------------------------------------------------------------------
# Contrast (contrast/contrast.cc, dynamiccolor/dynamic_color.cc)
# ---------------------------------------------------------------------------


class Contrast:
  """Color math for WCAG contrast calculations in HCT / CIE L*."""

  @staticmethod
  def ratio_of_ys(y1: float, y2: float) -> float:
    lighter = max(y1, y2)
    darker = min(y1, y2)
    return (lighter + 5.0) / (darker + 5.0)

  @staticmethod
  def ratio_of_tones(t1: float, t2: float) -> float:
    t1 = clamp_double(0.0, 100.0, t1)
    t2 = clamp_double(0.0, 100.0, t2)
    return Contrast.ratio_of_ys(y_from_lstar(t1), y_from_lstar(t2))

  @staticmethod
  def lighter(tone: float, ratio: float) -> float:
    if tone < 0.0 or tone > 100.0:
      return -1.0
    dark_y = y_from_lstar(tone)
    light_y = ratio * (dark_y + 5.0) - 5.0
    if light_y < 0.0 or light_y > 100.0:
      return -1.0
    real_contrast = Contrast.ratio_of_ys(light_y, dark_y)
    delta = abs(real_contrast - ratio)
    if real_contrast < ratio and delta > 0.04:
      return -1.0
    return_value = lstar_from_y(light_y) + 0.4
    if return_value < 0.0 or return_value > 100.0:
      return -1.0
    return return_value

  @staticmethod
  def darker(tone: float, ratio: float) -> float:
    if tone < 0.0 or tone > 100.0:
      return -1.0
    light_y = y_from_lstar(tone)
    dark_y = ((light_y + 5.0) / ratio) - 5.0
    if dark_y < 0.0 or dark_y > 100.0:
      return -1.0
    real_contrast = Contrast.ratio_of_ys(light_y, dark_y)
    delta = abs(real_contrast - ratio)
    if real_contrast < ratio and delta > 0.04:
      return -1.0
    return_value = lstar_from_y(dark_y) - 0.4
    if return_value < 0.0 or return_value > 100.0:
      return -1.0
    return return_value

  @staticmethod
  def lighter_unsafe(tone: float, ratio: float) -> float:
    lighter_safe = Contrast.lighter(tone, ratio)
    return 100.0 if lighter_safe < 0.0 else lighter_safe

  @staticmethod
  def darker_unsafe(tone: float, ratio: float) -> float:
    darker_safe = Contrast.darker(tone, ratio)
    return 0.0 if darker_safe < 0.0 else darker_safe


def tone_prefers_light_foreground(tone: float) -> bool:
  return round(tone) < 60


def tone_allows_light_foreground(tone: float) -> bool:
  return round(tone) <= 49


def foreground_tone(bg_tone: float, ratio: float) -> float:
  lighter_tone = Contrast.lighter_unsafe(bg_tone, ratio)
  darker_tone = Contrast.darker_unsafe(bg_tone, ratio)
  lighter_ratio = Contrast.ratio_of_tones(lighter_tone, bg_tone)
  darker_ratio = Contrast.ratio_of_tones(darker_tone, bg_tone)
  prefer_lighter = tone_prefers_light_foreground(bg_tone)
  if prefer_lighter:
    negligible_difference = (
        abs(lighter_ratio - darker_ratio) < 0.1
        and lighter_ratio < ratio
        and darker_ratio < ratio
    )
    if (
        lighter_ratio >= ratio
        or lighter_ratio >= darker_ratio
        or negligible_difference
    ):
      return lighter_tone
    return darker_tone
  else:
    if darker_ratio >= ratio or darker_ratio >= lighter_ratio:
      return darker_tone
    return lighter_tone


def enable_light_foreground(tone: float) -> float:
  if tone_prefers_light_foreground(tone) and not tone_allows_light_foreground(
      tone
  ):
    return 49.0
  return tone


# ---------------------------------------------------------------------------
# Dislike Analyzer (dislike/dislike.cc)
# ---------------------------------------------------------------------------


def is_disliked(hct: Hct) -> bool:
  hue_passes = 90.0 <= round(hct.hue) <= 111.0
  chroma_passes = round(hct.chroma) > 16.0
  tone_passes = round(hct.tone) < 65.0
  return hue_passes and chroma_passes and tone_passes


def fix_if_disliked(hct: Hct) -> Hct:
  if is_disliked(hct):
    return Hct.from_hct(hct.hue, hct.chroma, 70.0)
  return hct


# ---------------------------------------------------------------------------
# TemperatureCache (temperature/temperature_cache.cc)
# ---------------------------------------------------------------------------


class TemperatureCache:
  """Design utilities using color temperature theory (warm/cool complement & analogous colors)."""

  def __init__(self, input_hct: Hct):
    self._input = input_hct
    self._precomputed_complement: Hct | None = None
    self._precomputed_hcts_by_temp: list[Hct] | None = None
    self._precomputed_hcts_by_hue: list[Hct] | None = None
    self._precomputed_temps_by_hct: dict[int, float] | None = None

  @staticmethod
  def raw_temperature(color: Hct) -> float:
    lab = lab_from_int(color.to_int())
    hue = sanitize_degrees_double(math.atan2(lab[2], lab[1]) * 180.0 / math.pi)
    chroma = math.hypot(lab[1], lab[2])
    return -0.5 + 0.02 * math.pow(chroma, 1.07) * math.cos(
        sanitize_degrees_double(hue - 50.0) * math.pi / 180.0
    )

  def get_complement(self) -> Hct:
    if self._precomputed_complement is not None:
      return self._precomputed_complement

    coldest_hue = self._get_coldest().hue
    coldest_temp = self._get_temps_by_hct()[self._get_coldest().to_int()]

    warmest_hue = self._get_warmest().hue
    warmest_temp = self._get_temps_by_hct()[self._get_warmest().to_int()]
    temp_range = warmest_temp - coldest_temp
    start_hue_is_coldest_to_warmest = self._is_between(
        self._input.hue, coldest_hue, warmest_hue
    )
    start_hue = warmest_hue if start_hue_is_coldest_to_warmest else coldest_hue
    end_hue = coldest_hue if start_hue_is_coldest_to_warmest else warmest_hue
    direction_of_rotation = 1.0
    smallest_error = 1000.0
    answer = self._get_hcts_by_hue()[int(round(self._input.hue)) % 360]

    complement_relative_temp = 1.0 - self.get_relative_temperature(self._input)
    for hue_addend in range(361):
      hue = sanitize_degrees_double(
          start_hue + direction_of_rotation * float(hue_addend)
      )
      if not self._is_between(hue, start_hue, end_hue):
        continue
      possible_answer = self._get_hcts_by_hue()[int(round(hue)) % 360]
      relative_temp = (
          (self._get_temps_by_hct()[possible_answer.to_int()] - coldest_temp)
          / temp_range
          if temp_range != 0.0
          else 0.5
      )
      error = abs(complement_relative_temp - relative_temp)
      if error < smallest_error:
        smallest_error = error
        answer = possible_answer

    self._precomputed_complement = answer
    return self._precomputed_complement

  def get_relative_temperature(self, hct: Hct) -> float:
    temp_range = (
        self._get_temps_by_hct()[self._get_warmest().to_int()]
        - self._get_temps_by_hct()[self._get_coldest().to_int()]
    )
    difference_from_coldest = (
        self._get_temps_by_hct()[hct.to_int()]
        - self._get_temps_by_hct()[self._get_coldest().to_int()]
    )
    if temp_range == 0.0:
      return 0.5
    return difference_from_coldest / temp_range

  def _get_hcts_by_hue(self) -> list[Hct]:
    if self._precomputed_hcts_by_hue is not None:
      return self._precomputed_hcts_by_hue
    hcts = [
        Hct.from_hct(float(hue), self._input.chroma, self._input.tone)
        for hue in range(361)
    ]
    self._precomputed_hcts_by_hue = hcts
    return hcts

  def _get_temps_by_hct(self) -> dict[int, float]:
    if self._precomputed_temps_by_hct is not None:
      return self._precomputed_temps_by_hct
    temps: dict[int, float] = {}
    for hct in self._get_hcts_by_hue():
      temps[hct.to_int()] = TemperatureCache.raw_temperature(hct)
    temps[self._input.to_int()] = TemperatureCache.raw_temperature(self._input)
    self._precomputed_temps_by_hct = temps
    return temps

  def _get_hcts_by_temp(self) -> list[Hct]:
    if self._precomputed_hcts_by_temp is not None:
      return self._precomputed_hcts_by_temp
    hcts = list(self._get_hcts_by_hue())
    hcts.append(self._input)
    temps = self._get_temps_by_hct()
    hcts.sort(key=lambda h: temps[h.to_int()])
    self._precomputed_hcts_by_temp = hcts
    return hcts

  def _get_coldest(self) -> Hct:
    return self._get_hcts_by_temp()[0]

  def _get_warmest(self) -> Hct:
    return self._get_hcts_by_temp()[-1]

  @staticmethod
  def _is_between(angle: float, a: float, b: float) -> bool:
    if a < b:
      return a <= angle <= b
    return a <= angle or angle <= b


# ---------------------------------------------------------------------------
# Blend (blend/blend.cc)
# ---------------------------------------------------------------------------


class Blend:
  """Functions for blending in HCT and CAM16-UCS."""

  @staticmethod
  def harmonize(design_color: int, source_color: int) -> int:
    from_hct = Hct.from_int(design_color)
    to_hct = Hct.from_int(source_color)
    difference_deg = difference_degrees(from_hct.hue, to_hct.hue)
    rotation_deg = min(difference_deg * 0.5, 15.0)
    output_hue = sanitize_degrees_double(
        from_hct.hue
        + rotation_deg * rotation_direction(from_hct.hue, to_hct.hue)
    )
    return Hct.from_hct(output_hue, from_hct.chroma, from_hct.tone).to_int()

  @staticmethod
  def hct_hue(from_argb: int, to_argb: int, amount: float) -> int:
    ucs = Blend.cam16_ucs(from_argb, to_argb, amount)
    ucs_cam = Cam16.from_int(ucs)
    from_cam = Cam16.from_int(from_argb)
    blended = Hct.from_hct(
        ucs_cam.hue, from_cam.chroma, lstar_from_argb(from_argb)
    )
    return blended.to_int()

  @staticmethod
  def cam16_ucs(from_argb: int, to_argb: int, amount: float) -> int:
    from_cam = Cam16.from_int(from_argb)
    to_cam = Cam16.from_int(to_argb)
    jstar = lerp(from_cam.jstar, to_cam.jstar, amount)
    astar = lerp(from_cam.astar, to_cam.astar, amount)
    bstar = lerp(from_cam.bstar, to_cam.bstar, amount)
    return Cam16.from_ucs(jstar, astar, bstar).to_int()


# ---------------------------------------------------------------------------
# DynamicScheme & Material 3 Scheme Variants (scheme/*.cc)
# ---------------------------------------------------------------------------


@dataclass
class DynamicScheme:
  """Material 3 DynamicScheme holding the 6 tonal palettes and mode."""

  source_color_hct: Hct
  variant: str
  is_dark: bool
  contrast_level: float
  primary_palette: TonalPalette
  secondary_palette: TonalPalette
  tertiary_palette: TonalPalette
  neutral_palette: TonalPalette
  neutral_variant_palette: TonalPalette
  error_palette: TonalPalette


def build_dynamic_scheme(
    source_color_hct: Hct,
    *,
    is_dark: bool = False,
    variant: str = "FIDELITY",
    contrast_level: float = 0.0,
    secondary_key_hct: Hct | None = None,
    tertiary_key_hct: Hct | None = None,
    neutral_key_hct: Hct | None = None,
    neutral_variant_key_hct: Hct | None = None,
    error_key_hct: Hct | None = None,
) -> DynamicScheme:
  """Builds a Material 3 DynamicScheme from a source color and optional reference key colors."""
  variant_norm = (variant or "FIDELITY").strip().upper().replace("-", "_")
  h = source_color_hct.hue
  c = source_color_hct.chroma

  if variant_norm == "MONOCHROME":
    primary_palette = TonalPalette.from_hue_and_chroma(h, 0.0)
    secondary_palette = TonalPalette.from_hue_and_chroma(h, 0.0)
    tertiary_palette = TonalPalette.from_hue_and_chroma(h, 0.0)
    neutral_palette = TonalPalette.from_hue_and_chroma(h, 0.0)
    neutral_variant_palette = TonalPalette.from_hue_and_chroma(h, 0.0)
  elif variant_norm == "NEUTRAL":
    primary_palette = TonalPalette.from_hue_and_chroma(h, 12.0)
    secondary_palette = TonalPalette.from_hue_and_chroma(h, 8.0)
    tertiary_palette = TonalPalette.from_hue_and_chroma(h, 16.0)
    neutral_palette = TonalPalette.from_hue_and_chroma(h, 2.0)
    neutral_variant_palette = TonalPalette.from_hue_and_chroma(h, 2.0)
  elif variant_norm == "VIBRANT":
    primary_palette = TonalPalette.from_hue_and_chroma(h, 200.0)
    secondary_palette = TonalPalette.from_hue_and_chroma(
        sanitize_degrees_double(h + 18.0), 24.0
    )
    tertiary_palette = TonalPalette.from_hue_and_chroma(
        sanitize_degrees_double(h + 35.0), 32.0
    )
    neutral_palette = TonalPalette.from_hue_and_chroma(h, 10.0)
    neutral_variant_palette = TonalPalette.from_hue_and_chroma(h, 12.0)
  elif variant_norm == "EXPRESSIVE":
    primary_palette = TonalPalette.from_hue_and_chroma(
        sanitize_degrees_double(h + 240.0), 40.0
    )
    secondary_palette = TonalPalette.from_hue_and_chroma(
        sanitize_degrees_double(h + 45.0), 24.0
    )
    tertiary_palette = TonalPalette.from_hue_and_chroma(
        sanitize_degrees_double(h + 120.0), 32.0
    )
    neutral_palette = TonalPalette.from_hue_and_chroma(
        sanitize_degrees_double(h + 15.0), 8.0
    )
    neutral_variant_palette = TonalPalette.from_hue_and_chroma(
        sanitize_degrees_double(h + 15.0), 12.0
    )
  elif variant_norm == "TONAL_SPOT":
    primary_palette = TonalPalette.from_hue_and_chroma(h, 36.0)
    secondary_palette = TonalPalette.from_hue_and_chroma(h, 16.0)
    tertiary_palette = TonalPalette.from_hue_and_chroma(
        sanitize_degrees_double(h + 60.0), 24.0
    )
    neutral_palette = TonalPalette.from_hue_and_chroma(h, 6.0)
    neutral_variant_palette = TonalPalette.from_hue_and_chroma(h, 8.0)
  elif variant_norm == "CONTENT":
    primary_palette = TonalPalette.from_hue_and_chroma(h, c)
    secondary_palette = TonalPalette.from_hue_and_chroma(
        h, max(c - 32.0, c * 0.5)
    )
    tertiary_hct = fix_if_disliked(
        TemperatureCache(source_color_hct).get_complement()
    )
    tertiary_palette = TonalPalette.from_hct(tertiary_hct)
    neutral_palette = TonalPalette.from_hue_and_chroma(h, c / 8.0)
    neutral_variant_palette = TonalPalette.from_hue_and_chroma(
        h, (c / 8.0) + 4.0
    )
  else:
    # Default: FIDELITY (SchemeFidelity in material-color-utilities)
    primary_palette = TonalPalette.from_hct(source_color_hct)
    secondary_palette = TonalPalette.from_hue_and_chroma(
        h, max(c - 32.0, c * 0.5)
    )
    tertiary_hct = fix_if_disliked(
        TemperatureCache(source_color_hct).get_complement()
    )
    tertiary_palette = TonalPalette.from_hct(tertiary_hct)
    neutral_palette = TonalPalette.from_hue_and_chroma(h, c / 8.0)
    neutral_variant_palette = TonalPalette.from_hue_and_chroma(
        h, (c / 8.0) + 4.0
    )

  # Adapt palettes whenever the reference palette provides explicit key colors
  # for secondary, tertiary, neutral, neutral_variant, or error.
  if secondary_key_hct is not None:
    secondary_palette = TonalPalette.from_hct(secondary_key_hct)

  if tertiary_key_hct is not None:
    tertiary_palette = TonalPalette.from_hct(tertiary_key_hct)

  if neutral_key_hct is not None:
    if neutral_key_hct.chroma <= 24.0:
      neutral_palette = TonalPalette.from_hct(neutral_key_hct)
    else:
      neutral_palette = TonalPalette.from_hue_and_chroma(
          neutral_key_hct.hue, 16.0
      )
    if neutral_variant_key_hct is None:
      neutral_variant_palette = TonalPalette.from_hue_and_chroma(
          neutral_palette.hue, min(neutral_palette.chroma + 4.0, 20.0)
      )

  if neutral_variant_key_hct is not None:
    if neutral_variant_key_hct.chroma <= 28.0:
      neutral_variant_palette = TonalPalette.from_hct(neutral_variant_key_hct)
    else:
      neutral_variant_palette = TonalPalette.from_hue_and_chroma(
          neutral_variant_key_hct.hue, 24.0
      )

  if error_key_hct is not None:
    error_palette = TonalPalette.from_hct(error_key_hct)
  else:
    # Standard M3 error palette (hue 25, chroma 84) harmonized toward the source color
    base_error_argb = Hct.from_hct(25.0, 84.0, 40.0).to_int()
    harmonized_error = Hct.from_int(
        Blend.harmonize(base_error_argb, source_color_hct.to_int())
    )
    error_palette = TonalPalette.from_hue_and_chroma(
        harmonized_error.hue, max(harmonized_error.chroma, 72.0)
    )

  return DynamicScheme(
      source_color_hct=source_color_hct,
      variant=variant_norm,
      is_dark=is_dark,
      contrast_level=contrast_level,
      primary_palette=primary_palette,
      secondary_palette=secondary_palette,
      tertiary_palette=tertiary_palette,
      neutral_palette=neutral_palette,
      neutral_variant_palette=neutral_variant_palette,
      error_palette=error_palette,
  )


# ---------------------------------------------------------------------------
# Adaptive Role Resolution for the 22 Canonical DCB Color Tokens
# ---------------------------------------------------------------------------


def _contrast_safe_tone(
    bg_tone: float,
    preferred_tone: float,
    min_ratio: float = 4.5,
) -> float:
  """Returns `preferred_tone` if it meets `min_ratio` against `bg_tone`, else adapts via Contrast."""
  if Contrast.ratio_of_tones(bg_tone, preferred_tone) >= min_ratio:
    return preferred_tone
  return foreground_tone(bg_tone, min_ratio)


def _container_tone_for_role(
    role_tone: float,
    is_dark: bool,
    is_fidelity_primary: bool = False,
    source_tone: float | None = None,
) -> float:
  """Computes a container tone distinct from `role_tone` following M3 ToneDeltaPair rules."""
  if is_fidelity_primary and source_tone is not None:
    default_container = source_tone
  else:
    default_container = 30.0 if is_dark else 90.0

  if abs(default_container - role_tone) < 15.0:
    if is_dark:
      return clamp_double(
          10.0,
          95.0,
          role_tone - 35.0 if role_tone >= 50.0 else role_tone + 35.0,
      )
    return clamp_double(
        10.0,
        95.0,
        role_tone + 35.0 if role_tone <= 55.0 else role_tone - 35.0,
    )
  return default_container


def _extract_reference_key_colors(
    explicit_colors: dict[str, str],
    facts: dict[str, Any] | None,
    current_theme: dict[str, Any] | None,
) -> tuple[
    Hct,
    Hct | None,
    Hct | None,
    Hct | None,
    Hct | None,
    Hct | None,
    bool,
    str,
    str,
]:
  """Extracts key colors (primary, secondary, tertiary, neutral, neutral_variant, error),

  `is_dark`, `variant`, and `seed_hex` from explicit token overrides and
  reference `facts`.
  """
  ds = (facts or {}).get("design_system")
  if not isinstance(ds, dict):
    ds = {}
  raw_fact_colors = (facts or {}).get("colors")
  fact_colors = raw_fact_colors if isinstance(raw_fact_colors, list) else []

  parsed_facts: list[tuple[str, Hct, list[str], int]] = []
  for item in fact_colors:
    if not isinstance(item, dict):
      continue
    hx = item.get("hex")
    if not is_valid_hex(hx):
      continue
    try:
      hct = Hct.from_hex(hx)
    except ValueError:
      continue
    roles = [str(r).lower() for r in item.get("roles") or [] if r]
    count = int(item.get("count") or 1)
    parsed_facts.append((hct.to_hex(), hct, roles, count))

  explicit_scheme = explicit_colors.get("scheme", "").strip().lower()
  ds_mode = str(ds.get("color_mode") or "").strip().lower()
  is_dark: bool | None = None
  if explicit_scheme in ("dark", "light"):
    is_dark = explicit_scheme == "dark"
  elif ds_mode in ("dark", "light"):
    is_dark = ds_mode == "dark"
  elif "surface" in explicit_colors and is_valid_hex(
      explicit_colors["surface"]
  ):
    is_dark = Hct.from_hex(explicit_colors["surface"]).tone < 50.0
  elif "surface_container" in explicit_colors and is_valid_hex(
      explicit_colors["surface_container"]
  ):
    is_dark = Hct.from_hex(explicit_colors["surface_container"]).tone < 50.0
  elif "surface_container_low" in explicit_colors and is_valid_hex(
      explicit_colors["surface_container_low"]
  ):
    is_dark = Hct.from_hex(explicit_colors["surface_container_low"]).tone < 50.0
  elif "on_surface" in explicit_colors and is_valid_hex(
      explicit_colors["on_surface"]
  ):
    is_dark = Hct.from_hex(explicit_colors["on_surface"]).tone > 55.0
  elif is_valid_hex(ds.get("neutral")):
    n_tone = Hct.from_hex(ds["neutral"]).tone
    if n_tone < 35.0:
      is_dark = True
    elif n_tone > 70.0:
      is_dark = False
  if is_dark is None:
    for _, hct, roles, _ in parsed_facts:
      if any(
          any(k in r for k in ("surface", "background", "bg", "canvas", "page"))
          for r in roles
      ):
        if hct.tone < 35.0:
          is_dark = True
          break
        elif hct.tone > 70.0:
          is_dark = False
          break
  if is_dark is None:
    is_dark = (
        str((current_theme or {}).get("scheme") or "light").strip().lower()
        == "dark"
    )

  variant = (
      str(ds.get("color_variant") or "FIDELITY")
      .strip()
      .upper()
      .replace("-", "_")
  )
  if variant not in (
      "FIDELITY",
      "CONTENT",
      "TONAL_SPOT",
      "VIBRANT",
      "EXPRESSIVE",
      "NEUTRAL",
      "MONOCHROME",
  ):
    variant = "FIDELITY"

  primary_hex: str | None = None
  for candidate in (
      explicit_colors.get("primary"),
      ds.get("primary"),
      ds.get("primary_color"),
      ds.get("seed_color"),
      explicit_colors.get("primary_container"),
      explicit_colors.get("on_primary_container"),
  ):
    if is_valid_hex(candidate):
      primary_hex = Hct.from_hex(str(candidate)).to_hex()
      break

  if not primary_hex:
    for hx, hct, roles, _ in parsed_facts:
      if any("primary" in r or "brand" in r for r in roles):
        primary_hex = hx
        break
  if not primary_hex:
    chromatic_facts = [
        (hx, hct, roles, count)
        for hx, hct, roles, count in parsed_facts
        if hct.chroma >= 12.0 and 10.0 <= hct.tone <= 92.0
    ]
    if chromatic_facts:
      primary_hex = chromatic_facts[0][0]
  if not primary_hex:
    for tok in (
        "secondary",
        "tertiary",
        "secondary_container",
        "on_primary",
        "surface",
        "on_surface",
        "outline",
        "error",
    ):
      if is_valid_hex(explicit_colors.get(tok)):
        primary_hex = Hct.from_hex(explicit_colors[tok]).to_hex()
        break
  if not primary_hex and parsed_facts:
    primary_hex = parsed_facts[0][0]
  if not primary_hex:
    fallback_p = (current_theme or {}).get("primary") or "#0B57D0"
    primary_hex = (
        Hct.from_hex(fallback_p).to_hex()
        if is_valid_hex(fallback_p)
        else "#0B57D0"
    )

  primary_hct = Hct.from_hex(primary_hex)

  secondary_hct: Hct | None = None
  for candidate in (
      explicit_colors.get("secondary"),
      ds.get("secondary"),
      ds.get("secondary_color"),
      explicit_colors.get("secondary_container"),
  ):
    if is_valid_hex(candidate):
      secondary_hct = Hct.from_hex(str(candidate))
      break
  if secondary_hct is None:
    for hx, hct, roles, _ in parsed_facts:
      if any("secondary" in r for r in roles):
        secondary_hct = hct
        break
  if secondary_hct is None:
    for hx, hct, roles, _ in parsed_facts:
      if (
          hct.chroma >= 10.0
          and difference_degrees(hct.hue, primary_hct.hue) >= 18.0
          and not any("error" in r or "danger" in r for r in roles)
      ):
        secondary_hct = hct
        break

  tertiary_hct: Hct | None = None
  for candidate in (
      explicit_colors.get("tertiary"),
      ds.get("tertiary"),
      ds.get("tertiary_color"),
      explicit_colors.get("on_tertiary"),
  ):
    if is_valid_hex(candidate):
      cand_hct = Hct.from_hex(str(candidate))
      if (
          candidate == explicit_colors.get("on_tertiary")
          and cand_hct.chroma < 8.0
      ):
        continue
      tertiary_hct = cand_hct
      break
  if tertiary_hct is None:
    for hx, hct, roles, _ in parsed_facts:
      if any("tertiary" in r or "accent" in r for r in roles):
        tertiary_hct = hct
        break
  if tertiary_hct is None:
    for hx, hct, roles, _ in parsed_facts:
      if (
          hct.chroma >= 14.0
          and difference_degrees(hct.hue, primary_hct.hue) >= 30.0
          and (
              secondary_hct is None
              or difference_degrees(hct.hue, secondary_hct.hue) >= 20.0
          )
          and not any("error" in r or "danger" in r for r in roles)
      ):
        tertiary_hct = hct
        break

  neutral_hct: Hct | None = None
  for candidate in (
      explicit_colors.get("surface"),
      explicit_colors.get("surface_container"),
      explicit_colors.get("surface_container_low"),
      explicit_colors.get("surface_container_high"),
      ds.get("neutral"),
      ds.get("neutral_color"),
      ds.get("background_color"),
      ds.get("surface"),
  ):
    if is_valid_hex(candidate):
      neutral_hct = Hct.from_hex(str(candidate))
      break
  if neutral_hct is None:
    for hx, hct, roles, _ in parsed_facts:
      if hct.chroma <= 18.0 and any(
          any(k in r for k in ("surface", "background", "bg", "neutral"))
          for r in roles
      ):
        neutral_hct = hct
        break

  neutral_variant_hct: Hct | None = None
  for candidate in (
      explicit_colors.get("outline"),
      explicit_colors.get("outline_variant"),
      explicit_colors.get("on_surface_variant"),
  ):
    if is_valid_hex(candidate):
      neutral_variant_hct = Hct.from_hex(str(candidate))
      break

  error_hct: Hct | None = None
  for candidate in (
      explicit_colors.get("error"),
      explicit_colors.get("error_container"),
  ):
    if is_valid_hex(candidate):
      error_hct = Hct.from_hex(str(candidate))
      break

  return (
      primary_hct,
      secondary_hct,
      tertiary_hct,
      neutral_hct,
      neutral_variant_hct,
      error_hct,
      bool(is_dark),
      variant,
      primary_hct.to_hex(),
  )


def compute_m3_color_tokens(
    explicit_colors: dict[str, str],
    *,
    facts: dict[str, Any] | None = None,
    current_theme: dict[str, Any] | None = None,
) -> dict[str, str]:
  """Computes a complete 22-token Material 3 palette + `scheme` adapted to `explicit_colors` and `facts`.

  Any token already present in `explicit_colors` (as a valid hex color) is
  preserved,
  and paired/related tokens (`on_*`, `*_container`, `surface_container_*`,
  `outline*`)
  adapt their HCT tones to guarantee contrast and tonal hierarchy against the
  explicitly provided key colors.
  """
  normalized_explicit: dict[str, str] = {}
  for k, v in (explicit_colors or {}).items():
    if k == "scheme" and str(v).strip().lower() in ("light", "dark"):
      normalized_explicit["scheme"] = str(v).strip().lower()
    elif is_valid_hex(v):
      s = str(v).strip()
      if s.startswith("#") and len(s) == 7:
        normalized_explicit[k] = s
      else:
        normalized_explicit[k] = hex_from_argb(argb_from_hex(s))

  (
      primary_hct,
      secondary_hct,
      tertiary_hct,
      neutral_hct,
      neutral_variant_hct,
      error_hct,
      is_dark,
      variant,
      _,
  ) = _extract_reference_key_colors(normalized_explicit, facts, current_theme)

  scheme = build_dynamic_scheme(
      primary_hct,
      is_dark=is_dark,
      variant=variant,
      secondary_key_hct=secondary_hct,
      tertiary_key_hct=tertiary_hct,
      neutral_key_hct=neutral_hct,
      neutral_variant_key_hct=neutral_variant_hct,
      error_key_hct=error_hct,
  )

  out: dict[str, str] = {}
  out["scheme"] = "dark" if is_dark else "light"

  def _pick_or_compute(role: str, argb: int) -> tuple[str, float]:
    if role in normalized_explicit:
      hx = normalized_explicit[role]
      return hx, Hct.from_hex(hx).tone
    hx = hex_from_argb(argb)
    return hx, lstar_from_argb(argb)

  # --- Primary family ---
  default_primary_tone = 80.0 if is_dark else 40.0
  out["primary"], p_tone = _pick_or_compute(
      "primary", scheme.primary_palette.get(default_primary_tone)
  )

  preferred_on_p = 20.0 if is_dark else 100.0
  on_p_tone = _contrast_safe_tone(p_tone, preferred_on_p, 4.5)
  out["on_primary"], _ = _pick_or_compute(
      "on_primary", scheme.primary_palette.get(on_p_tone)
  )

  pc_default_tone = _container_tone_for_role(p_tone, is_dark)
  out["primary_container"], pc_tone = _pick_or_compute(
      "primary_container", scheme.primary_palette.get(pc_default_tone)
  )

  preferred_on_pc = 90.0 if pc_tone < 55.0 else 10.0
  on_pc_tone = _contrast_safe_tone(pc_tone, preferred_on_pc, 4.5)
  out["on_primary_container"], _ = _pick_or_compute(
      "on_primary_container", scheme.primary_palette.get(on_pc_tone)
  )

  # --- Secondary family ---
  default_sec_tone = 80.0 if is_dark else 40.0
  out["secondary"], s_tone = _pick_or_compute(
      "secondary", scheme.secondary_palette.get(default_sec_tone)
  )

  preferred_on_s = 20.0 if is_dark else 100.0
  on_s_tone = _contrast_safe_tone(s_tone, preferred_on_s, 4.5)
  out["on_secondary"], _ = _pick_or_compute(
      "on_secondary", scheme.secondary_palette.get(on_s_tone)
  )

  sc_default_tone = _container_tone_for_role(s_tone, is_dark)
  out["secondary_container"], sc_tone = _pick_or_compute(
      "secondary_container", scheme.secondary_palette.get(sc_default_tone)
  )

  preferred_on_sc = 90.0 if sc_tone < 55.0 else 10.0
  on_sc_tone = _contrast_safe_tone(sc_tone, preferred_on_sc, 4.5)
  out["on_secondary_container"], _ = _pick_or_compute(
      "on_secondary_container", scheme.secondary_palette.get(on_sc_tone)
  )

  # --- Tertiary family ---
  default_tert_tone = 80.0 if is_dark else 40.0
  out["tertiary"], t_tone = _pick_or_compute(
      "tertiary", scheme.tertiary_palette.get(default_tert_tone)
  )

  preferred_on_t = 20.0 if is_dark else 100.0
  on_t_tone = _contrast_safe_tone(t_tone, preferred_on_t, 4.5)
  out["on_tertiary"], _ = _pick_or_compute(
      "on_tertiary", scheme.tertiary_palette.get(on_t_tone)
  )

  # --- Surface / Neutral family ---
  default_surf_tone = 6.0 if is_dark else 98.0
  out["surface"], surf_tone = _pick_or_compute(
      "surface", scheme.neutral_palette.get(default_surf_tone)
  )

  preferred_on_surf = 90.0 if is_dark else 10.0
  on_surf_tone = _contrast_safe_tone(surf_tone, preferred_on_surf, 4.5)
  out["on_surface"], _ = _pick_or_compute(
      "on_surface", scheme.neutral_palette.get(on_surf_tone)
  )

  if "surface" in normalized_explicit:
    if is_dark:
      scl_tone = clamp_double(0.0, 100.0, surf_tone + 4.0)
      sc_tone_surf = clamp_double(0.0, 100.0, surf_tone + 6.0)
      sch_tone = clamp_double(0.0, 100.0, surf_tone + 11.0)
    else:
      scl_tone = clamp_double(0.0, 100.0, surf_tone - 2.0)
      sc_tone_surf = clamp_double(0.0, 100.0, surf_tone - 4.0)
      sch_tone = clamp_double(0.0, 100.0, surf_tone - 6.0)
  else:
    scl_tone = 10.0 if is_dark else 96.0
    sc_tone_surf = 12.0 if is_dark else 94.0
    sch_tone = 17.0 if is_dark else 92.0

  out["surface_container_low"], _ = _pick_or_compute(
      "surface_container_low", scheme.neutral_palette.get(scl_tone)
  )
  out["surface_container"], _ = _pick_or_compute(
      "surface_container", scheme.neutral_palette.get(sc_tone_surf)
  )
  out["surface_container_high"], _ = _pick_or_compute(
      "surface_container_high", scheme.neutral_palette.get(sch_tone)
  )

  # --- Neutral Variant / Outline family ---
  preferred_osv = 80.0 if is_dark else 30.0
  osv_tone = _contrast_safe_tone(surf_tone, preferred_osv, 4.5)
  out["on_surface_variant"], _ = _pick_or_compute(
      "on_surface_variant", scheme.neutral_variant_palette.get(osv_tone)
  )

  preferred_outline = 60.0 if is_dark else 50.0
  outline_tone = _contrast_safe_tone(surf_tone, preferred_outline, 3.0)
  out["outline"], _ = _pick_or_compute(
      "outline", scheme.neutral_variant_palette.get(outline_tone)
  )

  default_outline_var_tone = 30.0 if is_dark else 80.0
  if abs(default_outline_var_tone - surf_tone) < 10.0:
    default_outline_var_tone = (
        clamp_double(0.0, 100.0, surf_tone + 18.0)
        if is_dark
        else clamp_double(0.0, 100.0, surf_tone - 18.0)
    )
  out["outline_variant"], _ = _pick_or_compute(
      "outline_variant",
      scheme.neutral_variant_palette.get(default_outline_var_tone),
  )

  # --- Error family ---
  default_err_tone = 80.0 if is_dark else 40.0
  out["error"], err_tone = _pick_or_compute(
      "error", scheme.error_palette.get(default_err_tone)
  )

  preferred_on_err = 20.0 if is_dark else 100.0
  on_err_tone = _contrast_safe_tone(err_tone, preferred_on_err, 4.5)
  out["on_error"], _ = _pick_or_compute(
      "on_error", scheme.error_palette.get(on_err_tone)
  )

  ec_default_tone = _container_tone_for_role(err_tone, is_dark)
  out["error_container"], ec_tone = _pick_or_compute(
      "error_container", scheme.error_palette.get(ec_default_tone)
  )

  preferred_on_ec = 90.0 if ec_tone < 55.0 else 10.0
  on_ec_tone = _contrast_safe_tone(ec_tone, preferred_on_ec, 4.5)
  out["on_error_container"], _ = _pick_or_compute(
      "on_error_container", scheme.error_palette.get(on_ec_tone)
  )

  return out
