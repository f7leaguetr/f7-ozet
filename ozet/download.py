"""YouTube'dan mac videosunu ve Turkce otomatik altyaziyi indirir (yt-dlp)."""
from __future__ import annotations

import glob
import os
import subprocess

# YouTube sunuculardan gelen indirmelere "bot degilim" engeli koyabiliyor.
# Engel cikarsa farkli istemci kimlikleriyle tekrar denenir.
CLIENTS = [None, "tv", "web_embedded", "mweb", "android_vr"]


class DownloadError(RuntimeError):
    blocked = False


class BlockedError(DownloadError):
    """YouTube bot engeli / 429: macin suclu degil, sonra tekrar denenmeli."""
    blocked = True


def _cookie_args(work: str) -> list[str]:
    cookies = os.environ.get("YTDLP_COOKIES")
    if not cookies:
        return []
    os.makedirs(work, exist_ok=True)
    cpath = os.path.join(work, "cookies.txt")
    with open(cpath, "w") as f:
        f.write(cookies)
    return ["--cookies", cpath]


def blocked(url: str, work: str = "/tmp/ozet_probe") -> bool:
    """Indirmeden once: YouTube bu sunucuyu 'bot' diye engelliyor mu?"""
    # Bazen yalnizca varsayilan istemci engellenir; hepsi engelliyse "engelli" say.
    for client in CLIENTS:
        cmd = ["yt-dlp", "--simulate", "--no-playlist", "-q"] + _cookie_args(work)
        if client:
            cmd += ["--extractor-args", f"youtube:player_client={client}"]
        p = subprocess.run(cmd + [url], capture_output=True, text=True)
        if p.returncode == 0 or ("not a bot" not in p.stderr and "Sign in" not in p.stderr):
            return False
    print("Engel mesaji:", p.stderr.strip().splitlines()[-1][:300] if p.stderr.strip() else "-")
    return True


def fetch(url: str, work: str) -> tuple[str, str | None]:
    os.makedirs(work, exist_ok=True)
    base = ["yt-dlp", "--no-playlist", "--no-progress",
            "-f", "bv*[height<=720][ext=mp4]+ba[ext=m4a]/b[height<=720]/b",
            "--merge-output-format", "mp4",
            "--write-auto-subs", "--write-subs", "--sub-langs", "tr.*,tr", "--sub-format", "json3",
            "-o", os.path.join(work, "src.%(ext)s")]
    base += _cookie_args(work)

    last = ""
    for client in CLIENTS:
        cmd = list(base)
        if client:
            cmd += ["--extractor-args", f"youtube:player_client={client}"]
        p = subprocess.run(cmd + [url], capture_output=True, text=True)
        print(p.stdout[-1500:], p.stderr[-1500:])
        video = os.path.join(work, "src.mp4")
        if p.returncode == 0 and os.path.exists(video):
            subs = sorted(glob.glob(os.path.join(work, "src*.json3")))
            return video, (subs[0] if subs else None)
        last = p.stderr
        if "not a bot" not in last and "Sign in" not in last:
            break  # engel disi bir hata: diger istemcileri denemenin anlami yok

    if "not a bot" in last or "Sign in" in last or "429" in last:
        raise BlockedError("YouTube, sunucudan indirmeyi engelledi (bot doğrulaması). "
                            "GitHub'a YTDLP_COOKIES eklenmesi gerekiyor.")
    if "Private video" in last or "private" in last.lower():
        raise DownloadError("Video gizli olduğu için indirilemedi.")
    if "unavailable" in last.lower():
        raise DownloadError("Video YouTube'da bulunamadı veya kaldırılmış.")
    if "live event will begin" in last or "is live" in last.lower():
        raise DownloadError("Yayın henüz bitmemiş; bittikten sonra tekrar denenecek.")
    tail = last.strip().splitlines()[-1] if last.strip() else "bilinmeyen hata"
    raise DownloadError(f"Video indirilemedi: {tail[:200]}")
