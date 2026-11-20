"""Skor tabelasindan gol anlarini ve mac saatinin calistigi bolumleri bulur.

F7 yayin grafiginde tabela sol ustte sabit durur:
    [TAKIM A]  [skor A] [saat] [skor B]  [TAKIM B]
Koordinatlar 640x360'a gore oransal tutulur, boylece 720p/1080p fark etmez.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass, field

import numpy as np

W, H = 640, 360
STRIP_H = 40  # ustteki 40 piksellik serit yeterli

# (x0, x1, y0, y1) - 640x360 uzerinde
REGIONS = {
    "L": (82, 104, 5, 24),       # ev sahibi skoru
    "CLOCK": (106, 126, 5, 24),  # mac saati (sari kutu)
    "R": (127, 149, 5, 24),      # deplasman skoru
    "NAME": (14, 76, 7, 22),     # sol takim adi (tabela var mi kontrolu)
}


@dataclass
class Change:
    t: float          # tabelanin degistigi video saniyesi
    side: str         # "L" veya "R"
    strength: float   # degisimin buyuklugu (guven)


@dataclass
class ScoreboardResult:
    duration: float
    changes: list[Change]
    first_half: tuple[float, float] | None   # saat calisirken (baslangic, bitis)
    second_half_start: float | None          # saat 2. yarida da calisiyorsa
    warnings: list[str] = field(default_factory=list)


def video_duration(path: str) -> float:
    out = subprocess.check_output([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=nw=1:nk=1", path]).decode().strip()
    return float(out)


def read_strips(path: str, fps: float = 1.0):
    """Videodan saniyede `fps` kare, sadece ust seridi gri tonlamali okur."""
    cmd = [
        "ffmpeg", "-v", "error", "-i", path,
        "-vf", f"fps={fps},scale={W}:{H},crop={W}:{STRIP_H}:0:0,format=gray",
        "-f", "rawvideo", "-",
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE)
    size = W * STRIP_H
    frames = []
    while True:
        buf = proc.stdout.read(size)
        if len(buf) < size:
            break
        frames.append(np.frombuffer(buf, np.uint8).reshape(STRIP_H, W))
    proc.wait()
    times = np.arange(len(frames)) / fps
    return times, np.stack(frames) if frames else np.zeros((0, STRIP_H, W), np.uint8)


def _crop(frames: np.ndarray, key: str) -> np.ndarray:
    x0, x1, y0, y1 = REGIONS[key]
    return frames[:, y0:y1, x0:x1].astype(np.float32)


def _overlay_present(frames: np.ndarray) -> np.ndarray:
    """Tabelanin ekranda olup olmadigi: takim adi bolgesi medyan goruntuye benziyor mu."""
    name = _crop(frames, "NAME")
    ref = np.median(name, axis=0)
    diff = np.abs(name - ref).mean(axis=(1, 2))
    return diff < 25.0


def _stable_segments(feat: np.ndarray, present: np.ndarray, tol: float, min_len: int):
    """Ozellik vektorunun sabit kaldigi bolumler: [(i0, i1, ortalama_goruntu)]."""
    segs = []
    i, n = 0, len(feat)
    while i < n:
        if not present[i]:
            i += 1
            continue
        j = i + 1
        while j < n and present[j] and np.abs(feat[j] - feat[i]).mean() < tol:
            j += 1
        if j - i >= min_len:
            segs.append((i, j - 1, feat[i:j].mean(axis=0)))
        i = j
    return segs


def detect_changes(times, frames, side: str, expected: int | None) -> tuple[list[Change], list[str]]:
    """Bir takimin skor rakaminin degistigi anlar.

    Beklenen gol sayisi biliniyorsa en guclu `expected` degisimi secer; bu,
    tabeladaki kucuk parazitlere karsi esik ayarlamaktan cok daha saglamdir.
    """
    warnings: list[str] = []
    feat = _crop(frames, side)
    present = _overlay_present(frames)
    segs = _stable_segments(feat, present, tol=6.0, min_len=3)
    cands: list[Change] = []
    for (a0, a1, ma), (b0, b1, mb) in zip(segs, segs[1:]):
        strength = float(np.abs(ma - mb).mean())
        if strength > 8.0:
            cands.append(Change(t=float(times[b0]), side=side, strength=strength))
    # Ayni rakama geri donen ardisik parcalari (gecici grafik) ele
    merged: list[Change] = []
    for c in cands:
        if merged and c.t - merged[-1].t < 20:
            if c.strength > merged[-1].strength:
                merged[-1] = c
        else:
            merged.append(c)
    if expected is None:
        return merged, warnings
    if expected == 0:
        return [], warnings
    if len(merged) < expected:
        warnings.append(f"{side}: {expected} gol bekleniyordu, tabelada {len(merged)} degisim bulundu")
        return merged, warnings
    ranked = sorted(merged, key=lambda c: -c.strength)
    chosen = ranked[:expected]
    if len(ranked) > expected:
        weakest, next_ = chosen[-1].strength, ranked[expected].strength
        if weakest < next_ * 1.6:
            warnings.append(
                f"{side}: gol degisimleri ile parazit ayrismiyor ({weakest:.1f} / {next_:.1f})")
    return sorted(chosen, key=lambda c: c.t), warnings


def clock_running(times, frames) -> np.ndarray:
    """Mac saatinin her saniye degistigi (calistigi) kareler."""
    clk = _crop(frames, "CLOCK")
    d = np.zeros(len(clk))
    d[1:] = np.abs(clk[1:] - clk[:-1]).mean(axis=(1, 2))
    run = d > 3.0
    # 7 karelik kayan cogunluk ile yumusat
    k = 7
    pad = np.pad(run.astype(int), k // 2, mode="edge")
    smooth = np.convolve(pad, np.ones(k, int), "valid") > k // 2
    return smooth


def running_segments(times, running, min_len_s: float = 120):
    segs, start = [], None
    for i, r in enumerate(running):
        if r and start is None:
            start = i
        if (not r or i == len(running) - 1) and start is not None:
            end = i if not r else i
            if times[end] - times[start] >= min_len_s:
                segs.append((float(times[start]), float(times[end])))
            start = None
    return segs


def analyze(path: str, home_goals: int | None, away_goals: int | None) -> ScoreboardResult:
    duration = video_duration(path)
    times, frames = read_strips(path, fps=1.0)
    warnings: list[str] = []
    if len(frames) == 0:
        return ScoreboardResult(duration, [], None, None, ["video okunamadi"])

    changes: list[Change] = []
    for side, exp in (("L", home_goals), ("R", away_goals)):
        ch, w = detect_changes(times, frames, side, exp)
        changes += ch
        warnings += w
    changes.sort(key=lambda c: c.t)

    segs = running_segments(times, clock_running(times, frames))
    first = segs[0] if segs else None
    second = None
    if first:
        for s in segs[1:]:
            if s[0] - first[1] > 60:
                second = s[0]
                break
    if not first:
        warnings.append("mac saati okunamadi; dakikalar videodan tahmin edilecek")
    return ScoreboardResult(duration, changes, first, second, warnings)
