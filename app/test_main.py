"""Testes unitários do Kubegotchi.

Rodar:  make test   (em container, não precisa de Python local)
   ou:  cd app && pip install -r requirements-dev.txt && pytest
"""

import time

import pytest
from fastapi.testclient import TestClient
from prometheus_client import REGISTRY

import main

client = TestClient(main.app)


@pytest.fixture(autouse=True)
def pet(monkeypatch):
    """Bichinho zerado e sem caos a cada teste.

    O estado vive em globais do módulo, então sem isso um teste
    contaminaria o outro. DECAY_PER_MINUTE=0 impede que a thread de
    decay mexa nos status no meio de um teste.
    """
    monkeypatch.setattr(main, "DECAY_PER_MINUTE", 0)
    monkeypatch.setattr(main, "CHAOS_LATENCY_MS", 0)
    monkeypatch.setattr(main, "CHAOS_ERROR_RATE", 0)
    set_stats(hunger=50, happiness=50, energy=50)


def set_stats(**values: float) -> None:
    with main._lock:
        main._stats.update(values)
        main._publish()


def sample(name: str, **labels: str) -> float:
    """Valor atual de uma métrica (0 se a série ainda não existe)."""
    return REGISTRY.get_sample_value(name, labels) or 0.0


# ---------------------------------------------------------------------------
# Regras do bichinho
# ---------------------------------------------------------------------------
def test_status_formato():
    body = client.get("/status").json()
    assert body["name"] == main.PET_NAME
    assert body["mood"] == "ok"
    assert body["stats"] == {"hunger": 50, "happiness": 50, "energy": 50}
    assert {"age_minutes", "pod"} <= body.keys()


@pytest.mark.parametrize("action, expected", [
    ("feed", {"hunger": 75, "happiness": 50, "energy": 55}),
    ("play", {"hunger": 45, "happiness": 75, "energy": 40}),
    ("sleep", {"hunger": 50, "happiness": 45, "energy": 90}),
])
def test_cuidados_aplicam_deltas(action, expected):
    before = sample("kubegotchi_actions_total", action=action)
    r = client.post(f"/{action}")
    assert r.status_code == 200
    assert r.json()["stats"] == expected
    assert sample("kubegotchi_actions_total", action=action) == before + 1


def test_status_nao_passa_de_100_nem_de_0():
    set_stats(hunger=95, happiness=100, energy=3)
    assert client.post("/play").json()["stats"] == {
        "hunger": 90, "happiness": 100, "energy": 0,
    }


@pytest.mark.parametrize("worst, mood", [
    (100, "feliz"), (70, "feliz"),
    (69.9, "ok"), (40, "ok"),
    (39.9, "triste"), (15, "triste"),
    (14.9, "doente"), (0, "doente"),
])
def test_humor_vem_do_pior_status(worst, mood):
    set_stats(hunger=100, happiness=worst, energy=100)
    assert client.get("/status").json()["mood"] == mood


def test_decay_cai_e_para_no_zero(monkeypatch):
    monkeypatch.setattr(main, "DECAY_PER_MINUTE", 60)  # 10 por tick
    set_stats(hunger=15, happiness=50, energy=100)
    main._decay()
    assert main._stats == {"hunger": 5, "happiness": 40, "energy": 90}
    main._decay()
    assert main._stats["hunger"] == 0
    assert sample("kubegotchi_stat", stat="hunger") == 0


def test_gauge_acompanha_os_status():
    client.post("/feed")
    assert sample("kubegotchi_stat", stat="hunger") == 75
    assert sample("kubegotchi_stat", stat="energy") == 55


# ---------------------------------------------------------------------------
# Métricas (SLI)
# ---------------------------------------------------------------------------
def test_requisicao_conta_no_sli():
    before = sample("kubegotchi_http_requests_total",
                    path="/status", method="GET", code="200")
    client.get("/status")
    assert sample("kubegotchi_http_requests_total",
                  path="/status", method="GET", code="200") == before + 1


def test_path_desconhecido_nao_cria_serie_nova():
    before = sample("kubegotchi_http_requests_total",
                    path="unmatched", method="GET", code="404")
    assert client.get("/wp-admin").status_code == 404
    assert client.get("/x1").status_code == 404
    assert sample("kubegotchi_http_requests_total",
                  path="unmatched", method="GET", code="404") == before + 2
    assert sample("kubegotchi_http_requests_total",
                  path="/wp-admin", method="GET", code="404") == 0


def test_metodo_errado_usa_template_da_rota():
    assert client.get("/feed").status_code == 405
    assert sample("kubegotchi_http_requests_total",
                  path="/feed", method="GET", code="405") >= 1


@pytest.mark.parametrize("path", ["/healthz", "/readyz", "/metrics"])
def test_rotas_fora_do_sli_nao_sao_contadas(path):
    assert client.get(path).status_code == 200
    assert sample("kubegotchi_http_requests_total",
                  path=path, method="GET", code="200") == 0


def test_metrics_expoe_formato_prometheus():
    r = client.get("/metrics")
    assert r.headers["content-type"].startswith("text/plain")
    assert "kubegotchi_stat" in r.text


# ---------------------------------------------------------------------------
# Caos
# ---------------------------------------------------------------------------
def test_caos_erro_devolve_500_em_json(monkeypatch):
    monkeypatch.setattr(main, "CHAOS_ERROR_RATE", 1.0)
    before = sample("kubegotchi_http_requests_total",
                    path="/status", method="GET", code="500")
    r = client.get("/status")
    assert r.status_code == 500
    assert r.json() == {"detail": "o bichinho espirrou 🤧"}
    assert sample("kubegotchi_http_requests_total",
                  path="/status", method="GET", code="500") == before + 1


def test_caos_erro_nao_aplica_cuidado(monkeypatch):
    monkeypatch.setattr(main, "CHAOS_ERROR_RATE", 1.0)
    assert client.post("/feed").status_code == 500
    assert main._stats["hunger"] == 50


@pytest.mark.parametrize("path", ["/healthz", "/readyz", "/metrics"])
def test_caos_nao_derruba_probes(monkeypatch, path):
    monkeypatch.setattr(main, "CHAOS_ERROR_RATE", 1.0)
    monkeypatch.setattr(main, "CHAOS_LATENCY_MS", 500)
    start = time.perf_counter()
    assert client.get(path).status_code == 200
    assert time.perf_counter() - start < 0.4


def test_caos_latencia(monkeypatch):
    monkeypatch.setattr(main, "CHAOS_LATENCY_MS", 100)
    start = time.perf_counter()
    assert client.get("/status").status_code == 200
    assert time.perf_counter() - start >= 0.1
