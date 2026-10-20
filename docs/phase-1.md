# Phase 1: it lives on Kubernetes

**Goal:** kill pods and the pet stays alive.

> The outputs below were captured before the repo was translated to English, so
> they still show the old Portuguese values: mood `feliz` (now `happy`) and the
> load generator pod `carga` (now `load`).

## Environment

- k3d cluster `kubegotchi`: 1 server + 2 agents (`k3d/cluster.yaml`)
- Chart installed in the `pet` namespace with `make deploy`
- kubectl context: `k3d-kubegotchi`

```powershell
kubectl get nodes
helm list -A
kubectl -n pet get deploy,rs,pods,svc
```

---

## Exercise 3: kill a pod

```powershell
# Terminal 1: watch in real time
kubectl -n pet get pods -w

# Terminal 2: kill the pod
kubectl -n pet delete pod -l app.kubernetes.io/name=kubegotchi
```

### What showed up

```
kubegotchi-5ccc5ff449-54ddw   1/1     Running             0          62m
kubegotchi-5ccc5ff449-54ddw   1/1     Terminating         0          62m
kubegotchi-5ccc5ff449-dtdcr   0/1     Pending             0          0s
kubegotchi-5ccc5ff449-dtdcr   0/1     ContainerCreating   0          0s
kubegotchi-5ccc5ff449-54ddw   0/1     Completed           0          62m
kubegotchi-5ccc5ff449-dtdcr   0/1     Running             0          1s
kubegotchi-5ccc5ff449-dtdcr   1/1     Running             0          3s
```

### What it means

- **The new pod is born before the old one finishes.** The ReplicaSet sees
  "desired 1, existing 0" and creates another right away. It doesn't wait, it
  reconciles.
- **The same `5ccc5ff449` prefix** is the *pod-template-hash*: both pods come
  from the same ReplicaSet. The template didn't change, only the instance was
  replaced.
- **`Completed`, not `Error`:** Kubernetes sent SIGTERM, uvicorn shut down
  cleanly and exited with code 0 (*graceful shutdown*).
- **`0/1 Running` for about 2s:** the container is already running, but the
  readiness probe hasn't passed yet, so the Service doesn't send it traffic.

### Who recreated the pod?

```powershell
kubectl -n pet get rs
kubectl -n pet describe rs | Select-String "Controlled By|Replicas"
```

The chain is **Deployment → ReplicaSet → Pod**. The Deployment manages template
versions (one ReplicaSet per version). The ReplicaSet guarantees the pod count.

**Side effect:** the pet's state lives in the pod's memory, so killing the pod
resets the stats. Phase 4 fixes this (state coming from Prometheus).

---

## Exercise 4: break the liveness probe

The patch swaps the liveness path for one that doesn't exist:
[`experiments/phase-1/break-liveness.json`](../experiments/phase-1/break-liveness.json).
The patch lives in a file because PowerShell 5.1 mangles the quotes of JSON
passed inline.

```powershell
# Terminal 1
kubectl -n pet get pods -w

# Terminal 2 (from the repo root)
kubectl -n pet patch deployment kubegotchi --type=json --patch-file experiments/phase-1/break-liveness.json
```

### What to expect

- A **new ReplicaSet** shows up (different hash), because the template changed.
- The pod stays `1/1 Running` (readiness is still fine), but **RESTARTS** goes up
  every ~30s (3 failures × `periodSeconds: 10`).
- After a few restarts **`CrashLoopBackOff`** shows up, and the delay between
  attempts doubles: 10s, 20s, 40s… up to 5min.

### Investigate

```powershell
kubectl -n pet describe pod -l app.kubernetes.io/name=kubegotchi | Select-String -Context 0,15 "Events:"
```

Look for `Liveness probe failed: HTTP probe failed with statuscode: 404` and
`Container pet failed liveness probe, will be restarted`.

### Fix (rollback)

```powershell
kubectl -n pet rollout undo deployment/kubegotchi
kubectl -n pet rollout status deployment/kubegotchi
```

