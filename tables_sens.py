"""
tables_sens.py -- compact LaTeX tables for the sensitivity analysis, built from
the outputs of sensitivity_effects.py and summarize_sensitivity.py in the
tables folder.

  <tables_dir>/table7_idm.tex       IDM parameter sensitivity (all-AS fleet,
                                    the two sensitivity scenarios): seed-paired
                                    difference variant - calibrated
  <tables_dir>/table8_turns_lc.tex  turning shares and lane-changing parameters:
                                    AS effect (all AS - no AS) under each variant
Usage: python tables_sens.py
"""
import os
import numpy as np
import pandas as pd

import config as C

TDIR = C.TABLES_DIR
S1, S2 = C.SENS_SCENARIOS[0], C.SENS_SCENARIOS[-1]        # light, congested
FULL, BASE = C.FULL_COMP, C.BASE_COMP
SYMBOL = {"a": "a", "T": "T_0", "s0": "S_0", "b": "b", "v0": "V_0"}

IDM_ORDER = [(f"{p}_x{f:g}", f"${SYMBOL.get(p, p)} \\times {f:g}$")
             for p in C.SENS_IDM_PARAMS for f in C.SENS_IDM_FACTORS]
ALT_NAME = (os.path.splitext(os.path.basename(C.IDM_ALT_PARAM_FILE))[0]
            if C.IDM_ALT_PARAM_FILE else None)
if ALT_NAME:
    IDM_ORDER.append((ALT_NAME, r"Alternative set$^{\mathrm{a}}$"))
VAR_ORDER = [("reference (main batch)", "Base network and parameters")]
VAR_ORDER += [(f"turns_x{f:g}", f"Turn shares $\\times {f:g}$") for f in C.SENS_TURN_FACTORS]
VAR_ORDER += [(name, spec.get("label", name)) for name, spec in C.SENS_LC_VARIANTS.items()]


def cell(df, variant, sc, comparison, metric, nd):
    r = df[(df.variant == variant) & (df.scenario == sc) &
           (df.comparison == comparison) & (df.metric == metric)]
    if r.empty or pd.isna(r.iloc[0].mean_diff):
        return "--"
    r = r.iloc[0]
    if pd.isna(r.ci_lo):
        return f"{r.mean_diff:+.{nd}f}"
    return f"{r.mean_diff:+.{nd}f} [{r.ci_lo:.{nd}f}, {r.ci_hi:.{nd}f}]"


def alt_footnote():
    if not C.IDM_ALT_PARAM_FILE:
        return ""
    import com_idm_driver as cid
    p = cid.read_params(C.IDM_ALT_PARAM_FILE)
    return (r"$^{\mathrm{a}}$ Alternative set: $a = %.2f$ m/s$^2$, $b = %.2f$ m/s$^2$, "
            r"$V_0 = %.2f$ m/s, $S_0 = %.2f$ m, $T_0 = %.2f$ s." % (p["a"], p["b"], p["v0"], p["s0"], p["T"]))


def table7():
    ef = pd.read_csv(os.path.join(TDIR, "sensitivity_idm_effects.csv"))
    cm = pd.read_csv(os.path.join(TDIR, "sensitivity_idm.csv"))
    base = cm[(cm.variant == "base") & (cm.composition == FULL)].set_index("scenario")
    comp = "variant - base (all AS)"
    cols = [(S1, "node_delay_all", 2), (S2, "node_delay_all", 2),
            (S2, "node_stops_all", 3), (S2, "link_speed_all", 2),
            (S2, "lc_per_vehkm", 3), (S2, "node_delay_shuttle", 2),
            (S2, "trapped_veh_count", 0)]
    L = [r"\begin{table*}[htbp]", r"  \centering",
         r"  \caption{Sensitivity of the network results to the AS car-following parameters "
         f"({C.RATIO[FULL]} fleet, {S1} and {S2}, {len(C.SEEDS)} seeds)." + "}",
         r"  \resizebox{\textwidth}{!}{", r"    \begin{tabular}{lccccccc}", r"    \toprule",
         r"    Variant & \multicolumn{2}{c}{Delay (s/veh)} & Stops & Speed & LC & Shuttle delay & Trapped \\",
         f"            & {S1} & {S2} & {S2} (/veh) & {S2} (km/h) & {S2} (/veh-km) & {S2} (s/veh) & {S2} (veh/h) \\\\",
         r"    \midrule"]
    vals = [f"{base.loc[S1, 'node_delay_all']:.2f}", f"{base.loc[S2, 'node_delay_all']:.2f}",
            f"{base.loc[S2, 'node_stops_all']:.3f}", f"{base.loc[S2, 'link_speed_all']:.2f}",
            f"{base.loc[S2, 'lc_per_vehkm']:.3f}", f"{base.loc[S2, 'node_delay_shuttle']:.2f}",
            f"{base.loc[S2, 'trapped_veh_count']:.0f}"]
    L.append("    Calibrated parameters, values & " + " & ".join(vals) + r" \\")
    L.append(r"    \midrule")
    for key, label in IDM_ORDER:
        cells = [cell(ef, key, sc, comp, m, nd) for sc, m, nd in cols]
        L.append(f"    {label} & " + " & ".join(cells) + r" \\")
    L += [r"    \bottomrule", r"    \end{tabular}}",
          r"  \par\vspace{3pt}\parbox{\textwidth}{\footnotesize Entries: seed-paired difference from the calibrated "
          r"case, mean [95\% CI] over the seeds; first row: calibrated-case values. Network measures: all vehicles. "
          r"Shuttle delay: intersection delay of the shuttles; trapped: vehicles trapped behind a shuttle per hour. "
          + alt_footnote() + "}",
          r"  \label{tab:sens_idm}", r"\end{table*}"]
    open(os.path.join(TDIR, "table7_idm.tex"), "w", encoding="utf-8").write("\n".join(L) + "\n")
    return "\n".join(L)


