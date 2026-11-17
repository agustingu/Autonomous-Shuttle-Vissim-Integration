"""
Extract AS-specific trajectory metrics from Vissim .fzp vehicle records.

Walks <root>/<scenario>/<composition>/seed_N/*.fzp and produces:
  - metrics_by_run.csv    one row per run (SC, comp, seed + all metrics)
  - gaps_long.csv         follower-gap samples behind AS vs behind buses

Metrics per run:
  * follower gap behind AS vs. behind conventional buses (mean/median/p85)
  * vehicles trapped behind an AS (leader-chain) and dwell times
  * lane-change events within 100 m upstream of an AS vs. elsewhere
  * AS service metrics: travel time, avg speed, stops per AS

Usage:  python extract_metrics.py [batch_root]   (default: output_root of the config)
"""
import os
import sys

import numpy as np
import pandas as pd

import config as C

AS_TYPE, BUS_TYPE = C.AS_TYPE, C.BUS_TYPE
INFLUENCE_ZONE_M = 100.0   # upstream window for lane-change attribution
STOP_SPEED_KMH = 0.5       # below this = stopped
CHAIN_MAX_DEPTH = 25       # max leader-chain walk
SIM_COMPLETE_MIN_S = C.SIM_COMPLETE_MIN_S  # shorter runs are truncated (crash) and EXCLUDED

COLS = ["SimSec", "No", "Link", "Lane", "VehType", "Pos", "PosLat",
        "Spacing", "DesSpeed", "CoordFront", "CoordRear", "Speed",
        "Accel", "LeadTargNo", "LnChg"]


def read_fzp(path):
    """Parse a Vissim .fzp, locating the header line dynamically."""
    with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
        for i, line in enumerate(f):
            if line.startswith("$VEHICLE:"):
                skip = i + 1
                break
        else:
            raise ValueError(f"No $VEHICLE header in {path}")
    df = pd.read_csv(path, sep=";", skiprows=skip, names=COLS,
                     usecols=[c for c in COLS if not c.startswith("Coord")])
    df["LeadTargNo"] = pd.to_numeric(df["LeadTargNo"], errors="coerce")
    return df


def follower_gaps(df):
    """Gap (Vissim Spacing) of vehicles directly following an AS or a bus."""
    leaders = df[["SimSec", "No", "VehType"]].rename(
        columns={"No": "LeadTargNo", "VehType": "LeadType"})
    f = df.merge(leaders, on=["SimSec", "LeadTargNo"], how="inner")
    f = f[f.VehType != AS_TYPE]  # human/bus followers only
    out = {}
    for label, t in (("AS", AS_TYPE), ("Bus", BUS_TYPE)):
        g = f.loc[f.LeadType == t, "Spacing"].dropna()
        out[f"gap_behind_{label}_n"] = len(g)
        out[f"gap_behind_{label}_mean"] = g.mean()
        out[f"gap_behind_{label}_median"] = g.median()
        out[f"gap_behind_{label}_p85"] = g.quantile(0.85) if len(g) else np.nan
    samples = f[f.LeadType.isin([AS_TYPE, BUS_TYPE])][
        ["SimSec", "No", "LeadType", "Spacing", "Speed"]].copy()
    samples["behind"] = np.where(samples.LeadType == AS_TYPE, "AS", "Bus")
    return out, samples


def trapped_behind_as(df):
    """Vehicles whose leader chain resolves to an AS, and their dwell time."""
    as_ids = set(df.loc[df.VehType == AS_TYPE, "No"].unique())
    if not as_ids:
        return {"trapped_veh_count": 0, "trapped_mean_per_sec": 0.0,
                "trapped_dwell_mean_s": np.nan, "trapped_dwell_max_s": np.nan}
    trapped_records = []
    for t, grp in df.groupby("SimSec", sort=False):
        lead = dict(zip(grp.No, grp.LeadTargNo))
        for veh in grp.No:
            if veh in as_ids:
                continue
            cur, depth = veh, 0
            while depth < CHAIN_MAX_DEPTH:
                nxt = lead.get(cur)
                if nxt is None or np.isnan(nxt) or nxt not in lead:
                    break
                if nxt in as_ids:
                    trapped_records.append((veh, t))
                    break
                cur, depth = nxt, depth + 1
    if not trapped_records:
        return {"trapped_veh_count": 0, "trapped_mean_per_sec": 0.0,
                "trapped_dwell_mean_s": np.nan, "trapped_dwell_max_s": np.nan}
    tr = pd.DataFrame(trapped_records, columns=["No", "SimSec"])
    dwell = tr.groupby("No").SimSec.count()  # 1 record per second
    per_sec = tr.groupby("SimSec").No.count()
    return {"trapped_veh_count": tr.No.nunique(),
            "trapped_mean_per_sec": per_sec.reindex(
                df.SimSec.unique(), fill_value=0).mean(),
            "trapped_dwell_mean_s": dwell.mean(),
            "trapped_dwell_max_s": dwell.max()}