> `kubectl patch` is a manual change, outside Helm, and is only for the
> experiment. `rollout undo` goes back to the previous template. In Phase 2
> Argo CD reverts this kind of *drift* on its own.

### What showed up

A new ReplicaSet (`6966fdb7d8`) got `DESIRED 1`, and the old ones went to 0. The
old ones don't disappear: the Deployment keeps up to 10 (`revisionHistoryLimit`),
and `rollout undo` takes the previous version from that history.

```
NAME                          READY   STATUS    RESTARTS      AGE
kubegotchi-6966fdb7d8-fcgjb   1/1     Running   1 (28s ago)   59s
kubegotchi-6966fdb7d8-fcgjb   0/1     Running   2 (0s ago)    61s
kubegotchi-6966fdb7d8-fcgjb   1/1     Running   2 (6s ago)    67s
kubegotchi-6966fdb7d8-fcgjb   0/1     Running   3 (1s ago)    92s
kubegotchi-6966fdb7d8-fcgjb   1/1     Running   3 (2s ago)    93s
```

Events from `describe`:

```
Normal   Killing    21s (x3 over 81s)   Container pet failed liveness probe, will be restarted
Normal   Started    19s (x4 over 111s)  Container started
Warning  Unhealthy  19s (x7 over 110s)  Readiness probe failed: ... connect: connection refused
Warning  Unhealthy  11s (x3 over 101s)  Liveness probe failed: HTTP probe failed with statuscode: 500
Warning  Unhealthy  1s  (x8 over 91s)   Liveness probe failed: HTTP probe failed with statuscode: 404
```

### What it means

- **One restart every ~30s:** 3 consecutive failures (default
  `failureThreshold`) × `periodSeconds: 10`. The kubelet kills the
  **container**, not the pod: the pod name and age stay, only `RESTARTS` goes up.
- **`Killing` 3 times, `Started` 4 times:** the first start plus 3 restarts.
- **`0/1` right after each restart:** uvicorn isn't listening yet, so readiness
  fails with `connection refused` and the pod leaves the Service for a few
  seconds. As soon as the app is up, `/readyz` answers and the pod is back to
  `1/1`.
- **Why `500` as well as `404`?** The release had chaos on
  (`chaos.errorRate=0.3`). The app middleware only spares the routes in
  `_UNOBSERVED` (`/metrics`, `/healthz`, `/readyz`) from chaos. The broken path
  isn't on that list, so about 30% of the probes got a `500` before even reaching
  the `404`. To the probe it doesn't matter: any code outside 200–399 is a
  failure.
- **`CrashLoopBackOff` barely shows up here:** the container doesn't crash on its
  own, it runs ~30s until liveness kills it. Every death counts toward the
  backoff, but the `CrashLoopBackOff` status only shows during the wait between
  a death and the next start, which is short at first. In a container that
  crashes right on startup (import error, missing config) it shows up much more.

### Liveness vs readiness

| | Liveness | Readiness |
|---|---|---|
| Question | Is the process stuck? | Can it take traffic right now? |
| On failure | kubelet **restarts the container** | pod leaves the Service endpoints, **no restart** |
| Here | broken on purpose → restarts | fine → `1/1` most of the time |

The two are independent. That's why the pod looked "ready" while being restarted
every 30s. A misconfigured liveness probe (wrong path, timeout too short, an
external dependency in the health check) turns into a restart loop on an app that
was healthy. It's a classic cause of incidents.

### Cleanup

```powershell
kubectl -n pet rollout undo deployment/kubegotchi
helm upgrade kubegotchi charts/kubegotchi -n pet --reuse-values --set chaos.errorRate=0 --set chaos.latencyMs=0
```

Checked afterwards: liveness back on `/healthz`, `CHAOS_*` at `0`, pod
`1/1 Running` with 0 restarts.

---

## Exercise 5: OOMKilled

The pet uses about **35Mi** at rest (`kubectl -n pet top pod`), with a 128Mi
limit. The experiment gives it 24Mi, less than it needs just to start. This time
the change goes through Helm, because the resources are already in
`values.yaml`. The request goes down too, because Kubernetes doesn't accept a
request higher than the limit.

