"""
Statistics on AS-specific metrics across fleet compositions.

Design (scenario-mean analysis, kept for continuity with the first version of the paper):
  - The sampling unit is the *demand-scenario mean*: for each metric and
    composition, average over seeds within each demand scenario -> matched samples.
  - One-way ANOVA across the compositions per metric (scipy.f_oneway).
  - Pairwise comparisons: PAIRED t-tests matched by demand scenario (the same 6
    scenarios appear in every composition), Holm-corrected across the pairs
    within each metric.

AS-specific metrics are undefined without shuttles; those metrics are tested
across the AS compositions only.

Outputs (into the batch folder):
  stats_composition_summary.csv  per-composition mean/sd of the scenario means
  stats_anova.csv                metric, F, p, k compositions, n per group
  stats_pairwise.csv             metric, A, B, meandiff, t, p_raw, p_holm, sig
"""
import os
import sys
import numpy as np
import pandas as pd
from scipy import stats

import config as C

COMPS = C.COMPS
AS_SHARE = {c: 100 * C.SHARE[c] for c in COMPS}

# metrics to test: (column, needs_AS?)  needs_AS drops 00AS from the comparison
METRICS = [
    ("gap_behind_AS_mean", True),
    ("gap_behind_AS_median", True),
    ("gap_behind_AS_p85", True),
    ("trapped_veh_count", True),
    ("trapped_mean_per_sec", True),
    ("trapped_dwell_mean_s", True),
    ("lc_events_near_AS", True),
    ("lc_share_near_AS", True),
    ("as_travel_time_mean_s", True),
    ("as_travel_time_median_s", True),
    ("as_speed_mean_kmh", True),
    ("as_stops_mean", True),
    ("gap_behind_Bus_mean", False),
    ("lc_events_total", False),
]


def holm(pvals):
    """Holm-Bonferroni adjusted p-values (same order as input)."""
    p = np.asarray(pvals, float)
    m = len(p)
    order = np.argsort(p)
    adj = np.empty(m)
    running = 0.0
    for rank, idx in enumerate(order):
        val = (m - rank) * p[idx]
        running = max(running, val)
        adj[idx] = min(running, 1.0)
    return adj


def scenario_means(df, col):
    """DataFrame: rows=SC scenarios, cols=composition, values=mean over seeds."""
    sub = df[["scenario", "composition", col]].dropna(subset=[col])
    if sub.empty:
        return pd.DataFrame()
    return sub.pivot_table(index="scenario", columns="composition",
                           values=col, aggfunc="mean")


def main(root):
    df = pd.read_csv(os.path.join(root, "metrics_by_run.csv"))

    summary_rows, anova_rows, pair_rows = [], [], []
    for col, needs_as in METRICS:
        comps = [c for c in COMPS if not (needs_as and C.SHARE[c] == 0)]
        pv = scenario_means(df, col).reindex(columns=comps)
        pv = pv.dropna(axis=1, how="all")
        comps = list(pv.columns)
        if len(comps) < 2:
            continue

        for c in comps:
            v = pv[c].dropna()
            summary_rows.append({"metric": col, "composition": c,
                                 "as_share_pct": AS_SHARE[c],
                                 "n_scenarios": len(v),
                                 "mean": v.mean(), "sd": v.std(ddof=1)})

        groups = [pv[c].dropna().values for c in comps]
        if all(len(g) >= 2 for g in groups):
            F, p = stats.f_oneway(*groups)
        else:
            F, p = np.nan, np.nan
        anova_rows.append({"metric": col, "k_comps": len(comps),
                           "n_per_group": ";".join(str(len(g)) for g in groups),
                           "F": F, "p": p})

        # pairwise PAIRED t-tests (matched by scenario), Holm within metric
        raw = []
        for i in range(len(comps)):
            for j in range(i + 1, len(comps)):
                a, b = comps[i], comps[j]
                paired = pv[[a, b]].dropna()
                if len(paired) < 2:
                    continue
                t, pp = stats.ttest_rel(paired[a], paired[b])
                raw.append([col, a, b, paired[a].mean() - paired[b].mean(),
                            len(paired), t, pp])
        if raw:
            padj = holm([r[6] for r in raw])
            for r, pa in zip(raw, padj):
                pair_rows.append({"metric": r[0], "A": r[1], "B": r[2],
                                  "mean_diff_A_minus_B": r[3], "n_pairs": r[4],
                                  "t": r[5], "p_raw": r[6], "p_holm": pa,
                                  "sig_holm_0.05": pa < 0.05})

    for name, rows in (("stats_composition_summary", summary_rows),
                       ("stats_anova", anova_rows),
                       ("stats_pairwise", pair_rows)):
        pd.DataFrame(rows).to_csv(os.path.join(root, f"{name}.csv"), index=False)
        print(f"wrote {name}.csv  ({len(rows)} rows)")

    # console digest
    an = pd.DataFrame(anova_rows)
    print("\nANOVA across compositions (per metric):")
    print(an[["metric", "k_comps", "F", "p"]].to_string(index=False))
    sig = pd.DataFrame(pair_rows)
    if len(sig):
        sig = sig[sig["sig_holm_0.05"]]
        print(f"\nSignificant pairwise contrasts (Holm p<0.05): {len(sig)}")
        print(sig[["metric", "A", "B", "mean_diff_A_minus_B", "p_holm"]]
              .to_string(index=False))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else C.OUT_ROOT)
