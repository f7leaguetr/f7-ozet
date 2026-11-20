"""YouTube'dan mac videosunu ve Turkce otomatik altyaziyi indirir (yt-dlp)."""
from __future__ import annotations

import glob
import os
import subprocess

# YouTube sunuculardan gelen indirmelere "bot degilim" engeli koyabiliyor.
# Engel cikarsa farkli istemci kimlikleriyle tekrar denenir.
CLIENTS = [None, "tv", "web_embedded", "mweb", "android_vr"]


class DownloadError(RuntimeError):
    pass


def fetch(url: str, work: str) -> tuple[str, str | None]:
    os.makedirs(work, exist_ok=True)
    base = ["yt-dlp", "--no-playlist", "--no-progress",
            "-f", "bv*[height<=720][ext=mp4]+ba[ext=m4a]/b[height<=720]/b",
            "--merge-output-format", "mp4",
            "--write-auto-subs", "--write-subs", "--sub-langs", "tr.*,tr", "--sub-format", "json3",
            "-o", os.path.join(work, "src.%(ext)s")]
    cookies = os.environ.get("YTDLP_COOKIES")
    if cookies:
        cpath = os.path.join(work, "cookies.txt")
        with open(cpath, "w") as f:
            f.write(cookies)
        base += ["--cookies", cpath]

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

    if "not a bot" in last or "Sign in" in last:
        raise DownloadError("YouTube, sunucudan indirmeyi engelledi (bot doğrulaması). "
                            "GitHub'a YTDLP_COOKIES eklenmesi gerekiyor.")
    if "Private video" in last or "private" in last.lower():
        raise DownloadError("Video gizli olduğu için indirilemedi.")
    if "unavailable" in last.lower():
        raise DownloadError("Video YouTube'da bulunamadı veya kaldırılmış.")
    if "live event will begin" in last or "is live" in last.lower():
        raise DownloadError("Yayın henüz bitmemiş; bittikten sonra tekrar denenecek.")
    tail = last.strip().splitlines()[-1] if last.strip() else "bilinmeyen hata"
    raise DownloadError(f"Video indirilemedi: {tail[:200]}")
