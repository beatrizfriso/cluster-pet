CLUSTER := kubegotchi
# Tag nova a cada execução: com uma tag fixa o spec do Deployment não muda
# e o Helm não faz rollout do código novo. Para fixar: make deploy TAG=abc
# A tag vai com --set-string: com --set o Helm lê o timestamp como número e,
# num upgrade --reuse-values, renderiza como 1.79e+09 (InvalidImageName).
# Via sh -c para funcionar também no Windows (sh do Git for Windows no PATH)
TAG     := $(shell sh -c 'date +%s')
IMAGE   := kubegotchi:$(TAG)

.PHONY: run test cluster build load deploy up pet feed play sleep down

## Fase 0: roda o bichinho direto na sua máquina
run:
	cd app && pip install -r requirements.txt && uvicorn main:app --reload

## Testes unitários em container (não precisa de Python local).
## MSYS_NO_PATHCONV evita que o sh do Git for Windows reescreva o "/app".
test:
	MSYS_NO_PATHCONV=1 docker run --rm -v "$(CURDIR)/app:/app:ro" -w /app \
		-e PYTHONDONTWRITEBYTECODE=1 python:3.12-slim \
		sh -c "pip install -q --root-user-action=ignore -r requirements-dev.txt && pytest -q -p no:cacheprovider"

## Fase 1: cria o cluster local
cluster:
	k3d cluster create --config k3d/cluster.yaml

## Faz build da imagem e envia para dentro do cluster (sem registry)
build:
	docker build -t $(IMAGE) app
load: build
	k3d image import $(IMAGE) -c $(CLUSTER)

## Instala ou atualiza o chart
deploy: load
	helm upgrade --install kubegotchi charts/kubegotchi --namespace pet --create-namespace \
		--set-string image.tag=$(TAG)

## Tudo de uma vez
up: cluster deploy

## Conversar com o bichinho (deixe 'make pet' rodando em outro terminal)
pet:
	kubectl -n pet port-forward svc/kubegotchi 8000:80
feed:
	curl -s -X POST localhost:8000/feed; echo
play:
	curl -s -X POST localhost:8000/play; echo
sleep:
	curl -s -X POST localhost:8000/sleep; echo

down:
	k3d cluster delete $(CLUSTER)
