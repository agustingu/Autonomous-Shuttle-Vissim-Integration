"""
extract_node_link.py -- Table IV data (node delay/stops, link speed, lane
changes) for every batch run, WITHOUT re-running Vissim.

Sources:
  * Node analysis: the run's SQLite result database (<stem>.results\<runNo>.db,
    or the copy that run_batch.py leaves in the run folder; table
    MOVEMENT_EvaluationTimeIntervalClass; node-total rows have a plain numeric
    OBJECT_ID; vehicle classes ALL / bus / shuttle as configured).
  * Link analysis: space-mean speed from the .fzp trajectories
    (sum distance / sum time per link == Vissim link-result speed), per class.
  * Lane changes: LnChg events from the .fzp (same events as the .spw file the
    published analysis used), reported as total, per vehicle, and per veh-km.

Run numbers are linked to (scenario, composition, seed) via the .fzp filename
in each seed folder. Truncated runs are flagged, not dropped.

Output: <batch_root>/node_link_by_run.csv (one row per run)
Usage:  python extract_node_link.py [batch_root]
"""
import os
import re
import glob
import sqlite3

import numpy as np
import pandas as pd

import config as C

# defaults; overridable from the command line (see main)
BATCH = C.OUT_ROOT
RESULTS_DIR = C.RESULTS_DIR
OUT = os.path.join(BATCH, "node_link_by_run.csv")

AS_TYPE, BUS_TYPE = C.AS_TYPE, C.BUS_TYPE
CLASSES = C.NODE_CLASSES
SIM_COMPLETE_MIN_S = C.SIM_COMPLETE_MIN_S

FZP_IDX = {"SIMSEC": 0, "NO": 1, "LINK": 2, "VEHTYPE": 4, "SPEED": 11,
           "LNCHG": 14}


def run_map():
    """{run_no: (scenario, composition, seed, fzp_path)}"""
    out = {}
    for fzp in glob.glob(os.path.join(BATCH, "*", "*", "seed_*", "*.fzp")):
        parts = os.path.relpath(fzp, BATCH).replace("\\", "/").split("/")
        num = re.search(r"_(\d+)\.fzp$", fzp)
        if len(parts) == 4 and num and not parts[0].startswith("_"):
            out[int(num.group(1))] = (parts[0], parts[1], parts[2][5:], fzp)
    return out


def node_metrics(run_no, run_dir=None):
    """Vehicle-weighted node delay & stops per class from the run DB.
    A copy of the DB inside the run folder (written by run_batch.copy_run_db)
    takes precedence over the live .results folder."""
    db = os.path.join(RESULTS_DIR, f"{run_no}.db")
    if run_dir and os.path.exists(os.path.join(run_dir, f"{run_no}.db")):
        db = os.path.join(run_dir, f"{run_no}.db")
    row = {}
    if not os.path.exists(db):
        row["node_db_found"] = False
        return row
    con = sqlite3.connect(db)
    try:
        df = pd.read_sql_query(
            "SELECT ARG_VEHICLECLASS c, OBJECT_ID oid, VEHS, VEHDELAY, STOPS "
            "FROM MOVEMENT_EvaluationTimeIntervalClass", con)
    except Exception:
        # Vissim purges the stored result tables of all previous runs when a
        # new simulation starts after a network change -> use the backup copy
        con.close()
        row["node_db_found"] = False
        return row
    con.close()
    row["node_db_found"] = True
    # node-total rows have plain numeric OBJECT_ID ('1','2','3');
    # movement rows look like '1-7-259.51...-6-25.83...'
    tot = df[df.oid.str.fullmatch(r"\d+")]
    for cname, ccode in CLASSES.items():
        sub = tot[tot.c == ccode]
        w = sub.VEHS.astype(float)
        if len(sub) and w.sum() > 0:
            row[f"node_delay_{cname}"] = float(
                (sub.VEHDELAY * w).sum() / w.sum())
            row[f"node_stops_{cname}"] = float(
                (sub.STOPS * w).sum() / w.sum())
            row[f"node_vehs_{cname}"] = float(w.sum())
        else:
            row[f"node_delay_{cname}"] = np.nan
            row[f"node_stops_{cname}"] = np.nan
            row[f"node_vehs_{cname}"] = 0.0
    return row


