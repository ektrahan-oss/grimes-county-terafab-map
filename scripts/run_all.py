"""Run the map's data scripts from one place.

    python scripts/run_all.py --frequent     daily: stream gauges, news feed and the document, permit and entity watches,
                                             then the Permits & filings layer, rebuilt from what the permit watch found
    python scripts/run_all.py --all          monthly: the daily ones plus every layer that refreshes itself
                                             (on GitHub the reinvestment zone is skipped)
    python scripts/run_all.py --only NAME    one layer, for example --only pipelines

Yearly sources ride along in the monthly run. Population and housing checks for a new survey
release and does nothing if there is none; school enrollment picks up a new school year when
the state adds it.

Three layers are only run by hand, with --only: holdings (when checking for new purchases),
land (when a new parcel release is out) and drive (a one-time layer). Parcel lines have their
own script, build_parcel_tiles.py, also run by hand.

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
    "watersheds": (module_main("fetch_watersheds"), False, True),
    "streams":   (module_main("fetch_streams"), False, True),
    "outfalls":  (module_main("fetch_wastewater_outfalls"), False, True),
    "water_rights": (module_main("fetch_water_rights"), False, True),
    "wells":     (module_main("fetch_water_wells"), False, True),
    "aquifers":  (module_main("fetch_aquifers"), False, True),
    "census":    (module_main("fetch_population_housing"), False, True),    # only rebuilds when a new yearly survey is out
    "holdings":  (module_main("fetch_project_holdings"), False, False),  # checked by hand: --only holdings
    "land":      (module_main("build_land_values"), False, False),       # by hand when a new parcel release is out: --only land
    "drive":     (module_main("build_drive_times"), False, False),      # one-time layer: --only drive
    "gauges":    (module_main("build_stream_gauges"), True, True),         # river flow and reservoir level move daily
    "news":      (module_main("build_news"), True, True),
    "documents": (module_main("watch_documents"), True, True),
    "permits":   (module_main("watch_permits"), True, True),
    "filings":   (module_main("build_permits_filings"), True, True),   # after permits: puts what the watch found on the map
    "entities":  (module_main("watch_entities"), True, True),
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
    group.add_argument("--frequent", action="store_true", help="stream gauges, news feed and the document, permit and entity watches only")
    group.add_argument("--all", action="store_true", help="every layer except the three run by hand (holdings, land, drive)")
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
