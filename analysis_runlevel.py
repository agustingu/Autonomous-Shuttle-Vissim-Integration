"""
analysis_runlevel.py -- run-level two-way ANOVA of the experiment.

Model per metric (one observation = one simulation run):
    y = demand scenario * fleet composition + seed (block)
  * Type-II ANOVA with F, p and partial eta-squared for demand, composition
    and their interaction. The random seed enters as a blocking factor
    because the same five seeds are reused in every cell (same background
    arrival pattern), which removes that shared variance.
  * Estimated effects with 95% confidence intervals (t-based on HC3
    heteroscedasticity-consistent standard errors: the residual variance at
    SC-6 is an order of magnitude larger than at the other demand levels,
    so the pooled residual variance would overstate the uncertainty of the
    simple effects at SC-1..SC-5 and understate it at SC-6):
      - each composition vs the 0/20 baseline, per demand scenario (simple
        effects from the interaction model) and averaged over the six
        scenarios (marginal effect), Holm-adjusted within each metric;
      - all pairwise composition contrasts, marginal, Holm-adjusted;
      - the linear effect of AS share "per 5 shuttles" (per 0.25 of share),
        per scenario and marginal, from  y = demand * share + seed.
  * AS-specific metrics (undefined at 0/20) use the four AS compositions.

Design matrices are built by hand (treatment coding, fixed level order) so
the contrasts do not depend on the formula engine of statsmodels.

Inputs (default: output_root of the config): node_link_by_run.csv, metrics_by_run.csv
Outputs (same folder):
    stats_runlevel_anova.csv      metric, effect, df, F, p, partial_eta2
    stats_runlevel_effects.csv    metric, scope, A, B, estimate, se, ci_low,
                                  ci_high, t, p_raw, p_holm
    stats_runlevel_slopes.csv     metric, scope, estimate (per 5 shuttles), ci
Usage:  python analysis_runlevel.py [batch_root]
"""
import os
import sys

import numpy as np
import pandas as pd
import statsmodels.api as sm
from scipy import stats

import config as C

COMPS = C.COMPS
SHARE = C.SHARE
SCS = C.SCENARIOS
SHARE_STEP = C.SHARE_STEP          # slope unit: per this AS share (= N shuttles added)

# (column, label, needs_AS)
METRICS = [
    ("node_delay_all", "Node delay, all vehicles (s/veh)", False),
    ("node_stops_all", "Node stops, all vehicles (stops/veh)", False),
    ("link_speed_all", "Link speed, all vehicles (km/h)", False),
    ("lc_per_vehkm", "Lane changes per veh-km", False),
    ("lc_events_total", "Lane changes per hour (count)", False),
    ("node_delay_bus", "Node delay, buses (s/veh)", False),
    ("node_delay_shuttle", "Node delay, shuttles (s/veh)", True),
    ("node_stops_shuttle", "Node stops, shuttles (stops/veh)", True),
    ("link_speed_shuttle", "Link speed, shuttles (km/h)", True),
    ("as_travel_time_mean_s", "AS corridor travel time (s)", True),
    ("as_stops_mean", "Stops per AS", True),
    ("gap_behind_AS_mean", "Mean follower gap behind AS (m)", True),
    ("trapped_veh_count", "Vehicles trapped behind an AS", True),
    ("trapped_dwell_mean_s", "Mean dwell behind AS (s)", True),
    ("lc_share_near_AS", "Share of lane changes <=100 m upstream of AS", True),
]


def holm(p):
    p = np.asarray(p, float)
    m = len(p)
    order = np.argsort(p)
    adj = np.empty(m)
    run = 0.0
    for rank, i in enumerate(order):
        run = max(run, (m - rank) * p[i])
        adj[i] = min(run, 1.0)
    return adj


def load(root):
    nl = pd.read_csv(os.path.join(root, "node_link_by_run.csv"))
    if "complete" in nl:
        nl = nl[nl["complete"]]
    mt = pd.read_csv(os.path.join(root, "metrics_by_run.csv"))
    key = ["scenario", "composition", "seed"]
    mt_cols = key + [c for c in mt.columns if c not in nl.columns]
    df = nl.merge(mt[mt_cols], on=key, how="left")
    df["share"] = df.composition.map(SHARE)
    df["seed"] = df.seed.astype(int)
    return df


