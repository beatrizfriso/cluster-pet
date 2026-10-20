# Fase 0: o bichinho existe

**Objetivo:** rodar o app localmente e ver `kubegotchi_stat` mudar em `/metrics`.

## Rodando o app

Não há Python instalado na máquina (o `python` do PATH é só o atalho da Microsoft
Store), então `make run` falha com `pip: command not found`. A saída foi rodar a
mesma imagem que vai para o cluster:

```powershell
# Terminal 1 (Ctrl+C para parar)
make run-docker

# Terminal 2
curl.exe localhost:8000/status
curl.exe -s localhost:8000/metrics | Select-String kubegotchi_stat
curl.exe -X POST localhost:8000/feed
curl.exe -s localhost:8000/metrics | Select-String kubegotchi_stat
```

> No PowerShell, `curl` é apelido do `Invoke-WebRequest`. Use sempre `curl.exe`.

### Endpoints

| Endpoint | Método | Para quê |
|---|---|---|
| `/status` | GET | estado atual e humor |
| `/feed`, `/play`, `/sleep` | POST | cuidar do bichinho |
| `/healthz` | GET | liveness probe |
| `/readyz` | GET | readiness probe |
| `/metrics` | GET | métricas no formato do Prometheus |

Os stats também caem sozinhos com o tempo (`_decay_loop` no `main.py`,
controlado por `DECAY_PER_MINUTE`).

## Problemas no caminho

### Branch em vez de commit direto na `main`
Tudo estava só em stage, então bastou criar a branch antes do primeiro commit
(as mudanças em stage vão junto):

```bash
git switch -c feat/initial-scaffold
git commit -m "..."
git push -u origin feat/initial-scaffold
gh pr create --base main
```

### `Author identity unknown` no commit
O Git não sabia quem é o autor. Configurar uma vez:

```bash
git config --global user.name "Seu Nome"
git config --global user.email "seu-email-do-github"
```

### Gitleaks com `403 Resource not accessible by integration`
Não era secret vazada: o `GITHUB_TOKEN` não tinha permissão para listar os
commits do PR. Repos novos dão permissões mínimas ao token, então o workflow
precisa pedir explicitamente. Em `.github/workflows/ci.yaml`:

```yaml
permissions:
  contents: read          # padrão para todos os jobs

jobs:
  secrets:
    permissions:
      contents: read
      pull-requests: read # o que o erro pedia
```

E `GITLEAKS_ENABLE_COMMENTS: false`, para não precisar dar permissão de escrita
ao token só para comentar no PR.
