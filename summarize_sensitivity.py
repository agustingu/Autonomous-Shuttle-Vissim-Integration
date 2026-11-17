"""
summarize_sensitivity.py -- tables for the sensitivity families produced by
run_sensitivity.py (after extract_node_link.py / extract_metrics.py were run
on every <family>/<variant> folder).

For each family and variant: cell means (SC x composition) of the headline
metrics and their difference to the reference:
    idm         reference = idm/base (calibrated parameters), all-AS composition only
    turns       reference = the main batch cells of the sensitivity scenarios
    lanechange  reference = the main batch cells of the sensitivity scenarios
Also the AS effect (all AS minus no AS) under each variant for turns/lanechange.

Outputs: tables/sensitivity_<family>.csv and tables/sensitivity_<family>.tex
Usage:   python summarize_sensitivity.py [sensitivity_results] [batch_results]
"""
import os
import sys
import glob
import numpy as np
import pandas as pd

import config as C

HEAD = ["node_delay_all", "node_stops_all", "link_speed_all", "lc_per_vehkm",
        "node_delay_shuttle", "as_travel_time_mean_s", "gap_behind_AS_mean",
        "trapped_veh_count", "lc_share_near_AS"]
LABEL = {"node_delay_all": "Delay (s/veh)", "node_stops_all": "Stops (/veh)",
         "link_speed_all": "Speed (km/h)", "lc_per_vehkm": "LC (/veh-km)",
         "node_delay_shuttle": "AS node delay (s)", "as_travel_time_mean_s": "AS travel time (s)",
         "gap_behind_AS_mean": "Gap behind AS (m)", "trapped_veh_count": "Trapped veh",
         "lc_share_near_AS": "LC share near AS"}


def cell_means(folder):
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
    cols = [c for c in HEAD if c in nl.columns]
    return nl.groupby(["scenario", "composition"])[cols].mean()


def family_table(family, root, ref_means):
    rows = []
    for vdir in sorted(glob.glob(os.path.join(root, family, "*"))):
        name = os.path.basename(vdir)
        if name.startswith("_") or not os.path.isdir(vdir):
            continue
        m = cell_means(vdir)
        if m is None:
            print(f"  {family}/{name}: no node_link_by_run.csv yet, skipped")
            continue
        for (sc, comp), r in m.iterrows():
            row = dict(family=family, variant=name, scenario=sc, composition=comp)
            for c in m.columns:
                row[c] = r[c]
                ref = ref_means.loc[(sc, comp), c] if (ref_means is not None and
                                                      (sc, comp) in ref_means.index and
                                                      c in ref_means.columns) else np.nan
                row[c + "_vs_ref"] = r[c] - ref
            rows.append(row)
        # AS effect under this variant (20/0 minus 0/20)
        for sc in sorted({i[0] for i in m.index}):
            if (sc, C.FULL_COMP) in m.index and (sc, C.BASE_COMP) in m.index:
                row = dict(family=family, variant=name, scenario=sc,
                           composition="AS effect (all AS - no AS)")
                for c in m.columns:
                    row[c] = m.loc[(sc, C.FULL_COMP), c] - m.loc[(sc, C.BASE_COMP), c]
                rows.append(row)
    return pd.DataFrame(rows)


def to_tex(df, family, out):
    cols = [c for c in HEAD if c in df.columns]
    lines = [r"\begin{table}[htbp]", r"\centering",
             r"\caption{Sensitivity family: %s (cell means of five seeds).}" % family.replace("_", r"\_"),
             r"\resizebox{\textwidth}{!}{", r"\begin{tabular}{lll" + "r" * len(cols) + "}", r"\toprule",
             "Variant & Scenario & Composition & " + " & ".join(LABEL[c] for c in cols) + r" \\", r"\midrule"]
    for _, r in df.iterrows():
        vals = " & ".join("--" if pd.isna(r[c]) else f"{r[c]:.2f}" for c in cols)
        lines.append(f"{r.variant} & {r.scenario} & {r.composition} & {vals} \\\\".replace("_", r"\_"))
    lines += [r"\bottomrule", r"\end{tabular}}", r"\end{table}"]
    open(out, "w", encoding="utf-8").write("\n".join(lines) + "\n")


def main(root, batch_root):
    tdir = C.TABLES_DIR
    os.makedirs(tdir, exist_ok=True)
    main_means = cell_means(batch_root)
    for family in ("idm", "turns", "lanechange"):
        if not os.path.isdir(os.path.join(root, family)):
            continue
        ref = cell_means(os.path.join(root, "idm", "base")) if family == "idm" else main_means
        df = family_table(family, root, ref)
        if df.empty:
            continue
        df.to_csv(os.path.join(tdir, f"sensitivity_{family}.csv"), index=False)
        to_tex(df, family, os.path.join(tdir, f"sensitivity_{family}.tex"))
        pd.set_option("display.width", 220)
        print(f"\n== {family} ==")
        show = [c for c in ("node_delay_all", "link_speed_all", "lc_per_vehkm",
                            "as_travel_time_mean_s", "trapped_veh_count") if c in df.columns]
        print(df[["variant", "scenario", "composition"] + show].round(2).to_string(index=False))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else C.SENS_ROOT,
         sys.argv[2] if len(sys.argv) > 2 else C.OUT_ROOT)
