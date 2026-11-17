"""
run_sensitivity.py -- sensitivity experiments on a reduced grid (two demand
scenarios, all seeds), same network and shuttle driver as the main batch.
The grid and the variants come from the "sensitivity" block of
config/network_config.json.

Families
  idm         shuttle IDM parameters, full-AS composition only. Base = the
              calibrated file; each listed parameter at each factor (default
              a, T, s0, b at x0.75 and x1.25), plus an optional alternative
              parameter file (alternative_params_file) for contrast.
  turns       non-through route shares of the listed mainline routing
              decisions scaled by each factor (the through share absorbs the
              difference); first and last compositions (no AS / all AS).
  lanechange  attributes of one driving behavior (gap acceptance, cooperative
              lane change, decelerations, ...); first and last compositions.

USAGE
  python run_sensitivity.py --check               set + read back every variant via COM, no simulation
  python run_sensitivity.py idm|turns|lanechange  run one family (resumable: existing seed folders are skipped)
Outputs: <sensitivity_output_root>/<family>/<variant>/<scenario>/<composition>/seed_N/ (+ log.txt)
Then:    python extract_metrics.py   <root>/<family>/<variant>
         python extract_node_link.py <root>/<family>/<variant>
         python summarize_sensitivity.py ; python sensitivity_effects.py
"""
import os
import sys
import glob
import time

import config as C
import run_batch as rb
import com_idm_driver as cid

OUT_ROOT = C.SENS_ROOT
SENS_PAR_DIR = os.path.join(OUT_ROOT, "sens_params")   # the parameter files actually used
SCS = C.SENS_SCENARIOS
SEEDS = C.SEEDS
SIM_PERIOD = C.SIM_PERIOD
MAINLINE_DECISIONS = C.SENS_DECISIONS
COMPS_2 = [C.BASE_COMP, C.FULL_COMP]

IDM_VARIANTS = {"base": {}}
for p in C.SENS_IDM_PARAMS:
    for f in C.SENS_IDM_FACTORS:
        IDM_VARIANTS[f"{p}_x{f:g}"] = {p: f}
if C.IDM_ALT_PARAM_FILE:
    IDM_VARIANTS[os.path.splitext(os.path.basename(C.IDM_ALT_PARAM_FILE))[0]] = C.IDM_ALT_PARAM_FILE

TURN_VARIANTS = {f"turns_x{f:g}": f for f in C.SENS_TURN_FACTORS}

LC_VARIANTS = {name: spec["attributes"] for name, spec in C.SENS_LC_VARIANTS.items()}


def make_logger(family):
    return rb.make_logger(os.path.join(OUT_ROOT, family, "log.txt"))


# ---------------------------------------------------------------------------
def idm_variant_params():
    """{name: params dict}; also written to sens_params/ for the record."""
    os.makedirs(SENS_PAR_DIR, exist_ok=True)
    base = cid.read_params(C.IDM_PARAM_FILE)
    out = {}
    for name, spec in IDM_VARIANTS.items():
        if isinstance(spec, str):
            vals = cid.read_params(spec)
        else:
            vals = dict(base)
            for k, f in spec.items():
                vals[k] = round(base[k] * f, 6)
        with open(os.path.join(SENS_PAR_DIR, f"idm_{name}.txt"), "w") as fh:
            fh.write(f"# sensitivity variant {name}: {spec or 'calibrated values'}\n")
            for k, val in vals.items():
                fh.write(f"{k}={val}\n")
        out[name] = vals
    return out


def scale_turns(vissim, factor, log):
    """Scale the non-through route shares of the mainline decisions; the
    through route (largest share) absorbs the difference."""
    if not MAINLINE_DECISIONS:
        raise RuntimeError("sensitivity.mainline_routing_decisions is empty in the config")
    for dno in MAINLINE_DECISIONS:
        dec = vissim.Net.VehicleRoutingDecisionsStatic.ItemByKey(dno)
        routes = list(dec.VehRoutSta.GetAll())
        flows = [float(r.AttValue("RelFlow(1)")) for r in routes]
        tot = sum(flows)
        if tot <= 0:
            continue
        shares = [f / tot for f in flows]
        i_thr = max(range(len(shares)), key=lambda i: shares[i])
        new = [s * factor if i != i_thr else 0.0 for i, s in enumerate(shares)]
        new[i_thr] = max(0.0, 1.0 - sum(new))
        for r, s in zip(routes, new):
            r.SetAttValue("RelFlow(1)", s)
        back = [float(r.AttValue("RelFlow(1)")) for r in dec.VehRoutSta.GetAll()]
        log(f"    decision {dno} (link {dec.AttValue('Link')}): shares "
            f"{[round(s, 3) for s in shares]} -> {[round(b, 3) for b in back]}")
        if any(abs(a - b) > 1e-6 for a, b in zip(new, back)):
            raise RuntimeError(f"turn shares read-back mismatch at decision {dno}")


