"""Schulweghelferlein – Erfassung und Auswertung von Rotlicht-/Gelblichtverstößen."""

import asyncio
import csv
import hashlib
import hmac
import io
import os
import sqlite3
from contextlib import asynccontextmanager, closing
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Literal

import yaml
from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

BASE_DIR = Path(__file__).parent
STATIC_DIR = BASE_DIR / "static"
CONFIG_PATH = Path(os.environ.get("CONFIG_PATH", BASE_DIR.parent / "config.yaml"))
DB_PATH = Path(os.environ.get("DB_PATH", BASE_DIR.parent / "data" / "schulweg.db"))
ACCESS_KEY = os.environ.get("ACCESS_KEY", "")
COOKIE_NAME = "swh_device"
COOKIE_MAX_AGE = 400 * 24 * 3600  # Browser-Maximum (Chrome deckelt bei 400 Tagen)

CATEGORIES = [
    {"id": "red", "name": "Rot"},
    {"id": "other", "name": "Sonstige"},
    {"id": "yellow", "name": "Gelb"},
]
CATEGORY_IDS = [c["id"] for c in CATEGORIES]
WEEKDAYS = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]
MONTHS = ["Jan", "Feb", "Mär", "Apr", "Mai", "Jun", "Jul", "Aug", "Sep", "Okt", "Nov", "Dez"]


# --- Konfiguration --------------------------------------------------------------

def load_config() -> dict:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    locations = cfg.get("locations") or []
    ids = [loc["id"] for loc in locations]
    if not locations or len(ids) != len(set(ids)):
        raise RuntimeError(f"{CONFIG_PATH}: 'locations' fehlt oder enthält doppelte ids")
    return {
        "title": cfg.get("title", "Schulweghelferlein"),
        "locations": [{"id": str(loc["id"]), "name": str(loc["name"])} for loc in locations],
    }


CONFIG = load_config()
LOCATION_NAMES = {loc["id"]: loc["name"] for loc in CONFIG["locations"]}


# --- Datenbank ------------------------------------------------------------------

