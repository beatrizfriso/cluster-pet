# Phase 3: vital signs

**Goal:** Grafana shows how much error budget is left and the burn rate.

## Where to look

| URL | What | Login |
|---|---|---|
| http://grafana.localhost:8080/d/kubegotchi | **Kubegotchi / Vital signs** dashboard | `admin` / `make grafana-password` |
| http://pyrra.localhost:8080 | SLOs and error budget | none |
| http://prometheus.localhost:8080 | PromQL and alerts | none |
| http://argocd.localhost:8080 | GitOps state | `admin` |

---

## Design decisions

### SLIs are measured at Traefik, not in the app

| Failure | App metrics see it? | Traefik sees it? |
|---|---|---|
| Chaos errors (500) / latency | ✅ | ✅ |
| Pod dead or OOMKilled (single replica) | ❌ no pod to report | ✅ 502/503 |
| `Gateway Timeout` during a rollout | ❌ request never reaches the app | ✅ 504 |
| Broken deploy (`CrashLoopBackOff`) | ❌ | ✅ |
| Traefik or the node down | ❌ | ❌ only a synthetic probe from outside |

Measuring only in the app hides exactly the worst failures: when the pet is
dead, an app-side SLI reads 100%. This paid off immediately: the first query on
Traefik's counters showed a real `504` from an earlier rollout that the app never
recorded.

The app metrics (`kubegotchi_http_requests_total` by route) stay for diagnosis:
when the SLO burns, they tell which endpoint.

### The SLOs

| SLO | SLI | Target | Window | Budget |
|---|---|---|---|---|
| `pet-availability` | requests without 5xx (4xx don't count) | 99% | 7d | 1% of requests, ~1.7h of full outage |
| `pet-latency` | requests under 300ms | 95% | 7d | 5% of requests |

- **Latency as "share under a threshold", not p95:** it adds up across windows
  and yields an error budget; a percentile does neither. p95 lives on the
  dashboard. 300ms is an existing bucket of Traefik's histogram (0.1, 0.3, 1.2, 5).
- **7 days** instead of the industry's 30: realistic, but a burn is still visible
  within a study week.

### Pyrra over Sloth or hand-written rules

Pyrra turns a `ServiceLevelObjective` into recording rules and **multi-window
burn-rate alerts**, scaled to the SLO window (for 7d: 15m/1m, 1h30m/8m, 6h/...),
and serves its own error budget UI. Sloth targets 28/30d windows; hand-written
rules are ~20 rules per SLO and easy to get subtly wrong.

### Traffic generator

With only manual `curl`s, availability jumps between 0% and 100% per request.
`loadgen` (simulated owners, ~1.5 req/s: 70% `/status`, 10% each of `/feed`,
`/play`, `/sleep`) gives the SLIs volume. It goes **through Traefik**: traffic
that skipped it would be invisible to the SLOs. Replaced by k6 in Phase 4.

---

## What was built

All of it through Argo CD, from `gitops/`:

```
gitops/
  root.yaml                 app of apps (the only manual kubectl apply: make argocd-app)
  apps/
    kubegotchi.yaml         the pet
    monitoring.yaml         kube-prometheus-stack 92.11.0
    monitoring-config.yaml  everything under gitops/monitoring/ (recursive)
    loadgen.yaml            simulated owners
    pyrra.yaml              Pyrra v0.10.2
  monitoring/
    traefik-podmonitor.yaml
    slos/pet-availability.yaml, slos/pet-latency.yaml
    dashboards/pet.yaml     Grafana dashboard as a ConfigMap
  loadgen/                  owners.sh + Deployment (kustomize)
  pyrra/                    upstream manifests + kustomize patches
```

### kube-prometheus-stack choices

- **Retention 8d on a 5Gi PVC** (`local-path`): longer than the SLO window, and a
  Prometheus restart doesn't wipe the budget history.
- **k3s control plane scrapes off** (controller-manager, scheduler, etcd,
  kube-proxy): k3s runs them inside its own process, so they'd only be "target
  down" alerts from minute one. After install, the only firing alert was
  `Watchdog`, which is meant to always fire.
- **Selectors open** (`*SelectorNilUsesHelmValues: false`): Prometheus picks up
  every ServiceMonitor, PodMonitor and PrometheusRule, including Pyrra's.
- **`ServerSideApply=true`**: the Prometheus Operator CRDs are too big for
  client-side apply.

### Traefik needs a PodMonitor

k3s's Traefik exposes metrics on the pod (port `metrics`, 9100) but not on its
Service, so a ServiceMonitor can't reach them.

### Pyrra

The upstream example manifests are copied as-is into `gitops/pyrra/upstream/`
and changed only through kustomize patches:

- the example still references image `v0.9.0` → `v0.10.2`
- its Prometheus URL is for kube-prometheus (`prometheus-k8s`) → this cluster's
  `monitoring-kube-prometheus-prometheus`
- `--disable-webhooks` (no TLS certs in this setup) and `--generic-rules`

The SLOs were validated before the cluster with `pyrra generate`:

```powershell
docker run --rm -v "${PWD}/gitops/monitoring/slos:/slos:ro" -v "<out-dir>:/out" ghcr.io/pyrra-dev/pyrra:v0.10.2 generate --config-files="/slos/*.yaml" --prometheus-folder=/out/
```

### The dashboard

`Kubegotchi / Vital signs` is a ConfigMap labeled `grafana_dashboard: "1"`, so
edits in the UI are lost on restart: change the file. Its SLO math reads
Traefik's counters directly (same SLIs as Pyrra), so it works even if Pyrra is
down. Every query was run against the live Prometheus before writing the panel:
a broken query only shows up as an empty panel.

| Row | Panels |
|---|---|
| SLOs (7d) | availability, availability budget left, latency SLI, latency budget left |
| Trend | 1h burn rate (line at 1×), requests by status, p50/p95/p99 |
| Diagnosis | 5xx by route (app metrics) |
| The pet | stats, pod restarts |

Baseline right after install: availability 100%, latency SLI 98.6%, p95 95ms,
~1.47 req/s, burn rate 0.

---

## Problems along the way

### Argo CD doesn't read subfolders
The SLOs in `gitops/monitoring/slos/` were never applied: directory sources are
not recursive by default. Fix: `directory.recurse: true` on `monitoring-config`.

### Sync order on a fresh cluster
The pet chart now ships a ServiceMonitor, whose CRD only exists after the
monitoring app syncs, and automated sync doesn't retry a failed commit by itself.
`SkipDryRunOnMissingResource=true` plus a `retry` block on the apps that depend
on those CRDs.

### `000` in the load generator: DNS, not the pet
~1% of the generator's requests failed with curl code `000` (no response):

```
dns=0.0019 total=0.0042   ← most requests
dns=0.0877 total=0.0901   ← search-domain walk
```

With `ndots:5`, the 4-dot name `traefik.kube-system.svc.cluster.local` is first
tried against every search domain. When one of those UDP queries is lost,
Alpine's musl resolver retries only after **5s**, exactly curl's `-m 5`. Fix:
`dnsConfig` with `ndots:2` on the generator: DNS went to under 1ms every time.

**SRE takeaway:** those failures never showed up in the SLI. They happened in
the client, before reaching Traefik. Had the owners been real users, they'd have
seen errors while the SLO read 100%. Only a client-side (synthetic) SLI catches
that.

### PowerShell and double quotes
A commit message containing `"5xx by route"` passed through a PowerShell 5.1
here-string broke the arguments to `git`: the commit silently didn't happen and
the push sent an unchanged branch. Avoid double quotes in messages passed from
PowerShell to native commands, and check `git log -1` after committing.

---

## Chaos experiment: burning the budget

Chaos engineering is an experiment, not random breakage: steady state →
hypothesis → controlled injection with a stop button → observation → learning.

### Steady state
Availability ≥ 99%, latency SLI ≥ 95%, burn rate ≈ 0, no `ErrorBudgetBurn` alert.

### Hypothesis
With `chaos.errorRate: 0.5`:
- the error ratio goes to ~50%, so the burn rate goes to **~50×** (0.5 / 0.01);
- Pyrra's `ErrorBudgetBurn` alert fires within minutes (shortest window pair
  15m/1m);
