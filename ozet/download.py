"""YouTube'dan mac videosunu ve Turkce otomatik altyaziyi indirir (yt-dlp)."""
from __future__ import annotations

import glob
import os
import subprocess


def fetch(url: str, work: str) -> tuple[str, str | None]:
    os.makedirs(work, exist_ok=True)
    cmd = ["yt-dlp", "--no-playlist", "--no-progress",
           "-f", "bv*[height<=720][ext=mp4]+ba[ext=m4a]/b[height<=720]/b",
           "--merge-output-format", "mp4",
           "--write-auto-subs", "--write-subs", "--sub-langs", "tr.*,tr", "--sub-format", "json3",
           "-o", os.path.join(work, "src.%(ext)s"), url]
    cookies = os.environ.get("YTDLP_COOKIES")
    if cookies:
        cpath = os.path.join(work, "cookies.txt")
        with open(cpath, "w") as f:
            f.write(cookies)
        cmd[1:1] = ["--cookies", cpath]
    subprocess.run(cmd, check=True)
    video = os.path.join(work, "src.mp4")
    subs = sorted(glob.glob(os.path.join(work, "src*.json3")))
    return video, (subs[0] if subs else None)
