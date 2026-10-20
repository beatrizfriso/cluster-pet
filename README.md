# 🐣 Kubegotchi

A virtual pet whose health **is** your Kubernetes cluster.

SLOs become hunger, mood and energy: if latency goes up, it gets sad; if the error budget runs out, it gets sick. An AI vet diagnoses what's wrong and proposes the fix.

A study project for **Kubernetes, GitOps, SRE and AI**, built to run 100% locally with no cloud cost.

## What you need to install

- Docker
- [k3d](https://k3d.io) (k3s inside Docker)
- kubectl
- Helm
- make, curl

## Getting started

```bash
# Phase 0: the pet on your machine
make run          # or: make run-docker (no local Python needed)
# in another terminal
curl localhost:8000/status
curl -X POST localhost:8000/feed
curl localhost:8000/metrics | grep kubegotchi

# Phase 1: the pet on Kubernetes
make up           # creates the cluster, builds the image, installs the chart
curl localhost:8080/status            # through the Ingress
curl -X POST localhost:8080/feed
```

> On Windows PowerShell, `curl` is an alias for `Invoke-WebRequest`. Use `curl.exe`.

## Layout

```
app/                  the pet (FastAPI + Prometheus metrics)
charts/kubegotchi/    Helm chart (Deployment, Service, Ingress, opt-in HPA and ServiceMonitor)
k3d/cluster.yaml      local cluster: 1 server + 2 agents
.github/workflows/    CI: gitleaks, helm lint, unit tests, smoke test, docker build
docs/                 lab notes for each phase
experiments/          files used in the hands-on exercises
ROADMAP.md            the project phases, with checklists
```

## How the pet works

| Stat        | 100 = great | Decays over time | Recovers with |
|-------------|-------------|------------------|---------------|
| `hunger`    | full        | yes              | `POST /feed`  |
| `happiness` | happy       | yes              | `POST /play`  |
| `energy`    | rested      | yes              | `POST /sleep` |

The mood (`happy`, `ok`, `sad`, `sick`) comes from the worst stat. From Phase 3 on, the stats are driven by the **cluster's SLOs**, not just by care.

### Mistreating the pet on purpose

```bash
helm upgrade kubegotchi charts/kubegotchi -n pet --reuse-values \
  --set chaos.latencyMs=400 --set chaos.errorRate=0.1
```

## Exposed metrics

| Metric | Type | Purpose |
|---|---|---|
| `kubegotchi_http_requests_total{path,method,code}` | counter | availability SLI |
| `kubegotchi_http_request_duration_seconds` | histogram | latency SLI |
| `kubegotchi_stat{stat}` | gauge | the pet's stats |
| `kubegotchi_actions_total{action}` | counter | how much care it received |
