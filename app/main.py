"""
Kubegotchi - a virtual pet that lives in your Kubernetes cluster.

Phase 0: the pet has stats (hunger, happiness, energy) that decay over time.
You take care of it through the /feed, /play and /sleep endpoints.
Everything is exposed at /metrics for Prometheus.

Environment variables to "mistreat" the pet (useful from Phase 3 on):
  PET_NAME            the pet's name (default: Bolinha)
  CHAOS_LATENCY_MS    extra latency on every request, in ms (default: 0)
  CHAOS_ERROR_RATE    fraction of requests that fail with 500, 0.0 to 1.0 (default: 0)
  DECAY_PER_MINUTE    how much each stat drops per minute (default: 2)
"""

import asyncio
import os
import random
import threading
import time

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest
from starlette.routing import Match

PET_NAME = os.getenv("PET_NAME", "Bolinha")
CHAOS_LATENCY_MS = float(os.getenv("CHAOS_LATENCY_MS", "0"))
CHAOS_ERROR_RATE = float(os.getenv("CHAOS_ERROR_RATE", "0"))
DECAY_PER_MINUTE = float(os.getenv("DECAY_PER_MINUTE", "2"))

# ---------------------------------------------------------------------------
# Metrics (the "vital signs" Prometheus will scrape)
# ---------------------------------------------------------------------------
REQUESTS = Counter(
    "kubegotchi_http_requests_total",
    "Total HTTP requests",
    ["path", "method", "code"],
)
LATENCY = Histogram(
    "kubegotchi_http_request_duration_seconds",
    "HTTP request duration",
    ["path"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5),
)
STAT = Gauge("kubegotchi_stat", "Pet stat, from 0 to 100", ["stat"])
ACTIONS = Counter("kubegotchi_actions_total", "Care actions received", ["action"])

# ---------------------------------------------------------------------------
# Pet state
# ---------------------------------------------------------------------------
_lock = threading.Lock()
_stats = {"hunger": 100.0, "happiness": 100.0, "energy": 100.0}  # 100 = great
_born_at = time.time()


def _clamp(v: float) -> float:
    return max(0.0, min(100.0, v))


def _publish() -> None:
    for name, value in _stats.items():
        STAT.labels(stat=name).set(value)


def _decay() -> None:
    """One 10s tick: each stat drops 1/6 of the per-minute decay."""
    with _lock:
        for k in _stats:
            _stats[k] = _clamp(_stats[k] - DECAY_PER_MINUTE / 6)
        _publish()


def _decay_loop() -> None:
    """Stats drop over time, like a real tamagotchi."""
    while True:
        time.sleep(10)
        _decay()


def _mood() -> str:
    worst = min(_stats.values())
    if worst >= 70:
        return "happy"
    if worst >= 40:
        return "ok"
    if worst >= 15:
        return "sad"
    return "sick"


def _change(**deltas: float) -> dict:
    with _lock:
        for k, d in deltas.items():
            _stats[k] = _clamp(_stats[k] + d)
        _publish()
    return status()


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
app = FastAPI(title="Kubegotchi")

# Routes outside the SLI: no chaos and no metrics.
# Probes hit every few seconds and always return 200; counting them
# would inflate availability and hide the error budget burn.
_UNOBSERVED = ("/metrics", "/healthz", "/readyz")


def _route_path(request: Request) -> str:
    """Route template (e.g. /status), never the raw path.

    Using request.url.path as a label would let any scanner create
    new series in Prometheus (/wp-admin, /x1, /x2...).
    """
    for route in app.router.routes:
        match, _ = route.matches(request.scope)
        if match != Match.NONE:
            return route.path
    return "unmatched"


@app.middleware("http")
async def observe(request: Request, call_next):
    path = _route_path(request)
    start = time.perf_counter()
    code = 500
    try:
        if path not in _UNOBSERVED:
            if CHAOS_LATENCY_MS > 0:
                await asyncio.sleep(CHAOS_LATENCY_MS / 1000)
            if random.random() < CHAOS_ERROR_RATE:
                raise HTTPException(status_code=500, detail="the pet sneezed 🤧")
        response = await call_next(request)
        code = response.status_code
        return response
    except HTTPException as e:
        code = e.status_code
        return JSONResponse({"detail": e.detail}, status_code=code)
    finally:
        if path not in _UNOBSERVED:
            LATENCY.labels(path=path).observe(time.perf_counter() - start)
            REQUESTS.labels(path=path, method=request.method, code=str(code)).inc()


@app.get("/status")
def status() -> dict:
    with _lock:
        return {
            "name": PET_NAME,
            "mood": _mood(),
            "age_minutes": round((time.time() - _born_at) / 60, 1),
            "stats": {k: round(v, 1) for k, v in _stats.items()},
            "pod": os.getenv("HOSTNAME", "local"),
        }


@app.post("/feed")
def feed() -> dict:
    ACTIONS.labels(action="feed").inc()
    return _change(hunger=+25, energy=+5)


@app.post("/play")
def play() -> dict:
    ACTIONS.labels(action="play").inc()
    return _change(happiness=+25, energy=-10, hunger=-5)


@app.post("/sleep")
def sleep() -> dict:
    ACTIONS.labels(action="sleep").inc()
    return _change(energy=+40, happiness=-5)


@app.get("/healthz")
def healthz() -> dict:
    """Liveness: is the process alive?"""
    return {"ok": True}


@app.get("/readyz")
def readyz() -> dict:
    """Readiness: is the pet fit to receive visitors?"""
    return {"ready": True}


@app.get("/metrics")
def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


_publish()
threading.Thread(target=_decay_loop, daemon=True).start()