```powershell
# Terminal 1
kubectl -n pet get pods -w

# Terminal 2
helm upgrade kubegotchi charts/kubegotchi -n pet --reuse-values --set resources.limits.memory=24Mi --set resources.requests.memory=24Mi
```

### What showed up

```
kubegotchi-5cd5bcbf54-lhrk4   1/1     Running             0             97s
kubegotchi-66cd56b485-vhnhl   0/1     ContainerCreating   0             0s
kubegotchi-66cd56b485-vhnhl   0/1     Running             0             2s
kubegotchi-66cd56b485-vhnhl   0/1     OOMKilled           0             3s
kubegotchi-66cd56b485-vhnhl   0/1     CrashLoopBackOff    1 (2s ago)    6s
kubegotchi-66cd56b485-vhnhl   0/1     Running             2 (13s ago)   17s
kubegotchi-66cd56b485-vhnhl   0/1     OOMKilled           2 (14s ago)   18s
kubegotchi-66cd56b485-vhnhl   0/1     CrashLoopBackOff    2 (1s ago)    19s
kubegotchi-66cd56b485-vhnhl   0/1     Running             3 (28s ago)   46s
kubegotchi-66cd56b485-vhnhl   0/1     OOMKilled           3 (29s ago)   47s
kubegotchi-66cd56b485-vhnhl   0/1     CrashLoopBackOff    3 (1s ago)    48s
kubegotchi-66cd56b485-vhnhl   0/1     Terminating         3 (1s ago)    48s   ← helm rollback
```

```
Last State:     Terminated
  Reason:       OOMKilled
  Exit Code:    137
  Started:      17:07:13
  Finished:     17:07:14
```

```
$ kubectl -n pet rollout status deployment/kubegotchi --timeout=30s
Waiting for deployment "kubegotchi" rollout to finish: 1 old replicas are pending termination...
```

### What it means

- **Dies in about 1s** (`Started 17:07:13` → `Finished 17:07:14`): Python goes
  past 24Mi while still loading FastAPI. The kernel kills the process on the
  spot, with no warning and no SIGTERM.
- **`Exit Code: 137`** = 128 + 9 (SIGKILL). Whenever you see 137, suspect OOM
  first.
- **Now a real `CrashLoopBackOff`:** the container crashes on its own at
  startup. The backoff doubles on every attempt: the wait was ~10s, then ~20s,
  and would go up to 5min.
- **The old pod (`lhrk4`) stayed `1/1 Running` the whole time.** With 1 replica,
  the default rolling update (`maxUnavailable: 25%`, which rounds down to 0) only
  takes down the old pod once the new one is ready. The deploy broke, but the pet
  kept serving. `rollout status` shows exactly that: the swap stuck, waiting.
- **Helm said `Upgrade complete` / `deployed` even with the pod crashing.** By
  default Helm only applies the manifests and doesn't wait for pods to be ready.
  To make it wait and actually fail, use `--wait`, or `--rollback-on-failure` to
  roll back on its own. This matters in a CI/CD pipeline: without `--wait`, a
  broken deploy shows up green.

### Fix (rollback through Helm)

```powershell
helm history kubegotchi -n pet
helm rollback kubegotchi 5 -n pet
```

The rollback doesn't delete revision 6: it creates **revision 7** with the
content of 5 (`DESCRIPTION: Rollback to 5`). Checked afterwards: resources back
to 128Mi/64Mi and pod `lhrk4` intact, with 0 restarts, because it never went
down.

### `kubectl rollout undo` vs `helm rollback`

| | `kubectl rollout undo` | `helm rollback` |
|---|---|---|
| Reverts what | only the Deployment template | every resource in the release (Deployment, Service...) |
| History | old ReplicaSets | Helm revisions (values + manifests) |
| Does Helm know? | **no**: the next `helm upgrade` reapplies its values | **yes**: it becomes a new revision |

