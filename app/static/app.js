// Kubegotchi frontend.
// Two layers: a data source (where the pet comes from) and the drawing (the
// device and the vet chart). Phase 4's demo mode swaps the source for a
// recording without touching the drawing.

"use strict";

const POLL_MS = 5000;

// ---------------------------------------------------------------------------
// Data source: the live cluster. Relative URLs, so the page works under any path.
// ---------------------------------------------------------------------------
const liveSource = {
  kind: "live",
  async read() {
    const [status, vitals] = await Promise.all([getJson("status"), getJson("vitals")]);
    return { status, vitals, at: new Date() };
  },
  async care(action) {
    const r = await fetch(action, { method: "POST" });
    const body = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(body.detail || `${action} failed with ${r.status}`);
    return body;
  },
};

async function getJson(path) {
  const r = await fetch(path, { cache: "no-store" });
  const body = await r.json().catch(() => ({}));
  // A 503 still carries a reason worth showing (e.g. Prometheus unreachable)
  return { ok: r.ok, code: r.status, ...body };
}

// ---------------------------------------------------------------------------
// The pet, in pixels. 16x14 body, faces drawn on top. '#' is an ink pixel.
// ---------------------------------------------------------------------------
const BODY = [
  ".....######.....",
  "...##......##...",
  "..#..........#..",
  ".#............#.",
  ".#............#.",
  "#..............#",
  "#..............#",
  "#..............#",
  "#..............#",
  ".#............#.",
  ".#............#.",
  "..##........##..",
  "....########....",
  "...##......##...",
];

// [x, y] pixels relative to the body's top-left corner
const FACES = {
  happy: [[5, 5], [10, 5], [5, 8], [10, 8], [6, 9], [7, 9], [8, 9], [9, 9]],
  ok: [[5, 5], [10, 5], [6, 9], [7, 9], [8, 9], [9, 9]],
  sad: [[5, 5], [10, 5], [6, 8], [7, 8], [8, 8], [9, 8], [5, 9], [10, 9]],
  sick: [
    // x x eyes
    [4, 4], [6, 4], [5, 5], [4, 6], [6, 6],
    [9, 4], [11, 4], [10, 5], [9, 6], [11, 6],
    // wavy mouth
    [5, 9], [6, 8], [7, 9], [8, 8], [9, 9], [10, 8],
  ],
};

// Thermometer, drawn at the right edge of the screen when burning budget fast
const THERMOMETER = [[0, 0], [0, 1], [0, 2], [0, 3], [0, 4], [-1, 5], [0, 5], [1, 5], [-1, 6], [0, 6], [1, 6]];
// Bandage (a small plus) at the left edge of the screen when it restarted recently
const BANDAGE = [[1, 0], [0, 1], [1, 1], [2, 1], [1, 2]];

const canvas = document.getElementById("lcd");
const ctx = canvas.getContext("2d");
const rootStyle = getComputedStyle(document.documentElement);
const INK = rootStyle.getPropertyValue("--ink").trim() || "#4a2d4f";
const BLUSH = rootStyle.getPropertyValue("--blush").trim() || "#f28cb1";
// Two pixels per cheek, on the row between the eyes and the mouth
const CHEEKS = [[2, 7], [3, 7], [12, 7], [13, 7]];
const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

let face = "ok";
let fever = false;
let injured = false;
let frame = 0;
let flash = null; // a short-lived icon answering a button press

function px(x, y) { ctx.fillRect(x, y, 1, 1); }

function draw() {
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.fillStyle = INK;
  // Two-frame idle bob, like the real toy; still when motion is reduced
  const bob = reducedMotion.matches ? 0 : frame % 2;
  const ox = 8;
  const oy = 4 + bob;

  BODY.forEach((row, y) => [...row].forEach((c, x) => c === "#" && px(ox + x, oy + y)));
  FACES[face].forEach(([x, y]) => px(ox + x, oy + y));
  // Blushing cheeks while it's doing fine; a sick pet loses its color
  if (face === "happy" || face === "ok") {
    ctx.fillStyle = BLUSH;
    CHEEKS.forEach(([x, y]) => px(ox + x, oy + y));
    ctx.fillStyle = INK;
  }
  // Status icons on the screen's sides, like the real toy: a 16px face has no
  // room for a bandage without blurring into the outline
  if (injured) BANDAGE.forEach(([x, y]) => px(2 + x, 10 + y));
  if (fever) THERMOMETER.forEach(([x, y]) => px(28 + x, 6 + y));
  if (flash) drawFlash(flash);
}

// Tiny icons that answer Feed / Play / Sleep in the top-left corner
const FLASH_ICONS = {
  feed: [[1, 0], [2, 0], [0, 1], [1, 1], [2, 1], [3, 1], [0, 2], [1, 2], [2, 2], [3, 2], [1, 3], [2, 3]],
  play: [[1, 0], [2, 0], [0, 1], [3, 1], [0, 2], [3, 2], [1, 3], [2, 3]],
  sleep: [[0, 0], [1, 0], [2, 0], [1, 1], [0, 2], [1, 2], [2, 2]],
};
function drawFlash(action) { FLASH_ICONS[action].forEach(([x, y]) => px(2 + x, 2 + y)); }

