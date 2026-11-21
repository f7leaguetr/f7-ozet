"""Golcuyu spikerin sesinden bulur (Whisper ile yaziya dokup kadroyla eslestirir).

Kural: golcu en cok bagirilan isim DEGIL, golu bitiren fiilin/kalibin yanindaki isimdir.
  "Aslaaan sut, kaleci, ve Ali tamamladi"  -> Ali
  "golun sahibi Ali" / "Ali'den gol"        -> Ali
Emin olunamazsa (bitirici kalip yok ya da iki farkli isim ayni guvende) bos birakilir.
"""
from __future__ import annotations

import os
import re
import subprocess
import tempfile
import unicodedata

_MODEL = None

FINISH = {  # golu bitiren fiiller (isimden SONRA gelir)
    "tamamladi": 3, "yerlestirdi": 3, "gonderdi": 3, "birakti": 3, "atti": 3, "astı": 3,
    "noktaladi": 3, "firlatti": 2, "vurdu": 2, "dokundu": 2, "itti": 2, "sokuldu": 1,
}
PASS = {"pas", "pasi", "pasla", "asist", "asisti", "ortaladi", "orta", "ortasi", "verdi", "aktardi",
        "cikardi", "indirdi", "kaydirdi", "sut", "sutu", "cekti", "deniyor", "denedi", "kaleci", "kaleciye"}
SUFFIX = ("", "dan", "den", "tan", "ten", "nin", "nın", "in", "un", "un", "nun", "a", "e", "ya", "ye",
          "yla", "yle", "la", "le", "cigim", "cim", "im")


def _norm(s: str) -> str:
    s = s.replace("İ", "i").replace("I", "ı").lower()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.replace("ı", "i")
    return re.sub(r"(.)\1{2,}", r"\1", s)  # "aslaaan" -> "aslan"


def _tokens(words):
    """[(kelime, saniye)] -> [(norm_kok, ek, saniye, orijinal)]"""
    out = []
    for w, t in words:
        for part in re.findall(r"[\wçğıöşüÇĞİÖŞÜ']+", w):
            n = _norm(part)
            root, _, suf = n.partition("'")
            out.append((root, suf, t, part))
    return out


def _lev(a: str, b: str) -> int:
    if abs(len(a) - len(b)) > 2:
        return 9
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _names(p):
    parts = [_norm(x) for x in re.findall(r"[\wçğıöşüÇĞİÖŞÜ]+", p["name"])]
    keys = {parts[0]} if parts else set()
    if len(parts) > 1 and len(parts[0]) <= 3:      # "Ali Can" -> "alican" de olabilir
        keys.add(parts[0] + parts[1])
    if p.get("nickname"):
        keys.add(_norm(p["nickname"]))
    return keys


def _match(tok_root: str, roster):
    """Kelime kadrodan kime uyuyor? Tek kisiye uyuyorsa o oyuncu, yoksa None."""
    hits = []
    for p in roster:
        for k in _names(p):
            if len(k) < 3:
                continue
            ok = tok_root == k
            if not ok and tok_root.startswith(k) and tok_root[len(k):] in SUFFIX:
                ok = True  # Whisper bazen kesme isaretini koymaz: "aliden"
            if not ok and len(k) >= 5 and _lev(tok_root, k) <= 1:
                ok = True
            if ok:
                hits.append(p)
                break
    uniq = {p["id"]: p for p in hits}
    return next(iter(uniq.values())) if len(uniq) == 1 else None