- the availability budget drops visibly (at 50× the whole 7d budget lasts
  ~3.4h);
- "5xx by route" shows errors on every endpoint;
- the probes keep passing (`/healthz`, `/readyz` are spared from chaos), so the
  pod is **not** restarted: a sick pet, not a dead one.

### Turn it on

Through Git, like everything else (`selfHeal` would revert a manual
`helm upgrade`):

1. Branch `chaos/error-rate-50`: in `charts/kubegotchi/values.yaml`
   set `chaos.errorRate: 0.5`.
2. Open the PR and merge it.
3. Argo CD rolls out a new pod within ~3 min (**Refresh** on the `kubegotchi`
   card to skip the wait). The chart change doesn't touch `app/`, so CI doesn't
   bump the image.

### Watch

- Dashboard http://grafana.localhost:8080/d/kubegotchi, time range **Last 30
  minutes**:
  - *Requests by status*: half turn into `500`;
  - *Availability burn rate (1h)*: climbs past the red 1× line (it's a 1h
    average, so it ramps up);
  - *Availability error budget left*: drops from 100%;
  - *5xx by route*: every endpoint.
- Pyrra http://pyrra.localhost:8080: `pet-availability` → `ErrorBudgetBurn`
  firing after ~5–10 min.
- The generator logging failures live:
  ```powershell
  kubectl -n loadgen logs deploy/owners -f
  ```
- Good moment for a Grafana snapshot (Share → Snapshot) or a GIF for the README.

Keep it to **15–20 minutes**: at 50× every hour costs ~29% of the weekly budget.

### Turn it off

```bash
git switch main && git pull
git switch -c chaos/off
git revert --no-edit <commit that set errorRate: 0.5>
git push -u origin chaos/off   # PR → merge
```

Same mechanism as a deploy rollback in Phase 2. After the merge Argo CD rolls
back to `errorRate: 0`: `500`s stop, burn rate decays over the next hour, the
alert resolves. **The budget that was spent stays spent** until those errors
leave the 7d window.

### Result

_To be filled in after the experiment: when the alert fired, peak burn rate,
budget left at the end, and whether the hypothesis held._
