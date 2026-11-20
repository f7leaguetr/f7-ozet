"""Ozet videosunun grafikleri: alt bant, 2 sn gecis (stinger) ve kapanis karti."""
from __future__ import annotations

import math
import os
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(HERE, "..", "assets")
LOGO_PATH = next((p for p in (os.path.join(ASSETS, "logo.png"), os.path.join(ASSETS, "logo.webp")) if os.path.exists(p)), os.path.join(ASSETS, "logo.webp"))

GOLD = (245, 180, 0)
GOLD_HI = (255, 210, 74)
GOLD_LO = (196, 138, 0)
SILVER_HI = (236, 236, 236)
SILVER_LO = (141, 141, 141)
INK = (17, 17, 17)

FONT_CANDIDATES = [
    os.environ.get("OZET_FONT", ""),
    os.path.join(ASSETS, "Oswald-Bold.ttf"),
    "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]


@lru_cache(None)
def font(size: int) -> ImageFont.FreeTypeFont:
    for p in FONT_CANDIDATES:
        if p and os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default(size)


@lru_cache(None)
def logo(height: int) -> Image.Image:
    im = Image.open(LOGO_PATH).convert("RGBA")
    w = round(im.width * height / im.height)
    return im.resize((w, height), Image.LANCZOS)


def upper_tr(s: str) -> str:
    return s.replace("i", "İ").replace("ı", "I").upper()


def _vgrad(w, h, top, bottom):
    g = Image.new("RGBA", (w, h))
    d = ImageDraw.Draw(g)
    for y in range(h):
        k = y / max(1, h - 1)
        d.line([(0, y), (w, y)], fill=tuple(int(top[i] + (bottom[i] - top[i]) * k) for i in range(3)) + (255,))
    return g


def _hgrad(w, h, stops):
    """stops: [(konum 0-1, renk)]"""
    g = Image.new("RGBA", (w, h))
    d = ImageDraw.Draw(g)
    for x in range(w):
        k = x / max(1, w - 1)
        for (p0, c0), (p1, c1) in zip(stops, stops[1:]):
            if p0 <= k <= p1:
                t = (k - p0) / max(1e-6, p1 - p0)
                d.line([(x, 0), (x, h)], fill=tuple(int(c0[i] + (c1[i] - c0[i]) * t) for i in range(3)) + (255,))
                break
    return g


# ------------------------------------------------------------------ alt bant

def banner(minute: int | None, name: str, team: str, score: str, scale: float = 1.0) -> Image.Image:
    """[logo] [GOL] [12'] [OYUNCU ADI] [TAKIM · 2-0]"""
    H = round(64 * scale)
    pad = round(18 * scale)
    f_big = font(round(30 * scale))
    f_small = font(round(21 * scale))
    parts = []

    lg = logo(round(H * 0.8))
    box = Image.new("RGBA", (lg.width + 2 * round(10 * scale), H), (13, 13, 13, 255))
    box.alpha_composite(lg, ((box.width - lg.width) // 2, (H - lg.height) // 2))
    ImageDraw.Draw(box).rectangle([box.width - round(4 * scale), 0, box.width, H], fill=GOLD + (255,))
    parts.append(box)

    def text_box(text, fnt, bg, fg, grad=None):
        tw = round(fnt.getlength(text))
        b = grad(tw + 2 * pad, H) if grad else Image.new("RGBA", (tw + 2 * pad, H), bg)
        d = ImageDraw.Draw(b)
        asc, desc = fnt.getmetrics()
        d.text((pad, (H - asc - desc) // 2 + round(1 * scale)), text, font=fnt, fill=fg)
        return b

    parts.append(text_box("GOL", f_big, None, INK, grad=lambda w, h: _vgrad(w, h, GOLD_HI, (213, 154, 0))))
    if minute:
        parts.append(text_box(f"{minute}'", f_big, (27, 27, 27, 255), GOLD + (255,)))
    parts.append(text_box(upper_tr(name), f_big, (18, 18, 18, 236), (255, 255, 255, 255)))
    parts.append(text_box(f"{upper_tr(team)} · {score}", f_small, (245, 245, 245, 242), INK + (255,)))

    W = sum(p.width for p in parts)
    sh = round(10 * scale)
    out = Image.new("RGBA", (W + 2 * sh, H + 2 * sh), (0, 0, 0, 0))
    shadow = Image.new("RGBA", out.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rectangle([sh, sh + round(3 * scale), sh + W, sh + H + round(3 * scale)], fill=(0, 0, 0, 140))
    out.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(round(6 * scale))))
    x = sh
    for p in parts:
        out.alpha_composite(p, (x, sh))
        x += p.width
    return out


# ------------------------------------------------------------- 2 sn stinger

def _ease(t):
    t = min(1.0, max(0.0, t))
    return t * t * (3 - 2 * t)


def stinger_frames(w=1280, h=720, fps=30):
    """60 kare. 0.55-1.10 sn arasi ekran tamamen kapali; kesme 1.00 sn'de yapilir."""
    n = 2 * fps
    skew = round(h * math.tan(math.radians(20)))
    pw = w + skew + 60
    silver = _hgrad(pw + skew, h, [(0, SILVER_LO), (0.45, SILVER_HI), (1, (169, 169, 169))])
    gold = _hgrad(pw + skew, h, [(0, (168, 116, 0)), (0.45, GOLD_HI), (1, GOLD_LO)])

    def panel(src):
        m = Image.new("L", src.size, 0)
        ImageDraw.Draw(m).polygon([(skew, 0), (skew + pw, 0), (pw, h), (0, h)], fill=255)
        p = src.copy()
        p.putalpha(m)
        return p

    ps, pg = panel(silver), panel(gold)
    x_off, x_cov, x_end = -(pw + skew), -skew - 30, w

    def pos(f, f_in0, f_in1, f_out0, f_out1):
        if f <= f_in1:
            return x_off + (x_cov - x_off) * _ease((f - f_in0) / (f_in1 - f_in0))
        if f < f_out0:
            return x_cov
        return x_cov + (x_end - x_cov) * _ease((f - f_out0) / (f_out1 - f_out0))

    big = logo(round(h * 0.34))
    frames = []
    for f in range(n):
        im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        im.alpha_composite(ps, (round(pos(f, 0, 14, 36, 52)), 0))
        im.alpha_composite(pg, (round(pos(f, 3, 17, 33, 49)), 0))
        # logo: 15-27 buyur, 33-40 kuculur
        if 15 <= f <= 40:
            if f <= 27:
                k = (f - 15) / 12
                s = 1.15 * math.sin(k * math.pi / 2) if k < 0.7 else 1.15 - 0.15 * (k - 0.7) / 0.3
            elif f < 33:
                s = 1.0
            else:
                s = max(0.0, 1 - (f - 33) / 7)
            if s > 0.02:
                lg = big.resize((max(1, round(big.width * s)), max(1, round(big.height * s))), Image.LANCZOS)
                im.alpha_composite(lg, ((w - lg.width) // 2, (h - lg.height) // 2))
        frames.append(im)
    return frames


# ------------------------------------------------------------- kapanis karti

def end_card(home: str, away: str, hs: int, as_: int, top_scorer: str | None, w=1280, h=720) -> Image.Image:
    bg = _hgrad(w, h, [(0, (20, 20, 20)), (1, (8, 8, 8))])
    skew = round(h * math.tan(math.radians(20)))
    d = ImageDraw.Draw(bg)
    d.polygon([(w * 0.62 + skew, 0), (w, 0), (w, h), (w * 0.62, h)], fill=(196, 138, 0, 255))
    d.polygon([(w * 0.55 + skew, 0), (w * 0.62 + skew, 0), (w * 0.62, h), (w * 0.55, h)], fill=(200, 200, 200, 255))
    x = round(w * 0.07)
    maxw = w * 0.47

    def fit(text, size):
        while size > 12 and font(size).getlength(text) > maxw:
            size -= 2
        return font(size)

    lg = logo(round(h * 0.24))
    bg.alpha_composite(lg, (x, round(h * 0.08)))
    y = round(h * 0.40)
    d.text((x, y), "MAÇ SONU", font=font(round(h * 0.05)), fill=GOLD + (255,))
    y += round(h * 0.08)
    for line, size in ((upper_tr(home), round(h * 0.06)), (f"{hs} - {as_}", round(h * 0.11)),
                       (upper_tr(away), round(h * 0.06))):
        f = fit(line, size)
        d.text((x, y), line, font=f, fill=(255, 255, 255, 255))
        y += round(f.size * 1.2)
    if top_scorer:
        t = upper_tr(top_scorer)
        d.text((x, y + round(h * 0.02)), t, font=fit(t, round(h * 0.04)), fill=(210, 210, 210, 255))
    return bg