# ---------------------------------------------------------------------------
# hand-built treatment-coded design
# ---------------------------------------------------------------------------
class Design:
    """Columns: intercept | scenario dummies | composition dummies (or share)
    | interaction | seed dummies.  Reference levels = first of each list."""

    def __init__(self, scs, comps, seeds, numeric_share=False):
        self.scs, self.comps, self.seeds = scs, comps, seeds
        self.numeric = numeric_share
        self.names = ["intercept"] + [f"sc[{s}]" for s in scs[1:]]
        if numeric_share:
            self.names += ["share"] + [f"sc[{s}]:share" for s in scs[1:]]
        else:
            self.names += [f"comp[{c}]" for c in comps[1:]]
            self.names += [f"sc[{s}]:comp[{c}]" for s in scs[1:] for c in comps[1:]]
        self.names += [f"seed[{k}]" for k in seeds[1:]]
        self.idx = {n: i for i, n in enumerate(self.names)}

    def row(self, sc, comp=None, share=None, seed=None):
        x = np.zeros(len(self.names))
        x[0] = 1.0
        if sc != self.scs[0]:
            x[self.idx[f"sc[{sc}]"]] = 1.0
        if self.numeric:
            x[self.idx["share"]] = share
            if sc != self.scs[0]:
                x[self.idx[f"sc[{sc}]:share"]] = share
        else:
            if comp != self.comps[0]:
                x[self.idx[f"comp[{comp}]"]] = 1.0
                if sc != self.scs[0]:
                    x[self.idx[f"sc[{sc}]:comp[{comp}]"]] = 1.0
        if seed is not None and seed != self.seeds[0]:
            x[self.idx[f"seed[{seed}]"]] = 1.0
        return x

    def matrix(self, d):
        return np.vstack([self.row(r.scenario, r.composition, r.share, r.seed)
                          for r in d.itertuples()])

    def cols(self, group):
        """column indices of an effect group: 'sc', 'comp', 'sc:comp', 'seed'"""
        pat = {"sc": lambda n: n.startswith("sc[") and ":" not in n,
               "comp": lambda n: n.startswith("comp[") or n == "share",
               "sc:comp": lambda n: ":" in n,
               "seed": lambda n: n.startswith("seed[")}[group]
        return [i for i, n in enumerate(self.names) if pat(n)]


def fit(X, y):
    return sm.OLS(y, X).fit()


def type2_anova(des, X, y):
    """Type-II sums of squares by model comparison."""
    full = fit(X, y)
    ss_res, df_res = full.ssr, full.df_resid
    rows = []
    groups = {"sc": ["comp", "seed"], "comp": ["sc", "seed"],
              "sc:comp": ["sc", "comp", "seed"], "seed": ["sc", "comp", "sc:comp"]}
    for g, others in groups.items():
        keep = [0] + sum((des.cols(o) for o in others), [])
        # type II: the model WITH the effect contains the effect and everything
        # that does not contain it; the interaction is dropped when testing
        # main effects
        with_cols = sorted(set(keep + des.cols(g)))
        without_cols = sorted(set(keep))
        m_with = fit(X[:, with_cols], y)
        m_without = fit(X[:, without_cols], y)
        ss = m_without.ssr - m_with.ssr
        df_e = len(des.cols(g))
        F = (ss / df_e) / (ss_res / df_res)
        p = stats.f.sf(F, df_e, df_res)
        rows.append(dict(effect=g, df=df_e, F=F, p=p,
                         partial_eta2=ss / (ss + ss_res)))
    rows.append(dict(effect="Residual", df=int(df_res), F=np.nan, p=np.nan,
                     partial_eta2=np.nan))
    return full, rows


def contrast(model, L):
    r = model.t_test(L)
    ci = np.asarray(r.conf_int()).ravel()
    return dict(estimate=float(np.ravel(r.effect)[0]), se=float(np.ravel(r.sd)[0]),
                ci_low=float(ci[0]), ci_high=float(ci[1]),
                t=float(np.ravel(r.tvalue)[0]), p_raw=float(np.ravel(r.pvalue)[0]))


