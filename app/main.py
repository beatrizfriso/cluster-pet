"""
Kubegotchi - a virtual pet that lives in your Kubernetes cluster.

The pet has stats (hunger, happiness, energy) that drop over time and go back
up with care: /feed, /play and /sleep.

Phase 4: the pod holds no state. Every care action increments the
kubegotchi_actions_total counter, and the stats are computed from Prometheus
over a sliding window: with no care at all for CARE_WINDOW_MINUTES, a stat
reaches 0. Any replica answers with the same pet, so it can scale.

Environment variables:
  PET_NAME             the pet's name (default: Bolinha)
  PROMETHEUS_URL       where the care history lives (default: http://localhost:9090)
  CARE_WINDOW_MINUTES  how far back care counts (default: 60)
  CHAOS_LATENCY_MS     extra latency on every request, in ms (default: 0)
  CHAOS_ERROR_RATE     fraction of requests that fail with 500, 0.0 to 1.0 (default: 0)
  DECAY_PER_MINUTE     how much each stat drops per minute (default: 2)
  APP_VERSION          image tag being run, shown in /status (default: dev)
  POD_NAMESPACE        namespace whose pods /vitals reads (default: pet)
  TRAEFIK_SERVICE      Traefik service name of the pet, for the SLIs
                       (default: pet-kubegotchi-http@kubernetes)
  SLO_AVAILABILITY_TARGET  availability SLO target (default: 0.99)

/status is the pet as its owners keep it (care). /vitals is the pet as the
cluster keeps it: health is the availability error budget, mood the latency,
energy and hunger the CPU and memory headroom, injuries the restarts.
"""

import asyncio
import math
import os
import random
import time

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest
from starlette.routing import Match

PET_NAME = os.getenv("PET_NAME", "Bolinha")
PROMETHEUS_URL = os.getenv("PROMETHEUS_URL", "http://localhost:9090")
CARE_WINDOW_MINUTES = int(os.getenv("CARE_WINDOW_MINUTES", "60"))
CHAOS_LATENCY_MS = float(os.getenv("CHAOS_LATENCY_MS", "0"))
CHAOS_ERROR_RATE = float(os.getenv("CHAOS_ERROR_RATE", "0"))
DECAY_PER_MINUTE = float(os.getenv("DECAY_PER_MINUTE", "2"))
APP_VERSION = os.getenv("APP_VERSION", "dev")
# Where /vitals reads the cluster's signals about this pet
POD_NAMESPACE = os.getenv("POD_NAMESPACE", "pet")
TRAEFIK_SERVICE = os.getenv("TRAEFIK_SERVICE", "pet-kubegotchi-http@kubernetes")
SLO_AVAILABILITY_TARGET = float(os.getenv("SLO_AVAILABILITY_TARGET", "0.99"))

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
STAT = Gauge("kubegotchi_stat", "Pet stat, from 0 to 100 (last value this pod computed)", ["stat"])
ACTIONS = Counter("kubegotchi_actions_total", "Care actions received", ["action"])

# ---------------------------------------------------------------------------
# The pet, computed from its care history
# ---------------------------------------------------------------------------
STATS = ("hunger", "happiness", "energy")

# What each care action does to the stats
CARE = {
    "feed": {"hunger": +25, "energy": +5},
    "play": {"happiness": +25, "energy": -10, "hunger": -5},
    "sleep": {"energy": +40, "happiness": -5},
}

_started_at = time.time()


class PrometheusUnavailable(Exception):
    """The care history can't be read, so the pet's state is unknown."""


def _clamp(v: float) -> float:
    return max(0.0, min(100.0, v))


def _prom_query(query: str) -> list[dict]:
    """Instant query against Prometheus; any failure means the pet's state is unknown."""
    try:
        r = httpx.get(f"{PROMETHEUS_URL}/api/v1/query", params={"query": query}, timeout=2.0)
        r.raise_for_status()
        return r.json()["data"]["result"]
    except (httpx.HTTPError, KeyError, ValueError) as e:
        raise PrometheusUnavailable(str(e)) from e


def _prom_scalar(query: str) -> float | None:
    """First value of a query, or None when there's no data (e.g. no traffic yet)."""
    result = _prom_query(query)
    if not result:
        return None
    value = float(result[0]["value"][1])
    return None if math.isnan(value) else value


def _care_counts() -> dict[str, float]:
    """How many times each care action happened in the window, across all replicas."""
    result = _prom_query(
        f"sum by (action) (increase(kubegotchi_actions_total[{CARE_WINDOW_MINUTES}m]))"
    )
    return {s["metric"].get("action", ""): float(s["value"][1]) for s in result}


def _compute_stats(counts: dict[str, float]) -> dict[str, float]:
    """Each stat starts at 100, decays over the whole window and gets care back.

    Stateless and deterministic: the same history gives the same pet on any pod.
    """
    stats = {s: 100.0 - DECAY_PER_MINUTE * CARE_WINDOW_MINUTES for s in STATS}
    for action, n in counts.items():
        for stat, delta in CARE.get(action, {}).items():
            stats[stat] += delta * n
    return {s: _clamp(v) for s, v in stats.items()}


def _publish(stats: dict[str, float]) -> None:
    for name, value in stats.items():
        STAT.labels(stat=name).set(value)


def _mood(stats: dict[str, float]) -> str:
    worst = min(stats.values())
    if worst >= 70:
        return "happy"
    if worst >= 40:
        return "ok"
    if worst >= 15:
        return "sad"
    return "sick"