setInterval(() => { frame += 1; draw(); }, 700);
// ---------------------------------------------------------------------------
// Turning readings into the pet
// ---------------------------------------------------------------------------
const CONDITION_FACE = { healthy: "happy", unwell: "ok", ill: "sad", critical: "sick" };
const FACE_RANK = ["sick", "sad", "ok", "happy"];
const CONDITION_TEXT = {
  healthy: "Healthy",
  unwell: "Unwell",
  ill: "Ill",
  critical: "Critical",
  unknown: "No readings yet",
};

// The pet looks as bad as the worse of its two views: care and cluster
function worseFace(a, b) {
  return FACE_RANK.indexOf(a) <= FACE_RANK.indexOf(b) ? a : b;
}

const VITAL_ROWS = [
  ["health", "Health"],
  ["mood", "Mood"],
  ["energy", "Energy"],
  ["hunger", "Hunger"],
  ["injuries", "Injuries"],
];
const CARE_ROWS = [
  ["hunger", "Fed"],
  ["happiness", "Happiness"],
  ["energy", "Rested"],
];

function meter(value) {
  const el = document.createElement("div");
  el.className = "meter";
  el.setAttribute("aria-hidden", "true");
  const on = value == null ? 0 : Math.round(value / 10);
  if (value == null) el.classList.add("none");
  else if (value < 15) el.classList.add("low");
  for (let i = 0; i < 10; i++) {
    const s = document.createElement("span");
    if (i < on) s.className = "on";
    el.append(s);
  }
  return el;
}

function row(label, value, why) {
  const wrap = document.createElement("div");
  wrap.className = "reading";
  const dt = document.createElement("dt");
  dt.textContent = label;
  const bar = document.createElement("dd");
  bar.className = "bar";
  bar.append(meter(value));
  const num = document.createElement("dd");
  num.className = "value";
  num.textContent = value == null ? "–" : Math.round(value);
  wrap.append(dt, bar, num);
  if (why) {
    const w = document.createElement("dd");
    w.className = "why";
    w.textContent = why;
    wrap.append(w);
  }
  return wrap;
}

function render({ status, vitals, at }, source) {
  const name = (status.ok && status.name) || (vitals.ok && vitals.name) || "Kubegotchi";
  // The header always says Kubegotchi; the pet's name lives on the LCD and the tab
  document.title = `${name} | Kubegotchi`;

  // Vet chart: what the cluster says
  const vitalsEl = document.getElementById("vitals");
  const conditionEl = document.getElementById("condition");
  if (vitals.ok) {
    vitalsEl.replaceChildren(...VITAL_ROWS.map(([k, label]) =>
      row(label, vitals.vitals[k].value, vitals.vitals[k].reading)));
    const parts = [CONDITION_TEXT[vitals.condition] || vitals.condition];
    if (vitals.fever) parts.push(`fever: burning its error budget ${vitals.burn_rate}x too fast`);
    conditionEl.textContent = parts.join(", ");
    conditionEl.classList.toggle("alert", vitals.condition === "critical" || vitals.fever);
  } else {
    vitalsEl.replaceChildren();
    conditionEl.textContent = `The vet can't read the cluster: ${vitals.detail || `error ${vitals.code}`}`;
    conditionEl.classList.add("alert");
  }

  // What its owners did
  const careEl = document.getElementById("care");
  if (status.ok) {
    careEl.replaceChildren(...CARE_ROWS.map(([k, label]) => row(label, status.stats[k])));
  } else {
    careEl.replaceChildren();
  }

  // The pet on the LCD
  const careFace = status.ok ? status.mood : "sick";
  const clusterFace = vitals.ok ? (CONDITION_FACE[vitals.condition] || "ok") : "sick";
  face = worseFace(careFace, clusterFace);
  fever = Boolean(vitals.ok && vitals.fever);
  injured = Boolean(vitals.ok && vitals.vitals.injuries.value != null && vitals.vitals.injuries.value < 100);
  canvas.setAttribute("aria-label", `${name} looks ${face}`);
  setLcdLine(`${name} is ${face}`);
  draw();

  const sourceEl = document.getElementById("source");
  if (source.kind === "demo") {
    sourceEl.textContent = `Recording, ${at.toLocaleString([], { dateStyle: "medium", timeStyle: "short" })}`;
    sourceEl.className = "source recorded";
  } else {
    sourceEl.textContent = `Live, updated ${at.toLocaleTimeString()}`;
    sourceEl.className = "source live";
  }
}

function setLcdLine(text) { document.getElementById("lcd-line").textContent = text; }

