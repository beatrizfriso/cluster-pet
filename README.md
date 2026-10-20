# 🐣 Kubegotchi - WIP

Um bichinho virtual cuja saúde **é** o seu cluster Kubernetes.

Os SLOs viram fome, humor e energia: se a latência sobe, ele fica triste; se o error budget acaba, ele adoece. Um veterinário de AI diagnostica o que está errado e propõe o conserto.

Projeto de estudo de **Kubernetes, GitOps, SRE e AI**, feito para rodar 100% local e sem custo de cloud.

## O que você precisa instalar

- Docker
- [k3d](https://k3d.io) (k3s dentro do Docker)
- kubectl
- Helm
- make, curl

## Começando

```bash
# Fase 0: o bichinho na sua máquina
make run
# em outro terminal
curl localhost:8000/status
curl -X POST localhost:8000/feed
curl localhost:8000/metrics | grep kubegotchi

# Fase 1: o bichinho no Kubernetes
make up          # cria o cluster, faz build, instala o chart
make pet         # port-forward (deixe rodando)
make feed        # em outro terminal
```

## Estrutura

```
app/                  o bichinho (FastAPI + métricas Prometheus)
charts/kubegotchi/    Helm chart
k3d/cluster.yaml      cluster local: 1 server + 2 agents
.github/workflows/    CI: helm lint, smoke test, docker build
ROADMAP.md            as fases do projeto, com checklist
```

## Como o bichinho funciona

| Status      | 100 = ótimo | Cai com o tempo | Recupera com |
|-------------|-------------|-----------------|--------------|
| `hunger`    | saciado     | sim             | `POST /feed` |
| `happiness` | feliz       | sim             | `POST /play` |
| `energy`    | disposto    | sim             | `POST /sleep`|

O humor (`feliz`, `ok`, `triste`, `doente`) vem do pior status. A partir da Fase 3, quem manda nos status são os **SLOs do cluster**, não só os cuidados.

### Maltratando o bichinho de propósito

```bash
helm upgrade kubegotchi charts/kubegotchi -n pet --reuse-values \
  --set chaos.latencyMs=400 --set chaos.errorRate=0.1
```

## Métricas expostas

| Métrica | Tipo | Para quê |
|---|---|---|
| `kubegotchi_http_requests_total{path,method,code}` | counter | SLI de disponibilidade |
| `kubegotchi_http_request_duration_seconds` | histogram | SLI de latência |
| `kubegotchi_stat{stat}` | gauge | status do bichinho |
| `kubegotchi_actions_total{action}` | counter | quantos cuidados ele recebeu |
