"""ffmpeg ile ozet videosunu birlestirir."""
from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass

from . import graphics

W, H, FPS = 1280, 720, 30
MAX_MB = float(os.environ.get("OZET_MAX_MB", "45"))  # Supabase ucretsiz planda dosya siniri 50 MB


@dataclass
class ClipSpec:
    start: float
    end: float
    banner: object  # PIL.Image
    t_in: float = 0.8     # alt bandin girdigi an (klip icinde)


def run(cmd):
    print("+", " ".join(str(c) for c in cmd)[:300])
    subprocess.run(cmd, check=True)


def has_audio(path: str) -> bool:
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries",
                          "stream=index", "-of", "csv=p=0", path], capture_output=True, text=True).stdout
    return bool(out.strip())


def _encode_args(crf=20):
    return ["-c:v", "libx264", "-preset", "veryfast", "-crf", str(crf), "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2"]


def render_clip(src: str, spec: ClipSpec, work: str, idx: int, audio: bool) -> str:
    bpath = os.path.join(work, f"banner_{idx}.png")
    spec.banner.save(bpath)
    dur = spec.end - spec.start
    x0, y_margin = 40, 44
    t_in, t_out = spec.t_in, max(spec.t_in + 2, dur - 1.4)
    # soldan kayarak gir, sona dogru sola kayarak cik
    xexpr = (f"if(lt(t,{t_in}),-w,"
             f"if(lt(t,{t_in + 0.5}),-w+({x0}+w)*(1-pow(1-(t-{t_in})/0.5,3)),"
             f"if(lt(t,{t_out}),{x0},"
             f"{x0}-({x0}+w)*pow(min(1,(t-{t_out})/0.4),2))))")
    out = os.path.join(work, f"clip_{idx:02d}.mp4")
    cmd = ["ffmpeg", "-y", "-v", "error", "-ss", f"{spec.start:.2f}", "-t", f"{dur:.2f}", "-i", src,
           "-loop", "1", "-i", bpath]
    if not audio:
        cmd += ["-f", "lavfi", "-t", f"{dur:.2f}", "-i", "anullsrc=r=48000:cl=stereo"]
    fc = (f"[0:v]scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,"
          f"fps={FPS},setsar=1[b];"
          f"[b][1:v]overlay=x='{xexpr}':y=H-h-{y_margin}:shortest=1:eval=frame,format=yuv420p[v]")
    cmd += ["-filter_complex", fc, "-map", "[v]", "-map", "0:a" if audio else "2:a",
            "-t", f"{dur:.2f}"] + _encode_args() + [out]
    run(cmd)
    return out


def render_still(img, seconds: float, work: str, name: str) -> str:
    p = os.path.join(work, f"{name}.png")
    img.convert("RGB").save(p)
    out = os.path.join(work, f"{name}.mp4")
    run(["ffmpeg", "-y", "-v", "error", "-loop", "1", "-t", f"{seconds}", "-i", p,
         "-f", "lavfi", "-t", f"{seconds}", "-i", "anullsrc=r=48000:cl=stereo",
         "-vf", f"fps={FPS},format=yuv420p", "-shortest"] + _encode_args() + [out])
    return out


def make_stinger(work: str) -> str:
    d = os.path.join(work, "stinger")
    os.makedirs(d, exist_ok=True)
    for i, f in enumerate(graphics.stinger_frames(W, H, FPS)):
        f.save(os.path.join(d, f"{i:03d}.png"))
    out = os.path.join(work, "stinger.mov")
    run(["ffmpeg", "-y", "-v", "error", "-framerate", str(FPS), "-i", os.path.join(d, "%03d.png"),
         "-c:v", "qtrle", "-pix_fmt", "argb", out])
    return out


def duration(path: str) -> float:
    return float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                          "-of", "default=nw=1:nk=1", path]).decode())


def render_highlight(src: str, clips: list[ClipSpec], end_img, out_path: str, work: str) -> str:
    os.makedirs(work, exist_ok=True)
    audio = has_audio(src)
    parts = [render_clip(src, c, work, i, audio) for i, c in enumerate(clips)]
    parts.append(render_still(end_img, 3.0, work, "endcard"))

    # 1) Kesik kesik birlestir (hepsi ayni formatta, yeniden kodlama yok)
    lst = os.path.join(work, "list.txt")
    with open(lst, "w") as f:
        for p in parts:
            f.write(f"file '{os.path.abspath(p)}'\n")
    body = os.path.join(work, "body.mp4")
    run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", body])

    # 2) Kesme noktalarina stinger bindir: baslangic + her kesme
    cuts, t = [], 0.0
    for p in parts[:-1]:
        t += duration(p)
        cuts.append(t)
    stinger = make_stinger(work)
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", body]
    # acilis: stinger'in ikinci yarisi (panel ekrandan cekilerek ilk golu acar),
    # sonra her kesmede stinger'in tam kapali ani (1.0 sn) kesmeye denk gelir
    offsets = [-1.0] + [c - 1.0 for c in cuts]
    for _ in offsets:
        cmd += ["-i", stinger]
    chain, last = [], "0:v"
    for k, off in enumerate(offsets, 1):
        src = f"s{k}"
        if off < 0:
            chain.append(f"[{k}:v]trim=start={-off},setpts=PTS-STARTPTS[{src}]")
        else:
            chain.append(f"[{k}:v]setpts=PTS-STARTPTS+{off:.3f}/TB[{src}]")
        nxt = f"v{k}"
        chain.append(f"[{last}][{src}]overlay=eof_action=pass:repeatlast=0[{nxt}]")
        last = nxt
    total = duration(body)
    kbps = int(max(800, min(4500, MAX_MB * 8 * 1024 / total - 140)))
    cmd += ["-filter_complex", ";".join(chain), "-map", f"[{last}]", "-map", "0:a",
            "-c:v", "libx264", "-preset", "veryfast", "-b:v", f"{kbps}k", "-maxrate", f"{int(kbps * 1.4)}k",
            "-bufsize", f"{kbps * 2}k", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart", out_path]
    run(cmd)
    return out_path


def cleanup(work: str):
    shutil.rmtree(work, ignore_errors=True)
