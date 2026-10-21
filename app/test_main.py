"""Kubegotchi unit tests.

Run:  make test   (in a container, no local Python needed)
  or: cd app && pip install -r requirements-dev.txt && pytest
"""

import time

import httpx
import pytest
from fastapi.testclient import TestClient
from prometheus_client import REGISTRY

import main

client = TestClient(main.app)


class FakePrometheus:
    """Stands in for the Prometheus query API.

    Care queries get one sample per action from `counts`; /vitals queries are
    recognized by a distinctive fragment and get the matching `scalars` value
    (missing key = no data, like a series that doesn't exist yet).
    """

    VITALS = {
        "budget": "[7d]",
        "burn": "[1h]",
        "p95": "histogram_quantile",
        "cpu": "container_cpu_usage_seconds_total",
        "memory": "container_memory_working_set_bytes",
        "restarts": "kube_pod_container_status_restarts_total",
    }

    def __init__(self):
        self.counts: dict[str, float] = {}
        self.scalars: dict[str, float] = {}
        self.down = False
        self.queries: list[str] = []

    def _result(self, query: str) -> list[dict]:
        now = time.time()
        if "kubegotchi_actions_total" in query:
            return [{"metric": {"action": a}, "value": [now, str(n)]}
                    for a, n in self.counts.items()]
        for key, fragment in self.VITALS.items():
            if fragment in query:
                if key not in self.scalars:
                    return []
                return [{"metric": {}, "value": [now, str(self.scalars[key])]}]
        raise AssertionError(f"unexpected query: {query}")

    def get(self, url, params=None, timeout=None):
        self.queries.append(params["query"])
        if self.down:
            raise httpx.ConnectError("connection refused")
        result = self._result(params["query"])
        return httpx.Response(
            200,
            json={"status": "success", "data": {"resultType": "vector", "result": result}},
            request=httpx.Request("GET", url),
        )


@pytest.fixture(autouse=True)
def prom(monkeypatch):
    """No chaos, a 60-minute window losing 2 points a minute, and a fake Prometheus.

    With these defaults a pet with no care at all sits at 100 - 2*60 = -20 -> 0.
    """
    monkeypatch.setattr(main, "CHAOS_LATENCY_MS", 0)
    monkeypatch.setattr(main, "CHAOS_ERROR_RATE", 0)
    monkeypatch.setattr(main, "DECAY_PER_MINUTE", 2)
    monkeypatch.setattr(main, "CARE_WINDOW_MINUTES", 60)
    fake = FakePrometheus()
    monkeypatch.setattr(main.httpx, "get", fake.get)
    return fake


def sample(name: str, **labels: str) -> float:
    """Current value of a metric (0 if the series doesn't exist yet)."""
    return REGISTRY.get_sample_value(name, labels) or 0.0


# ---------------------------------------------------------------------------
# Pet rules (state computed from the care history)
# ---------------------------------------------------------------------------
def test_status_shape():
    body = client.get("/status").json()
    assert body["name"] == main.PET_NAME
    assert body["version"] == main.APP_VERSION
    assert {"mood", "stats", "pod", "pod_uptime_minutes"} <= body.keys()


def test_no_care_for_a_whole_window_is_a_sick_pet():
    body = client.get("/status").json()
    assert body["stats"] == {"hunger": 0, "happiness": 0, "energy": 0}
    assert body["mood"] == "sick"


def test_stats_come_from_care_counts(prom):
    # base -20; feed +25 hunger +5 energy; play +25 happiness -10 energy -5 hunger
    prom.counts = {"feed": 3, "play": 2, "sleep": 1}
    assert client.get("/status").json()["stats"] == {
        "hunger": 100 - 120 + 3 * 25 - 2 * 5,     # 45
        "happiness": 100 - 120 + 2 * 25 - 1 * 5,  # 25
        "energy": 100 - 120 + 3 * 5 - 2 * 10 + 1 * 40,  # 15
    }


def test_query_sums_all_replicas_over_the_window(prom):
    client.get("/status")
    assert prom.queries[-1] == "sum by (action) (increase(kubegotchi_actions_total[60m]))"


def test_stats_stay_between_0_and_100(prom):
    prom.counts = {"feed": 100}
    stats = client.get("/status").json()["stats"]
    assert stats["hunger"] == 100
    assert stats["happiness"] == 0


@pytest.mark.parametrize("action", ["feed", "play", "sleep"])
def test_care_counts_and_already_shows_in_the_answer(prom, action):
    # Prometheus hasn't scraped this action yet: the answer must include it anyway.
    # Counts chosen so no stat hits 0 or 100 (clamping happens after summing)
    prom.counts = {"feed": 2, "play": 2, "sleep": 1}
    before_status = client.get("/status").json()["stats"]
    before_counter = sample("kubegotchi_actions_total", action=action)

    r = client.post(f"/{action}")

    assert r.status_code == 200
    assert sample("kubegotchi_actions_total", action=action) == before_counter + 1
    after = r.json()["stats"]
    for stat, delta in main.CARE[action].items():
        assert after[stat] == max(0, min(100, before_status[stat] + delta))


