CLUSTER := kubegotchi
# New tag on every run: with a fixed tag the Deployment spec doesn't change
# and Helm doesn't roll out the new code. To pin it: make deploy TAG=abc
# The tag goes through --set-string: with --set Helm reads the timestamp as a
# number and, on an upgrade --reuse-values, renders it as 1.79e+09 (InvalidImageName).
# Via sh -c so it also works on Windows (Git for Windows sh on the PATH)
TAG     := $(shell sh -c 'date +%s')
IMAGE   := kubegotchi:$(TAG)

ARGOCD_CHART_VERSION := 10.10.5

.PHONY: run run-docker test cluster build load deploy up pet feed play sleep argocd argocd-app argocd-password down

## Phase 0: run the pet directly on your machine
run:
	cd app && pip install -r requirements.txt && uvicorn main:app --reload

## Phase 0 without local Python: same image that goes to the cluster (Ctrl+C to stop)
run-docker:
	docker build -t kubegotchi:dev app
	docker run --rm -it -p 8000:8000 kubegotchi:dev

## Unit tests in a container (no local Python needed).
## MSYS_NO_PATHCONV keeps Git for Windows sh from rewriting "/app".
test:
	MSYS_NO_PATHCONV=1 docker run --rm -v "$(CURDIR)/app:/app:ro" -w /app \
		-e PYTHONDONTWRITEBYTECODE=1 python:3.12-slim \
		sh -c "pip install -q --root-user-action=ignore -r requirements-dev.txt && pytest -q -p no:cacheprovider"

## Phase 1: create the local cluster
cluster:
	k3d cluster create --config k3d/cluster.yaml

## Build the image and push it into the cluster (no registry)
build:
	docker build -t $(IMAGE) app
load: build
	k3d image import $(IMAGE) -c $(CLUSTER)

## Install or upgrade the chart with a locally built image.
## Once Argo CD manages the app (make argocd-app), selfHeal reverts this: use Git instead.
deploy: load
	helm upgrade --install kubegotchi charts/kubegotchi --namespace pet --create-namespace \
		--set image.repository=kubegotchi --set-string image.tag=$(TAG)

## Everything at once
up: cluster deploy

## Talk to the pet (leave 'make pet' running in another terminal)
pet:
	kubectl -n pet port-forward svc/kubegotchi 8000:80
feed:
	curl -s -X POST localhost:8000/feed; echo
play:
	curl -s -X POST localhost:8000/play; echo
sleep:
	curl -s -X POST localhost:8000/sleep; echo

## Phase 2: install or upgrade Argo CD (UI at http://argocd.localhost:8080)
argocd:
	helm repo add argo https://argoproj.github.io/argo-helm --force-update
	helm upgrade --install argocd argo/argo-cd --version $(ARGOCD_CHART_VERSION) \
		--namespace argocd --create-namespace -f gitops/argocd/values.yaml --wait --timeout 5m

## Hand the pet over to Argo CD. Deleting the Helm release secrets only makes
## Helm forget the release: the running resources stay and Argo CD adopts them.
argocd-app:
	kubectl apply -f gitops/apps/kubegotchi.yaml
	kubectl -n pet delete secret -l owner=helm,name=kubegotchi --ignore-not-found

## Initial admin password (change it in the UI and delete the secret afterwards)
argocd-password:
	@kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d; echo

down:
	k3d cluster delete $(CLUSTER)
