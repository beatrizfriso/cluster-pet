# Fase 1: ele mora no Kubernetes

**Objetivo:** matar pods e o bichinho continuar vivo.

## Ambiente

- Cluster k3d `kubegotchi`: 1 server + 2 agents (`k3d/cluster.yaml`)
- Chart instalado no namespace `pet` com `make deploy`
- Contexto do kubectl: `k3d-kubegotchi`

```powershell
kubectl get nodes
helm list -A
kubectl -n pet get deploy,rs,pods,svc
```

---

## Exercício 3: matar um pod

```powershell
# Terminal 1: acompanha em tempo real
kubectl -n pet get pods -w

# Terminal 2: mata o pod
kubectl -n pet delete pod -l app.kubernetes.io/name=kubegotchi
```

### O que apareceu

```
kubegotchi-5ccc5ff449-54ddw   1/1     Running             0          62m
kubegotchi-5ccc5ff449-54ddw   1/1     Terminating         0          62m
kubegotchi-5ccc5ff449-dtdcr   0/1     Pending             0          0s
kubegotchi-5ccc5ff449-dtdcr   0/1     ContainerCreating   0          0s
kubegotchi-5ccc5ff449-54ddw   0/1     Completed           0          62m
kubegotchi-5ccc5ff449-dtdcr   0/1     Running             0          1s
kubegotchi-5ccc5ff449-dtdcr   1/1     Running             0          3s
```

### O que isso significa

- **O pod novo nasce antes do antigo terminar.** O ReplicaSet vê "desejado 1,
  existentes 0" e cria outro na hora. Ele não espera, ele reconcilia.
- **Mesmo prefixo `5ccc5ff449`** é o *pod-template-hash*: os dois pods vêm do
  mesmo ReplicaSet. O template não mudou, só a instância foi trocada.
- **`Completed` e não `Error`:** o Kubernetes mandou SIGTERM, o uvicorn desligou
  limpo e saiu com código 0 (*graceful shutdown*).
- **`0/1 Running` por cerca de 2s:** o container já roda, mas a readiness probe
  ainda não passou, então o Service não manda tráfego para ele.

### Quem recriou o pod?

```powershell
kubectl -n pet get rs
kubectl -n pet describe rs | Select-String "Controlled By|Replicas"
```

A cadeia é **Deployment → ReplicaSet → Pod**. O Deployment gerencia versões do
template (um ReplicaSet por versão). O ReplicaSet garante a quantidade de pods.

**Efeito colateral:** o estado do bichinho vive na memória do pod, então matar o
pod zera os stats. A Fase 4 resolve isso (estado vindo do Prometheus).

---

## Exercício 4: quebrar a liveness probe

O patch troca o path da liveness por um que não existe:
[`experiments/fase-1/break-liveness.json`](../experiments/fase-1/break-liveness.json).
O patch vai num arquivo porque o PowerShell 5.1 estraga as aspas de JSON
passado inline.

```powershell
# Terminal 1
kubectl -n pet get pods -w

# Terminal 2 (na raiz do repo)
kubectl -n pet patch deployment kubegotchi --type=json --patch-file experiments/fase-1/break-liveness.json
```

### O que esperar

- Surge um **ReplicaSet novo** (outro hash), porque o template mudou.
- O pod fica `1/1 Running` (a readiness continua OK), mas **RESTARTS** sobe a
  cada ~30s (3 falhas × `periodSeconds: 10`).
- Depois de alguns restarts aparece **`CrashLoopBackOff`**, e o intervalo entre
  as tentativas dobra: 10s, 20s, 40s… até 5min.

### Investigar

```powershell
kubectl -n pet describe pod -l app.kubernetes.io/name=kubegotchi | Select-String -Context 0,15 "Events:"
```

Procurar `Liveness probe failed: HTTP probe failed with statuscode: 404` e
`Container pet failed liveness probe, will be restarted`.

### Consertar (rollback)

```powershell
kubectl -n pet rollout undo deployment/kubegotchi
kubectl -n pet rollout status deployment/kubegotchi
```

> O `kubectl patch` é uma mudança manual, fora do Helm, e serve só para o
> experimento. O `rollout undo` volta para o template anterior. Na Fase 2 o
> Argo CD desfaz esse tipo de *drift* sozinho.

### O que apareceu

Um ReplicaSet novo (`6966fdb7d8`) ficou com `DESIRED 1`, e os antigos foram para
0. Os antigos não somem: o Deployment guarda até 10 (`revisionHistoryLimit`), e é
desse histórico que o `rollout undo` tira a versão anterior.

```
NAME                          READY   STATUS    RESTARTS      AGE
kubegotchi-6966fdb7d8-fcgjb   1/1     Running   1 (28s ago)   59s
kubegotchi-6966fdb7d8-fcgjb   0/1     Running   2 (0s ago)    61s
kubegotchi-6966fdb7d8-fcgjb   1/1     Running   2 (6s ago)    67s
kubegotchi-6966fdb7d8-fcgjb   0/1     Running   3 (1s ago)    92s
kubegotchi-6966fdb7d8-fcgjb   1/1     Running   3 (2s ago)    93s
```

Eventos do `describe`:

```
Normal   Killing    21s (x3 over 81s)   Container pet failed liveness probe, will be restarted
Normal   Started    19s (x4 over 111s)  Container started
Warning  Unhealthy  19s (x7 over 110s)  Readiness probe failed: ... connect: connection refused
Warning  Unhealthy  11s (x3 over 101s)  Liveness probe failed: HTTP probe failed with statuscode: 500
Warning  Unhealthy  1s  (x8 over 91s)   Liveness probe failed: HTTP probe failed with statuscode: 404
```

### O que isso significa

