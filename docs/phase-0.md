# Phase 0: the pet exists

**Goal:** run the app locally and see `kubegotchi_stat` change in `/metrics`.

## Running the app

There is no Python installed on the machine (the `python` on the PATH is just the
Microsoft Store shortcut), so `make run` fails with `pip: command not found`. The
way out was to run the same image that goes to the cluster:

```powershell
# Terminal 1 (Ctrl+C to stop)
make run-docker

# Terminal 2
curl.exe localhost:8000/status
curl.exe -s localhost:8000/metrics | Select-String kubegotchi_stat
curl.exe -X POST localhost:8000/feed
curl.exe -s localhost:8000/metrics | Select-String kubegotchi_stat
```

> In PowerShell, `curl` is an alias for `Invoke-WebRequest`. Always use `curl.exe`.

### Endpoints

| Endpoint | Method | Purpose |
|---|---|---|
| `/status` | GET | current state and mood |
| `/feed`, `/play`, `/sleep` | POST | take care of the pet |
| `/healthz` | GET | liveness probe |
| `/readyz` | GET | readiness probe |
| `/metrics` | GET | metrics in Prometheus format |

The stats also drop on their own over time (`_decay_loop` in `main.py`,
controlled by `DECAY_PER_MINUTE`).

## Problems along the way

### A branch instead of committing straight to `main`
Everything was only staged, so it was enough to create the branch before the
first commit (staged changes come along):

```bash
git switch -c feat/initial-scaffold
git commit -m "..."
git push -u origin feat/initial-scaffold
gh pr create --base main
```

### `Author identity unknown` on commit
Git didn't know who the author was. Set it once:

```bash
git config --global user.name "Your Name"
git config --global user.email "your-github-email"
```

### Gitleaks failing with `403 Resource not accessible by integration`
It wasn't a leaked secret: the `GITHUB_TOKEN` had no permission to list the PR
commits. New repos give the token minimal permissions, so the workflow has to ask
explicitly. In `.github/workflows/ci.yaml`:

```yaml
permissions:
  contents: read          # default for every job

jobs:
  secrets:
    permissions:
      contents: read
      pull-requests: read # what the error asked for
```

Plus `GITLEAKS_ENABLE_COMMENTS: false`, so the token doesn't need write
permission just to comment on the PR.
