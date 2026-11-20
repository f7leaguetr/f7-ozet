"""Supabase REST + Storage erisimi (service role anahtariyla, sadece sunucu tarafinda)."""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import requests

URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
BUCKET = os.environ.get("OZET_BUCKET", "media")
TABLE = "highlight_jobs"


def _h(extra=None):
    h = {"apikey": KEY, "Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}
    h.update(extra or {})
    return h


def pending_jobs(limit=3):
    stale = (datetime.now(timezone.utc) - timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
    q = (f"{URL}/rest/v1/{TABLE}?select=*&attempts=lt.3"
         f"&or=(status.eq.bekliyor,and(status.eq.isleniyor,updated_at.lt.{stale}))"
         f"&order=created_at.asc&limit={limit}")
    r = requests.get(q, headers=_h(), timeout=30)
    r.raise_for_status()
    return r.json()


def claim(job) -> bool:
    """Ayni isi iki calisma birden almasin diye durum kosullu guncellenir."""
    q = f"{URL}/rest/v1/{TABLE}"
    params = {"match_key": f"eq.{job['match_key']}", "status": f"eq.{job['status']}",
              "attempts": "is.null" if job.get("attempts") is None else f"eq.{job['attempts']}"}
    r = requests.patch(q, params=params, headers=_h({"Prefer": "return=representation"}), timeout=30,
                       json={"status": "isleniyor", "attempts": (job.get("attempts") or 0) + 1,
                             "updated_at": datetime.now(timezone.utc).isoformat()})
    r.raise_for_status()
    return bool(r.json())


def update(match_key, **fields):
    fields["updated_at"] = datetime.now(timezone.utc).isoformat()
    r = requests.patch(f"{URL}/rest/v1/{TABLE}", params={"match_key": f"eq.{match_key}"},
                       headers=_h(), json=fields, timeout=30)
    r.raise_for_status()


def apply_goals(match_key) -> str:
    """Sitedeki apply_highlight_goals fonksiyonunu cagirir (Bolt'un ekledigi)."""
    r = requests.post(f"{URL}/rest/v1/rpc/apply_highlight_goals", headers=_h(),
                      json={"p_match_key": match_key}, timeout=60)
    return "ok" if r.ok else f"{r.status_code} {r.text[:200]}"


def roster(team_id):
    if not team_id:
        return []
    r = requests.get(f"{URL}/rest/v1/players?select=id,name,jersey_number&team_id=eq.{team_id}",
                     headers=_h(), timeout=30)
    r.raise_for_status()
    return r.json()


def upload_video(path: str, name: str) -> str:
    obj = f"highlights/{name}"
    with open(path, "rb") as f:
        r = requests.post(f"{URL}/storage/v1/object/{BUCKET}/{obj}", data=f, timeout=600,
                          headers={"apikey": KEY, "Authorization": f"Bearer {KEY}",
                                   "Content-Type": "video/mp4", "x-upsert": "true",
                                   "Cache-Control": "max-age=31536000"})
    r.raise_for_status()
    return f"{URL}/storage/v1/object/public/{BUCKET}/{obj}"