- **Um restart a cada ~30s:** 3 falhas seguidas (`failureThreshold` padrão) ×
  `periodSeconds: 10`. O kubelet mata o **container**, não o pod: o nome do pod
  e a idade continuam, só `RESTARTS` sobe.
- **`Killing` 3 vezes, `Started` 4 vezes:** a primeira subida mais 3 restarts.
- **`0/1` logo depois de cada restart:** o uvicorn ainda não está escutando, então
  a readiness falha com `connection refused` e o pod sai do Service por alguns
  segundos. Assim que o app sobe, `/readyz` responde e o pod volta a `1/1`.
- **Por que `500` além de `404`?** O release estava com chaos ligado
  (`chaos.errorRate=0.3`). O middleware do app só poupa do chaos as rotas em
  `_UNOBSERVED` (`/metrics`, `/healthz`, `/readyz`). O `/nao-existe` não está
  nessa lista, então cerca de 30% das probes levaram `500` antes mesmo de chegar
  no `404`. Para a probe, não importa: qualquer código fora de 200–399 é falha.
- **`CrashLoopBackOff` quase não aparece aqui:** o container não quebra sozinho,
  ele roda ~30s até a liveness matá-lo. Cada morte conta para o backoff, mas o
  status `CrashLoopBackOff` só aparece durante a espera entre uma morte e a
  próxima subida, que no começo é curta. Num container que quebra logo ao subir
  (erro de import, config faltando), ele aparece bem mais.

### Liveness × readiness

| | Liveness | Readiness |
|---|---|---|
| Pergunta | O processo travou? | Pode receber tráfego agora? |
| Se falhar | kubelet **reinicia o container** | pod sai dos endpoints do Service, **sem restart** |
| Aqui | quebrada de propósito → restarts | OK → `1/1` na maior parte do tempo |

As duas são independentes. Por isso o pod aparecia "pronto" e ao mesmo tempo era
reiniciado a cada 30s. Uma liveness mal configurada (path errado, timeout curto
demais, dependência externa no health check) vira um loop de restarts num app que
estava saudável. Essa é uma causa clássica de incidente.

### Limpeza

```powershell
kubectl -n pet rollout undo deployment/kubegotchi
helm upgrade kubegotchi charts/kubegotchi -n pet --reuse-values --set chaos.errorRate=0 --set chaos.latencyMs=0
```

Conferido depois: liveness de volta em `/healthz`, `CHAOS_*` em `0`, pod
`1/1 Running` com 0 restarts.

---

## Exercício 5: OOMKilled

O bichinho usa cerca de **35Mi** em repouso (`kubectl -n pet top pod`), com limit
de 128Mi. O experimento dá a ele 24Mi, menos do que ele precisa só para subir.
Desta vez a mudança vai pelo Helm, porque os resources já estão no `values.yaml`.
O request baixa junto, porque o Kubernetes não aceita request maior que limit.

```powershell
# Terminal 1
kubectl -n pet get pods -w

# Terminal 2
helm upgrade kubegotchi charts/kubegotchi -n pet --reuse-values --set resources.limits.memory=24Mi --set resources.requests.memory=24Mi
```

### O que apareceu

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

### O que isso significa

- **Morre em cerca de 1s** (`Started 17:07:13` → `Finished 17:07:14`): o Python
  passa de 24Mi ainda carregando o FastAPI. O kernel mata o processo na hora, sem
  aviso nem SIGTERM.
- **`Exit Code: 137`** = 128 + 9 (SIGKILL). Sempre que aparecer 137, a primeira
  suspeita é OOM.
- **Agora sim `CrashLoopBackOff`:** o container quebra sozinho ao subir. O
  backoff dobra a cada tentativa: a espera foi de ~10s, depois ~20s, e iria
  até 5min.
- **O pod antigo (`lhrk4`) ficou `1/1 Running` o tempo todo.** Com 1 réplica, o
  rolling update padrão (`maxUnavailable: 25%`, que arredonda para 0) só derruba
  o antigo quando o novo fica pronto. O deploy quebrou, mas o bichinho continuou
  atendendo. O `rollout status` mostra exatamente isso: a troca travada esperando.
- **O Helm disse `Upgrade complete` / `deployed` mesmo com o pod quebrando.** Por
  padrão o Helm só aplica os manifestos e não espera os pods ficarem prontos.
  Para ele esperar e falhar de verdade, use `--wait`, ou `--rollback-on-failure`
  para voltar sozinho. Isso importa num pipeline de CI/CD: sem `--wait`, um deploy
  quebrado aparece como verde.

### Consertar (rollback pelo Helm)

```powershell
helm history kubegotchi -n pet
helm rollback kubegotchi 5 -n pet
```

O rollback não apaga a revisão 6: ele cria a **revisão 7** com o conteúdo da 5
(`DESCRIPTION: Rollback to 5`). Conferido depois: resources de volta a
128Mi/64Mi e o pod `lhrk4` intacto, com 0 restarts, porque ele nunca saiu do ar.

### `kubectl rollout undo` × `helm rollback`

| | `kubectl rollout undo` | `helm rollback` |
|---|---|---|
| Volta o quê | só o template do Deployment | todos os recursos do release (Deployment, Service...) |
| Histórico | ReplicaSets antigos | revisões do Helm (values + manifestos) |
| O Helm fica sabendo? | **não**: o próximo `helm upgrade` reaplica os values dele | **sim**: vira uma revisão nova |

Quando o deploy foi feito pelo Helm, o rollback também deve ser pelo Helm. O
`rollout undo` no exercício 4 funcionou porque o estrago também tinha sido feito
por fora do Helm (`kubectl patch`).

## Ingress em `localhost:8080`

_A fazer._

## HorizontalPodAutoscaler

_A fazer._
