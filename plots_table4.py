"""
plots_table4.py -- summary table and line figures (delay, stops, speed, lane
changes per demand scenario, one series per fleet composition) from the
node/link results, with the bus / shuttle class break-out.

Inputs:  <root>/node_link_by_run.csv   (extract_node_link.py)
Outputs: <tables_dir>/table4.tex            node + link summary per cell
         <tables_dir>/table4_classes.tex    per-class delay/stops/speed
         <tables_dir>/table4_long.csv       same numbers, machine readable
         <figures_dir>/{delay,stops,speed,lc}.png   one series per composition,
             markers from the config, dashed lines, 300 dpi
         <figures_dir>/class_delay.png      shuttle vs bus node delay

Definitions (state them in the paper):
  delay, stops  : node evaluation, vehicle-weighted over the nodes
  speed         : link evaluation, mean over links of the space-mean speed
  lane changes  : lane-change events per vehicle-kilometer travelled
Only complete runs are used; cell = mean over seeds.

Usage:  python plots_table4.py [batch_root]
"""
import os
import sys
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

import config as C

COMPS = C.COMPS
RATIO = C.RATIO
NSHUT = C.NSHUT
MARKER = C.MARKER
SCS = C.SCENARIOS
DEMAND_VPH = C.DEMAND_TOTAL

FIGS = [  # column, file, title, ylabel, decimals
    ("node_delay_all", "delay", "Average Delay per Demand Scenario", "Delay(s/veh)"),
    ("node_stops_all", "stops", "Average Number of Stops per Demand Scenario", "Stops (stop/veh)"),
    ("link_speed_all", "speed", "Average Speed per Demand Scenario", "Speed (km/hr)"),
    ("lc_per_vehkm", "lc", "Average Lane Changes per Demand Scenario", "Lane changes (lc/veh-km)"),
]


def cell_means(df):
    d = df[df["complete"]] if "complete" in df else df
    g = d.groupby(["composition", "scenario"])
    m = g.mean(numeric_only=True)
    m["n_seeds"] = g.size()
    # 95% CI half-width of the cell mean over seeds: t(0.975, n-1) * sd / sqrt(n)
    from scipy import stats as _st
    sd = g.std(numeric_only=True)
    n = g.size()
    for col in sd.columns:
        m[col + "_ci"] = _st.t.ppf(0.975, n - 1) * sd[col] / np.sqrt(n)
    return m


def fmt_change(v, prev, nd):
    if prev is None or np.isnan(prev):
        return f"{v:.{nd}f}"
    return f"{v:.{nd}f}({v - prev:+.{nd}f})"


def fmt_ci(v, h, nd):
    if h is None or np.isnan(h):
        return f"{v:.{nd}f}"
    return f"{v:.{nd}f} $\\pm$ {h:.{nd}f}"


def table4(m, outdir):
    cols = [("node_delay_all", 2), ("node_stops_all", 2),
            ("link_speed_all", 2), ("lc_per_vehkm", 2)]
    lines = [r"\begin{table*}[htbp]", r"  \centering",
             r"  \caption{Summary of node and link results (mean $\pm$ 95\% confidence "
             r"interval over the seeds of each cell).}",
             r"  \resizebox{.8\textwidth}{!}{", r"    \begin{tabular}{cccccc}",
             r"    \toprule",
             r"    \multirow{AS/Buses} & \multirow{Scenario No.} & \multicolumn{2}{c}{\underline{Node Analysis}} & \multicolumn{2}{c}{\underline{Link Analysis}} \\",
             r"         &       & Delay (s) & Stops (stop/veh) & Speed (km/hr) & Lane changes (lc/veh-km) \\",
             r"    \midrule"]
    prev_comp = None
    for comp in COMPS:
        for i, sc in enumerate(SCS):
            if (comp, sc) not in m.index:
                continue
            row = m.loc[(comp, sc)]
            cells = []
            for col, nd in cols:
                cells.append(fmt_ci(row[col], row.get(col + "_ci", np.nan), nd))
            lead = r"\multirow{%s}" % RATIO[comp] if i == 0 else ""
            star = "" if row["n_seeds"] >= 5 else r"$^{\dagger}$"
            lines.append(f"    {lead} & {sc}{star} & " + " & ".join(cells) + r" \\")
        lines.append(r"    \midrule")
        prev_comp = comp
    lines[-1] = r"    \bottomrule"
    lines += [r"    \end{tabular}}",
              r"  \par\vspace{3pt}\parbox{.95\textwidth}{\footnotesize Note: delay and stops are vehicle-weighted "
              r"means over the nodes; speed is the mean over links of the space-mean speed; lane changes are "
              r"events per vehicle-km of human-driven traffic.}",
              r"  \label{tab:resr_tab_all}", r"\end{table*}"]
    open(os.path.join(outdir, "table4.tex"), "w", encoding="utf-8").write("\n".join(lines) + "\n")