@pytest.mark.parametrize("worst, mood", [
    (100, "happy"), (70, "happy"),
    (69.9, "ok"), (40, "ok"),
    (39.9, "sad"), (15, "sad"),
    (14.9, "sick"), (0, "sick"),
])
def test_mood_comes_from_worst_stat(worst, mood):
    assert main._mood({"hunger": 100, "happiness": worst, "energy": 100}) == mood


def test_gauge_tracks_computed_stats(prom):
    prom.counts = {"feed": 2}
    client.get("/status")
    assert sample("kubegotchi_stat", stat="hunger") == 30


# ---------------------------------------------------------------------------
# Vitals (the pet as the cluster sees it)
# ---------------------------------------------------------------------------
HEALTHY = {"budget": 1.0, "burn": 0.0, "p95": 0.05, "cpu": 0.1, "memory": 0.4, "restarts": 0}


def test_vitals_map_cluster_signals(prom):
    prom.scalars = dict(HEALTHY)
    body = client.get("/vitals").json()
    assert {k: v["value"] for k, v in body["vitals"].items()} == {
        "health": 100, "mood": 100, "energy": 90, "hunger": 60, "injuries": 100,
    }
    assert body["condition"] == "unwell"  # worst vital is hunger at 60
    assert body["fever"] is False
    assert body["vitals"]["health"]["reading"] == "100% of the weekly error budget left"


def test_spent_budget_is_a_critical_pet(prom):
    prom.scalars = {**HEALTHY, "budget": -3.76, "burn": 5.65}
    body = client.get("/vitals").json()
    assert body["vitals"]["health"]["value"] == 0
    assert body["condition"] == "critical"
    assert body["fever"] is True
    assert body["burn_rate"] == 5.65


@pytest.mark.parametrize("p95, mood", [(0.05, 100), (0.1, 100), (0.2, 80), (0.3, 60),
                                       (0.75, 30), (1.2, 0), (5, 0)])
def test_latency_sets_the_mood(p95, mood):
    assert main._p95_to_mood(p95) == pytest.approx(mood)


@pytest.mark.parametrize("restarts, injuries", [(0, 100), (1.03, 75), (2, 50), (6, 0)])
def test_restarts_are_injuries(prom, restarts, injuries):
    prom.scalars = {**HEALTHY, "restarts": restarts}
    assert client.get("/vitals").json()["vitals"]["injuries"]["value"] == injuries


def test_no_traffic_yet_is_not_a_failure(prom):
    # Fresh cluster: no Traefik series, so health, mood and fever have no data
    prom.scalars = {"cpu": 0.1, "memory": 0.2, "restarts": 0}
    body = client.get("/vitals").json()
    assert body["vitals"]["health"] == {"value": None, "reading": "no traffic yet"}
    assert body["vitals"]["mood"]["value"] is None
    assert body["fever"] is False
    assert body["condition"] == "healthy"  # from the vitals that do have data


def test_vitals_read_the_configured_pet(prom, monkeypatch):
    monkeypatch.setattr(main, "TRAEFIK_SERVICE", "other-pet-http@kubernetes")
    monkeypatch.setattr(main, "POD_NAMESPACE", "other")
    prom.scalars = dict(HEALTHY)
    client.get("/vitals")
    joined = " ".join(prom.queries)
    assert 'service="other-pet-http@kubernetes"' in joined
    assert 'namespace="other",container="pet"' in joined
    assert 'service="pet-kubegotchi-http@kubernetes"' not in joined


# ---------------------------------------------------------------------------
# Prometheus down
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("method, path", [("GET", "/status"), ("POST", "/feed"),
                                          ("GET", "/vitals")])
def test_prometheus_down_is_a_503_not_made_up_numbers(prom, method, path):
    prom.down = True
    r = client.request(method, path)
    assert r.status_code == 503
    assert "Prometheus" in r.json()["detail"]


def test_prometheus_down_counts_against_the_sli(prom):
    prom.down = True
    before = sample("kubegotchi_http_requests_total", path="/status", method="GET", code="503")
    client.get("/status")
    assert sample("kubegotchi_http_requests_total",
                  path="/status", method="GET", code="503") == before + 1


def test_prometheus_down_keeps_the_pod_ready(prom):
    # If readiness depended on Prometheus, its outage would remove every replica
    prom.down = True
    assert client.get("/readyz").status_code == 200
    assert client.get("/healthz").status_code == 200


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
    client.get("/status")
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


def test_chaos_error_does_not_count_the_care(monkeypatch):
    monkeypatch.setattr(main, "CHAOS_ERROR_RATE", 1.0)
    before = sample("kubegotchi_actions_total", action="feed")
    assert client.post("/feed").status_code == 500
    assert sample("kubegotchi_actions_total", action="feed") == before


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
