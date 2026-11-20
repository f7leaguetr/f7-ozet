"""Spikerin anlatimindan (YouTube otomatik altyazisi) golculeri bulur."""
from __future__ import annotations

import json
import os
import re
import unicodedata

import requests

from .timeline import Goal

ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5-5")


def load_json3(path: str) -> list[tuple[float, str]]:
    with open(path, encoding="utf-8") as f:
        j = json.load(f)
    out = []
    for e in j.get("events", []):
        segs = e.get("segs")
        if not segs:
            continue
        txt = "".join(s.get("utf8", "") for s in segs).replace("\n", " ").strip()
        if txt:
            out.append((e.get("tStartMs", 0) / 1000.0, txt))
    return out


def window_text(caps, t0: float, t1: float) -> str:
    return re.sub(r"\s+", " ", " ".join(t for s, t in caps if t0 <= s <= t1)).strip()


def _norm(s: str) -> str:
    s = s.replace("İ", "i").replace("I", "ı").lower()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return s.replace("ı", "i")


# ---------------------------------------------------------------- LLM yolu

PROMPT = """Bir halı saha futbol maçının spiker anlatımının otomatik (hatalı olabilen) altyazısı var.
Tabela analiziyle golleri ve hangi takımın attığını kesin olarak biliyoruz. Görevin: her golü kimin attığını anlatımdan bulmak.

Ev sahibi (tabelada solda): {home}
Ev sahibi kadrosu: {home_roster}
Deplasman (tabelada sağda): {away}
Deplasman kadrosu: {away_roster}

Kurallar:
- Spiker genelde sadece ilk isim söyler ("Umutcan vurdu ve gol"). Golü atan takımın kadrosundaki isimle eşleştir.
- Altyazı isimleri bozabilir (örn. takım adı "Walwes" → "Volvos"); benzer sesli isimleri eşleştir.
- Golü atan takımın kadrosunda eşleşen yoksa "guest_name" olarak duyduğun ismi yaz (baş harfi büyük, sadece isim).
- Kendi kalesine gol ise "note": "kendi kalesine (oyuncu adı)" yaz ve player_id/guest_name boş bırak.
- Emin değilsen confidence düşük ver; uydurma.
- Ayrıca anlatımda ikinci yarının başladığı an geçiyorsa "second_half_start" alanına o anın saniyesini yaz, yoksa null.

Goller (zaman = tabelanın değiştiği video saniyesi; gol bundan ~20 sn önce olur):
{goals}

İkinci yarı başlangıcı aranacak bölüm:
{half_text}

SADECE şu JSON'u döndür:
{{"goals":[{{"n":1,"heard":"Umutcan","player_id":"... veya null","guest_name":"... veya null","confidence":0.0,"note":""}}],"second_half_start":null}}"""


def _roster_str(players):
    return ", ".join(f'{p["name"]} (#{p.get("jersey_number") or "?"}, id={p["id"]})' for p in players) or "(kadro boş)"


def ask_llm(goals: list[Goal], caps, home, away, home_roster, away_roster, half_window):
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return None
    gl = []
    for g in goals:
        team = home if g.side == "L" else away
        txt = window_text(caps, g.update_t - 60, g.update_t + 15)
        gl.append(f'#{g.n} | atan takım: {team} | skor sonrası {g.home}-{g.away} | zaman {g.update_t:.0f}s\n'
                  f'   anlatım: "{txt}"')
    half_text = "\n".join(f"[{s:.0f}s] {t}" for s, t in caps if half_window[0] <= s <= half_window[1])
    prompt = PROMPT.format(home=home, away=away,
                           home_roster=_roster_str(home_roster), away_roster=_roster_str(away_roster),
                           goals="\n".join(gl), half_text=half_text[:6000] or "(yok)")
    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
        json={"model": ANTHROPIC_MODEL, "max_tokens": 2000,
              "messages": [{"role": "user", "content": prompt}]},
        timeout=120)
    r.raise_for_status()
    text = "".join(b.get("text", "") for b in r.json().get("content", []))
    m = re.search(r"\{.*\}", text, re.S)
    return json.loads(m.group(0)) if m else None


# ---------------------------------------------------------- kural tabanli yedek

def rule_based(goal: Goal, caps, roster) -> dict:
    """'<isim> ... gol' kalibinda, gol kelimesinden onceki son kadro ismini alir."""
    txt = window_text(caps, goal.update_t - 50, goal.update_t + 5)
    n = _norm(txt)
    gi = n.find(" gol")
    if gi < 0:
        return {}
    before = n[:gi]
    best, pos = None, -1
    for p in roster:
        first = _norm(p["name"].split()[0])
        k = before.rfind(first)
        if k > pos:
            best, pos = p, k
    if best and gi - pos < 80:
        return {"player_id": best["id"], "confidence": 0.5}
    # Kadroda yoksa: "Kaan ... vurdu ve gol" kalibindan ismi misafir olarak al
    m = None
    for m in re.finditer(r"([A-ZÇĞİÖŞÜ][a-zçğıöşü]{2,})(?:'\w+)?\s+(?:\S+\s+){0,3}?(?:vurdu|attı|gönderdi)\b[^.]{0,30}\bgol",
                         txt):
        pass
    if m:
        return {"guest_name": m.group(1), "confidence": 0.3}
    return {}


def assign(goals, caps, home, away, home_roster, away_roster, half_window):
    by_id = {p["id"]: p for p in home_roster + away_roster}
    second_half = None
    data = None
    try:
        data = ask_llm(goals, caps, home, away, home_roster, away_roster, half_window)
    except Exception as e:  # LLM hatasi isi durdurmasin
        print("LLM hatasi:", e)
    res = {g["n"]: g for g in (data or {}).get("goals", [])}
    if data:
        second_half = data.get("second_half_start")
    for g in goals:
        roster = home_roster if g.side == "L" else away_roster
        r = res.get(g.n) or rule_based(g, caps, roster)
        pid = r.get("player_id")
        if pid and pid in by_id and by_id[pid] in roster:
            g.player_id, g.player_name = pid, by_id[pid]["name"]
        elif r.get("guest_name"):
            g.guest_name = str(r["guest_name"]).strip().title()[:40]
        g.confidence = float(r.get("confidence") or 0)
        g.note = r.get("note") or ""
    return second_half
