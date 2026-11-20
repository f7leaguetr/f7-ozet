"""Gol disindaki onemli pozisyonlari bulur: spikerin heyecanlandigi / sesin yukseldigi anlar.

Iki isaret birlestirilir:
  1. Ses: saniye basina ses yuksekligi; cevresine gore ani yukselmeler (spiker, seyirci).
  2. Anlatim: otomatik altyazida "kurtardi", "direk", "kacirdi" gibi kelimeler.
"""
from __future__ import annotations

import re
import subprocess
import unicodedata
from dataclasses import dataclass

import numpy as np

KEYWORDS = {
    # kelime koku: agirlik
    "kurtar": 2.0, "kurtaris": 2.0, "direk": 2.5, "direg": 2.5, "dirsek": 0.5, "ust direk": 2.5,
    "kacir": 2.0, "kacti": 1.5, "auta": 0.8, "az farkla": 2.0, "cizgiden": 2.5, "son anda": 1.5,
    "pozisyon": 1.2, "tehlikeli": 1.5, "firsat": 1.5, "sut": 1.0, "vurdu": 1.0, "kafa": 1.2,
    "karsi karsiya": 2.0, "tek basina": 1.5, "penalti": 2.5, "kirmizi": 2.0, "faul": 0.5,
    "inanilmaz": 1.5, "muhtesem": 1.5, "harika": 1.0, "cikardi": 1.5, "uzandi": 1.5,
}


@dataclass
class Moment:
    t: float           # en heyecanli saniye
    score: float
    clip: tuple[float, float]
    text: str = ""


def _norm(s: str) -> str:
    s = s.replace("İ", "i").replace("I", "ı").lower()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return s.replace("ı", "i")


def audio_loudness(path: str, rate: int = 8000) -> np.ndarray:
    """Saniye basina RMS (dB). Ses yoksa bos dizi."""
    p = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-vn", "-ac", "1", "-ar", str(rate),
                        "-f", "s16le", "-"], capture_output=True)
    a = np.frombuffer(p.stdout, np.int16).astype(np.float32)
    n = len(a) // rate
    if n == 0:
        return np.zeros(0)
    a = a[: n * rate].reshape(n, rate)
    rms = np.sqrt((a ** 2).mean(axis=1)) + 1.0
    return 20 * np.log10(rms)


def _rolling_median(x: np.ndarray, w: int) -> np.ndarray:
    pad = np.pad(x, w // 2, mode="edge")
    # basit ve yeterince hizli: 1 sn cozunurlukte ~3500 deger
    return np.array([np.median(pad[i:i + w]) for i in range(len(x))])


def excitement(path: str, caps, duration: float) -> np.ndarray:
    n = int(duration) + 1
    score = np.zeros(n)
    loud = audio_loudness(path)
    if len(loud) > 30:
        base = _rolling_median(loud, 61)
        spread = np.median(np.abs(loud - base)) + 0.5
        z = np.clip((loud - base) / spread, 0, 6)
        # 5 sn'lik pencerede ortalama: tek bir gurultu yerine surekli heyecan
        z = np.convolve(z, np.ones(5) / 5, "same")
        score[: len(z)] += z[:n]
    for t, txt in caps:
        w = sum(v for k, v in KEYWORDS.items() if k in _norm(txt))
        if w:
            i = int(t)
            score[max(0, i - 4): min(n, i + 8)] += w
    return score


def find_moments(path: str, caps, duration: float, goal_times: list[float], count: int,
                 play_start: float | None = None) -> list[Moment]:
    """En heyecanli `count` ani; gollerin +-50 sn'si ve mac oncesi/sonrasi haric."""
    if count <= 0:
        return []
    sc = excitement(path, caps, duration)
    lo = int(play_start or 30)
    hi = int(duration) - 20
    mask = np.zeros_like(sc, dtype=bool)
    mask[lo:hi] = True
    for g in goal_times:
        mask[max(0, int(g) - 60): int(g) + 40] = False
    picks: list[Moment] = []
    s = np.where(mask, sc, -1)
    while len(picks) < count:
        i = int(np.argmax(s))
        if s[i] < 1.5:  # yeterince heyecanli an kalmadi
            break
        txt = " ".join(t for ts, t in caps if i - 15 <= ts <= i + 5)
        picks.append(Moment(float(i), float(s[i]), (max(0.0, i - 14.0), min(duration, i + 6.0)),
                            re.sub(r"\s+", " ", txt)[:200]))
        s[max(0, i - 60): i + 60] = -1
    return sorted(picks, key=lambda m: m.t)


TARGET_SECONDS = 240   # ozet hedefi ~4 dk
MOMENT_SECONDS = 20


def wanted_count(goals: int, goal_clip: float = 22.0) -> int:
    """Ozet ~4 dk olsun diye gollerden kalan sureyi pozisyonlarla doldur."""
    left = TARGET_SECONDS - 3 - goals * goal_clip
    return int(max(0, min(12, round(left / MOMENT_SECONDS))))