def lane_change_attribution(df):
    """Lane-change events, split by presence of an AS <=100 m downstream."""
    d = df.sort_values(["No", "SimSec"])
    lc = d.LnChg.notna() & d.groupby("No").LnChg.shift().isna()
    events = d[lc][["SimSec", "No", "Link", "Pos"]]
    as_pos = df[df.VehType == AS_TYPE][["SimSec", "Link", "Pos"]]
    n_near = 0
    if len(as_pos) and len(events):
        m = events.merge(as_pos, on=["SimSec", "Link"],
                         suffixes=("", "_as"), how="left")
        near = ((m.Pos_as > m.Pos) &
                (m.Pos_as - m.Pos <= INFLUENCE_ZONE_M))
        n_near = m[near].groupby(["SimSec", "No"]).ngroups
    return {"lc_events_total": len(events),
            "lc_events_near_AS": n_near,
            "lc_share_near_AS": n_near / len(events) if len(events) else np.nan}


def as_service_metrics(df):
    """AS service metrics from the trajectories (.fzp).

    Travel time = first-to-last appearance of each AS (corridor traversal time).
    Right-censored trips (shuttle still in the network at sim end) are excluded
    so the mean/median are not biased low by unfinished trips. Speed/stops are
    also reported over completed trips for a consistent per-trip sample.
    """
    a = df[df.VehType == AS_TYPE]
    if a.empty:
        return {"as_count": 0, "as_completed_count": 0}
    sim_end = df.SimSec.max()
    per = a.groupby("No").agg(t0=("SimSec", "min"), t1=("SimSec", "max"),
                              v=("Speed", "mean"), vmax=("Speed", "max"))
    stops = (a.assign(stopped=a.Speed < STOP_SPEED_KMH)
              .sort_values(["No", "SimSec"])
              .groupby("No").stopped
              .apply(lambda s: (s & ~s.shift(fill_value=False)).sum()))
    done = per[per.t1 < sim_end - 2.0]           # completed (exited) AS trips
    tt = (done.t1 - done.t0)
    return {"as_count": len(per),
            "as_completed_count": len(done),
            "as_travel_time_mean_s": tt.mean() if len(tt) else np.nan,
            "as_travel_time_median_s": tt.median() if len(tt) else np.nan,
            "as_speed_mean_kmh": per.v.reindex(done.index).mean()
                if len(done) else np.nan,
            "as_speed_max_kmh": per.vmax.max(),
            "as_stops_mean": stops.reindex(done.index).mean()
                if len(done) else np.nan}


def process_run(fzp_path):
    df = read_fzp(fzp_path)
    row = {"sim_end_s": float(df.SimSec.max())}
    gaps, samples = follower_gaps(df)
    row.update(gaps)
    row.update(trapped_behind_as(df))
    row.update(lane_change_attribution(df))
    row.update(as_service_metrics(df))
    row["n_vehicles"] = df.No.nunique()
    row["n_buses"] = df.loc[df.VehType == BUS_TYPE, "No"].nunique()
    return row, samples


def run_meta(root, dirpath):
    """(scenario, composition, seed) from <root>/<scenario>/<composition>/seed_N."""
    parts = os.path.relpath(dirpath, root).replace("\\", "/").split("/")
    if len(parts) >= 3 and parts[-1].startswith("seed_"):
        return {"scenario": parts[-3], "composition": parts[-2], "seed": parts[-1][5:]}
    return {"scenario": "?", "composition": "?", "seed": "?"}


def main(root):
    rows, all_samples, dropped = [], [], []
    for dirpath, _, files in os.walk(root):
        if '_stale' in dirpath:
            continue
        for f in files:
            if not f.lower().endswith(".fzp"):
                continue
            path = os.path.join(dirpath, f)
            meta = run_meta(root, dirpath)
            print("processing", path)
            row, samples = process_run(path)
            if row["sim_end_s"] < SIM_COMPLETE_MIN_S:
                print(f"  EXCLUDED (truncated at {row['sim_end_s']:.0f} s) {path}")
                dropped.append({**meta, "sim_end_s": row["sim_end_s"]})
                continue
            rows.append({**meta, **row})
            samples = samples.assign(**meta)
            all_samples.append(samples)
    out = pd.DataFrame(rows).sort_values(["scenario", "composition", "seed"])
    out.to_csv(os.path.join(root, "metrics_by_run.csv"), index=False)
    pd.concat(all_samples, ignore_index=True).to_csv(
        os.path.join(root, "gaps_long.csv"), index=False)
    print(f"\n{len(out)} runs -> metrics_by_run.csv, gaps_long.csv")
    if dropped:
        print(f"EXCLUDED {len(dropped)} truncated run(s):")
        for d in dropped:
            print(f"  {d['scenario']} {d['composition']} seed_{d['seed']} "
                  f"(ended {d['sim_end_s']:.0f} s)")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else C.OUT_ROOT)
