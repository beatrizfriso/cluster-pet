# Phase 2: GitOps

**Goal:** a `git push` changes the pet without any manual `kubectl`.

## The flow

```
PR merged into main
  → CI: gitleaks, helm lint, tests, smoke test
  → publish: build → Trivy scan → push ghcr.io/beatrizfriso/kubegotchi:sha-<short>
  → bump-tag (only if app/ changed): commit "chore(deploy): run image sha-<short>"
  → Argo CD sees the new commit on main (polls every ~3 min)
  → rolling update in the cluster
```

Nobody runs `kubectl` or `helm` against the pet anymore. With `selfHeal` on, Argo
CD reverts any manual change back to what is in Git.

---

## 1. Publishing the image (`publish` job)

[`.github/workflows/ci.yaml`](../.github/workflows/ci.yaml), runs only on pushes
to `main` and `v*` tags, never on PRs.

| Event | Image tag |
|---|---|
| push to `main` | `sha-<short>` (what Argo CD deploys) |
| tag `v0.2.0` | `0.2.0` (release) |

- **No `latest`:** a tag that moves makes it impossible to know what is running.
- **Trivy scans before pushing:** fixable HIGH/CRITICAL CVEs block the publish.
- **Third-party actions pinned by commit SHA:** a moved tag (e.g. a compromised
  maintainer account) can't change what runs. When pinning, use the **commit**
  SHA: for annotated tags `git ls-remote` shows the tag object first, the commit
  is the `^{}` line.

### The scan paid off on day one

Running Trivy locally before the first push found 3 fixable HIGH CVEs in
Starlette 0.46.2 (pulled by FastAPI 0.115). Bumping FastAPI to 0.156 brought
Starlette 1.9.0 and a clean scan. Without it, the very first publish would have
failed.

```powershell
docker run --rm -v //var/run/docker.sock:/var/run/docker.sock aquasec/trivy:0.76.0 image --severity CRITICAL,HIGH --ignore-unfixed kubegotchi:scan
```

### ghcr.io packages start private

After the first publish the package is private and the cluster can't pull it.
Package settings → Change visibility → Public.

---

## 2. Argo CD

Installed with Helm, values versioned in the repo:

```powershell
make argocd            # chart argo/argo-cd 10.10.5 (Argo CD v3.5.5)
make argocd-password   # initial admin password
```

[`gitops/argocd/values.yaml`](../gitops/argocd/values.yaml):

- UI behind Traefik at **http://argocd.localhost:8080**. `*.localhost` resolves to
  127.0.0.1 in browsers, so no hosts file edit.
- `server.insecure: true`: Traefik serves plain HTTP locally; without it the UI
  loops redirecting to HTTPS. Only acceptable in a local lab.
- Dex (SSO) and notifications off.

Both Ingresses share port 8080: Traefik routes `argocd.localhost` to Argo CD
(specific host) and everything else to the pet (no host).

---

## 3. The Application

[`gitops/apps/kubegotchi.yaml`](../gitops/apps/kubegotchi.yaml) syncs
`charts/kubegotchi` from `main` into the `pet` namespace:

- `automated.prune`: resources removed from Git are removed from the cluster.
- `automated.selfHeal`: manual changes are reverted.
- `releaseName: kubegotchi`: same name as the old Helm release, so Argo CD
  adopts the running resources instead of creating a copy.
- Finalizer `resources-finalizer.argocd.argoproj.io/background`: deleting the
  Application also deletes what it deployed.

### Handing over from Helm without downtime

```powershell
make argocd-app
```

1. `kubectl apply` of the Application.
2. Deletes the Helm release secrets (`sh.helm.release.v1.kubegotchi.*`). Helm
   forgets the release, but the Deployment, Service and Ingress keep running and
   Argo CD adopts them. `helm list -n pet` is empty afterwards, as expected.

**Order matters:** the Application reads `main`, so it can only be applied after
the chart on `main` points to the ghcr.io image. Before that, it would deploy
the local-only `kubegotchi:dev`.

### `Repository not found`

```
ComparisonError: failed to list refs: authentication required: Repository not found.
```

The repo was private and Argo CD reads it anonymously. GitHub answers 404 for
private repos to anyone without access. Options were: make it public, a
read-only deploy key (private key in a Kubernetes Secret, never in Git), or a
fine-grained token. The repo was made public after a full-history gitleaks scan:

