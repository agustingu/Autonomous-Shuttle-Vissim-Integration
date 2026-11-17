"""
AS-specific figures (trajectory-level measures) per demand scenario.

Style matches the existing plots: one series per fleet composition with a
distinct marker (o v p * +), dashed connecting lines, 300 dpi. Line-marker
panels plot a metric against the demand scenarios (increasing volume);
one distribution panel contrasts follower gaps behind AS vs behind buses.

Outputs -> <figures_dir>/*.png
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
LABEL = {c: f"{int(round(100 * C.SHARE[c]))}% AS" for c in COMPS}
MARKER = C.MARKER
SC_ORDER = C.SCENARIOS
# x axis: total network demand (veh/h) of each scenario, from the config
DEMAND_VPH = C.DEMAND_TOTAL
# metrics whose y-axis starts at zero, matching the paper (e.g. Fig. 5 speed)
ZERO_BASE = {"as_speed_mean_kmh"}

PANELS = [  # (column, y-label, title, needs_AS)
    ("as_travel_time_mean_s", "AS travel time (s)",
     "AS corridor travel time", True),
    ("as_speed_mean_kmh", "AS mean speed (km/hr)", "AS mean speed", True),
    ("as_stops_mean", "Stops per AS", "AS stops", True),
    ("gap_behind_AS_mean", "Follower gap behind AS (m)",
     "Mean follower gap behind AS", True),
    ("trapped_veh_count", "Vehicles trapped behind AS",
     "Vehicles caught in an AS leader-chain", True),
    ("trapped_dwell_mean_s", "Mean dwell behind AS (s)",
     "Dwell time trapped behind AS", True),
    ("lc_share_near_AS", "Share of lane changes ≤100 m upstream of AS",
     "Lane-change attribution near AS", True),
]


def line_panel(ax, df, col, ylabel, title, needs_as):
    xs = [DEMAND_VPH[sc] for sc in SC_ORDER]
    for c in COMPS:
        if needs_as and C.SHARE[c] == 0:
            continue
        sub = df[df.composition == c]
        s = sub.groupby("scenario")[col].mean().reindex(SC_ORDER)
        if s.dropna().empty:
            continue
        ax.plot(xs, s.values, linestyle="--", marker=MARKER[c],
                markersize=8, label=LABEL[c])
    ax.set_xlabel("Network Demand (vph)")
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    if col in ZERO_BASE:
        ax.set_ylim(bottom=0)
    ax.grid(True, alpha=0.3)


def make_line_figures(df, outdir):
    for col, ylabel, title, needs_as in PANELS:
        if col not in df.columns:
            print(f"  skip {col} (not in metrics)")
            continue
        fig, ax = plt.subplots(figsize=(6.4, 4.4))
        line_panel(ax, df, col, ylabel, title, needs_as)
        ax.legend(title="Fleet composition", fontsize=8)
        fig.tight_layout()
        p = os.path.join(outdir, f"as_{col}.png")
        fig.savefig(p, dpi=300)
        plt.close(fig)
        print(f"  wrote {os.path.basename(p)}")

    # combined grid of all line panels
    cols = [p for p in PANELS if p[0] in df.columns]
    n = len(cols)
    ncol = 3
    nrow = int(np.ceil(n / ncol))
    fig, axes = plt.subplots(nrow, ncol, figsize=(5.2 * ncol, 3.8 * nrow))
    axes = np.atleast_1d(axes).ravel()
    for ax, (col, ylabel, title, needs_as) in zip(axes, cols):
        line_panel(ax, df, col, ylabel, title, needs_as)
    for ax in axes[n:]:
        ax.axis("off")
    axes[0].legend(title="Fleet composition", fontsize=7)
    fig.tight_layout()
    p = os.path.join(outdir, "as_metrics_grid.png")
    fig.savefig(p, dpi=300)
    plt.close(fig)
    print(f"  wrote {os.path.basename(p)}")


def make_gap_distribution(root, outdir):
    path = os.path.join(root, "gaps_long.csv")
    if not os.path.exists(path):
        print("  gaps_long.csv not found; skipping distribution figure")
        return
    g = pd.read_csv(path, usecols=["behind", "Spacing", "composition"])
    g = g.dropna(subset=["Spacing"])
    g = g[g.Spacing.between(0, 120)]                     # clip for readability

    # (a) overall AS vs Bus follower-gap distribution
    fig, ax = plt.subplots(figsize=(6.0, 4.2))
    data = [g.loc[g.behind == "AS", "Spacing"].values,
            g.loc[g.behind == "Bus", "Spacing"].values]
    ax.boxplot(data, labels=["behind AS", "behind Bus"], showfliers=False,
               widths=0.5)
    ax.set_ylabel("Follower gap / spacing (m)")
    ax.set_title("Follower-gap distribution: behind AS vs behind Bus")
    ax.grid(True, axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(os.path.join(outdir, "gap_dist_AS_vs_Bus.png"), dpi=300)
    plt.close(fig)
    print("  wrote gap_dist_AS_vs_Bus.png in " + outdir)

    # (b) gap behind AS by composition
    data, labels = [], []
    for c in C.AS_COMPS:
        vals = g[(g.behind == "AS") & (g.composition == c)]["Spacing"].values
        if len(vals):
            data.append(vals)
            labels.append(LABEL[c])
    fig, ax = plt.subplots(figsize=(6.4, 4.2))
    ax.boxplot(data, labels=labels, showfliers=False, widths=0.6)
    ax.set_ylabel("Follower gap behind AS (m)")
    ax.set_title("Follower gap behind AS by fleet composition")
    ax.grid(True, axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(os.path.join(outdir, "gap_behind_AS_by_comp.png"), dpi=300)
    plt.close(fig)
    print("  wrote gap_behind_AS_by_comp.png")


def main(root):
    outdir = C.FIGURES_DIR
    os.makedirs(outdir, exist_ok=True)
    df = pd.read_csv(os.path.join(root, "metrics_by_run.csv"))
    print("line-marker figures:")
    make_line_figures(df, outdir)
    print("distribution figures:")
    make_gap_distribution(root, outdir)
    print(f"\nfigures -> {outdir}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else C.OUT_ROOT)
