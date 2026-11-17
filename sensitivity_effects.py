"""
sensitivity_effects.py -- seed-paired effects with 95% CIs for the sensitivity
families (complements summarize_sensitivity.py, which gives cell means).

  idm         variant minus base (calibrated IDM parameters), same scenario and
              seed, all-AS composition only.
  turns       (i) AS effect = all AS minus no AS (same seed) under the variant,
  lanechange  (ii) the same AS effect in the main batch (reference cells),
              (iii) difference (i) - (ii) paired by seed,
              (iv) baseline shift = variant 0/20 minus reference 0/20,
              (v) shift 20/0 = variant 20/0 minus reference 20/0.
CI: mean +/- t(0.975, n-1) * sd / sqrt(n) over the seeds.
pct = mean difference as % of the reference mean.

Outputs: tables/sensitivity_<family>_effects.csv and .tex
Usage:   python sensitivity_effects.py [sensitivity_results] [batch_results]
"""
import os
import sys
import glob
import numpy as np
import pandas as pd
from scipy import stats

import config as C

METRICS = ["node_delay_all", "node_stops_all", "link_speed_all", "lc_per_vehkm",
           "node_delay_shuttle", "as_travel_time_mean_s", "trapped_veh_count",
           "lc_share_near_AS", "gap_behind_AS_mean", "n_vehicles"]
LABEL = {"node_delay_all": "Delay (s/veh)", "node_stops_all": "Stops (/veh)",
         "link_speed_all": "Speed (km/h)", "lc_per_vehkm": "LC (/veh-km)",
         "node_delay_shuttle": "AS node delay (s)", "as_travel_time_mean_s": "AS travel time (s)",
         "trapped_veh_count": "Trapped veh (/h)", "lc_share_near_AS": "LC share near AS",
         "gap_behind_AS_mean": "Gap behind AS (m)", "n_vehicles": "Vehicles served"}
TEX_METRICS = ["node_delay_all", "node_stops_all", "link_speed_all", "lc_per_vehkm",
               "as_travel_time_mean_s"]
SCS = C.SENS_SCENARIOS
BS = chr(92)


def per_run(folder):
    nl_p = os.path.join(folder, "node_link_by_run.csv")
    mt_p = os.path.join(folder, "metrics_by_run.csv")
    if not os.path.exists(nl_p):
        return None
    nl = pd.read_csv(nl_p)
    if "complete" in nl:
        nl = nl[nl["complete"]]
    key = ["scenario", "composition", "seed"]
    if os.path.exists(mt_p):
        mt = pd.read_csv(mt_p)
        nl = nl.merge(mt[key + [c for c in mt.columns if c not in nl.columns]],
                      on=key, how="left")
    return nl[nl["scenario"].isin(SCS)]


def ci(d):
    d = np.asarray(d, float)
    d = d[~np.isnan(d)]
    n = len(d)
    if n == 0:
        return np.nan, np.nan, np.nan, 0
    if n == 1:
        return d[0], np.nan, np.nan, 1
    m = d.mean()
    h = stats.t.ppf(0.975, n - 1) * d.std(ddof=1) / np.sqrt(n)
    return m, m - h, m + h, n


def paired_diff(a, b, metrics):
    """Per-seed differences a - b, paired on (scenario, seed).
    Returns {scenario: {metric: (diffs array, mean of b)}}."""
    out = {}
    m = a.merge(b, on=["scenario", "seed"], suffixes=("_a", "_b"))
    for sc, g in m.groupby("scenario"):
        out[sc] = {}
        for c in metrics:
            if c + "_a" in g and c + "_b" in g:
                out[sc][c] = (g[c + "_a"].values - g[c + "_b"].values, g[c + "_b"].mean())
    return out


def rows_from(family, variant, comparison, diffs, metrics):
    rows = []
    for sc, dd in diffs.items():
        for c in metrics:
            if c not in dd:
                continue
            d, ref = dd[c]
            m, lo, hi, n = ci(d)
            pct = 100 * m / ref if (ref is not None and not np.isnan(ref) and ref != 0) else np.nan
            rows.append(dict(family=family, variant=variant, scenario=sc,
                             comparison=comparison, metric=c, ref_mean=ref,
                             mean_diff=m, ci_lo=lo, ci_hi=hi, n=n, pct=pct))
    return rows


def comp(df, name):
    return df[df["composition"] == name].copy()


def family_idm(root):
    base = per_run(os.path.join(root, "idm", "base"))
    if base is None:
        print("  idm/base not extracted yet")
        return pd.DataFrame()
    base = comp(base, C.FULL_COMP)
    metrics = [c for c in METRICS if c in base.columns]
    rows = []
    for vdir in sorted(glob.glob(os.path.join(root, "idm", "*"))):
        name = os.path.basename(vdir)
        if name == "base" or name.startswith("_") or not os.path.isdir(vdir):
            continue
        v = per_run(vdir)
        if v is None:
            print(f"  idm/{name}: not extracted yet, skipped")
            continue
        v = comp(v, C.FULL_COMP)
        rows += rows_from("idm", name, "variant - base (all AS)",
                          paired_diff(v, base, metrics), metrics)
    return pd.DataFrame(rows)


