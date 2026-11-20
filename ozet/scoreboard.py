"""Skor tabelasindan gol anlarini ve mac saatinin calistigi bolumleri bulur.

Ligde iki farkli yayin grafigi goruldu (koordinatlar 640x360 uzerinde):
  A) Siyah serit:      [TAKIM]  5 [24:22] 1  [TAKIM]      (Vadi / Gaziemir yayinlari)
  B) Mavi-sari bant:   saat ustte, altta  TAKIM 0 - 5 TAKIM
Program her videoda hangi grafigin kullanildigini kendisi secer. Tabeladaki sol
takimin sitedeki ev sahibi olmadigi yayinlar da var; bu da skordan anlasilir.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass, field

import numpy as np

W, H = 640, 360
STRIP_H = 40  # ustteki 40 piksellik serit yeterli

# (x0, x1, y0, y1)
TEMPLATES = {
    "A": {"L": (82, 104, 5, 24), "CLOCK": (106, 126, 5, 24),
          "R": (127, 149, 5, 24), "NAME": (14, 76, 7, 22)},
    "B": {"L": (84, 100, 20, 34), "CLOCK": (87, 114, 10, 20),
          "R": (103, 120, 20, 34), "NAME": (48, 75, 24, 31)},
}


@dataclass
class Change:
    t: float          # tabelanin degistigi video saniyesi
    side: str         # "L" = ev sahibi, "R" = deplasman (sitedeki anlamiyla)
    strength: float   # degisimin buyuklugu (guven)


@dataclass
class ScoreboardResult:
    duration: float
    changes: list[Change]
    first_half: tuple[float, float] | None   # saat calisirken (baslangic, bitis)
    second_half_start: float | None          # saat 2. yarida da calisiyorsa
    warnings: list[str] = field(default_factory=list)
    template: str | None = None
    swapped: bool = False                    # tabelada solda deplasman var


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


def _crop(frames: np.ndarray, box) -> np.ndarray:
    x0, x1, y0, y1 = box
    return frames[:, y0:y1, x0:x1].astype(np.float32)


def _overlay_present(frames: np.ndarray, tpl) -> np.ndarray:
    """Tabelanin ekranda olup olmadigi: takim adi bolgesi medyan goruntuye benziyor mu."""
    name = _crop(frames, tpl["NAME"])
    ref = np.median(name, axis=0)
    diff = np.abs(name - ref).mean(axis=(1, 2))
    return diff < 25.0


def _stability(frames: np.ndarray, box) -> float:
    """Bolgenin ardisik karelerde ne kadar sabit kaldigi (0-1)."""
    c = _crop(frames, box)
    d = np.abs(c[1:] - c[:-1]).mean(axis=(1, 2))
    return float((d < 4.0).mean()) if len(d) else 0.0


def clock_running(frames, tpl) -> np.ndarray:
    """Mac saatinin her saniye degistigi (calistigi) kareler."""
    clk = _crop(frames, tpl["CLOCK"])
    d = np.zeros(len(clk))
    d[1:] = np.abs(clk[1:] - clk[:-1]).mean(axis=(1, 2))
    run = d > 3.0
    k = 7
    pad = np.pad(run.astype(int), k // 2, mode="edge")
    return np.convolve(pad, np.ones(k, int), "valid") > k // 2


def choose_template(frames) -> tuple[str | None, dict]:
    """Takim adi ve skor bolgeleri sabit, saat bolgesi surekli degisen grafigi secer."""
    scores = {}
    for name, tpl in TEMPLATES.items():
        stab = (_stability(frames, tpl["NAME"]) + _stability(frames, tpl["L"])
                + _stability(frames, tpl["R"])) / 3
        run = float(clock_running(frames, tpl).mean())
        scores[name] = round(stab * 0.6 + run * 0.4, 3)
    best = max(scores, key=scores.get)
    return (best if scores[best] > 0.45 else None), scores


def _stable_segments(feat: np.ndarray, present: np.ndarray, tol: float, min_len: int):
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


def candidate_changes(times, frames, tpl, box_key: str) -> list[Change]:
    feat = _crop(frames, tpl[box_key])
    present = _overlay_present(frames, tpl)
    segs = _stable_segments(feat, present, tol=6.0, min_len=3)
    cands: list[Change] = []
    for k, ((a0, a1, ma), (b0, b1, mb)) in enumerate(zip(segs, segs[1:])):
        strength = float(np.abs(ma - mb).mean())
        if strength <= 8.0:
            continue
        # Skor rakami hic geri donmez: yeni goruntu daha onceki bir durumla aynıysa
        # bu bir gol degil, gecici grafik/replay sonrasi eski hale donus.
        if any(np.abs(mb - segs[j][2]).mean() < 6.0 for j in range(k)):
            continue
        cands.append(Change(t=float(times[b0]), side=box_key, strength=strength))
    merged: list[Change] = []
    for c in cands:
        if merged and c.t - merged[-1].t < 20:
            if c.strength > merged[-1].strength:
                merged[-1] = c
        else:
            merged.append(c)
    return merged


def pick(cands: list[Change], expected: int, label: str) -> tuple[list[Change], list[str], float]:
    """Beklenen gol sayisi kadar en guclu degisimi secer. (secilenler, uyarilar, ayrisma orani)"""
    if expected == 0:
        return [], [], 99.0
    if len(cands) < expected:
        return cands, [f"{label}: {expected} gol bekleniyordu, tabelada {len(cands)} degisim bulundu"], 0.0
    ranked = sorted(cands, key=lambda c: -c.strength)
    chosen = ranked[:expected]
    ratio = 99.0
    warnings = []
    if len(ranked) > expected:
        weakest, next_ = chosen[-1].strength, ranked[expected].strength
        ratio = weakest / max(next_, 1e-6)
        if ratio < 1.3:
            warnings.append(f"{label}: gol degisimleri ile parazit ayrismiyor ({weakest:.1f} / {next_:.1f})")
    return sorted(chosen, key=lambda c: c.t), warnings, ratio


def running_segments(times, running, min_len_s: float = 120):
    segs, start = [], None
    for i, r in enumerate(running):
        if r and start is None:
            start = i
        if (not r or i == len(running) - 1) and start is not None:
            end = i
            if times[end] - times[start] >= min_len_s:
                segs.append((float(times[start]), float(times[end])))
            start = None
    return segs


def analyze(path: str, home_goals: int | None, away_goals: int | None) -> ScoreboardResult:
    duration = video_duration(path)
    times, frames = read_strips(path, fps=1.0)
    if len(frames) == 0:
        return ScoreboardResult(duration, [], None, None, ["video okunamadi"])

    _, tscores = choose_template(frames)
    # Her grafik ve her taraf eslesmesi icin dene; beklenen skora en tutarli olani sec.
    options = []
    for tname, tpl in TEMPLATES.items():
        left = candidate_changes(times, frames, tpl, "L")
        right = candidate_changes(times, frames, tpl, "R")
        for swapped, (eL, eR) in ((False, (home_goals, away_goals)), (True, (away_goals, home_goals))):
            cl, wl, rl = pick(left, eL or 0, "sol")
            cr, wr, rr = pick(right, eR or 0, "sag")
            options.append((len(wl) + len(wr), -min(rl, rr), -tscores[tname],
                            tname, swapped, cl, cr, wl + wr))
    options.sort(key=lambda o: o[:3])
    _, _, _, tname, swapped, cl, cr, warnings = options[0]
    tpl = TEMPLATES[tname]
    if home_goals == away_goals:
        # esit skorda taraf skordan anlasilmaz; varsayilan: sol = ev sahibi
        swapped = False
    changes = ([Change(c.t, "R" if swapped else "L", c.strength) for c in cl]
               + [Change(c.t, "L" if swapped else "R", c.strength) for c in cr])
    changes.sort(key=lambda c: c.t)

    segs = running_segments(times, clock_running(frames, tpl))
    first = segs[0] if segs else None
    second = None
    if first:
        for s in segs[1:]:
            if s[0] - first[1] > 60:
                second = s[0]
                break
    if not first:
        warnings.append("mac saati okunamadi; dakikalar videodan tahmin edilecek")
    return ScoreboardResult(duration, changes, first, second, warnings, tname, swapped)
