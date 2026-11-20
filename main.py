#!/usr/bin/env python3
"""Friday 20 November 2026, in one image.

A program that draws a newspaper front page summing up the world's news of
Friday 20 November 2026 and writes it to /output/entry.png (or
$MAKEFUN_OUTPUT_DIR/entry.png when that is set, for local runs).

Pure standard library: no network, no files read at run time, no clock, no
randomness. Two runs write the same bytes. The picture is 1024x1024 RGB and
under 1 MiB.

Every story on the page happened, or was first reported, on Friday
20 November 2026, as reported that day by SCMP, AP, Reuters, OneTurn and
the Open Chronicle. The count in the top bar is the count of stories the
page actually lists: 3 hero stories, 6 mid stories, 22 more below.

Type is drawn from glyph bitmaps embedded in fonts.py (DejaVu, extracted
from the system fonts on the build machine); the dotted world map comes
from the land mask in worldmap.py. Nothing else is embedded.
"""

import base64
import json
import math
import os
import struct
import sys
import zlib

from fonts import FONTS
from worldmap import MAP_BITS, MAP_H, MAP_W

W, H = 1024, 1024

PAPER = (247, 243, 232)
INK = (17, 17, 17)
RED = (176, 42, 32)
BLUE = (31, 78, 121)
NAVY = (16, 28, 46)
GOLD = (176, 132, 32)
GREY = (96, 92, 82)
FAINT = (232, 226, 210)
CREAM = (238, 231, 214)
WHITE = (255, 255, 255)
SKY = (31, 58, 95)
STONE = (120, 110, 92)


# ---------------------------------------------------------------- fonts