When the deploy was done through Helm, the rollback should be done through Helm
too. `rollout undo` worked in exercise 4 because the damage was also done outside
Helm (`kubectl patch`).

## Ingress on `localhost:8080`

New template: [`charts/kubegotchi/templates/ingress.yaml`](../charts/kubegotchi/templates/ingress.yaml),
controlled by the `ingress` block in `values.yaml` (on by default, class
`traefik`, empty `host` to accept any name).

A request now goes:

```
localhost:8080 → k3d loadbalancer (port 8080:80, k3d/cluster.yaml)
              → Traefik (ingress controller that ships with k3s)
              → kubegotchi Ingress → kubegotchi Service :80 → Pod :8000
```

### Applying

```powershell
helm upgrade kubegotchi charts/kubegotchi -n pet --reset-then-reuse-values --wait --timeout 2m
kubectl -n pet get ingress
curl.exe -i localhost:8080/status
```

```
NAME         CLASS     HOSTS   ADDRESS                            PORTS   AGE
kubegotchi   traefik   *       172.18.0.2,172.18.0.3,172.18.0.4   80      2s

HTTP/1.1 200 OK
{"name":"Bolinha","mood":"feliz",...,"pod":"kubegotchi-5cd5bcbf54-lhrk4"}
```

No `port-forward`: `make pet` is no longer needed to talk to the pet.

### Lessons

- **`--reuse-values` breaks when the chart gains new keys.** It only reuses the
  values from the last revision, which had no `ingress`, and the template would
  fail with a nil pointer. `--reset-then-reuse-values` takes the chart's new
  defaults **and** keeps what was passed before (the image tag, for example).
- **`--wait`** makes Helm wait for resources to be ready (see exercise 5).
- **The pod didn't restart** (`lhrk4` stayed the same): the Deployment didn't
  change, only a new resource (the Ingress) was created. Helm only touches what
  changed.
- **`ADDRESS` with 3 IPs:** those are the 3 k3d nodes. Traefik is exposed by the
  k3s ServiceLB on all of them.
- **VS Code errors on the templates are false positives:** the editor's YAML
  validator doesn't understand `{{ }}`. What counts is `helm lint` and
  `helm template`.

## HorizontalPodAutoscaler

New template: [`charts/kubegotchi/templates/hpa.yaml`](../charts/kubegotchi/templates/hpa.yaml),
`autoscaling/v2` on CPU, between 1 and 3 replicas, targeting **60% of
`requests.cpu`** (50m), i.e. about 30m per pod. It's **off by default**
(`autoscaling.enabled: false`), because the state lives in the pod's memory.

With the HPA on, `deployment.yaml` **stops setting `replicas`**. If it kept
setting it, every `helm upgrade` would reset the count to `replicaCount` and
fight the HPA.

The HPA reads CPU from **metrics-server**, which ships with k3s
(`kubectl get apiservice v1beta1.metrics.k8s.io` → `AVAILABLE True`).

### Turning it on and generating load

```powershell
helm upgrade kubegotchi charts/kubegotchi -n pet --reset-then-reuse-values --set autoscaling.enabled=true

# a pod inside the cluster hammering the Service
kubectl -n pet run load --image=busybox:1.36 --restart=Never -- /bin/sh -c "while true; do wget -q -O- http://kubegotchi/status >/dev/null; done"

kubectl -n pet get hpa -w
```

```
17:19:27  cpu: <unknown>/60%   MIN 1  MAX 3  REPLICAS 0   ← metrics-server has no sample yet
17:19:42  cpu: 4%/60%                        REPLICAS 1
17:19:57  cpu: 190%/60%                      REPLICAS 1
17:20:14  cpu: 502%/60%                      REPLICAS 3
```

```
NAME                          CPU(cores)   MEMORY(bytes)
carga                         346m         4Mi
kubegotchi-5cd5bcbf54-lhrk4   215m         36Mi
kubegotchi-5cd5bcbf54-pdtms   92m          36Mi
kubegotchi-5cd5bcbf54-xvrxg   150m         36Mi
```