// ---------------------------------------------------------------------------
// Buttons
// ---------------------------------------------------------------------------
const PAST = { feed: "Fed", play: "Played with", sleep: "Put to sleep" };

function wireButtons(source) {
  document.querySelectorAll(".key").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const action = btn.dataset.action;
      const hint = document.getElementById("hint");
      btn.disabled = true;
      try {
        const status = await source.care(action);
        flash = action;
        draw();
        setTimeout(() => { flash = null; draw(); }, 1200);
        hint.textContent = `${PAST[action]} ${status.name}.`;
        await refresh(source);
      } catch (e) {
        hint.textContent = e.message;
      } finally {
        btn.disabled = false;
      }
    });
  });
}

// ---------------------------------------------------------------------------
// Polling
// ---------------------------------------------------------------------------
async function refresh(source) {
  try {
    render(await source.read(), source);
  } catch (e) {
    const el = document.getElementById("source");
    el.textContent = "Can't reach the pet. Is the cluster running?";
    el.className = "source live down";
  }
}

function start(source) {
  wireButtons(source);
  draw();
  refresh(source);
  setInterval(() => refresh(source), POLL_MS);
}

// ---------------------------------------------------------------------------
// Demo: a recording of a real chaos experiment instead of the live cluster.
// Used on GitHub Pages (no cluster behind it) or with ?demo in the URL.
// ---------------------------------------------------------------------------
const RECORDING = "static/demo/chaos-2026-10-20.json";
const PLAY_MS = 350;

function demoSource(recording) {
  return {
    kind: "demo",
    index: 0,
    recording,
    read() {
      const f = recording.frames[this.index];
      return { status: f.status, vitals: f.vitals, at: new Date(f.at) };
    },
    async care() {
      throw new Error("This is a recording, so the buttons are off. Run the cluster to take care of a live pet.");
    },
  };
}

function frameAt(recording, iso) {
  const t = Date.parse(iso);
  let best = 0;
  recording.frames.forEach((f, i) => { if (Date.parse(f.at) <= t) best = i; });
  return best;
}

function startDemo(source, firstFrame = 0) {
  const { recording } = source;
  const range = document.getElementById("tape-range");
  const play = document.getElementById("tape-play");
  const marks = document.getElementById("tape-marks");
  document.getElementById("tape").hidden = false;
  document.getElementById("tape-title").textContent = recording.title;

  document.querySelectorAll(".key").forEach((k) => { k.disabled = true; });
  document.getElementById("hint").textContent =
    "A recording of a real chaos experiment. Press play or pick a moment below.";

  const markIndexes = recording.markers.map((m) => frameAt(recording, m.at));
  marks.replaceChildren(...recording.markers.map((m, n) => {
    const li = document.createElement("li");
    const btn = document.createElement("button");
    btn.type = "button";
    const time = document.createElement("time");
    time.dateTime = m.at;
    time.textContent = new Date(m.at).toLocaleTimeString([], { timeStyle: "short" });
    btn.append(time, m.label);
    btn.addEventListener("click", () => { stop(); seek(markIndexes[n]); });
    li.append(btn);
    return li;
  }));

  range.max = String(recording.frames.length - 1);

  function seek(i) {
    source.index = i;
    range.value = String(i);
    // The latest milestone already reached is the current step
    let current = -1;
    markIndexes.forEach((mi, n) => { if (mi <= i) current = n; });
    [...marks.children].forEach((li, n) => {
      if (n === current) li.setAttribute("aria-current", "step");
      else li.removeAttribute("aria-current");
    });
    refresh(source);
  }

  let timer = null;
  function stop() {
    clearInterval(timer);
    timer = null;
    play.textContent = "Play";
    play.setAttribute("aria-pressed", "false");
  }
  play.addEventListener("click", () => {
    if (timer) return stop();
    if (source.index >= recording.frames.length - 1) seek(0);
    play.textContent = "Pause";
    play.setAttribute("aria-pressed", "true");
    timer = setInterval(() => {
      if (source.index >= recording.frames.length - 1) return stop();
      seek(source.index + 1);
    }, PLAY_MS);
  });
  range.addEventListener("input", () => { stop(); seek(Number(range.value)); });

  draw();
  seek(Math.min(Math.max(firstFrame, 0), recording.frames.length - 1));
}

async function boot() {
  const params = new URLSearchParams(location.search);
  const demo = params.has("demo") || location.hostname.endsWith("github.io");
  if (!demo) return start(liveSource);
  try {
    const r = await fetch(RECORDING);
    if (!r.ok) throw new Error(`recording not found (${r.status})`);
    // ?frame=N opens a given moment, so a moment of the recording can be linked
    startDemo(demoSource(await r.json()), Number(params.get("frame")) || 0);
  } catch (e) {
    const el = document.getElementById("source");
    el.textContent = `Can't load the recording: ${e.message}`;
    el.className = "source live down";
  }
}

boot();