def _decode_font(blob):
    data = json.loads(zlib.decompress(base64.b64decode(blob)))
    glyphs = {}
    for ch, g in data["glyphs"].items():
        if "px" not in g:
            glyphs[ch] = {"adv": g["adv"]}
            continue
        raw = base64.b64decode(g["px"])
        alpha = bytearray(g["w"] * g["h"])
        for i in range(g["w"] * g["h"]):
            byte = raw[i // 2]
            alpha[i] = (byte >> 4) if i % 2 == 0 else (byte & 15)
        glyphs[ch] = {
            "adv": g["adv"], "w": g["w"], "h": g["h"],
            "x0": g["x0"], "y0": g["y0"], "alpha": bytes(alpha),
        }
    return {"ascent": data["ascent"], "descent": data["descent"],
            "size": data["size"], "glyphs": glyphs}


FONTS_D = {name: _decode_font(blob) for name, blob in FONTS.items()}


def text_width(font_name, text, tracking=0):
    font = FONTS_D[font_name]
    total = 0
    for ch in text:
        g = font["glyphs"].get(ch)
        total += (g["adv"] if g else font["size"] // 2) + tracking
    return total - tracking if text else 0


def wrap_text(font_name, text, max_w, tracking=0):
    """Greedy word wrap. Returns a list of lines that fit max_w."""
    words = text.split(" ")
    lines, cur = [], ""
    for word in words:
        trial = word if not cur else cur + " " + word
        if text_width(font_name, trial, tracking) <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


# ---------------------------------------------------------------- canvas

class Canvas:
    def __init__(self, w, h, bg):
        self.w, self.h = w, h
        self.px = bytearray(w * h * 3)
        self.fill_rect(0, 0, w, h, bg)

    def _idx(self, x, y):
        return (y * self.w + x) * 3

    def set(self, x, y, color):
        if 0 <= x < self.w and 0 <= y < self.h:
            i = self._idx(x, y)
            self.px[i], self.px[i + 1], self.px[i + 2] = color

    def blend(self, x, y, color, a15):
        """Blend color over the pixel with alpha a15 (0..15)."""
        if a15 <= 0 or not (0 <= x < self.w and 0 <= y < self.h):
            return
        i = self._idx(x, y)
        if a15 >= 15:
            self.px[i], self.px[i + 1], self.px[i + 2] = color
            return
        for k in range(3):
            self.px[i + k] = (self.px[i + k] * (15 - a15) + color[k] * a15) // 15

    def fill_rect(self, x, y, w, h, color):
        x0, y0 = max(0, x), max(0, y)
        x1, y1 = min(self.w, x + w), min(self.h, y + h)
        for yy in range(y0, y1):
            i = self._idx(x0, yy)
            for xx in range(x0, x1):
                self.px[i], self.px[i + 1], self.px[i + 2] = color
                i += 3

    def frame(self, x, y, w, h, color, t=1):
        self.fill_rect(x, y, w, t, color)
        self.fill_rect(x, y + h - t, w, t, color)
        self.fill_rect(x, y, t, h, color)
        self.fill_rect(x + w - t, y, t, h, color)

    def hline(self, x, y, w, color, t=1):
        self.fill_rect(x, y, w, t, color)

    def vline(self, x, y, h, color, t=1):
        self.fill_rect(x, y, t, h, color)

    def line(self, x0, y0, x1, y1, color, t=1):
        """Bresenham line, thickness t (square brush)."""
        dx = abs(x1 - x0)
        dy = -abs(y1 - y0)
        sx = 1 if x0 < x1 else -1
        sy = 1 if y0 < y1 else -1
        err = dx + dy
        x, y = x0, y0
        r = t // 2
        while True:
            self.fill_rect(x - r, y - r, t, t, color)
            if x == x1 and y == y1:
                break
            e2 = 2 * err
            if e2 >= dy:
                err += dy
                x += sx
            if e2 <= dx:
                err += dx
                y += sy

    def disc(self, cx, cy, r, color):
        for yy in range(cy - r, cy + r + 1):
            dx = int((r * r - (yy - cy) * (yy - cy)) ** 0.5) if abs(yy - cy) <= r else 0
            self.fill_rect(cx - dx, yy, 2 * dx + 1, 1, color)

    def ring(self, cx, cy, r, color, t=2):
        for yy in range(cy - r - 1, cy + r + 2):
            for xx in range(cx - r - 1, cx + r + 2):
                d = ((xx - cx) ** 2 + (yy - cy) ** 2) ** 0.5
                if abs(d - r) <= t / 2.0:
                    self.set(xx, yy, color)

    def polygon(self, points, color):
        """Scanline fill of a simple polygon (list of (x, y))."""
        if len(points) < 3:
            return
        ys = [p[1] for p in points]
        for yy in range(max(0, min(ys)), min(self.h, max(ys) + 1)):
            nodes = []
            n = len(points)
            for i in range(n):
                x0, y0 = points[i]
                x1, y1 = points[(i + 1) % n]
                if (y0 <= yy < y1) or (y1 <= yy < y0):
                    nodes.append(x0 + (yy - y0) * (x1 - x0) / (y1 - y0))
            nodes.sort()
            for i in range(0, len(nodes) - 1, 2):
                xa, xb = int(nodes[i]), int(nodes[i + 1])
                self.fill_rect(xa, yy, xb - xa + 1, 1, color)

    def ellipse(self, cx, cy, rx, ry, color):
        pts = []
        steps = 48
        for i in range(steps):
            a = 2 * math.pi * i / steps
            pts.append((int(cx + rx * math.cos(a)),
                        int(cy + ry * math.sin(a))))
        self.polygon(pts, color)

    # ------------------------------------------------------------ text

    def draw_text(self, font_name, x, y_top, text, color, align="left",
                  tracking=0):
        """Draw text with its ascender line at y_top. Returns width drawn."""
        font = FONTS_D[font_name]
        width = text_width(font_name, text, tracking)
        if align == "center":
            x -= width // 2
        elif align == "right":
            x -= width
        cx = x
        for ch in text:
            g = font["glyphs"].get(ch)
            if g is None:
                g = font["glyphs"].get("?")
            if g is None:
                cx += font["size"] // 2 + tracking
                continue
            if "alpha" in g:
                for row in range(g["h"]):
                    base = row * g["w"]
                    for col in range(g["w"]):
                        a = g["alpha"][base + col]
                        if a:
                            self.blend(cx + g["x0"] + col, y_top + g["y0"] + row,
                                       color, a)
            cx += g["adv"] + tracking
        return width

    def draw_wrapped(self, font_name, x, y_top, text, color, max_w,
                     leading=None, tracking=0):
        """Draw wrapped text; returns the y just below the last line.
        A newline in the text forces a line break."""
        font = FONTS_D[font_name]
        lh = leading if leading is not None else int(font["size"] * 1.18)
        y = y_top
        for part in text.split("\n"):
            for line in wrap_text(font_name, part, max_w, tracking):
                self.draw_text(font_name, x, y, line, color, tracking=tracking)
                y += lh
        return y


def write_png(path, canvas):
    """Deterministic RGB PNG: fixed filter 0, zlib level 9, one IDAT."""
    w, h = canvas.w, canvas.h
    raw = bytearray()
    stride = w * 3
    for y in range(h):
        raw.append(0)
        raw += canvas.px[y * stride:(y + 1) * stride]
    comp = zlib.compress(bytes(raw), 9)

    def chunk(tag, data):
        out = struct.pack(">I", len(data)) + tag + data
        out += struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        return out

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", comp)
    png += chunk(b"IEND", b"")
    with open(path, "wb") as f:
        f.write(png)
    return len(png)

# ---------------------------------------------------------------- flags

def draw_star(c, cx, cy, r, color):
    pts = []
    for i in range(10):
        ang = -math.pi / 2 + i * math.pi / 5
        rr = r if i % 2 == 0 else r * 0.42
        pts.append((int(cx + rr * math.cos(ang)), int(cy + rr * math.sin(ang))))
    c.polygon(pts, color)


def draw_us_flag(c, x, y, w, h):
    stripe = h / 13.0
    for i in range(13):
        c.fill_rect(x, int(y + i * stripe), w, int(stripe) + 1,
                    (178, 34, 52) if i % 2 == 0 else WHITE)
    cw, ch = int(w * 0.4), int(stripe * 7)
    c.fill_rect(x, y, cw, ch, (60, 59, 110))
    for row in range(5):
        for col in range(6):
            sx = x + 4 + col * (cw - 8) // 5
            sy = y + 3 + row * (ch - 6) // 4
            c.disc(sx, sy, 1, WHITE)


def draw_cn_flag(c, x, y, w, h):
    c.fill_rect(x, y, w, h, (222, 41, 16))
    draw_star(c, x + int(w * 0.22), y + int(h * 0.28), max(3, h // 6),
              (255, 222, 0))
    for sx, sy in ((0.48, 0.12), (0.58, 0.28), (0.55, 0.48), (0.42, 0.55)):
        draw_star(c, x + int(w * sx), y + int(h * sy), max(2, h // 12),
                  (255, 222, 0))


def draw_ru_flag(c, x, y, w, h):
    c.fill_rect(x, y, w, h // 3, WHITE)
    c.fill_rect(x, y + h // 3, w, h // 3, (0, 57, 166))
    c.fill_rect(x, y + 2 * h // 3, w, h - 2 * h // 3, (213, 0, 50))


# ---------------------------------------------------------------- illustrations

def hero_shenzhen(c, x, y, w, h):
    """Three flags over a summit table: the Shenzhen trilateral."""
    c.fill_rect(x, y, w, h, SKY)
    c.disc(x + w - 26, y + 24, 16, (240, 220, 160))
    for i, col in enumerate((INK, (40, 44, 52), (24, 28, 38))):
        bx = x + 18 + i * 62
        bh = 34 + (i % 2) * 14
        c.fill_rect(bx, y + h - 58 - bh, 46, bh + 58, col)
    c.fill_rect(x, y + h - 58, w, 58, (22, 30, 44))
    pole_x = [x + 42, x + w // 2, x + w - 42]
    for px, draw in zip(pole_x, (draw_us_flag, draw_cn_flag, draw_ru_flag)):
        c.fill_rect(px - 1, y + 16, 3, 62, (210, 200, 170))
        draw(c, px - 26, y + 18, 52, 30)
    c.ellipse(x + w // 2, y + h - 26, 92, 12, (150, 110, 60))
    c.fill_rect(x + w // 2 - 92, y + h - 26, 184, 4, (150, 110, 60))


def hero_kyiv(c, x, y, w, h):
    """Night skyline, air-defence bursts, drones."""
    c.fill_rect(x, y, w, h, NAVY)
    for sx, sy in ((28, 26), (90, 14), (180, 30), (250, 18), (290, 40)):
        c.disc(x + sx, y + sy, 1, WHITE)
    for bx, bw, bh in ((0, 44, 46), (48, 30, 70), (82, 52, 40), (138, 34, 78),
                       (176, 46, 52), (226, 38, 66), (268, 44, 44)):
        c.fill_rect(x + bx, y + h - 30 - bh, bw, bh + 30, (8, 12, 22))
        for wy in range(y + h - 24 - bh, y + h - 32, 12):
            for wx in range(x + bx + 6, x + bx + bw - 4, 12):
                c.fill_rect(wx, wy, 3, 4, (240, 200, 90))
    for bx, by, r in ((70, 52, 26), (230, 44, 30), (160, 78, 20)):
        burst = []
        for i in range(16):
            ang = i * math.pi / 8
            rr = r if i % 2 == 0 else r * 0.45
            burst.append((int(bx + rr * math.cos(ang)),
                          int(by + rr * math.sin(ang))))
        c.polygon(burst, (255, 150, 40))
        c.disc(bx, by, 7, (255, 220, 120))
    c.line(x + 40, y + 96, x + 66, y + 62, (200, 220, 255), 2)
    c.line(x + 250, y + 100, x + 232, y + 58, (200, 220, 255), 2)
    c.disc(x + 66, y + 62, 3, WHITE)
    c.disc(x + 232, y + 58, 3, WHITE)


def hero_icj(c, x, y, w, h):
    """Courthouse pediment and scales of justice."""
    c.fill_rect(x, y, w, h, (226, 214, 188))
    c.polygon([(x + 12, y + 58), (x + w // 2, y + 14), (x + w - 12, y + 58)],
              (120, 110, 92))
    c.disc(x + w // 2, y + 40, 9, GOLD)
    for i in range(4):
        cx = x + 34 + i * ((w - 68) // 3)
        c.fill_rect(cx - 7, y + 62, 14, h - 84, (248, 244, 230))
        c.fill_rect(cx - 10, y + 58, 20, 5, (248, 244, 230))
    c.fill_rect(x + 10, y + h - 24, w - 20, 6, (248, 244, 230))
    c.fill_rect(x + 10, y + h - 18, w - 20, 18, (120, 110, 92))
    cx, top = x + w // 2, y + 66
    c.line(cx, top, cx, top + 34, INK, 3)
    c.line(cx - 26, top + 8, cx + 26, top + 8, INK, 2)
    for sgn in (-1, 1):
        px = cx + sgn * 26
        c.line(px, top + 8, px, top + 18, INK, 2)
        c.polygon([(px - 11, top + 18), (px + 11, top + 18), (px, top + 30)],
                  GOLD)


def draw_icon(c, kind, x, y, s, color):
    """Small white pictogram inside a coloured square (mid stories)."""
    if kind == "tower":
        c.fill_rect(x + s // 2 - 5, y + 6, 10, s - 12, color)
        c.polygon([(x + s // 2 - 7, y + 6), (x + s // 2 + 7, y + 6),
                   (x + s // 2, y + 1)], color)
        c.fill_rect(x + 4, y + s - 8, s - 8, 4, color)
    elif kind == "chart":
        c.line(x + 5, y + s - 6, x + s - 5, y + s - 6, color, 2)
        c.line(x + 5, y + s - 6, x + 5, y + 6, color, 2)
        c.line(x + 8, y + 12, x + s // 2, y + 20, color, 3)
        c.line(x + s // 2, y + 20, x + s - 8, y + s - 12, color, 3)
    elif kind == "pin":
        c.disc(x + s // 2, y + s // 2 - 3, 9, color)
        c.polygon([(x + s // 2 - 8, y + s // 2 + 2), (x + s // 2 + 8, y + s // 2 + 2),
                   (x + s // 2, y + s - 4)], color)
        c.disc(x + s // 2, y + s // 2 - 3, 3, PAPER)
    elif kind == "leaf":
        c.ellipse(x + s // 2, y + s // 2, 13, 8, color)
        c.line(x + 6, y + s - 8, x + s - 6, y + 8, PAPER, 2)
    elif kind == "ship":
        c.polygon([(x + 4, y + s - 12), (x + s - 4, y + s - 12),
                   (x + s - 10, y + s - 4), (x + 10, y + s - 4)], color)
        c.fill_rect(x + s // 2 - 3, y + 6, 6, 12, color)
        c.disc(x + s // 2 + 8, y + 8, 3, (255, 150, 40))
    elif kind == "cross":
        c.fill_rect(x + s // 2 - 4, y + 5, 8, s - 10, color)
        c.fill_rect(x + 5, y + s // 2 - 4, s - 10, 8, color)


# ---------------------------------------------------------------- dotted map

def draw_dotted_map(c, x, y, w, h, dot_color, story_dots):
    """Land cells as dots; story_dots is a list of (lon, lat, color, r)."""
    bits = base64.b64decode(MAP_BITS)
    step_x = w / MAP_W
    step_y = h / MAP_H
    for row in range(MAP_H):
        for col in range(MAP_W):
            i = row * MAP_W + col
            if (bits[i // 8] >> (i % 8)) & 1:
                cx = int(x + (col + 0.5) * step_x)
                cy = int(y + (row + 0.5) * step_y)
                c.disc(cx, cy, 2, dot_color)
    for lon, lat, color, r in story_dots:
        px = int(x + (lon + 180.0) / 360.0 * w)
        py = int(y + (84.0 - lat) / (84.0 + 58.0) * h)
        c.ring(px, py, r + 3, color, 2)
        c.disc(px, py, r, color)

# ---------------------------------------------------------------- stories

HERO = [
    {
        "region": "Asia",
        "kicker": "ASIA - SHENZHEN SUMMIT",
        "headline": "Trump, Xi,\nPutin meet",
        "summary": "Trump, Xi, Putin meet; peace declaration signed.",
        "source": "SCMP - OneTurn",
        "art": "shenzhen",
    },
    {
        "region": "Europe",
        "kicker": "EUROPE - UKRAINE WAR",
        "headline": "Russian strikes\nbatter Kyiv",
        "summary": "400 drones, 110 missiles; eight killed.",
        "source": "SCMP - AP",
        "art": "kyiv",
    },
    {
        "region": "Asia",
        "kicker": "JUSTICE - THE HAGUE - A FIRST",
        "headline": "Myanmar\nguilty",
        "summary": "ICJ: Myanmar guilty of Rohingya genocide.",
        "source": "OneTurn - AP",
        "art": "icj",
    },
]

MID = [
    {
        "region": "Middle East",
        "kicker": "MIDDLE EAST - RIYADH",
        "headline": "Missile hits\nRiyadh tower",
        "summary": "Windows blown out across eight floors of the PIF tower.",
        "icon": "tower", "color": RED,
    },
    {
        "region": "North America",
        "kicker": "MARKETS",
        "headline": "Stocks fall\nfor a fifth day",
        "summary": "S&P 500 down 2%; Nikkei falls 4.8% after Anthropic's debut.",
        "icon": "chart", "color": BLUE,
    },
    {
        "region": "Middle East",
        "kicker": "MIDDLE EAST - GAZA",
        "headline": "Troops cross\nYellow Line",
        "summary": "First crossing of the ceasefire line since the truce.",
        "icon": "pin", "color": RED,
    },
    {
        "region": "Europe",
        "kicker": "CLIMATE - COP31",
        "headline": "COP31 drops\nfossil-fuel goal",
        "summary": "Talks in Antalya go into overtime as nations quarrel.",
        "icon": "leaf", "color": (46, 110, 58),
    },
    {
        "region": "Middle East",
        "kicker": "MIDDLE EAST - HORMUZ",
        "headline": "Tanker ablaze\nin Hormuz",
        "summary": "Iran's war in its ninth month; tanker attacks intensify.",
        "icon": "ship", "color": BLUE,
    },
    {
        "region": "Africa",
        "kicker": "AFRICA - HEALTH",
        "headline": "Kenya's last\nEbola patient",
        "summary": "Home 37 days after the first case; 42-day countdown starts.",
        "icon": "cross", "color": (46, 110, 58),
    },
]

ALSO = [
    ("Europe", "EU-UK summit opens in Brussels"),
    ("Europe", "Denmark backs US-Greenland deal"),
    ("Europe", "Estonia to shut Russia border"),
    ("North America", "White House fires cyber chief"),
    ("Asia", "Cyclone Akvan hits Odisha"),
    ("South America", "US-Venezuela military pact signed"),
    ("South America", "Bukele seeks a third term"),
    ("Africa", "Dozens missing as migrant boat sinks"),
    ("Africa", "Drone strike kills 10 at Sudan mine"),
    ("North America", "34 gang suspects killed in Haiti"),
    ("Europe", "Italy fines Meta EUR 805m"),
    ("North America", "FAA ground stop strands 1.5m"),
    ("Europe", "Gas blast kills 6 in Georgia"),
    ("North America", "IMF: debt nears 100% of GDP"),
    ("North America", "Ella Langley sweeps the CMA Awards"),
    ("North America", "Mamdani: plan to close Rikers"),
    ("North America", "Trump held up to $265k of Nvidia"),
    ("North America", "Fiona the hippo dies in the US"),
    ("Europe", "Kerch Bridge rail traffic resumes"),
    ("North America", "AI favours US and UK accents"),
    ("North America", "Anthropic shares slide on day two"),
    ("Asia", "China rare-earth exports fall"),
]

TIMELINE = [
    ("01:58", "Riyadh tower hit"),
    ("02:33", "Kyiv embassies hit"),
    ("03:05", "Cyber chief fired"),
    ("03:29", "Georgia gas blast"),
    ("04:44", "Trump on Iran"),
]

# Story dots on the map: (lon, lat, color, radius)
MAP_DOTS = [
    (114.0, 22.5, RED, 4),      # Shenzhen
    (30.5, 50.4, RED, 4),       # Kyiv
    (4.3, 52.1, GOLD, 3),       # The Hague
    (46.7, 24.7, RED, 3),       # Riyadh
    (56.5, 26.5, RED, 3),       # Hormuz
    (36.8, -1.3, (46, 110, 58), 3),  # Nairobi
    (-77.0, 38.9, BLUE, 3),     # Washington
    (-0.1, 51.5, BLUE, 3),      # London/Brussels area
]

REGION_COLORS = {
    "North America": BLUE,
    "South America": (150, 90, 40),
    "Europe": (90, 60, 120),
    "Middle East": RED,
    "Africa": (46, 110, 58),
    "Asia": GOLD,
}
REGION_ORDER = ["North America", "South America", "Europe",
                "Middle East", "Africa", "Asia"]


def story_count():
    return len(HERO) + len(MID) + len(ALSO)


def region_counts():
    counts = {r: 0 for r in REGION_ORDER}
    for s in HERO + MID:
        counts[s["region"]] += 1
    for region, _ in ALSO:
        counts[region] += 1
    return counts


# ---------------------------------------------------------------- compose

def compose():
    c = Canvas(W, H, PAPER)
    mx, cw = 26, 972

    # Top bar
    c.fill_rect(0, 0, W, 30, INK)
    c.draw_text("sans_bold_13", mx + 2, 8, "ONE DAY OF WORLD NEWS", WHITE)
    total = story_count()
    right = "%d STORIES - 6 REGIONS - ALL FROM THIS FRIDAY" % total
    c.draw_text("sans_bold_13", W - mx - 2, 8, right, WHITE, align="right")

    # Masthead
    date_line = "FRIDAY 20 NOVEMBER 2026"
    c.draw_text("serif_bold_56", W // 2, 38, date_line, INK, align="center")
    c.draw_text("serif_19", W // 2, 100,
                "The world in one day - every story here happened, or was "
                "first reported, on this Friday.",
                GREY, align="center")
    c.hline(mx, 126, cw, INK, 3)
    c.hline(mx, 131, cw, INK, 1)

    # Timeline strip: one line per event, time + label
    ty = 140
    c.draw_text("sans_bold_13", mx, ty, "THE DAY IN UTC", RED)
    c.draw_text("sans_bold_13", W - mx, ty, "WHEN THE NEWS BROKE", GREY,
                align="right")
    c.hline(mx, ty + 22, cw, INK, 2)
    slot = cw / len(TIMELINE)
    for i, (t, label) in enumerate(TIMELINE):
        cx = int(mx + slot * i + slot / 2)
        c.disc(cx, ty + 22, 4, RED)
        c.draw_text("sans_15", cx, ty + 32, "%s  %s" % (t, label), INK,
                    align="center")

    # Hero row
    hy, hh = 200, 120
    col_w, gap = 300, 36
    for i, story in enumerate(HERO):
        x = mx + i * (col_w + gap)
        art = {"shenzhen": hero_shenzhen, "kyiv": hero_kyiv,
               "icj": hero_icj}[story["art"]]
        art(c, x, hy, col_w, hh)
        c.frame(x, hy, col_w, hh, INK, 1)
        y = hy + hh + 8
        c.draw_text("sans_bold_13", x, y, story["kicker"], RED)
        y += 17
        y = c.draw_wrapped("serif_bold_34", x, y, story["headline"], INK,
                           col_w, leading=38)
        y += 3
        y = c.draw_wrapped("serif_19", x, y, story["summary"], INK, col_w,
                           leading=22)
        c.draw_text("sans_15", x, y + 1, story["source"], GREY)

    c.hline(mx, 494, cw, INK, 2)

    # Mid rows: two rows of three
    my = 504
    for row in range(2):
        for col in range(3):
            story = MID[row * 3 + col]
            x = mx + col * (col_w + gap)
            y = my + row * 112
            c.fill_rect(x, y + 4, 46, 46, story["color"])
            draw_icon(c, story["icon"], x + 2, y + 6, 42, WHITE)
            tx = x + 56
            c.draw_text("sans_bold_13", tx, y, story["kicker"], story["color"])
            y2 = c.draw_wrapped("serif_bold_26", tx, y + 15,
                                story["headline"], INK, col_w - 56, leading=27)
            c.draw_wrapped("sans_15", tx, y2 + 2, story["summary"], GREY,
                           col_w - 56, leading=17)

    c.hline(mx, 732, cw, INK, 2)

    # Bottom left: map + region counts (two columns)
    by = 742
    c.draw_text("sans_bold_13", mx, by, "WHERE IT HAPPENED", INK)
    map_x, map_y, map_w, map_h = mx, by + 20, 330, 140
    c.frame(map_x - 4, map_y - 4, map_w + 8, map_h + 8, INK, 1)
    draw_dotted_map(c, map_x, map_y, map_w, map_h, (150, 140, 115), MAP_DOTS)
    counts = region_counts()
    ly = map_y + map_h + 12
    for j, region in enumerate(REGION_ORDER):
        lx = mx + (j // 3) * 170
        ry = ly + (j % 3) * 16
        c.disc(lx + 6, ry + 6, 4, REGION_COLORS[region])
        c.draw_text("sans_15", lx + 16, ry,
                    "%s - %d" % (region, counts[region]), INK)

    # Bottom right: also list + date check
    rx, rw = 374, 624
    c.draw_text("sans_bold_13", rx, by, "ALSO ON FRIDAY 20 NOVEMBER", INK)
    col2_w = (rw - 18) // 2
    for i, (region, text) in enumerate(ALSO):
        col = i // 11
        row = i % 11
        x = rx + col * (col2_w + 18)
        y = by + 20 + row * 17
        c.fill_rect(x, y + 4, 5, 5, REGION_COLORS[region])
        c.draw_text("sans_15", x + 11, y, text, INK)
    dy = by + 20 + 11 * 17 + 4
    c.hline(rx, dy, rw, INK, 1)
    c.draw_wrapped("sans_15", rx, dy + 7,
                   "DATE CHECK: left out on purpose - anything from before "
                   "Friday 20 November 2026. Every story here happened, or "
                   "was first reported, on that day.",
                   INK, rw, leading=17)

    # Footer
    c.draw_text("sans_bold_13", W // 2, 1010,
                "Sources: SCMP, AP, Reuters, OneTurn, Open Chronicle - "
                "Drawn by a program; no photo, no network.",
                GREY, align="center")
    return c

def main():
    out_dir = os.environ.get("MAKEFUN_OUTPUT_DIR", "/output")
    os.makedirs(out_dir, exist_ok=True)
    canvas = compose()
    path = os.path.join(out_dir, "entry.png")
    size = write_png(path, canvas)
    print("wrote %s (%d bytes, %dx%d)" % (path, size, W, H))


if __name__ == "__main__":
    main()
