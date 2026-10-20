"""Kubegotchi unit tests.

Run:  make test   (in a container, no local Python needed)
  or: cd app && pip install -r requirements-dev.txt && pytest
"""

import time

import pytest
from fastapi.testclient import TestClient
from prometheus_client import REGISTRY

import main

client = TestClient(main.app)


@pytest.fixture(autouse=True)
def pet(monkeypatch):
    """A fresh pet with no chaos for every test.

    State lives in module globals, so without this one test would
    leak into the next. DECAY_PER_MINUTE=0 keeps the decay thread
    from changing stats in the middle of a test.
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
    """Current value of a metric (0 if the series doesn't exist yet)."""
    return REGISTRY.get_sample_value(name, labels) or 0.0


# ---------------------------------------------------------------------------
# Pet rules
# ---------------------------------------------------------------------------
def test_status_shape():
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
def test_care_actions_apply_deltas(action, expected):
    before = sample("kubegotchi_actions_total", action=action)
    r = client.post(f"/{action}")
    assert r.status_code == 200
    assert r.json()["stats"] == expected
    assert sample("kubegotchi_actions_total", action=action) == before + 1


def test_stats_stay_between_0_and_100():
    set_stats(hunger=95, happiness=100, energy=3)
    assert client.post("/play").json()["stats"] == {
        "hunger": 90, "happiness": 100, "energy": 0,
    }


@pytest.mark.parametrize("worst, mood", [
    (100, "happy"), (70, "happy"),
    (69.9, "ok"), (40, "ok"),
    (39.9, "sad"), (15, "sad"),
    (14.9, "sick"), (0, "sick"),
])
def test_mood_comes_from_worst_stat(worst, mood):
    set_stats(hunger=100, happiness=worst, energy=100)
    assert client.get("/status").json()["mood"] == mood


def test_decay_drops_and_stops_at_zero(monkeypatch):
    monkeypatch.setattr(main, "DECAY_PER_MINUTE", 60)  # 10 per tick
    set_stats(hunger=15, happiness=50, energy=100)
    main._decay()
    assert main._stats == {"hunger": 5, "happiness": 40, "energy": 90}
    main._decay()
    assert main._stats["hunger"] == 0
    assert sample("kubegotchi_stat", stat="hunger") == 0


def test_gauge_tracks_stats():
    client.post("/feed")
    assert sample("kubegotchi_stat", stat="hunger") == 75
    assert sample("kubegotchi_stat", stat="energy") == 55


# ---------------------------------------------------------------------------
# Metrics (SLI)
# ---------------------------------------------------------------------------
def test_request_counts_toward_sli():
    before = sample("kubegotchi_http_requests_total",
                    path="/status", method="GET", code="200")
    client.get("/status")
    assert sample("kubegotchi_http_requests_total",
                  path="/status", method="GET", code="200") == before + 1


def test_unknown_path_does_not_create_new_series():
    before = sample("kubegotchi_http_requests_total",
                    path="unmatched", method="GET", code="404")
    assert client.get("/wp-admin").status_code == 404
    assert client.get("/x1").status_code == 404
    assert sample("kubegotchi_http_requests_total",
                  path="unmatched", method="GET", code="404") == before + 2
    assert sample("kubegotchi_http_requests_total",
                  path="/wp-admin", method="GET", code="404") == 0


def test_wrong_method_uses_route_template():
    assert client.get("/feed").status_code == 405
    assert sample("kubegotchi_http_requests_total",
                  path="/feed", method="GET", code="405") >= 1


@pytest.mark.parametrize("path", ["/healthz", "/readyz", "/metrics"])
def test_routes_outside_sli_are_not_counted(path):
    assert client.get(path).status_code == 200
    assert sample("kubegotchi_http_requests_total",
                  path=path, method="GET", code="200") == 0


def test_metrics_exposes_prometheus_format():
    r = client.get("/metrics")
    assert r.headers["content-type"].startswith("text/plain")
    assert "kubegotchi_stat" in r.text


# ---------------------------------------------------------------------------
# Chaos
# ---------------------------------------------------------------------------
def test_chaos_error_returns_json_500(monkeypatch):
    monkeypatch.setattr(main, "CHAOS_ERROR_RATE", 1.0)
    before = sample("kubegotchi_http_requests_total",
                    path="/status", method="GET", code="500")
    r = client.get("/status")
    assert r.status_code == 500
    assert r.json() == {"detail": "the pet sneezed 🤧"}
    assert sample("kubegotchi_http_requests_total",
                  path="/status", method="GET", code="500") == before + 1


def test_chaos_error_does_not_apply_care(monkeypatch):
    monkeypatch.setattr(main, "CHAOS_ERROR_RATE", 1.0)
    assert client.post("/feed").status_code == 500
    assert main._stats["hunger"] == 50


@pytest.mark.parametrize("path", ["/healthz", "/readyz", "/metrics"])
def test_chaos_does_not_break_probes(monkeypatch, path):
    monkeypatch.setattr(main, "CHAOS_ERROR_RATE", 1.0)
    monkeypatch.setattr(main, "CHAOS_LATENCY_MS", 500)
    start = time.perf_counter()
    assert client.get(path).status_code == 200
    assert time.perf_counter() - start < 0.4


def test_chaos_latency(monkeypatch):
    monkeypatch.setattr(main, "CHAOS_LATENCY_MS", 100)
    start = time.perf_counter()
    assert client.get("/status").status_code == 200
    assert time.perf_counter() - start >= 0.1
