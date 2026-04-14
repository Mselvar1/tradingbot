"""
FastAPI signal platform dashboard (read-heavy + Monte Carlo trigger).

Run locally:
  uvicorn dashboard.app:app --reload --host 0.0.0.0 --port 8080

Railway: add a second service with start command:
  uvicorn dashboard.app:app --host 0.0.0.0 --port $PORT
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates

from config.settings import settings
from services.memory import count_all_candles, init_db
from services.signal_platform.circuit_breaker import get_state, is_paused
from services.signal_platform.candles_store import fetch_candles_from_db
from services.signal_platform.strategy_runner import latest_scores_summary
from services.signal_platform.validation_engine import (
    fetch_latest_snapshots,
    run_monte_carlo_job,
)

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.database_url:
        try:
            await init_db()
        except Exception as e:
            print(f"Dashboard init_db: {e}")
    yield


app = FastAPI(title="Signal Platform", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


def _auth_ok(request: Request) -> bool:
    tok = getattr(settings, "dashboard_auth_token", None) or None
    if not tok:
        return True
    h = request.headers.get("Authorization") or ""
    return h == f"Bearer {tok}"


def _fmt_ts(x) -> str:
    if x is None:
        return "—"
    if hasattr(x, "strftime"):
        return x.strftime("%Y-%m-%d %H:%M UTC")
    return str(x)


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    if not settings.database_url:
        return HTMLResponse("<h1>DATABASE_URL not set</h1>", status_code=503)
    candle_count = await count_all_candles()
    circuit_raw = await get_state()
    paused_live = await is_paused()
    circuit = {
        "consecutive_sl": circuit_raw.get("consecutive_sl", 0),
        "paused_until": _fmt_ts(circuit_raw.get("paused_until")),
        "updated_at": _fmt_ts(circuit_raw.get("updated_at")),
        "paused_active": paused_live,
    }
    snaps = await fetch_latest_snapshots(5)
    last_job = snaps[0] if snaps else None
    if last_job and isinstance(last_job.get("payload"), (dict, list)):
        last_job = {
            **last_job,
            "payload": json.dumps(last_job["payload"])[:400],
        }
    scores = await latest_scores_summary()
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "candle_count": candle_count,
            "circuit": circuit,
            "last_job": last_job,
            "scores": scores,
            "streak_limit": getattr(settings, "circuit_breaker_sl_streak", 8),
        },
    )


@app.get("/strategies", response_class=HTMLResponse)
async def strategies_page(request: Request):
    scores = await latest_scores_summary()
    return templates.TemplateResponse(
        "strategies.html", {"request": request, "scores": scores}
    )


@app.get("/validation", response_class=HTMLResponse)
async def validation_page(request: Request):
    snaps = await fetch_latest_snapshots(40)
    for s in snaps:
        p = s.get("payload")
        s["payload"] = p if isinstance(p, str) else json.dumps(p, indent=2, default=str)
    return templates.TemplateResponse(
        "validation.html", {"request": request, "snapshots": snaps}
    )


@app.get("/monte-carlo", response_class=HTMLResponse)
async def monte_get(request: Request):
    return templates.TemplateResponse(
        "monte.html", {"request": request, "result": None}
    )


@app.post("/monte-carlo/run")
async def monte_run(
    request: Request,
    ticker: str = Form("BTC-USD"),
    iterations: int = Form(400),
):
    if not _auth_ok(request):
        raise HTTPException(401, "Invalid or missing Authorization Bearer token")
    if iterations > 8000:
        iterations = 8000
    out = await run_monte_carlo_job(ticker, iterations)
    body = json.dumps(out, indent=2, default=str)
    return templates.TemplateResponse(
        "monte.html", {"request": request, "result": body}
    )


@app.get("/candles", response_class=HTMLResponse)
async def candles_page(request: Request, instrument: str = "BTC-USD", tf: str = "M15"):
    rows = await fetch_candles_from_db(instrument, tf, limit=80)
    for r in rows:
        if r.get("open_time"):
            r["open_time"] = r["open_time"].strftime("%Y-%m-%d %H:%M") if hasattr(
                r["open_time"], "strftime"
            ) else str(r["open_time"])
    return templates.TemplateResponse(
        "candles.html",
        {
            "request": request,
            "rows": rows,
            "instrument": instrument,
            "timeframe": tf,
        },
    )


@app.get("/circuit", response_class=HTMLResponse)
async def circuit_page(request: Request):
    circuit = await get_state()
    pu = circuit.get("paused_until")
    if pu and hasattr(pu, "strftime"):
        circuit = {**circuit, "paused_until": pu.strftime("%Y-%m-%d %H:%M UTC")}
    u = circuit.get("updated_at")
    if u and hasattr(u, "strftime"):
        circuit = {**circuit, "updated_at": u.strftime("%Y-%m-%d %H:%M UTC")}
    return templates.TemplateResponse(
        "circuit.html",
        {
            "request": request,
            "circuit": circuit,
            "streak_limit": getattr(settings, "circuit_breaker_sl_streak", 8),
            "pause_hours": getattr(settings, "circuit_breaker_pause_hours", 24),
        },
    )