def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with closing(db()) as conn, conn:
        # Kein WAL: braucht Shared Memory auf demselben Host und ist auf NFS nicht sicher
        conn.execute("PRAGMA journal_mode=DELETE")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS entries (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                date        TEXT    NOT NULL,
                location_id TEXT    NOT NULL,
                red         INTEGER NOT NULL DEFAULT 0,
                yellow      INTEGER NOT NULL DEFAULT 0,
                other       INTEGER NOT NULL DEFAULT 0,
                note        TEXT,
                created_at  TEXT    NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_entries_date ON entries(date)")


# --- Zugangsschutz --------------------------------------------------------------
# Kein Benutzerkonto: wer den gemeinsamen Schlüssel kennt, bekommt ein langlebiges
# Cookie. Das Cookie ist ein HMAC des Schlüssels – wird der Schlüssel geändert,
# sind alle Geräte automatisch abgemeldet.

def device_token() -> str:
    return hmac.new(ACCESS_KEY.encode(), b"swh-device-v1", hashlib.sha256).hexdigest()


def is_authorized(request: Request) -> bool:
    token = request.cookies.get(COOKIE_NAME, "")
    return bool(ACCESS_KEY) and hmac.compare_digest(token, device_token())


def key_matches(key: str) -> bool:
    return bool(ACCESS_KEY) and hmac.compare_digest(key.strip().encode(), ACCESS_KEY.encode())


def set_device_cookie(request: Request, response: Response) -> None:
    response.set_cookie(
        COOKIE_NAME,
        device_token(),
        max_age=COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
    )


@asynccontextmanager
async def lifespan(_: FastAPI):
    if not ACCESS_KEY:
        raise RuntimeError("Umgebungsvariable ACCESS_KEY ist nicht gesetzt")
    init_db()
    yield


app = FastAPI(title="Schulweghelferlein", lifespan=lifespan, docs_url=None, redoc_url=None)

PUBLIC_API = {"/api/login", "/api/session"}


@app.middleware("http")
async def require_key(request: Request, call_next):
    path = request.url.path
    if path.startswith("/api/") and path not in PUBLIC_API and not is_authorized(request):
        return JSONResponse({"detail": "Nicht angemeldet"}, status_code=401)
    return await call_next(request)


class LoginIn(BaseModel):
    key: str = Field(max_length=200)


@app.post("/api/login")
async def login(body: LoginIn, request: Request, response: Response):
    if not key_matches(body.key):
        await asyncio.sleep(1)  # bremst Durchprobieren
        raise HTTPException(401, "Falscher Schlüssel")
    set_device_cookie(request, response)
    return {"ok": True}


@app.get("/login")
async def login_link(request: Request, key: str = ""):
    """Einladungslink zum Teilen, z. B. https://host/login?key=… (auch als QR-Code)."""
    if not key_matches(key):
        await asyncio.sleep(1)
        return RedirectResponse("/", status_code=303)
    response = RedirectResponse("/", status_code=303)
    set_device_cookie(request, response)
    return response


@app.get("/api/session")
def session(request: Request):
    return {"authorized": is_authorized(request)}


@app.get("/healthz")
def healthz():
    return {"ok": True}


# --- Einträge -------------------------------------------------------------------

class EntryIn(BaseModel):
    date: date
    location_id: str
    red: int = Field(ge=0, le=999)
    yellow: int = Field(ge=0, le=999)
    other: int = Field(ge=0, le=999)
    note: str | None = Field(default=None, max_length=500)


def entry_out(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["location_name"] = LOCATION_NAMES.get(d["location_id"], f"{d['location_id']} (entfernt)")
    return d


@app.get("/api/config")
def get_config():
    return {**CONFIG, "categories": CATEGORIES}


@app.post("/api/entries", status_code=201)
def create_entry(entry: EntryIn):
    if entry.location_id not in LOCATION_NAMES:
        raise HTTPException(422, "Unbekannter Standort")
    # +1 Tag Toleranz, weil das Datum aus der lokalen Zeit des Handys kommt
    if entry.date > date.today() + timedelta(days=1):
        raise HTTPException(422, "Datum liegt in der Zukunft")
    note = (entry.note or "").strip() or None
    with closing(db()) as conn, conn:
        cur = conn.execute(
            "INSERT INTO entries (date, location_id, red, yellow, other, note, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (entry.date.isoformat(), entry.location_id, entry.red, entry.yellow, entry.other,
             note, datetime.now().isoformat(timespec="seconds")),
        )
        row = conn.execute("SELECT * FROM entries WHERE id = ?", (cur.lastrowid,)).fetchone()
    return entry_out(row)


@app.get("/api/entries")
def list_entries(limit: int = Query(50, ge=1, le=500)):
    with closing(db()) as conn:
        rows = conn.execute(
            "SELECT * FROM entries ORDER BY date DESC, id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [entry_out(r) for r in rows]


@app.delete("/api/entries/{entry_id}", status_code=204)
def delete_entry(entry_id: int):
    with closing(db()) as conn, conn:
        if conn.execute("DELETE FROM entries WHERE id = ?", (entry_id,)).rowcount == 0:
            raise HTTPException(404, "Eintrag nicht gefunden")


@app.get("/api/export.csv")
def export_csv():
    with closing(db()) as conn:
        rows = conn.execute("SELECT * FROM entries ORDER BY date, id").fetchall()
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")  # Semikolon: öffnet in deutschem Excel direkt
    w.writerow(["Datum", "Standort", "Rot", "Gelb", "Sonstige", "Gesamt", "Notiz", "Erfasst am"])
    for r in rows:
        e = entry_out(r)
        w.writerow([e["date"], e["location_name"], e["red"], e["yellow"], e["other"],
                    e["red"] + e["yellow"] + e["other"], e["note"] or "", e["created_at"]])
    return Response(
        "﻿" + buf.getvalue(),  # BOM, damit Excel Umlaute richtig erkennt
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="schulweg-verstoesse.csv"'},
    )


# --- Statistik ------------------------------------------------------------------

Granularity = Literal["day", "week", "month"]


def bucket_of(d: date, granularity: Granularity) -> date:
    if granularity == "week":
        return d - timedelta(days=d.weekday())
    if granularity == "month":
        return d.replace(day=1)
    return d


def bucket_label(b: date, granularity: Granularity) -> str:
    if granularity == "week":
        return f"KW {b.isocalendar().week}"
    if granularity == "month":
        return f"{MONTHS[b.month - 1]} {b.year % 100:02d}"
    return f"{WEEKDAYS[b.weekday()]} {b.day:02d}.{b.month:02d}."


def all_buckets(start: date, end: date, granularity: Granularity) -> list[date]:
    """Lückenlose Zeitachse; bei Tagen nur Schultage (Mo–Fr)."""
    out, b = [], bucket_of(start, granularity)
    while b <= end:
        if granularity != "day" or b.weekday() < 5:
            out.append(b)
        if granularity == "month":
            b = (b + timedelta(days=32)).replace(day=1)
        else:
            b += timedelta(days=7 if granularity == "week" else 1)
    return out


def default_start(end: date, granularity: Granularity) -> date:
    if granularity == "week":
        return end - timedelta(weeks=11)
    if granularity == "month":
        return (end.replace(day=1) - timedelta(days=335)).replace(day=1)
    return end - timedelta(days=27)


@app.get("/api/stats")
def stats(
    granularity: Granularity = "day",
    start: date | None = None,
    end: date | None = None,
    location: str | None = None,
):
    end = end or date.today()
    start = start or default_start(end, granularity)
    if start > end:
        raise HTTPException(422, "Start liegt nach Ende")

    sql = "SELECT date, location_id, red, yellow, other FROM entries WHERE date BETWEEN ? AND ?"
    params: list = [start.isoformat(), end.isoformat()]
    if location:
        sql += " AND location_id = ?"
        params.append(location)
    with closing(db()) as conn:
        rows = conn.execute(sql, params).fetchall()

    buckets = all_buckets(start, end, granularity)
    # Wochenenden nur zeigen, wenn dort tatsächlich etwas erfasst wurde
    buckets = sorted(set(buckets) | {bucket_of(date.fromisoformat(r["date"]), granularity) for r in rows})
    index = {b: i for i, b in enumerate(buckets)}
    n = len(buckets)

    by_category = {c: [0] * n for c in CATEGORY_IDS}
    shifts = [0] * n
    location_ids = list(LOCATION_NAMES) + sorted(
        {r["location_id"] for r in rows} - set(LOCATION_NAMES)
    )
    by_location = {lid: [0] * n for lid in location_ids}
    loc_totals = {lid: {c: 0 for c in CATEGORY_IDS} | {"shifts": 0} for lid in location_ids}

    for r in rows:
        i = index[bucket_of(date.fromisoformat(r["date"]), granularity)]
        total = r["red"] + r["yellow"] + r["other"]
        shifts[i] += 1
        by_location[r["location_id"]][i] += total
        lt = loc_totals[r["location_id"]]
        lt["shifts"] += 1
        for c in CATEGORY_IDS:
            by_category[c][i] += r[c]
            lt[c] += r[c]

    totals = {c: sum(by_category[c]) for c in CATEGORY_IDS}
    totals["total"] = sum(totals.values())
    totals["shifts"] = len(rows)

    if location:
        location_ids = [location]
    return {
        "granularity": granularity,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "labels": [bucket_label(b, granularity) for b in buckets],
        "bucket_starts": [b.isoformat() for b in buckets],
        "by_category": by_category,
        "shifts": shifts,
        "by_location": [
            {"id": lid, "name": LOCATION_NAMES.get(lid, f"{lid} (entfernt)"), "values": by_location[lid]}
            for lid in location_ids
        ],
        "location_totals": [
            {
                "id": lid,
                "name": LOCATION_NAMES.get(lid, f"{lid} (entfernt)"),
                **loc_totals[lid],
                "total": sum(loc_totals[lid][c] for c in CATEGORY_IDS),
            }
            for lid in location_ids
        ],
        "totals": totals,
    }


# --- Frontend -------------------------------------------------------------------

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/manifest.webmanifest")
def manifest():
    return JSONResponse(
        {
            "name": CONFIG["title"],
            "short_name": CONFIG["title"],
            "start_url": "/",
            "display": "standalone",
            "background_color": "#f6f5f2",
            "theme_color": "#1d6b3a",
            "icons": [{"src": "/static/icon.svg", "sizes": "any", "type": "image/svg+xml"}],
        },
        media_type="application/manifest+json",
    )
