"""Tabela degisimlerinden gol listesi, dakika ve klip araliklari uretir."""
from __future__ import annotations

from dataclasses import dataclass

from .scoreboard import ScoreboardResult

# Tabela, golden ortalama ~12 sn sonra guncelleniyor (operator gecikmesi)
SCOREBOARD_LAG = 12.0
CLIP_BEFORE = SCOREBOARD_LAG + 10.0   # klip golden ~10 sn once baslar
CLIP_AFTER = 0.0                      # ...ve tabela degistigi anda biter (sevinc dahil)


@dataclass
class Goal:
    n: int
    side: str            # "L" ev sahibi, "R" deplasman
    update_t: float      # tabelanin degistigi an
    goal_t: float        # tahmini gol ani
    minute: int | None
    home: int            # golden sonraki skor
    away: int
    clip: tuple[float, float]
    # skorer bilgisi sonradan doldurulur
    player_id: str | None = None
    player_name: str | None = None
    guest_name: str | None = None
    confidence: float = 0.0
    note: str = ""


def minute_at(sb: ScoreboardResult, gt: float, half_minutes: int,
              second_half_start: float | None) -> int | None:
    """Video saniyesinden mac dakikasi (tabela saatine gore)."""
    if not sb.first_half:
        return None
    k0, k1 = sb.first_half
    if (k1 - k0) > (half_minutes + 8) * 60:  # saat devre arasinda sifirlanmiyor
        return max(1, int((gt - k0) // 60) + 1)
    if gt <= k1 + 30 or second_half_start is None or gt < second_half_start:
        return max(1, min(int((gt - k0) // 60) + 1, half_minutes + 5))
    return half_minutes + int((gt - second_half_start) // 60) + 1


def build_goals(sb: ScoreboardResult, half_minutes: int,
                second_half_start: float | None) -> list[Goal]:
    goals: list[Goal] = []
    h = a = 0
    first = sb.first_half
    for i, c in enumerate(sb.changes, 1):
        if c.side == "L":
            h += 1
        else:
            a += 1
        gt = max(0.0, c.t - SCOREBOARD_LAG)
        minute = minute_at(sb, gt, half_minutes, second_half_start)
        start = max(0.0, c.t - CLIP_BEFORE)
        end = min(sb.duration, c.t + CLIP_AFTER)
        goals.append(Goal(i, c.side, c.t, gt, minute, h, a, (start, end)))
    # Ust uste binen klipleri kirp
    for prev, cur in zip(goals, goals[1:]):
        if cur.clip[0] < prev.clip[1]:
            mid = (prev.clip[1] + cur.clip[0]) / 2
            prev.clip = (prev.clip[0], mid)
            cur.clip = (mid, cur.clip[1])
    return goals


def estimate_second_half(sb: ScoreboardResult, half_minutes: int) -> float | None:
    """Saat 2. yarida durmussa: mac sonu - yari suresi."""
    if sb.second_half_start:
        return sb.second_half_start
    if sb.first_half:
        return max(sb.first_half[1] + 120, sb.duration - half_minutes * 60 - 30)
    return None
