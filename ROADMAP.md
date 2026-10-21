# 🗺️ Kubegotchi Roadmap

Every phase ends with something working and visible. Check items off as you go.

---

## Phase 0: the pet exists ✅ (ships ready in the repo)
- [x] App with `/status`, `/feed`, `/play`, `/sleep`
- [x] Metrics at `/metrics`
- [x] Dockerfile running as non-root
- [x] Run it with `make run` and play with it using curl
- [ ] Read all of `main.py` and understand every metric

**Done when:** you feed the pet and see `kubegotchi_stat` change in `/metrics`.

---

## Phase 1: it lives on Kubernetes (1–2 weeks)
- [x] Create the cluster with `make cluster` and explore it with `kubectl get nodes,pods -A`
- [ ] `make deploy` and understand every field in `deployment.yaml`
- [x] Kill a pod (`kubectl delete pod`) and watch the Deployment recreate it
- [x] Break the liveness probe on purpose and watch the CrashLoopBackOff
- [x] Lower the memory limit until you see an OOMKilled
- [x] Add an Ingress to the chart to reach it at `localhost:8080`
- [x] Add a HorizontalPodAutoscaler

**Done when:** you kill pods and the pet stays alive.

**Concepts:** Pod, Deployment, ReplicaSet, Service, probes, requests/limits, Helm templates.

---

## Phase 2: GitOps (1 week)
- [x] Push the repo to GitHub and watch CI pass
- [x] Publish the image to GitHub Container Registry (ghcr.io)
- [x] Install Argo CD in the cluster
- [x] Create an Argo CD `Application` pointing at `charts/kubegotchi`
- [x] CI bumps the image tag in `values.yaml` on every push
- [x] Change `petName` through a commit and watch Argo CD sync on its own
- [x] Roll back through Argo CD (skipped hands-on: with `selfHeal` on, a rollback is a `git revert` of the `chore(deploy)` commit; see [docs/phase-2.md](docs/phase-2.md#rollback))

**Done when:** a `git push` changes the pet without any manual `kubectl`.

---

## Phase 3: vital signs (2 weeks)
- [x] Install `kube-prometheus-stack` (release `monitoring`)
- [x] Turn on `serviceMonitor.enabled=true` and find the metrics in Prometheus
- [x] Write the PromQL queries for the SLIs (availability and p95 latency)
- [x] Define SLOs with Sloth or Pyrra (e.g. 99% availability over 7 days)
- [x] Grafana dashboard: pet status + remaining error budget
- [x] Turn on chaos and watch the budget burn (procedure and results in [docs/phase-3.md](docs/phase-3.md#chaos-experiment-burning-the-budget))
- [x] Fix startup restarts under CPU throttling: `startupProbe` + CPU limit (found by the chaos experiment)

**Done when:** Grafana shows how much error budget is left and the burn rate.

**SLO → pet mapping idea:**
| Cluster signal | Becomes |
|---|---|
| Availability / error budget | health (sick when the budget runs out) |
| p95 latency | mood |
| CPU and memory usage vs limits | hunger and energy |
| Pod restarts | injuries |

---

## Phase 4: the pet gets a face (1–2 weeks)
- [ ] `/vitals` endpoint that queries Prometheus and returns the state computed from the SLOs
- [ ] Frontend that draws the pet reacting in real time
- [ ] k6 traffic generator simulating day-to-day load, with care at a realistic rate (~8/h, not the
  current ~540/h that pins fed/happiness/rested at 100); then record a new demo where care drops
  during an incident too (the current recording hides the care section for that reason)
- [ ] Frontend deployed to the cluster by the same chart (or a sibling chart)

**Done when:** you open the page and the pet reacts to what happens in the cluster.

---

## Phase 5: mistreat the pet, lovingly (1–2 weeks)
- [ ] Install Chaos Mesh
- [ ] Experiments: `PodChaos` (pod-kill), `NetworkChaos` (latency), `StressChaos` (CPU)
- [ ] Zero-downtime rollouts: a `preStop` sleep so Traefik stops routing before the pod dies (a `Gateway Timeout` showed up during the v0.1.0 deploy)
- [ ] Burn rate alerts in Alertmanager
- [ ] A runbook in `docs/runbooks/` for every alert
- [ ] First game day: injected failure → alert → diagnosis → cure → postmortem

**Done when:** you cure the pet by following only the runbook.

---

## Phase 6: the AI vet (2–3 weeks)
- [ ] Dedicated ServiceAccount with read-only RBAC (`get`, `list`, `watch`)
- [ ] Agent that reads alerts, metrics (Prometheus API), events and logs
- [ ] Diagnosis mode: explains the problem and suggests the command
- [ ] "Apply with approval" mode, with minimal RBAC for the allowed actions
- [ ] Log every diagnosis (good material for evaluating the agent)

**Done when:** a Chaos Mesh experiment fires and the agent explains the right cause.

---

## Releases (one per completed phase)
A release is a versioned milestone, not a deploy: Argo CD keeps deploying every
merge to `main` by commit SHA.
- [x] End of Phase 1: manual `v0.1.0` (`git tag` + `gh release create --generate-notes`), aligned with `version`/`appVersion` in `Chart.yaml`
- [ ] Phase 2: workflow that, on a `v*` tag, publishes the image `ghcr.io/<user>/kubegotchi:<version>`
- [ ] Later: automate with release-please (version and changelog from Conventional Commits)

---

## Bonus
- [ ] The pet evolves based on long-term reliability
- [ ] AI-generated postmortem after every game day
- [ ] Several pets (one per namespace) competing for resources
- [ ] MTTR scoreboard over the weeks

---

## Claude Code skills per phase
| Phase | Skill |
|---|---|
| Start | `/10x-team` (creates the `.10x/` folder and runs the brainstorming) |
| 1, 2, 5 | `/devops-engineer` |
| 3, 5 | `/sre` |
| 6 | `/security-engineer` (agent RBAC) |
| App and agent code | `/sde` |
| Learning the concepts | `/teach` |
