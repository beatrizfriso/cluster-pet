// One "day" of Kubegotchi, compressed into an hour, then it starts over.
//
// visitors: people opening the page, which polls /status and /vitals. Traffic
//           follows a day: quiet at night, busy at noon.
// feed / play / sleep: owners caring at a realistic rate. With the app's
//           60-minute care window and 2 points/minute of decay, these rates
//           keep the stats around 65-85 instead of pinned at 100:
//             hunger    -20 + 25*5 - 5*4          = 85
//             happiness -20 + 25*4 - 5*3          = 65
//             energy    -20 + 5*5 - 10*4 + 40*3   = 85
// spike:    only when SPIKE_RATE > 0 (requests per second on top of the day),
//           to make the HPA scale.
//
// Everything goes through Traefik, where the SLIs are measured. k6's own
// metrics (sent to Prometheus) are what the users saw, including failures that
// never reach Traefik, like the DNS timeouts of the old generator.

import http from "k6/http";
import { check } from "k6";

const TARGET = __ENV.TARGET || "http://traefik.kube-system.svc.cluster.local";
const DAY = "60m";
const SPIKE_RATE = Number(__ENV.SPIKE_RATE || 0);

function careScenario(rate) {
  return {
    executor: "constant-arrival-rate",
    rate,
    timeUnit: "1h",
    duration: DAY,
    preAllocatedVUs: 1,
    exec: "care",
  };
}

const scenarios = {
  visitors: {
    executor: "ramping-arrival-rate",
    // page polls per 10s: 2 at night (0.2/s) up to 15 at noon (1.5/s)
    timeUnit: "10s",
    startRate: 2,
    preAllocatedVUs: 5,
    maxVUs: 30,
    stages: [
      { target: 2, duration: "10m" },  // night
      { target: 10, duration: "10m" }, // morning
      { target: 15, duration: "10m" }, // noon
      { target: 15, duration: "10m" },
      { target: 6, duration: "10m" },  // evening
      { target: 2, duration: "10m" },  // night again
    ],
    exec: "visit",
  },
  feed: { ...careScenario(5), env: { ACTION: "feed" } },
  play: { ...careScenario(4), env: { ACTION: "play" } },
  sleep: { ...careScenario(3), env: { ACTION: "sleep" } },
};

if (SPIKE_RATE > 0) {
  scenarios.spike = {
    executor: "constant-arrival-rate",
    rate: SPIKE_RATE,
    timeUnit: "1s",
    duration: DAY,
    preAllocatedVUs: 20,
    maxVUs: 200,
    exec: "visit",
  };
}

export const options = {
  scenarios,
  // Short summary; the real numbers go to Prometheus
  summaryTrendStats: ["avg", "p(95)", "p(99)"],
};

export function visit() {
  const [status, vitals] = http.batch([
    ["GET", `${TARGET}/status`, null, { tags: { name: "status" } }],
    ["GET", `${TARGET}/vitals`, null, { tags: { name: "vitals" } }],
  ]);
  check(status, { "status answered": (r) => r.status === 200 });
  check(vitals, { "vitals answered": (r) => r.status === 200 });
}

export function care() {
  const action = __ENV.ACTION;
  const r = http.post(`${TARGET}/${action}`, null, { tags: { name: action } });
  check(r, { "care accepted": (res) => res.status === 200 });
}
