"""
as_time_states.py -- how the shuttles spend their time (read-only on the .fzp
vehicle records of a batch). Explains why the network results are insensitive
to moderate changes of the IDM parameters: the parameters only act while the
shuttle follows a leader below the cap or accelerates in free flow.

States per 1-s shuttle record:
  stopped       speed < 1 km/h
  at_cap        speed >= cap - 0.5 km/h
  following     otherwise, with a leader within 50 m (IDM interaction term active)
  free_accel    otherwise, accelerating (>= +0.1 m/s2), no leader within 50 m
  free_decel    otherwise, decelerating (<= -0.1 m/s2), no leader within 50 m
  cruise_below  otherwise (nearly constant speed below the cap)
Also: share of records with a leader within the look-ahead distance.

Usage: python as_time_states.py [batch_root] [composition] [scenario ...]
       defaults: output_root of the config, the all-AS composition, the
       sensitivity scenarios.  Writes <batch_root>/shuttle_time_states.csv
"""
import glob
import os
import sys
import numpy as np
import pandas as pd

import config as C
import com_idm_driver as cid


def read_fzp_shuttles(fz):
    with open(fz, encoding="latin-1") as fh:
        for i, line in enumerate(fh):
            if line.startswith("$VEHICLE:"):
                cols = line[len("$VEHICLE:"):].strip().split(";")
                hdr = i
                break
        else:
            raise ValueError(f"no $VEHICLE header in {fz}")
    df = pd.read_csv(fz, sep=";", skiprows=hdr + 1, names=cols, encoding="latin-1",
                     usecols=["SIMSEC", "NO", "VEHTYPE", "SPACING", "SPEED", "ACCELERATION", "LEADTARGNO"],
                     dtype={"LEADTARGNO": str})
    return df[df.VEHTYPE == C.AS_TYPE].copy()


def main(root, comp, scenarios):
    cap_kmh = cid.read_params(C.IDM_PARAM_FILE)["cap"] * 3.6
    rows = []
    for sc in scenarios:
        for seed in C.SEEDS:
            d = os.path.join(root, sc, comp, f"seed_{seed}")
            fz = glob.glob(os.path.join(d, "*.fzp"))
            if not fz:
                print("missing", d)
                continue
            a = read_fzp_shuttles(fz[0])
            lead = pd.to_numeric(a.LEADTARGNO, errors="coerce").fillna(-1)
            has_lead_la = (lead > 0) & (a.SPACING < C.LOOKAHEAD_M)
            has_lead50 = (lead > 0) & (a.SPACING < 50)
            v, acc = a.SPEED, a.ACCELERATION
            st = np.full(len(a), "cruise_below", dtype=object)
            st[(~has_lead50) & (acc <= -0.1)] = "free_decel"
            st[(~has_lead50) & (acc >= 0.1)] = "free_accel"
            st[has_lead50.values] = "following"
            st[(v >= cap_kmh - 0.5).values] = "at_cap"
            st[(v < 1.0).values] = "stopped"
            r = {"scenario": sc, "composition": comp, "seed": seed, "n_records": len(a),
                 "n_shuttles": a.NO.nunique(), "mean_speed_kmh": v.mean(),
                 "lead_within_lookahead": has_lead_la.mean(), "lead_within_50m": has_lead50.mean()}
            for s in ("stopped", "at_cap", "following", "free_accel", "free_decel", "cruise_below"):
                r[s] = (st == s).mean()
            rows.append(r)
            print(sc, seed, {k: round(r[k], 3) for k in ("stopped", "at_cap", "following", "free_accel")}, flush=True)
    res = pd.DataFrame(rows)
    out = os.path.join(root, "shuttle_time_states.csv")
    res.to_csv(out, index=False)
    print("\n=== mean over seeds (share of shuttle time) ===")
    print(res.groupby("scenario")[["mean_speed_kmh", "stopped", "at_cap", "following", "free_accel",
                                   "free_decel", "cruise_below", "lead_within_lookahead"]].mean().round(3).to_string())
    print("written", out)


if __name__ == "__main__":
    root = sys.argv[1] if len(sys.argv) > 1 else C.OUT_ROOT
    comp = sys.argv[2] if len(sys.argv) > 2 else C.FULL_COMP
    scs = sys.argv[3:] if len(sys.argv) > 3 else C.SENS_SCENARIOS
    main(root, comp, scs)