def set_driving_behavior(vissim, attrs, log):
    db = vissim.Net.DrivingBehaviors.ItemByKey(C.SENS_DRIVING_BEHAVIOR)
    for k, v in attrs.items():
        before = db.AttValue(k)
        db.SetAttValue(k, v)
        after = db.AttValue(k)
        log(f"    driving behavior {C.SENS_DRIVING_BEHAVIOR}: {k} {before} -> {after}")
        if (isinstance(v, bool) and bool(after) != v) or \
           (not isinstance(v, bool) and abs(float(after) - v) > 1e-9):
            raise RuntimeError(f"driving behavior read-back mismatch for {k}")


# ---------------------------------------------------------------------------
def grid(vissim, sim, family, variant, comps, params, log):
    net_dir = os.path.dirname(rb.NET_FILE)
    n = 0
    for sc in SCS:
        inputs = rb.set_demand(vissim, rb.DEMAND[sc])
        log(f"  {sc}: inputs " + ", ".join(
            f"{no}:link{lk}={vol:.0f}" for no, (lk, vol) in sorted(inputs.items())))
        for comp in comps:
            rb.set_composition(vissim, rb.COMPOSITIONS[comp], log)
            for seed in SEEDS:
                dest = os.path.join(OUT_ROOT, family, variant, sc, comp, f"seed_{seed}")
                if glob.glob(os.path.join(dest, "*.fzp")):
                    log(f"  skip (exists) {variant} {sc} {comp} seed {seed}")
                    continue
                dur = rb.run_one(vissim, sim, seed, params, log)
                k = rb.collect_outputs(net_dir, dest)
                rb.copy_run_db(dest)
                n += 1
                log(f"  [{variant}] {sc} {comp} seed {seed} done in {dur:.0f}s ({k} files)")
    return n


def open_net(log):
    vissim, sim = rb.open_vissim(log)
    sim.SetAttValue("SimPeriod", SIM_PERIOD)
    rb.collect_outputs(os.path.dirname(rb.NET_FILE), os.path.join(OUT_ROOT, "_stale"))
    return vissim, sim


def family_idm(log):
    rb.load_params(log)
    total = 0
    vissim, sim = open_net(log)
    for name, params in idm_variant_params().items():
        log(f"=== idm variant {name}: {params}")
        total += grid(vissim, sim, "idm", name, [C.FULL_COMP], params, log)
    rb.close_vissim(vissim)
    return total


def family_turns(log):
    params = rb.load_params(log)
    total = 0
    for name, factor in TURN_VARIANTS.items():
        log(f"=== turns variant {name}: factor {factor}")
        vissim, sim = open_net(log)
        scale_turns(vissim, factor, log)
        total += grid(vissim, sim, "turns", name, COMPS_2, params, log)
        rb.close_vissim(vissim)
    return total


def family_lanechange(log):
    params = rb.load_params(log)
    total = 0
    for name, attrs in LC_VARIANTS.items():
        log(f"=== lanechange variant {name}: {attrs}")
        vissim, sim = open_net(log)
        set_driving_behavior(vissim, attrs, log)
        total += grid(vissim, sim, "lanechange", name, COMPS_2, params, log)
        rb.close_vissim(vissim)
    return total


def check(log):
    """COM mechanics only: no simulation."""
    log("    " + C.describe())
    rb.check_network_driver_flag(log)
    log("idm variants:")
    for name, vals in idm_variant_params().items():
        log(f"  {name:12s} {vals}")
    vissim, sim = open_net(log)
    for name, factor in TURN_VARIANTS.items():
        log(f"turns {name}:")
        scale_turns(vissim, factor, log)
        vissim.LoadNet(rb.NET_FILE)
    for name, attrs in LC_VARIANTS.items():
        log(f"lanechange {name}:")
        set_driving_behavior(vissim, attrs, log)
        vissim.LoadNet(rb.NET_FILE)
    rb.close_vissim(vissim)
    log("CHECK DONE (no simulation run)")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    arg = sys.argv[1]
    if arg == "--check":
        check(make_logger("_check"))
        return
    fam = {"idm": family_idm, "turns": family_turns, "lanechange": family_lanechange}
    if arg not in fam:
        print(__doc__)
        return
    log = make_logger(arg)
    log(f"=== {arg} start {time.strftime('%Y-%m-%d %H:%M:%S')} ===")
    n = fam[arg](log)
    log(f"=== {arg} done: {n} runs ===")


if __name__ == "__main__":
    main()