def table4_classes(m, outdir):
    lines = [r"\begin{table}[htbp]", r"  \centering",
             r"  \caption{Node delay and stops by vehicle class (mean over the seeds of each cell).}",
             r"  \resizebox{.5\textwidth}{!}{", r"    \begin{tabular}{ccrrrrrr}",
             r"    \toprule",
             r"    AS/Buses & Scenario & \multicolumn{2}{c}{Delay (s/veh)} & \multicolumn{2}{c}{Stops (stop/veh)} & \multicolumn{2}{c}{Speed (km/hr)} \\",
             r"             &          & Bus & Shuttle & Bus & Shuttle & Bus & Shuttle \\",
             r"    \midrule"]
    for comp in COMPS:
        for i, sc in enumerate(SCS):
            if (comp, sc) not in m.index:
                continue
            r = m.loc[(comp, sc)]
            f = lambda v, nd=2: "--" if pd.isna(v) else f"{v:.{nd}f}"
            lead = RATIO[comp] if i == 0 else ""
            lines.append(f"    {lead} & {sc} & {f(r['node_delay_bus'])} & {f(r['node_delay_shuttle'])} & "
                         f"{f(r['node_stops_bus'])} & {f(r['node_stops_shuttle'])} & "
                         f"{f(r['link_speed_bus'])} & {f(r['link_speed_shuttle'])} \\\\")
        lines.append(r"    \midrule")
    lines[-1] = r"    \bottomrule"
    lines += [r"    \end{tabular}}", r"  \label{tab:res_classes}", r"\end{table}"]
    open(os.path.join(outdir, "table4_classes.tex"), "w", encoding="utf-8").write("\n".join(lines) + "\n")


def line_figure(m, col, fname, title, ylabel, outdir):
    fig, ax = plt.subplots(figsize=(8, 6))
    xs = [DEMAND_VPH[s] for s in SCS]
    for comp in COMPS:
        ys = [m.loc[(comp, s), col] if (comp, s) in m.index else np.nan for s in SCS]
        if all(np.isnan(ys)):
            continue
        ax.plot(xs, ys, linestyle="--", alpha=0.35, color=f"C{COMPS.index(comp)}")
        ax.scatter(xs, ys, marker=MARKER[comp], s=110, color=f"C{COMPS.index(comp)}",
                   label=f"{NSHUT[comp]} Autonomous Shuttles per hour", zorder=3)
    ax.set_title(title, fontsize=16)
    ax.set_xlabel("Network Demand (vph)", fontsize=14)
    ax.set_ylabel(ylabel, fontsize=14)
    ax.tick_params(labelsize=12)
    ax.legend(fontsize=12)
    fig.tight_layout()
    fig.savefig(os.path.join(outdir, f"{fname}.png"), dpi=300)
    plt.close(fig)


def class_figure(m, outdir):
    fig, ax = plt.subplots(figsize=(8, 6))
    xs = [DEMAND_VPH[s] for s in SCS]
    for comp in COMPS:
        if C.SHARE[comp] == 0:
            continue
        ys = [m.loc[(comp, s), "node_delay_shuttle"] if (comp, s) in m.index else np.nan for s in SCS]
        ax.plot(xs, ys, "--", alpha=0.35, color=f"C{COMPS.index(comp)}")
        ax.scatter(xs, ys, marker=MARKER[comp], s=110, color=f"C{COMPS.index(comp)}",
                   label=f"Shuttles, {NSHUT[comp]} AS per hour", zorder=3)
    ys = [m.loc[(C.BASE_COMP, s), "node_delay_bus"] if (C.BASE_COMP, s) in m.index else np.nan for s in SCS]
    ax.plot(xs, ys, "-", color="k", alpha=0.6)
    ax.scatter(xs, ys, marker="s", s=80, color="k", label="Buses, 0 AS per hour", zorder=3)
    ax.set_title("Node Delay of Transit Vehicles per Demand Scenario", fontsize=16)
    ax.set_xlabel("Network Demand (vph)", fontsize=14)
    ax.set_ylabel("Delay (s/veh)", fontsize=14)
    ax.tick_params(labelsize=12)
    ax.legend(fontsize=11)
    fig.tight_layout()
    fig.savefig(os.path.join(outdir, "class_delay.png"), dpi=300)
    plt.close(fig)


def main(root):
    tdir = C.TABLES_DIR
    fdir = C.FIGURES_DIR
    os.makedirs(tdir, exist_ok=True)
    os.makedirs(fdir, exist_ok=True)
    df = pd.read_csv(os.path.join(root, "node_link_by_run.csv"))
    m = cell_means(df)
    m.reset_index().to_csv(os.path.join(tdir, "table4_long.csv"), index=False)
    table4(m, tdir)
    table4_classes(m, tdir)
    for col, fname, title, ylabel in FIGS:
        line_figure(m, col, fname, title, ylabel, fdir)
    class_figure(m, fdir)
    print(f"tables -> {tdir}\nfigures -> {fdir}")
    print(m[["node_delay_all", "node_stops_all", "link_speed_all", "lc_per_vehkm", "n_seeds"]]
          .round(2).to_string())


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else C.OUT_ROOT)