def family_two_comp(family, root, ref):
    metrics = [c for c in METRICS if c in ref.columns]
    ref_as = paired_diff(comp(ref, C.FULL_COMP), comp(ref, C.BASE_COMP), metrics)
    rows = rows_from(family, "reference (main batch)", "AS effect (all AS - no AS)", ref_as, metrics)
    for vdir in sorted(glob.glob(os.path.join(root, family, "*"))):
        name = os.path.basename(vdir)
        if name.startswith("_") or not os.path.isdir(vdir):
            continue
        v = per_run(vdir)
        if v is None:
            print(f"  {family}/{name}: not extracted yet, skipped")
            continue
        v_as = paired_diff(comp(v, C.FULL_COMP), comp(v, C.BASE_COMP), metrics)
        rows += rows_from(family, name, "AS effect (all AS - no AS)", v_as, metrics)
        dd = {}
        for sc in v_as:
            if sc not in ref_as:
                continue
            dd[sc] = {}
            for c in metrics:
                if c in v_as[sc] and c in ref_as[sc]:
                    dd[sc][c] = (v_as[sc][c][0] - ref_as[sc][c][0], ref_as[sc][c][0].mean())
        rows += rows_from(family, name, "AS effect: variant - reference", dd, metrics)
        rows += rows_from(family, name, "baseline shift (no AS): variant - reference",
                          paired_diff(comp(v, C.BASE_COMP), comp(ref, C.BASE_COMP), metrics),
                          metrics)
        rows += rows_from(family, name, "shift (all AS): variant - reference",
                          paired_diff(comp(v, C.FULL_COMP), comp(ref, C.FULL_COMP), metrics),
                          metrics)
    return pd.DataFrame(rows)


def fmt(r):
    if pd.isna(r["mean_diff"]):
        return "--"
    if pd.isna(r["ci_lo"]):
        return "%.2f" % r["mean_diff"]
    return "%.2f [%.2f, %.2f]" % (r["mean_diff"], r["ci_lo"], r["ci_hi"])


def to_tex(df, family, out):
    metrics = [c for c in TEX_METRICS if c in set(df["metric"])]
    piv = df[df["metric"].isin(metrics)].copy()
    piv["cell"] = piv.apply(fmt, axis=1)
    tab = piv.pivot_table(index=["variant", "scenario", "comparison"], columns="metric",
                          values="cell", aggfunc="first", sort=False)
    tab = tab.reindex(columns=metrics)
    L = []
    L.append(BS + "begin{table*}[htbp]")
    L.append(BS + "centering")
    L.append(BS + "caption{Sensitivity family %s: seed-paired differences, mean [95%s CI] over five seeds.}"
             % (family.replace("_", BS + "_"), BS + "%"))
    L.append(BS + "resizebox{" + BS + "textwidth}{!}{")
    L.append(BS + "begin{tabular}{lll" + "r" * len(metrics) + "}")
    L.append(BS + "toprule")
    L.append("Variant & Scenario & Comparison & " + " & ".join(LABEL[c] for c in metrics) + " " + BS * 2)
    L.append(BS + "midrule")
    for (var, sc, cmpn), r in tab.iterrows():
        cells = " & ".join("--" if pd.isna(r[c]) else str(r[c]) for c in metrics)
        line = "%s & %s & %s & %s " % (var, sc, cmpn, cells) + BS * 2
        L.append(line.replace("_", BS + "_").replace("%", BS + "%"))
    L.append(BS + "bottomrule")
    L.append(BS + "end{tabular}}")
    L.append(BS + "end{table*}")
    open(out, "w", encoding="utf-8").write(chr(10).join(L) + chr(10))


def main(root, batch_root):
    tdir = C.TABLES_DIR
    os.makedirs(tdir, exist_ok=True)
    ref = per_run(batch_root)
    pd.set_option("display.width", 250)
    pd.set_option("display.max_colwidth", 40)
    for family in ("idm", "turns", "lanechange"):
        if not os.path.isdir(os.path.join(root, family)):
            continue
        df = family_idm(root) if family == "idm" else family_two_comp(family, root, ref)
        if df.empty:
            continue
        df.to_csv(os.path.join(tdir, f"sensitivity_{family}_effects.csv"), index=False)
        to_tex(df, family, os.path.join(tdir, f"sensitivity_{family}_effects.tex"))
        show = df[df["metric"].isin(TEX_METRICS)].copy()
        show["effect"] = show.apply(fmt, axis=1)
        show["metric"] = show["metric"].map(LABEL)
        print(f"\n== {family} (seed-paired, mean [95% CI]) ==")
        print(show.pivot_table(index=["variant", "scenario", "comparison"], columns="metric",
                               values="effect", aggfunc="first", sort=False)
              .reindex(columns=[LABEL[c] for c in TEX_METRICS]).to_string())


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else C.SENS_ROOT,
         sys.argv[2] if len(sys.argv) > 2 else C.OUT_ROOT)
