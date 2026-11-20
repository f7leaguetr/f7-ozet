"""F7 otomatik mac ozeti.

Sunucu modu (GitHub Actions):  python -m ozet.main
Yerel deneme:                  python -m ozet.main --video mac.mp4 --home-goals 9 --away-goals 2 \
                                   --home "WALWES FC" --away "TURGUT REİS FK" [--subs altyazi.json3]
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import re
import tempfile
import traceback

from . import graphics, moments, render, scoreboard, scorers, timeline

HALF_MINUTES = int(os.environ.get("OZET_HALF_MINUTES", "25"))


def build(video, subs, home, away, hg, ag, home_roster, away_roster, out_path, work):
    """Videoyu analiz eder, ozeti uretir. (rapor, goller, kontrol_gerekli) dondurur."""
    sb = scoreboard.analyze(video, hg, ag)
    caps = scorers.load_json3(subs) if subs else []
    sh_guess = timeline.estimate_second_half(sb, HALF_MINUTES)
    goals = timeline.build_goals(sb, HALF_MINUTES, sh_guess)

    found_h = sum(g.side == "L" for g in goals)
    found_a = sum(g.side == "R" for g in goals)
    problems = list(sb.warnings)
    if (found_h, found_a) != (hg, ag):
        problems.append(f"tabelada {found_h}-{found_a} bulundu, mac skoru {hg}-{ag}")

    if caps and goals:
        half_window = ((sb.first_half[1] if sb.first_half else sb.duration * 0.4),
                       (sb.first_half[1] + 900 if sb.first_half else sb.duration * 0.65))
        sh_llm = scorers.assign(goals, caps, home, away, home_roster, away_roster, half_window)
        if sh_llm and not sb.second_half_start and sb.first_half and sh_llm > sb.first_half[1]:
            # 2. yari baslangici anlatimdan bulunduysa dakikalari yeniden hesapla
            fixed = timeline.build_goals(sb, HALF_MINUTES, float(sh_llm))
            for g, f in zip(goals, fixed):
                g.minute = f.minute

    report = [f"Tabela: {found_h}-{found_a} (beklenen {hg}-{ag}) · grafik {sb.template or '?'}"
              + (" · tabelada takımlar ters sırada" if sb.swapped else "")]
    for g in goals:
        who = g.player_name or (f"Misafir ({g.guest_name})" if g.guest_name else "?")
        report.append(f"#{g.n} {g.minute or '?'}' {home if g.side == 'L' else away} {g.home}-{g.away} "
                      f"{who} [video {int(g.update_t // 60)}:{int(g.update_t % 60):02d}, guven {g.confidence:.1f}] {g.note}")
    report += [f"UYARI: {p}" for p in problems]

    # Tabela okumasi belirsizse (parazitle gol ayrismiyorsa) yayinlama, yoneticiye birak
    blocking = any("bulundu, mac skoru" in p or "gol bekleniyordu" in p or "ayrismiyor" in p
                   or ("okunamadi" in p and goals) for p in problems)
    if blocking:
        return "\n".join(report), goals, True

    # Onemli pozisyonlar: az gollu maclarda ozeti zenginlestirir, golsuz macta ozetin kendisi
    want = moments.wanted_count(len(goals), timeline.CLIP_BEFORE + timeline.CLIP_AFTER)
    found = moments.find_moments(video, caps, sb.duration, [g.update_t for g in goals], want,
                                 play_start=sb.first_half[0] if sb.first_half else None)
    if not goals and not found:
        report.append("UYARI: gol yok ve önemli pozisyon bulunamadı")
        return "\n".join(report), goals, True

    def label(g):
        if g.player_name:
            return g.player_name
        if g.guest_name:
            return f"Misafir ({g.guest_name})"
        return home if g.side == "L" else away

    def moment_label(m):
        t = moments._norm(m.text)
        if "penalti" in t:
            return "PENALTI"
        if "direk" in t or "direg" in t:
            return "DİREKTEN DÖNDÜ"
        if "kurtar" in t or "cikardi" in t or "uzandi" in t:
            return "KURTARIŞ"
        if "kacir" in t or "az farkla" in t:
            return "KAÇAN FIRSAT"
        return ""

    items = []
    for g in goals:
        items.append((g.clip[0], render.ClipSpec(g.clip[0], g.clip[1], graphics.banner(
            g.minute, label(g), home if g.side == "L" else away, f"{g.home}-{g.away}"))))
    for m in found:
        mn = timeline.minute_at(sb, m.t, HALF_MINUTES, sh_guess)
        h = sum(1 for g in goals if g.side == "L" and g.update_t <= m.t)
        a = sum(1 for g in goals if g.side == "R" and g.update_t <= m.t)
        items.append((m.clip[0], render.ClipSpec(m.clip[0], m.clip[1], graphics.banner(
            mn, moment_label(m), None, f"{h}-{a}", tag="POZİSYON"))))
        report.append(f"POZİSYON {mn or '?'}' {moment_label(m) or '-'} [video {int(m.t // 60)}:{int(m.t % 60):02d}, "
                      f"puan {m.score:.1f}]")
    clips = [c for _, c in sorted(items, key=lambda x: x[0])]
    tally = collections.Counter(g.player_name for g in goals if g.player_name)
    top = None
    if tally:
        name, cnt = tally.most_common(1)[0]
        if cnt >= 2:
            top = f"{name} {cnt} gol"
    end = graphics.end_card(home, away, hg, ag, top)
    render.render_highlight(video, clips, end, out_path, os.path.join(work, "render"))
    return "\n".join(report), goals, False


def goals_json(goals, home_id, away_id):
    return [{
        "n": g.n, "minute": g.minute, "team_id": home_id if g.side == "L" else away_id,
        "side": "home" if g.side == "L" else "away",
        "player_id": g.player_id, "guest_name": g.guest_name,
        "score": f"{g.home}-{g.away}", "video_time": round(g.update_t - timeline.SCOREBOARD_LAG),
        "confidence": round(g.confidence, 2), "note": g.note,
    } for g in goals]


def process_job(job):
    from . import db, download
    key = job["match_key"]
    work = tempfile.mkdtemp(prefix="ozet_")
    try:
        video, subs = download.fetch(job["youtube_url"], os.path.join(work, "dl"))
        hr, ar = db.roster(job.get("home_team_id")), db.roster(job.get("away_team_id"))
        out = os.path.join(work, "ozet.mp4")
        report, goals, needs_check = build(video, subs, job["home_team_name"], job["away_team_name"],
                                           int(job["home_score"]), int(job["away_score"]), hr, ar, out, work)
        print(report)
        gj = goals_json(goals, job.get("home_team_id"), job.get("away_team_id"))
        if needs_check:
            db.update(key, status="kontrol", report=report, goals=gj)
            return
        safe = re.sub(r"[^A-Za-z0-9_-]", "_", key)
        url = db.upload_video(out, f"{safe}.mp4")
        db.update(key, status="hazir", highlight_url=url, report=report, goals=gj, error=None)
        print("Goller siteye:", db.apply_goals(key))
    except download.DownloadError as e:
        db.update(key, status="hata", error=str(e)[:500])
    except Exception as e:
        traceback.print_exc()
        db.update(key, status="hata", error=f"Özet hazırlanırken beklenmeyen hata: {str(e)[:300]}")
    finally:
        render.cleanup(work)


def serve():
    import time
    from . import db, download
    only = [k.strip() for k in os.environ.get("OZET_ONLY", "").split(",") if k.strip()]
    budget = float(os.environ.get("OZET_BUDGET_MIN", "110")) * 60
    t0 = time.time()
    checked = False
    while time.time() - t0 < budget:
        if only:  # elle calistirmada belirli maclari isle
            jobs = [j for j in db.pending_jobs(limit=100) if j["match_key"] in only]
        else:
            jobs = db.pending_jobs()
        if not jobs:
            print("Kuyruk bos.")
            return
        if not checked:
            if download.blocked(jobs[0]["youtube_url"]):
                print("YouTube bu sunucuyu engelliyor (bot dogrulamasi). YTDLP_COOKIES gerekli; "
                      "maclara dokunulmadi, bir sonraki calismada tekrar denenecek.")
                return
            checked = True
        did = 0
        for job in jobs:
            if db.claim(job):  # paralel calisan diger isciler ayni maci alamaz
                did += 1
                print("== isleniyor:", job["match_key"], job["youtube_url"], flush=True)
                process_job(job)
        if only or not did:
            return


def local(a):
    roster = lambda p: json.load(open(p, encoding="utf-8")) if p else []
    work = tempfile.mkdtemp(prefix="ozet_")
    report, goals, check = build(a.video, a.subs, a.home, a.away, a.home_goals, a.away_goals,
                                 roster(a.home_roster), roster(a.away_roster), a.out, work)
    print(report)
    print("KONTROL GEREKLI" if check else f"Ozet: {a.out}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--video")
    p.add_argument("--subs")
    p.add_argument("--home", default="EV SAHİBİ")
    p.add_argument("--away", default="DEPLASMAN")
    p.add_argument("--home-goals", type=int)
    p.add_argument("--away-goals", type=int)
    p.add_argument("--home-roster")
    p.add_argument("--away-roster")
    p.add_argument("--out", default="ozet.mp4")
    a = p.parse_args()
    local(a) if a.video else serve()
