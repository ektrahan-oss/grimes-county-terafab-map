"""Run the map's data scripts from one place.

    python scripts/run_all.py --frequent     news feed and document watching only
    python scripts/run_all.py --all          every layer except drive times
                                             (on GitHub the reinvestment zone is skipped too)
    python scripts/run_all.py --only NAME    one layer, for example --only pipelines

If one layer fails, the error is reported and the rest still run. The exit code is 1
if anything failed, so an automated run shows up as failed while still keeping the
layers that worked.
"""

import argparse
import importlib
import json
import os
import sys
import time
import traceback

from common import DATA, describe_change, record_layer, report_change


def module_main(module_name):
    def run():
        importlib.import_module(module_name).main()
    return run


def reinvestment_zone():
    """build_reinvestment_zone.py predates the shared helper, so the runner does that part for it."""
    if os.environ.get("GITHUB_ACTIONS") == "true":
        # TxGIO's download server refuses GitHub's servers (HTTP 403), so the parcel file cannot be
        # fetched there. The zone only changes if the county changes its order, which
        # watch_documents.py reports; when it does, run "--only zone" on your own computer.
        print("  Skipped on GitHub: the parcel download is refused from GitHub's servers. Run by hand when the order changes.")
        return
    zone = importlib.import_module("build_reinvestment_zone")
    out = DATA / "reinvestment_zone.geojson"
    old = json.loads(out.read_text(encoding="utf-8"))["features"] if out.exists() else None

    argv, sys.argv = sys.argv, ["build_reinvestment_zone.py", "--write"]     # it reads its own flags
    try:
        zone.main()
    finally:
        sys.argv = argv

    new = json.loads(out.read_text(encoding="utf-8"))["features"]
    report_change("zone", "Reinvestment zone",
                  describe_change(old, new, ("outline", "outlines"), key=lambda f: f["properties"].get("name")))
    record_layer("zone", zone.ECON_DEV_PAGE, len(new))


# name: (what runs, in --frequent, in --all). The county boundary goes first because other layers clip to it.
LAYERS = {
    "county":    (module_main("fetch_county_boundary"), False, True),
    "zone":      (reinvestment_zone, False, True),
    "traffic":   (module_main("fetch_traffic_counts"), False, True),
    "flood":     (module_main("fetch_floodplains"), False, True),
    "pipelines": (module_main("fetch_pipelines"), False, True),
    "power":     (module_main("fetch_power_lines"), False, True),
    "schools":   (module_main("fetch_school_districts"), False, True),
    "drive":     (module_main("build_drive_times"), False, False),      # one-time layer: --only drive
    "news":      (module_main("build_news"), True, True),
    "documents": (module_main("watch_documents"), True, True),
}


def run(name):
    """Run one layer. Returns (ok, seconds, error message)."""
    print(f"\n=== {name} ===", flush=True)
    started = time.monotonic()
    try:
        LAYERS[name][0]()
        error = None
    except SystemExit as e:                     # scripts stop this way when a source looks wrong
        error = None if e.code in (None, 0) else str(e.code)
    except Exception as e:
        traceback.print_exc()
        error = f"{type(e).__name__}: {e}"
    if error:
        print(f"FAILED {name}: {error}", flush=True)
    return error is None, time.monotonic() - started, error


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--frequent", action="store_true", help="news feed and document watching only")
    group.add_argument("--all", action="store_true", help="every layer except drive times")
    group.add_argument("--only", metavar="NAME", choices=LAYERS, help="one of: " + ", ".join(LAYERS))
    args = ap.parse_args()

    if args.only:
        names = [args.only]
    else:
        names = [n for n, (_, frequent, everything) in LAYERS.items() if (frequent if args.frequent else everything)]

    results = {name: run(name) for name in names}

    print("\n=== Summary ===")
    for name, (ok, seconds, error) in results.items():
        print(f"  {'ok    ' if ok else 'FAILED'}  {name:<10} {seconds:5.0f}s" + (f"  {error.splitlines()[0]}" if error else ""))
    failed = [n for n, (ok, _, _) in results.items() if not ok]
    if failed:
        sys.exit(f"{len(failed)} of {len(results)} failed: {', '.join(failed)}")
    print(f"All {len(results)} ran.")


if __name__ == "__main__":
    main()
