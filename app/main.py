"""
Kubegotchi - um bichinho virtual que vive no seu cluster Kubernetes.

Fase 0: o bichinho tem status (fome, humor, energia) que caem com o tempo.
Você cuida dele pelos endpoints /feed, /play e /sleep.
Tudo é exposto em /metrics para o Prometheus.

Variáveis de ambiente para "maltratar" o bichinho (úteis a partir da Fase 3):
  PET_NAME            nome do bichinho (padrão: Bolinha)
  CHAOS_LATENCY_MS    latência extra em toda requisição, em ms (padrão: 0)
  CHAOS_ERROR_RATE    fração de requisições que falham com 500, 0.0 a 1.0 (padrão: 0)
  DECAY_PER_MINUTE    quanto cada status cai por minuto (padrão: 2)
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
# Métricas (os "sinais vitais" que o Prometheus vai coletar)
# ---------------------------------------------------------------------------
REQUESTS = Counter(
    "kubegotchi_http_requests_total",
    "Total de requisições HTTP",
    ["path", "method", "code"],
)
LATENCY = Histogram(
    "kubegotchi_http_request_duration_seconds",
    "Duração das requisições HTTP",
    ["path"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5),
)
STAT = Gauge("kubegotchi_stat", "Status do bichinho, de 0 a 100", ["stat"])
ACTIONS = Counter("kubegotchi_actions_total", "Cuidados recebidos", ["action"])

# ---------------------------------------------------------------------------
# Estado do bichinho
# ---------------------------------------------------------------------------
_lock = threading.Lock()
_stats = {"hunger": 100.0, "happiness": 100.0, "energy": 100.0}  # 100 = ótimo
_born_at = time.time()


def _clamp(v: float) -> float:
    return max(0.0, min(100.0, v))


def _publish() -> None:
    for name, value in _stats.items():
        STAT.labels(stat=name).set(value)


def _decay() -> None:
    """Um tick de 10s: cada status cai 1/6 do decay por minuto."""
    with _lock:
        for k in _stats:
            _stats[k] = _clamp(_stats[k] - DECAY_PER_MINUTE / 6)
        _publish()


def _decay_loop() -> None:
    """Os status caem com o tempo, como num tamagotchi de verdade."""
    while True:
        time.sleep(10)
        _decay()


def _mood() -> str:
    worst = min(_stats.values())
    if worst >= 70:
        return "feliz"
    if worst >= 40:
        return "ok"
    if worst >= 15:
        return "triste"
    return "doente"


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

# Rotas fora do SLI: não sofrem chaos e não entram nas métricas.
# As probes batem a cada poucos segundos e sempre dão 200; contá-las
# inflaria a disponibilidade e esconderia a queima do error budget.
_UNOBSERVED = ("/metrics", "/healthz", "/readyz")


def _route_path(request: Request) -> str:
    """Template da rota (ex.: /status), nunca o path cru.

    Usar request.url.path como label deixaria qualquer scanner criar
    séries novas no Prometheus (/wp-admin, /x1, /x2...).
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
                raise HTTPException(status_code=500, detail="o bichinho espirrou 🤧")
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
    """Liveness: o processo está vivo?"""
    return {"ok": True}


@app.get("/readyz")
def readyz() -> dict:
    """Readiness: o bichinho está em condições de receber visitas?"""
    return {"ready": True}


@app.get("/metrics")
def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


_publish()
threading.Thread(target=_decay_loop, daemon=True).start()