def pick_scorer(words, roster, goal_t: float | None = None):
    """words: [(kelime, saniye)]. Donus: (oyuncu, guven) ya da (None, 0)."""
    toks = _tokens(words)
    cands = {}  # id -> (puan, oyuncu)

    def add(p, score):
        if p:
            old = cands.get(p["id"], (0, p))[0]
            cands[p["id"]] = (max(old, score), p)

    for i, (root, suf, t, orig) in enumerate(toks):
        p = _match(root, roster)
        if not p:
            continue
        nxt = [x[0] for x in toks[i + 1:i + 4]]
        prv = [x[0] for x in toks[max(0, i - 3):i]]
        # isimden hemen sonra pas/sut kelimesi geliyorsa bu kisi pasi veren / sutu ceken
        if nxt and nxt[0] in PASS:
            continue
        if suf in ("nin", "nın", "in", "un", "nun") and nxt and nxt[0] in ("pasi", "asisti", "ortasi", "pasiyla"):
            continue
        # A) "Ali tamamladi", "Ali ve gol", "Ali golu atti"
        # Not: "Aslan ... gol" tek basina YETMEZ; bagirilan isim golcu olmayabilir.
        after = [x[0] for x in toks[i + 1:i + 7]]
        for k, w in enumerate(nxt):
            if w in FINISH:
                sc = FINISH[w] - (0.5 if k else 0)
                if sc < 3 and "gol" in after[k + 1:k + 4]:  # "vurdu ve gol"
                    sc = 3
                add(p, sc)
                break
        # B) "golun sahibi Ali", "Ali'den gol", "gol Ali'den"
        if suf in ("dan", "den", "tan", "ten") or root.endswith(("dan", "den")) and root not in ("ve",):
            if "gol" in nxt[:2] or "gol" in prv[-2:]:
                add(p, 3.5)
        if prv[-2:] == ["golun", "sahibi"] or prv[-1:] == ["sahibi"]:
            add(p, 4)
        if suf in ("nin", "nın", "in", "un", "nun") and nxt[:1] and nxt[0] in ("golu", "golunu"):
            add(p, 3.5)

    if not cands:
        return None, 0.0
    ranked = sorted(cands.values(), key=lambda x: -x[0])
    best, p = ranked[0]
    if best < 2.5:
        return None, 0.0
    if len(ranked) > 1 and ranked[1][0] >= best - 0.5:
        return None, 0.0  # iki farkli isim ayni guvende: emin degiliz
    return p, (0.95 if best >= 3 else 0.9)


# ---------------------------------------------------------------- ses -> yazi

def _model():
    global _MODEL
    if _MODEL is None:
        from faster_whisper import WhisperModel
        _MODEL = WhisperModel(os.environ.get("OZET_WHISPER", "small"), device="cpu", compute_type="int8")
    return _MODEL


def transcribe(video: str, t0: float, t1: float, prompt: str = ""):
    wav = tempfile.mktemp(suffix=".wav")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{max(0, t0):.2f}", "-t", f"{t1 - t0:.2f}",
                    "-i", video, "-vn", "-ac", "1", "-ar", "16000", wav], check=True)
    try:
        segs, _ = _model().transcribe(wav, language="tr", word_timestamps=True, initial_prompt=prompt or None,
                                      beam_size=5, condition_on_previous_text=False)
        words = []
        for s in segs:
            for w in (s.words or []):
                words.append((w.word.strip(), t0 + w.start))
        return words
    finally:
        try:
            os.remove(wav)
        except OSError:
            pass


def assign(goals, video, home_roster, away_roster, log=print):
    """Her gol icin sesten golcu bulur; emin olursa goal alanlarini doldurur."""
    for g in goals:
        roster = home_roster if g.side == "L" else away_roster
        if not roster:
            continue
        names = ", ".join(p["name"].split()[0] for p in roster)
        prompt = f"Halı saha futbol maçı anlatımı. Oyuncular: {names}. Gol! Tamamladı, vurdu, golün sahibi."
        try:
            words = transcribe(video, g.update_t - 40, g.update_t + 12, prompt)
        except Exception as e:  # ses tanima calismazsa ozet durmasin
            log("Ses tanima hatasi:", e)
            return
        text = " ".join(w for w, _ in words)
        p, conf = pick_scorer(words, roster)
        g.note = (g.note + f" [ses: {text[-160:]}]").strip()
        if p:
            g.player_id, g.player_name, g.guest_name, g.confidence = p["id"], p["name"], None, conf
            log(f"#{g.n} golcu sesten: {p['name']} ({conf})")