def table8():
    frames, cms = [], []
    for fam in ("turns", "lanechange"):
        p = os.path.join(TDIR, f"sensitivity_{fam}_effects.csv")
        if os.path.exists(p):
            frames.append(pd.read_csv(p))
        q = os.path.join(TDIR, f"sensitivity_{fam}.csv")
        if os.path.exists(q):
            cms.append(pd.read_csv(q))
    ef = pd.concat(frames, ignore_index=True)
    cm = pd.concat(cms, ignore_index=True) if cms else pd.DataFrame()
    main = pd.read_csv(os.path.join(C.OUT_ROOT, "node_link_by_run.csv"))
    main = main.groupby(["scenario", "composition"])["node_delay_all"].mean()
    comp = "AS effect (all AS - no AS)"
    L = [r"\begin{table*}[htbp]", r"  \centering",
         r"  \caption{Sensitivity of the AS effect to the turning-movement shares and to the lane-changing "
         f"parameters of the human drivers ({S1} and {S2}, {len(C.SEEDS)} seeds)." + "}",
         r"  \resizebox{\textwidth}{!}{", r"    \begin{tabular}{llccccc}", r"    \toprule",
         r"    Variant & Scen. & Base delay & \multicolumn{4}{c}{AS effect (" + C.RATIO[FULL] + " $-$ " + C.RATIO[BASE] + r")} \\",
         r"            &       & (s/veh) & Delay (s/veh) & Stops (/veh) & Speed (km/h) & LC (/veh-km) \\",
         r"    \midrule"]
    seen = set(ef.variant)
    for key, label in VAR_ORDER:
        if key not in seen:
            continue
        for i, sc in enumerate((S1, S2)):
            if key == "reference (main batch)":
                bd = main.loc[(sc, BASE)]
            else:
                r = cm[(cm.variant == key) & (cm.scenario == sc) & (cm.composition == BASE)]
                bd = r.iloc[0].node_delay_all if len(r) else np.nan
            cells = [f"{bd:.1f}" if not pd.isna(bd) else "--",
                     cell(ef, key, sc, comp, "node_delay_all", 2),
                     cell(ef, key, sc, comp, "node_stops_all", 3),
                     cell(ef, key, sc, comp, "link_speed_all", 2),
                     cell(ef, key, sc, comp, "lc_per_vehkm", 3)]
            lead = label if i == 0 else ""
            L.append(f"    {lead} & {sc} & " + " & ".join(cells) + r" \\")
    L += [r"    \bottomrule", r"    \end{tabular}}",
          r"  \par\vspace{3pt}\parbox{\textwidth}{\footnotesize AS effect: " + C.RATIO[FULL] + " minus " + C.RATIO[BASE]
          + r", paired by seed, mean [95\% CI] over the seeds. Base delay: " + C.RATIO[BASE] + " fleet. "
          r"The lane-changing parameters are those of the configured driving behavior.}",
          r"  \label{tab:sens_turns_lc}", r"\end{table*}"]
    open(os.path.join(TDIR, "table8_turns_lc.tex"), "w", encoding="utf-8").write("\n".join(L) + "\n")
    return "\n".join(L)


if __name__ == "__main__":
    print(table7())
    print(table8())
