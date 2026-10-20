#!/bin/sh
# Simulated pet owners: ~1.5 requests/second, mostly checking on the pet.
# Goes through Traefik (not straight to the pet's Service) because the SLIs are
# measured at Traefik: traffic that skips it would be invisible to the SLOs.
TARGET="${TARGET:-http://traefik.kube-system.svc.cluster.local}"

while true; do
  # awk's srand() seeds by the second, so loops within one second would repeat
  roll=$(( $(od -An -N1 -tu1 /dev/urandom) % 10 ))
  case "$roll" in
    7) method=POST path=/feed ;;
    8) method=POST path=/play ;;
    9) method=POST path=/sleep ;;
    *) method=GET path=/status ;;
  esac
  code=$(curl -s -o /dev/null -m 5 -X "$method" -w '%{http_code}' "$TARGET$path")
  # Only failures are logged, so `kubectl logs` reads like an incident timeline
  case "$code" in
    2*) ;;
    *) echo "$(date -u +%H:%M:%S) $method $path -> $code" ;;
  esac
  sleep 0.6
done
