"""Sitenin veritabanina Bolt'taki `highlight-worker` sunucu fonksiyonu uzerinden erisir.

Bolt veritabanini kendisi yonettigi icin service_role anahtari disari verilmiyor;
fonksiyon icerde yetkili calisir, biz ona sadece OZET_WORKER_TOKEN ile baglaniriz.
"""
from __future__ import annotations

import os

import requests

URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
TOKEN = os.environ.get("OZET_WORKER_TOKEN", "")
FN = f"{URL}/functions/v1/highlight-worker"


def _call(action: str, **payload):
    r = requests.post(FN, json={"action": action, **payload}, timeout=120,
                      headers={"x-worker-token": TOKEN, "Content-Type": "application/json"})
    if not r.ok:
        raise RuntimeError(f"{action}: {r.status_code} {r.text[:300]}")
    return r.json()


def pending_jobs(limit=3):
    return _call("pending", limit=limit).get("jobs", [])


def claim(job) -> bool:
    return bool(_call("claim", match_key=job["match_key"], status=job["status"],
                      attempts=job.get("attempts")).get("claimed"))


def update(match_key, **fields):
    _call("update", match_key=match_key, fields=fields)


def apply_goals(match_key) -> str:
    try:
        return str(_call("apply_goals", match_key=match_key).get("result", "ok"))
    except Exception as e:
        return f"hata: {e}"


def roster(team_id):
    if not team_id:
        return []
    return _call("roster", team_id=team_id).get("players", [])


def upload_video(path: str, name: str) -> str:
    """Fonksiyondan imzali yukleme adresi alir, dosyayi dogrudan depoya yukler."""
    info = _call("upload_url", path=f"highlights/{name}")
    with open(path, "rb") as f:
        r = requests.put(info["signed_url"], data=f, timeout=900,
                         headers={"Content-Type": "video/mp4", "x-upsert": "true"})
    if not r.ok:
        raise RuntimeError(f"yukleme: {r.status_code} {r.text[:300]}")
    return info["public_url"]