def analyse_metric(df, col, needs_as, anova_rows, eff_rows, slope_rows):
    comps = [c for c in COMPS if not (needs_as and SHARE[c] == 0)]
    d = df[df.composition.isin(comps)][["scenario", "composition", "seed",
                                        "share", col]].dropna()
    d = d.rename(columns={col: "y"})
    if d.composition.nunique() < 2 or len(d) < 20:
        return
    scs = [s for s in SCS if s in set(d.scenario)]
    seeds = sorted(d.seed.unique())
    y = d.y.values

    des = Design(scs, comps, seeds)
    X = des.matrix(d)
    m, rows = type2_anova(des, X, y)
    for r in rows:
        anova_rows.append(dict(metric=col, **r))
    # contrasts use HC3 robust standard errors (heteroscedastic residuals)
    mr = m.get_robustcov_results(cov_type="HC3", use_t=True)

    cell = {(sc, c): des.row(sc, c) for sc in scs for c in comps}
    base = comps[0]
    local = []
    for c in comps[1:]:
        for sc in scs:
            local.append(dict(metric=col, scope=sc, A=c, B=base,
                              **contrast(mr, cell[(sc, c)] - cell[(sc, base)])))
        L = np.mean([cell[(sc, c)] - cell[(sc, base)] for sc in scs], axis=0)
        local.append(dict(metric=col, scope="marginal", A=c, B=base, **contrast(mr, L)))
    pairs = []
    for i in range(len(comps)):
        for j in range(i + 1, len(comps)):
            a, b = comps[i], comps[j]
            L = np.mean([cell[(sc, b)] - cell[(sc, a)] for sc in scs], axis=0)
            pairs.append(dict(metric=col, scope="marginal_pairwise", A=b, B=a,
                              **contrast(mr, L)))
    for group in (local, pairs):
        for r, pa in zip(group, holm([r["p_raw"] for r in group])):
            r["p_holm"] = pa
        eff_rows.extend(group)

    # linear effect of AS share: per SHARE_STEP of share (= N shuttles added)
    dn = Design(scs, comps, seeds, numeric_share=True)
    Xn = dn.matrix(d)
    mn = fit(Xn, y).get_robustcov_results(cov_type="HC3", use_t=True)
    per_sc = []
    for sc in scs:
        L = (dn.row(sc, share=1.0) - dn.row(sc, share=0.0)) * SHARE_STEP
        per_sc.append(L)
        slope_rows.append(dict(metric=col, scope=sc, **contrast(mn, L)))
    slope_rows.append(dict(metric=col, scope="marginal",
                           **contrast(mn, np.mean(per_sc, axis=0))))


def main(root):
    df = load(root)
    print(f"{len(df)} complete runs loaded from {root}")
    anova_rows, eff_rows, slope_rows = [], [], []
    for col, label, needs_as in METRICS:
        if col not in df.columns:
            print(f"  skip {col} (not in data)")
            continue
        analyse_metric(df, col, needs_as, anova_rows, eff_rows, slope_rows)
    an, ef, sl = (pd.DataFrame(r) for r in (anova_rows, eff_rows, slope_rows))
    an.to_csv(os.path.join(root, "stats_runlevel_anova.csv"), index=False)
    ef.to_csv(os.path.join(root, "stats_runlevel_effects.csv"), index=False)
    sl.to_csv(os.path.join(root, "stats_runlevel_slopes.csv"), index=False)

    pd.set_option("display.width", 220)
    fmt_p = lambda p: f"{p:.3f}" if p >= 0.001 else "<0.001"
    print("\nTwo-way ANOVA (seed as block): partial eta^2 (p)")
    a = an[an.effect != "Residual"]
    tab = (a.pivot(index="metric", columns="effect", values="partial_eta2").round(3).astype(str)
           + " (" + a.pivot(index="metric", columns="effect", values="p").applymap(fmt_p) + ")")
    print(tab[["sc", "comp", "sc:comp"]].rename(
        columns={"sc": "demand", "comp": "composition", "sc:comp": "interaction"}).to_string())
    print(f"\nMarginal effect of {C.FULL_COMP} vs the baseline composition (first AS composition for AS-only metrics), 95% CI, Holm p:")
    top = ef[(ef.scope == "marginal") & (ef.A == C.FULL_COMP)]
    print(top[["metric", "B", "estimate", "ci_low", "ci_high", "p_holm"]].round(3).to_string(index=False))
    print(f"\nLinear effect per {SHARE_STEP:g} of AS share (marginal over demand), 95% CI:")
    print(sl[sl.scope == "marginal"][["metric", "estimate", "ci_low", "ci_high", "p_raw"]]
          .round(4).to_string(index=False))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else C.OUT_ROOT)