def link_metrics(fzp):
    """Link speeds per class + lane-change stats from one .fzp.

    Per-link space-mean speed = sum(speed*1s)/sum(1s) over rows on the link
    (== Vissim link-result speed: distance/time). The network 'link speed'
    reported (Table IV style) is the unweighted mean across links, matching
    the link-analysis convention. The vehicle-time-pooled network speed is
    also kept (congestion-weighted, always lower).
    """
    per_link = {}  # link -> {class: [sum_speed, n]}
    lc_events = 0
    veh_prev_lnchg = {}
    veh_ids = set()
    total_rows = {"all": 0, "bus": 0, "shuttle": 0}
    total_speed = {"all": 0.0, "bus": 0.0, "shuttle": 0.0}
    maxsim = 0.0
    with open(fzp, errors="ignore") as f:
        for line in f:
            if not line[:1].isdigit():
                continue
            p = line.rstrip("\n").split(";")
            if len(p) < 15:
                continue
            try:
                simsec = float(p[FZP_IDX["SIMSEC"]])
                vt = int(p[FZP_IDX["VEHTYPE"]])
                v = float(p[FZP_IDX["SPEED"]])
            except ValueError:
                continue
            if simsec > maxsim:
                maxsim = simsec
            veh = p[FZP_IDX["NO"]]
            link = p[FZP_IDX["LINK"]]
            veh_ids.add(veh)
            cls = ("shuttle" if vt == AS_TYPE
                   else "bus" if vt == BUS_TYPE else None)
            acc = per_link.setdefault(link, {"all": [0.0, 0],
                                             "bus": [0.0, 0],
                                             "shuttle": [0.0, 0]})
            for c in ("all", cls) if cls else ("all",):
                total_rows[c] += 1
                total_speed[c] += v
                acc[c][0] += v
                acc[c][1] += 1
            # lane-change event = LnChg transitions from empty/None to a value
            lnchg = p[FZP_IDX["LNCHG"]].strip()
            active = lnchg not in ("", "None")
            if active and not veh_prev_lnchg.get(veh, False):
                lc_events += 1
            veh_prev_lnchg[veh] = active
    row = {"sim_end_s": maxsim}
    for c in ("all", "bus", "shuttle"):
        means = [acc[c][0] / acc[c][1] for acc in per_link.values()
                 if acc[c][1] > 0]
        row[f"link_speed_{c}"] = float(np.mean(means)) if means else np.nan
        row[f"net_speed_{c}"] = (total_speed[c] / total_rows[c]
                                 if total_rows[c] else np.nan)
    veh_km = total_speed["all"] / 3600.0  # sum(v[km/h] * 1s) / 3600 = km
    row["lc_events_total"] = lc_events
    row["lc_per_veh"] = lc_events / len(veh_ids) if veh_ids else np.nan
    row["lc_per_vehkm"] = lc_events / veh_km if veh_km else np.nan
    row["veh_km_total"] = veh_km
    row["n_vehicles"] = len(veh_ids)
    return row


def main():
    rm = run_map()
    print(f"{len(rm)} runs mapped")
    rows = []
    for run_no, (sc, comp, seed, fzp) in sorted(rm.items()):
        r = {"run_no": run_no, "scenario": sc, "composition": comp,
             "seed": int(seed)}
        r.update(node_metrics(run_no, os.path.dirname(fzp)))
        r.update(link_metrics(fzp))
        r["complete"] = r["sim_end_s"] >= SIM_COMPLETE_MIN_S
        rows.append(r)
        print(f"  run {run_no:>3} {sc} {comp} seed_{seed}: "
              f"delay={r.get('node_delay_all', float('nan')):.2f} "
              f"speed={r['link_speed_all']:.2f} "
              f"lc/vehkm={r['lc_per_vehkm']:.2f} "
              f"{'OK' if r['complete'] else 'TRUNCATED'}")
    df = pd.DataFrame(rows).sort_values(["scenario", "composition", "seed"])
    df.to_csv(OUT, index=False)
    print(f"\n{len(df)} runs -> {OUT}")
    inc = df[~df.complete]
    if len(inc):
        print(f"flagged incomplete (exclude from analysis): "
              f"{[tuple(x) for x in inc[['scenario','composition','seed']].values]}")
    nodb = df[~df.node_db_found] if "node_db_found" in df else []
    if len(nodb):
        print(f"runs with NO results DB: "
              f"{[tuple(x) for x in nodb[['scenario','composition','seed']].values]}")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("batch_root", nargs="?", default=BATCH,
                    help="folder with <scenario>/<composition>/seed_*/ (default: output_root of the config)")
    ap.add_argument("--results-dir", default=RESULTS_DIR,
                    help="Vissim .results folder holding <runNo>.db (default: "
                         "<network stem>.results next to the .inpx; the copy "
                         "inside each run folder is preferred when present)")
    ap.add_argument("--out", default=None,
                    help="output csv (default <batch_root>/node_link_by_run.csv)")
    a = ap.parse_args()
    BATCH = os.path.abspath(a.batch_root)
    RESULTS_DIR = os.path.abspath(a.results_dir)
    OUT = a.out or os.path.join(BATCH, "node_link_by_run.csv")
    main()
