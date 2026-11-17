"""
tables_stats.py -- LaTeX tables of the run-level statistics (analysis_runlevel.py).

  <tables_dir>/table5_anova.tex    two-way ANOVA (demand x composition, seed
                                   block): F, p and partial eta^2 per effect
  <tables_dir>/table6_effects.tex  effects with 95% CI (HC3): full replacement
                                   marginal, per-N-shuttle slope marginal, at the
                                   lowest and at the highest demand level
Also <figures_dir>/as_interaction.png: 2x2 panel of the AS-specific measures.
Usage: python tables_stats.py [batch_root]
"""
import os
import sys
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

import config as C

ROOT = sys.argv[1] if len(sys.argv) > 1 else C.OUT_ROOT
TDIR = C.TABLES_DIR
FDIR = C.FIGURES_DIR
os.makedirs(TDIR, exist_ok=True)
os.makedirs(FDIR, exist_ok=True)
SC_LO, SC_HI = C.SCENARIOS[0], C.SCENARIOS[-1]
NSTEP = C.NSHUT_STEP or round(C.SHARE_STEP, 2)        # "per N shuttles"

METRICS = [
    ("node_delay_all", "Delay (s/veh)", 2, False),
    ("node_stops_all", "Stops (stops/veh)", 3, False),
    ("link_speed_all", "Speed (km/h)", 2, False),
    ("lc_per_vehkm", "Lane changes (lc/veh-km)", 3, False),
    ("node_delay_bus", "Bus delay (s/veh)", 2, False),
    ("trapped_veh_count", "Vehicles trapped behind AS (veh/h)", 0, True),
    ("trapped_dwell_mean_s", "Dwell time behind AS (s)", 1, True),
    ("lc_share_near_AS", "Share of lane changes near AS", 3, True),
    ("as_travel_time_mean_s", "AS corridor travel time (s)", 0, True),
]
EFFECTS = [("sc", "Demand"), ("comp", "Composition"), ("sc:comp", "Demand $\\times$ comp.")]


def stars(p):
    if p < 0.001:
        return "$^{***}$"
    if p < 0.01:
        return "$^{**}$"
    if p < 0.05:
        return "$^{*}$"
    return ""


def table5(an):
    res = an[an.effect == "Residual"].set_index("metric")["df"]
    df_all = int(res.get("node_delay_all", 0))
    df_as = int(res.get("trapped_veh_count", 0))
    L = [r"\begin{table*}[htbp]", r"  \centering",
         r"  \caption{Run-level two-way ANOVA (demand $\times$ fleet composition, random seed as block). "
         r"Cells: $F$ ($\eta_p^2$).}",
         r"  \resizebox{.7\textwidth}{!}{", r"    \begin{tabular}{lccc}", r"    \toprule",
         r"    Measure & Demand & Composition & Demand $\times$ comp. \\", r"    \midrule"]
    for col, label, nd, needs_as in METRICS:
        a = an[an.metric == col].set_index("effect")
        if a.empty:
            continue
        cells = []
        for eff, _ in EFFECTS:
            r = a.loc[eff]
            cells.append(f"{r.F:.1f}{stars(r.p)} ({r.partial_eta2:.2f})")
        L.append(f"    {label} & " + " & ".join(cells) + r" \\")
    L += [r"    \bottomrule",
          r"    \multicolumn{4}{l}{$^{*}$ $p<0.05$, $^{**}$ $p<0.01$, $^{***}$ $p<0.001$. "
          f"Residual df = {df_all} ({df_as} for AS-specific measures)." + r"}\\",
          r"    \end{tabular}}", r"  \label{tab:anova}", r"\end{table*}"]
    open(os.path.join(TDIR, "table5_anova.tex"), "w", encoding="utf-8").write("\n".join(L) + "\n")


def ci_cell(r, nd):
    if r is None:
        return "--"
    return f"{r.estimate:+.{nd}f} [{r.ci_low:.{nd}f}, {r.ci_high:.{nd}f}]"


