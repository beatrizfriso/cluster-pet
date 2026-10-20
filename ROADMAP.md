# 🗺️ Roadmap do Kubegotchi

Cada fase termina com algo funcionando e visível. Marque os itens conforme avança.

---

## Fase 0: o bichinho existe ✅ (já vem pronto no repo)
- [x] App com `/status`, `/feed`, `/play`, `/sleep`
- [x] Métricas em `/metrics`
- [x] Dockerfile rodando como não-root
- [x] Rodar com `make run` e brincar com ele pelo curl
- [ ] Ler o `main.py` inteiro e entender cada métrica

**Pronto quando:** você alimenta o bichinho e vê `kubegotchi_stat` mudar em `/metrics`.

---

## Fase 1: ele mora no Kubernetes (1–2 semanas)
- [x] Criar o cluster com `make cluster` e explorar com `kubectl get nodes,pods -A`
- [ ] `make deploy` e entender cada campo do `deployment.yaml`
- [x] Matar um pod (`kubectl delete pod`) e ver o Deployment recriá-lo
- [x] Quebrar a liveness probe de propósito e observar o CrashLoopBackOff
- [x] Baixar o limit de memória até ver um OOMKilled
- [x] Adicionar um Ingress no chart para acessar em `localhost:8080`
- [x] Adicionar um HorizontalPodAutoscaler

**Pronto quando:** você mata pods e o bichinho continua vivo.

**Conceitos:** Pod, Deployment, ReplicaSet, Service, probes, requests/limits, Helm templates.

---

## Fase 2: GitOps (1 semana)
- [ ] Subir o repo no GitHub e ver o CI passar
- [ ] Publicar a imagem no GitHub Container Registry (ghcr.io)
- [ ] Instalar o Argo CD no cluster
- [ ] Criar uma `Application` do Argo CD apontando para `charts/kubegotchi`
- [ ] CI atualiza a tag da imagem no `values.yaml` a cada push
- [ ] Mudar o `petName` via commit e ver o Argo CD sincronizar sozinho
- [ ] Fazer um rollback pelo Argo CD

**Pronto quando:** um `git push` muda o bichinho sem nenhum `kubectl` manual.

---

## Fase 3: os sinais vitais (2 semanas)
- [ ] Instalar o `kube-prometheus-stack` (release `monitoring`)
- [ ] Ligar `serviceMonitor.enabled=true` e achar as métricas no Prometheus
- [ ] Escrever as queries PromQL dos SLIs (disponibilidade e latência p95)
- [ ] Definir SLOs com Sloth ou Pyrra (ex.: 99% de disponibilidade em 7 dias)
- [ ] Dashboard no Grafana: status do bichinho + error budget restante
- [ ] Ligar o chaos (`chaos.errorRate=0.05`) e ver o budget queimar

**Pronto quando:** o Grafana mostra quanto error budget sobra e a taxa de queima.

**Ideia de mapeamento SLO → bichinho:**
| Sinal do cluster | Vira |
|---|---|
| Disponibilidade / error budget | saúde (doente quando o budget acaba) |
| Latência p95 | humor |
| Uso de CPU e memória vs limits | fome e energia |
| Restarts de pod | machucados |

---

## Fase 4: o bichinho tem cara (1–2 semanas)
- [ ] Endpoint `/vitals` que consulta o Prometheus e devolve o estado calculado pelos SLOs
- [ ] Frontend que desenha o bichinho reagindo em tempo real
- [ ] Gerador de tráfego com k6 simulando o dia a dia
- [ ] Frontend deployado no cluster pelo mesmo chart (ou um chart irmão)

**Pronto quando:** você abre a página e o bichinho reage ao que acontece no cluster.

---

## Fase 5: maltratar o bichinho, com carinho (1–2 semanas)
- [ ] Instalar o Chaos Mesh
- [ ] Experimentos: `PodChaos` (pod-kill), `NetworkChaos` (latência), `StressChaos` (CPU)
- [ ] Alertas de burn rate no Alertmanager
- [ ] Um runbook em `docs/runbooks/` para cada alerta
- [ ] Primeiro game day: falha injetada → alerta → diagnóstico → cura → postmortem

**Pronto quando:** você cura o bichinho seguindo só o runbook.

---

## Fase 6: o veterinário de AI (2–3 semanas)
- [ ] ServiceAccount própria com RBAC só de leitura (`get`, `list`, `watch`)
- [ ] Agente que lê alertas, métricas (API do Prometheus), eventos e logs
- [ ] Modo diagnóstico: explica o problema e sugere o comando
- [ ] Modo "aplicar com aprovação", com RBAC mínimo para as ações permitidas
- [ ] Registrar cada diagnóstico (bom material para avaliar o agente)

**Pronto quando:** um experimento do Chaos Mesh dispara e o agente explica a causa certa.

---

## Releases (uma por fase concluída)
Release é um marco versionado, não um deploy: o Argo CD continua fazendo deploy
de cada merge na `main` pelo SHA do commit.
- [ ] Fim da Fase 1: `v0.1.0` manual (`git tag` + `gh release create --generate-notes`), alinhada com `version`/`appVersion` do `Chart.yaml`
- [ ] Fase 2: workflow que, numa tag `v*`, publica a imagem `ghcr.io/<user>/kubegotchi:<versão>`
- [ ] Depois: automatizar com release-please (versão e changelog a partir dos Conventional Commits)

---

## Bônus
- [ ] O bichinho evolui conforme a confiabilidade de longo prazo
- [ ] Postmortem gerado pela AI depois de cada game day
- [ ] Vários bichinhos (um por namespace) disputando recursos
- [ ] Placar de MTTR ao longo das semanas

---

## Skills do Claude Code por fase
| Fase | Skill |
|---|---|
| Início | `/10x-team` (cria a pasta `.10x/` e faz o brainstorming) |
| 1, 2, 5 | `/devops-engineer` |
| 3, 5 | `/sre` |
| 6 | `/security-engineer` (RBAC do agente) |
| Código do app e do agente | `/sde` |