```bash
docker run --rm -v "$PWD:/repo" ghcr.io/gitleaks/gitleaks:v8.30.1 git /repo --log-opts="--all" --redact
```

---

## 4. Changing the pet through Git

`petName: Bolinha` → `Pipoca` in a PR. After the merge, with no command run, Argo
CD went `OutOfSync` → `Synced`, created a new ReplicaSet (the env var changed the
template) and `/status` answered `"name":"Pipoca"`.

---

## 5. CI bumps the image tag (`bump-tag` job)

After `publish`, on `main` only:

- If `app/` changed in the merge, it sets `image.tag` in `values.yaml` to the
  image just published and pushes `chore(deploy): run image sha-<short>`.
- Docs and chart-only merges keep the running image, so the pod (and the pet's
  in-memory state) isn't restarted for nothing.
- `concurrency` keeps two merges from racing; `git pull --rebase` handles a
  `main` that moved meanwhile.
- The push uses `GITHUB_TOKEN`, which doesn't trigger workflows: no CI loop.

### End-to-end test

`/status` gained a `version` field (the chart passes `image.tag` as
`APP_VERSION`). Merging that PR:

```
82f1b04  github-actions[bot] | chore(deploy): run image sha-844e85e
844e85e  Beatriz Friso       | Merge pull request #10 (status-version)
ccf4a40  Beatriz Friso       | Merge pull request #9 (ci-bump-tag)
```

```
Argo CD: Synced Healthy 82f1b04
{"name":"Pipoca",...,"pod":"kubegotchi-5f75b48b9f-m4t5x","version":"sha-844e85e"}
```

---

## Rollback

With `selfHeal` on, the **History and Rollback** button in the Argo CD UI
doesn't stick: Argo CD goes back to what Git says. In GitOps the rollback is a
commit too:

```bash
git switch -c rollback/sha-844e85e main
git revert --no-edit <chore(deploy) commit>
git push -u origin rollback/sha-844e85e   # PR → merge
```

Argo CD then rolls out the previous image. The revert only touches the chart, so
`bump-tag` doesn't fire. To undo the rollback, revert the revert. Not done
hands-on in this phase.

---

## Problems along the way

### `!` inside double quotes in Bash
`git commit -m "refactor!: ..."` failed with `unrecognized history modifier`:
`!` triggers history expansion in interactive Bash. Use single quotes, or
`set +H` in `~/.bashrc`.

### Personal email in public commits
Commits carry the email from `git config user.email`, set on the local machine.
GitHub's "Keep my email addresses private" only covers what is done on the
website. Fix for new commits:

```bash
git config --global user.email "<id>+<user>@users.noreply.github.com"
```

and enable "Block command line pushes that expose my email". Old commits keep
the old email unless history is rewritten.

### Rewriting history to drop co-author trailers
Done with `git filter-branch --msg-filter` on a scratch branch, then a
`--force-with-lease` push to `main` and moving the `v0.1.0` tag. Lessons:

- **Limit the range** to the commits that need changing
  (`rewrite-main ^<last good main> ^<last good side branch>`). Merges made on
  github.com are **signed by GitHub**, and any rewrite drops the signature, so
  rewriting them changes their hash even when the message is identical.
- **Pass untouched messages through byte for byte** (temp file + `cat`):
  `$(cat)` strips trailing newlines and `git stripspace` normalizes them, both of
  which change hashes.
- **Verify before pushing:** `git diff origin/main rewrite-main` must be empty
  and the old merges must still be ancestors.
- Old feature branches on the remote still held the original commits: delete
  merged branches (or enable "Automatically delete head branches").

### `Gateway Timeout` during a rollout
One request failed while the old pod was terminating: Traefik still routed to it
for a moment. Fix planned for Phase 5: a `preStop` sleep.

---

## Phase 2 done

| Item | Status |
|---|---|
| CI passing on GitHub | ✅ |
| Image on ghcr.io, scanned, no `latest` | ✅ |
| Argo CD installed (Helm, versioned values) | ✅ |
| Application with prune + selfHeal | ✅ |
| Change through a commit (`petName`) | ✅ |
| CI bumps the image tag | ✅ end-to-end with `sha-844e85e` |
| Rollback | ⏭️ documented, not done hands-on |