def table6(ef, sl):
    per = f"Per {NSTEP} AS"
    L = [r"\begin{table*}[htbp]", r"  \centering",
         r"  \caption{Estimated effects of the AS share, mean [95\% CI].}",
         r"  \resizebox{\textwidth}{!}{", r"    \begin{tabular}{lcccc}", r"    \toprule",
         f"    Measure & Full replacement & {per} (all) & {per}, {SC_LO} & {per}, {SC_HI} \\\\",
         r"    \midrule"]
    for col, label, nd, needs_as in METRICS:
        e = ef[(ef.metric == col) & (ef.scope == "marginal") & (ef.A == C.FULL_COMP)]
        base = C.FIRST_AS_COMP if needs_as else C.BASE_COMP
        e = e[e.B == base]
        full = ci_cell(e.iloc[0], nd) if len(e) else "--"
        if len(e):
            full += stars(e.iloc[0].p_holm)
        s = sl[sl.metric == col].set_index("scope")
        if s.empty:
            continue
        cells = [full, ci_cell(s.loc["marginal"], nd) + stars(s.loc["marginal"].p_raw),
                 ci_cell(s.loc[SC_LO], nd), ci_cell(s.loc[SC_HI], nd)]
        L.append(f"    {label} & " + " & ".join(cells) + r" \\")
    L += [r"    \bottomrule", r"    \end{tabular}}",
          r"  \par\vspace{3pt}\parbox{\textwidth}{\footnotesize Full replacement: " + C.RATIO[C.FULL_COMP]
          + " minus " + C.RATIO[C.BASE_COMP] + " (" + (C.RATIO[C.FIRST_AS_COMP] if C.FIRST_AS_COMP else "")
          + f" for the AS-specific measures), averaged over demand levels. {per}: linear effect of {NSTEP} shuttles "
          f"added to the fleet, averaged over demand levels and at {SC_LO} and {SC_HI}. HC3 standard errors. "
          r"Stars: Holm-adjusted $p$ (full replacement) or raw $p$ (slope): $^{*}$ $p<0.05$, $^{**}$ $p<0.01$, "
          r"$^{***}$ $p<0.001$.}",
          r"  \label{tab:effects}", r"\end{table*}"]
    open(os.path.join(TDIR, "table6_effects.tex"), "w", encoding="utf-8").write("\n".join(L) + "\n")


def as_figure(mt):
    comps = C.AS_COMPS
    panels = [("trapped_veh_count", "Vehicles trapped behind an AS (veh/h)"),
              ("trapped_dwell_mean_s", "Mean dwell time behind an AS (s)"),
              ("lc_share_near_AS", "Share of lane changes within 100 m upstream of an AS"),
              ("as_travel_time_mean_s", "AS corridor travel time (s)")]
    m = mt.groupby(["scenario", "composition"]).mean(numeric_only=True)
    fig, axes = plt.subplots(2, 2, figsize=(9, 7))
    for ax, (col, ylabel) in zip(axes.ravel(), panels):
        if col not in m.columns:
            ax.axis("off")
            continue
        for c in comps:
            xs = [C.DEMAND_TOTAL[sc] for sc in C.SCENARIOS if (sc, c) in m.index]
            ys = [m.loc[(sc, c), col] for sc in C.SCENARIOS if (sc, c) in m.index]
            ax.plot(xs, ys, linestyle="--", marker=C.MARKER[c], markersize=8,
                    label=f"{C.NSHUT[c]} Autonomous Shuttles per hour")
        ax.set_xlabel("Network demand (veh/h)")
        ax.set_ylabel(ylabel)
        ax.grid(alpha=0.3)
    axes[0, 0].legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(os.path.join(FDIR, "as_interaction.png"), dpi=300)
    plt.close(fig)


def main():
    an = pd.read_csv(os.path.join(ROOT, "stats_runlevel_anova.csv"))
    ef = pd.read_csv(os.path.join(ROOT, "stats_runlevel_effects.csv"))
    sl = pd.read_csv(os.path.join(ROOT, "stats_runlevel_slopes.csv"))
    mt = pd.read_csv(os.path.join(ROOT, "metrics_by_run.csv"))
    table5(an)
    table6(ef, sl)
    as_figure(mt)
    print(open(os.path.join(TDIR, "table5_anova.tex"), encoding="utf-8").read())
    print(open(os.path.join(TDIR, "table6_effects.tex"), encoding="utf-8").read())


if __name__ == "__main__":
    main()
