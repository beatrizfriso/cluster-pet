"""Record what the pet looked like over a past time range, for the demo page.

Replays the app's own logic (_care_counts, _compute_stats, _vitals) against
Prometheus at each moment, so the recording is exactly what /status and
/vitals would have answered then. Not part of the image.

Usage (Prometheus reachable at PROMETHEUS_URL, e.g. via kubectl port-forward):
  python record_demo.py --start 2026-10-20T23:25:00Z --end 2026-10-21T00:25:00Z \
      --step 30 --title "..." --marker "2026-10-20T23:38:45Z=Chaos on" ... \
      --out static/demo/recording.json
"""

import argparse
import json
from datetime import datetime, timezone

import main


def parse_time(value: str) -> float:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def frame(at: float) -> dict:
    stats = main._compute_stats(main._care_counts(at))
    return {
        "at": datetime.fromtimestamp(at, timezone.utc).isoformat().replace("+00:00", "Z"),
        "status": {
            "ok": True,
            "name": main.PET_NAME,
            "mood": main._mood(stats),
            "stats": {k: round(v, 1) for k, v in stats.items()},
        },
        "vitals": {"ok": True, **main._vitals(at)},
    }


def main_cli() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--start", required=True, help="ISO time, e.g. 2026-10-20T23:25:00Z")
    p.add_argument("--end", required=True)
    p.add_argument("--step", type=int, default=30, help="seconds between frames")
    p.add_argument("--title", required=True)
    p.add_argument("--marker", action="append", default=[],
                   help="TIME=label, shown on the demo timeline (repeatable)")
    p.add_argument("--out", required=True)
    args = p.parse_args()

    start, end = parse_time(args.start), parse_time(args.end)
    frames = []
    t = start
    while t <= end:
        frames.append(frame(t))
        t += args.step

    markers = []
    for m in args.marker:
        when, label = m.split("=", 1)
        markers.append({"at": when, "label": label})

    recording = {"title": args.title, "step_seconds": args.step,
                 "markers": markers, "frames": frames}
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(recording, f, separators=(",", ":"))
    print(f"{len(frames)} frames written to {args.out}")


if __name__ == "__main__":
    main_cli()