def _status(stats: dict[str, float]) -> dict:
    _publish(stats)
    return {
        "name": PET_NAME,
        "mood": _mood(stats),
        "stats": {k: round(v, 1) for k, v in stats.items()},
        "pod": os.getenv("HOSTNAME", "local"),
        "pod_uptime_minutes": round((time.time() - _started_at) / 60, 1),
        "version": APP_VERSION,
    }


# ---------------------------------------------------------------------------
# Vitals: the pet as the cluster sees it
# ---------------------------------------------------------------------------
def _p95_to_mood(p95: float) -> float:
    """100 up to 100ms, 60 at the 300ms SLO threshold, 0 at 1.2s and beyond."""
    if p95 <= 0.1:
        return 100.0
    if p95 <= 0.3:
        return 100 - (p95 - 0.1) / 0.2 * 40
    return _clamp(60 - (p95 - 0.3) / 0.9 * 60)


def _condition(values: list[float]) -> str:
    worst = min(values)
    if worst >= 70:
        return "healthy"
    if worst >= 40:
        return "unwell"
    if worst >= 15:
        return "ill"
    return "critical"


def _vitals() -> dict:
    svc = f'service="{TRAEFIK_SERVICE}"'
    pod = f'namespace="{POD_NAMESPACE}",container="pet"'
    budget_fraction = 1 - SLO_AVAILABILITY_TARGET
    errors = f'traefik_service_requests_total{{{svc},code=~"5.."}}'
    total = f"traefik_service_requests_total{{{svc}}}"

    budget = _prom_scalar(
        f"1 - ((sum(increase({errors}[7d])) or vector(0)) "
        f"/ sum(increase({total}[7d]))) / {budget_fraction}"
    )
    burn = _prom_scalar(
        f"((sum(rate({errors}[1h])) or vector(0)) / sum(rate({total}[1h]))) / {budget_fraction}"
    )
    p95 = _prom_scalar(
        "histogram_quantile(0.95, sum by (le) "
        f"(rate(traefik_service_request_duration_seconds_bucket{{{svc}}}[5m])))"
    )
    cpu = _prom_scalar(
        f"sum(rate(container_cpu_usage_seconds_total{{{pod}}}[5m])) "
        f'/ sum(kube_pod_container_resource_limits{{{pod},resource="cpu"}})'
    )
    memory = _prom_scalar(
        f"sum(container_memory_working_set_bytes{{{pod}}}) "
        f'/ sum(kube_pod_container_resource_limits{{{pod},resource="memory"}})'
    )
    restarts = _prom_scalar(
        f"sum(increase(kube_pod_container_status_restarts_total{{{pod}}}[24h])) or vector(0)"
    )

    def vital(value: float | None, reading: str) -> dict:
        return {"value": None if value is None else round(value, 1), "reading": reading}

    vitals = {
        "health": vital(
            None if budget is None else _clamp(budget * 100),
            "no traffic yet" if budget is None
            else f"{budget:.0%} of the weekly error budget left",
        ),
        "mood": vital(
            None if p95 is None else _p95_to_mood(p95),
            "no traffic yet" if p95 is None else f"p95 latency {p95 * 1000:.0f}ms",
        ),
        "energy": vital(
            None if cpu is None else _clamp(100 * (1 - cpu)),
            "no data" if cpu is None else f"{cpu:.0%} of its CPU limit in use",
        ),
        "hunger": vital(
            None if memory is None else _clamp(100 * (1 - memory)),
            "no data" if memory is None else f"{memory:.0%} of its memory limit in use",
        ),
        "injuries": vital(
            None if restarts is None else _clamp(100 - 25 * round(restarts)),
            f"{round(restarts or 0)} restart(s) in the last 24h",
        ),
    }
    known = [v["value"] for v in vitals.values() if v["value"] is not None]
    return {
        "name": PET_NAME,
        "condition": _condition(known) if known else "unknown",
        # Burning the budget faster than it lasts the week
        "fever": burn is not None and burn > 1,
        "burn_rate": None if burn is None else round(burn, 2),
        "vitals": vitals,
        "version": APP_VERSION,
    }


def _care(action: str) -> dict:
    ACTIONS.labels(action=action).inc()
    # Prometheus only sees this action on its next scrape, so it is added here
    # too: the owner gets an answer that already reflects what they just did
    counts = _care_counts()
    counts[action] = counts.get(action, 0) + 1
    return _status(_compute_stats(counts))


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
app = FastAPI(title="Kubegotchi")

# Routes outside the SLI: no chaos and no metrics.
# Probes hit every few seconds and always return 200; counting them
# would inflate availability and hide the error budget burn.
_UNOBSERVED = ("/metrics", "/healthz", "/readyz")


@app.exception_handler(PrometheusUnavailable)
async def prometheus_unavailable(request: Request, exc: PrometheusUnavailable):
    # 503 and not made-up numbers: the owner sees the pet is unreachable,
    # and it counts against the availability SLO, as it should
    return JSONResponse(
        {"detail": "the pet can't remember: care history (Prometheus) is unreachable"},
        status_code=503,
    )


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
    return _status(_compute_stats(_care_counts()))


@app.get("/vitals")
def vitals() -> dict:
    return _vitals()


@app.post("/feed")
def feed() -> dict:
    return _care("feed")


@app.post("/play")
def play() -> dict:
    return _care("play")


@app.post("/sleep")
def sleep() -> dict:
    return _care("sleep")


@app.get("/healthz")
def healthz() -> dict:
    """Liveness: is the process alive?"""
    return {"ok": True}


@app.get("/readyz")
def readyz() -> dict:
    """Readiness: can this pod take traffic?

    Deliberately does not check Prometheus: if it did, a Prometheus outage
    would take every replica out of the Service at once, turning a degraded
    pet (503 on /status) into no pet at all.
    """
    return {"ready": True}


@app.get("/metrics")
def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