- **The percentage is of the request, not the limit:** 502% of 50m ≈ 250m,
  exactly the CPU limit. The pod was being *throttled*.
- **The HPA math:** `desired = ceil(current × usage / target) = ceil(1 × 502 / 60) = 9`,
  capped by `maxReplicas: 3`. It went straight from 1 to 3.
- **It took about 45s** from the start of the load: metrics-server scrapes every
  ~15s, and the HPA re-evaluates every 15s.
- **The new pods have the same `5cd5bcbf54` hash:** the HPA only changes the
  replica count, not the template. No new ReplicaSet.

### The pet splits

```powershell
1..3 | % { curl.exe -s -X POST localhost:8080/feed }
1..8 | % { curl.exe -s localhost:8080/status }
```

```
feed  -> pod=pdtms hunger=100,0
feed  -> pod=lhrk4 hunger=95,7
feed  -> pod=xvrxg hunger=100,0

status-> pod=lhrk4 hunger=95,7  age=14,8min
status-> pod=xvrxg hunger=100,0 age=0,5min
status-> pod=pdtms hunger=100,0 age=0,5min
status-> pod=xvrxg hunger=100,0 age=0,5min
...
```

There are **three different pets**. `lhrk4` is 14.8 minutes old, the other two
were just born. Each `/feed` landed on one pod (the Service spreads across the
ready endpoints), and `/status` answers depending on which pod gets picked.

This is the classic problem of a **stateful app scaled horizontally**. The way
out is to move the state out of the pod (a database, Redis, or Prometheus, as in
Phase 4) or to use *session affinity*, which only hides the problem. Scaling
horizontally assumes a **stateless** app.

### Scaling down

```powershell
kubectl -n pet delete pod load
```

```
17:20:27 (+00:00)  cpu: 311%/60%   REPLICAS 3   ← load removed
17:21:27 (+01:00)  cpu: 133%/60%   REPLICAS 3   ← last high sample
17:21:58 (+01:31)  cpu: 6%/60%     REPLICAS 3
...                cpu: ~5%/60%    REPLICAS 3   (5 minutes like this)
17:26:01 (+05:34)  cpu: 6%/60%     REPLICAS 3
17:26:32 (+06:04)  cpu: 6%/60%     REPLICAS 1
```

- **CPU dropped within ~1.5 min, but the replicas stayed for 5 min.** The HPA
  waits on purpose before scaling down: the scale-down *stabilization window* is
  300s by default. It uses the **highest** recommendation of the last 5 minutes,
  and the last high sample was at 17:21:27. Add 5 min and the scale-down came at
  ~17:26:30. This prevents *flapping* (scaling up and down on every blip).
  Scaling up is fast, scaling down is cautious.
- **It went straight from 3 to 1**, skipping 2: it applies the whole
  recommendation at once.
- **`lhrk4`, the oldest pod, survived.** On scale-down the ReplicaSet removes the
  newest pods first. Luckily the "original" pet survived. The other two, and
  whatever they had eaten, are gone.

### Turning it off

```powershell
helm upgrade kubegotchi charts/kubegotchi -n pet --reset-then-reuse-values --set autoscaling.enabled=false
```

The HPA is removed and the Deployment goes back to `replicas: 1` set by the
chart.

---

## Phase 1 done

| Item | Status |
|---|---|
| k3d cluster + deploy | ✅ |
| Kill a pod | ✅ ReplicaSet recreates it right away |
| Broken liveness | ✅ restarts every ~30s; liveness ≠ readiness |
| OOMKilled | ✅ exit 137, `CrashLoopBackOff`, rolling update protected the app |
| Ingress | ✅ `localhost:8080` via Traefik |
| HPA | ✅ 1 → 3 under load, 3 → 1 after ~5 min; the pet splits |

Final cluster state: release revision 11, 1 pod (`lhrk4`, 0 restarts), Ingress
active, HPA off, chaos at zero.

**Still to read carefully:** every field in `deployment.yaml` (the last open
Phase 1 item in the ROADMAP) and all of `main.py` (Phase 0).
