"""
run_batch.py -- full experiment: demand scenarios x fleet compositions x seeds,
one Vissim run each, shuttles driven by the calibrated IDM through COM
(com_idm_driver.py). Everything network-specific comes from config.py
(config/network_config.json).

What one run does
  1. set the vehicle inputs of the demand scenario (Volume(1) of each input),
  2. rebuild the transit composition for the requested AS/bus share and verify
     it by reading it back (a wrong RelFlow silently changes the experiment),
  3. forbid lane changes for the shuttle class on every lane (once per session),
  4. run SIM_PERIOD seconds step by step; at each step the driver applies
         v_shuttle = min(v_Vissim, v_IDM, v_cap),
  5. move the direct outputs (.fzp vehicle record, .err, ...) and a copy of the
     run's result database (<stem>.results/<runNo>.db, node evaluation) into
     <output_root>/<scenario>/<composition>/seed_<N>/.

Resumable: a cell whose seed folder already holds a .fzp is skipped.
A second worker can run on a copy of the network: AS_NET=<copy.inpx>.

USAGE
  python run_batch.py --smoke   # COM-only composition check + one 600 s run
  python run_batch.py           # the full grid
Requires Windows, PTV Vissim (COM) and pywin32:  pip install pywin32
"""
import os
import re
import sys
import glob
import shutil
import time
import win32com.client as com

import config as C
import com_idm_driver as cid

HERE = C.HERE
NET_FILE = C.NET_FILE
NET_STEM = C.NET_STEM
RESULTS_DIR = C.RESULTS_DIR
OUT_ROOT = C.OUT_ROOT
IDM_PARAM_FILE = C.IDM_PARAM_FILE
SEEDS = C.SEEDS
SIM_PERIOD = C.SIM_PERIOD
DEMAND = C.DEMAND                 # {scenario: {input_no: veh/h}}
COMPOSITIONS = C.SHARE            # {composition name: AS share of the transit composition}
AS_TYPE, AS_DSD = C.AS_TYPE, C.AS_DSD
BUS_TYPE, BUS_DSD = C.BUS_TYPE, C.BUS_DSD
TRANSIT_COMP = C.TRANSIT_COMP
OUTPUT_EXTS = (".fzp", ".err", ".mer", ".rsr", ".att")


class CompositionError(RuntimeError):
    pass


def read_composition(vissim):
    """{veh_type: (des_speed_distr, rel_flow)} as Vissim currently holds it."""
    rel = vissim.Net.VehicleCompositions.ItemByKey(TRANSIT_COMP).VehCompRelFlows
    out = {}
    for rf in rel.GetAll():
        out[int(rf.AttValue("VehType"))] = (int(rf.AttValue("DesSpeedDistr")),
                                             float(rf.AttValue("RelFlow")))
    return out


def set_composition(vissim, as_share, log=print):
    """Rebuild the transit composition for the requested AS/bus split and VERIFY it."""
    wanted = {}
    if as_share > 0:
        wanted[AS_TYPE] = (AS_DSD, as_share)
    if as_share < 1:
        wanted[BUS_TYPE] = (BUS_DSD, 1.0 - as_share)

    for attempt in range(3):
        comp = vissim.Net.VehicleCompositions.ItemByKey(TRANSIT_COMP)
        rel = comp.VehCompRelFlows
        existing = {int(rf.AttValue("VehType")): rf for rf in rel.GetAll()}
        for vt, (dsd, _) in wanted.items():
            if vt not in existing:
                sd = getattr(vissim.Net, "DesSpeedDistributions", None) \
                    or vissim.Net.SpeedDistributions
                rel.AddVehicleCompositionRelativeFlow(
                    vissim.Net.VehicleTypes.ItemByKey(vt), sd.ItemByKey(dsd))
        rel = vissim.Net.VehicleCompositions.ItemByKey(TRANSIT_COMP).VehCompRelFlows
        for rf in rel.GetAll():
            vt = int(rf.AttValue("VehType"))
            if vt in wanted:
                rf.SetAttValue("DesSpeedDistr", wanted[vt][0])
                rf.SetAttValue("RelFlow", wanted[vt][1])
        rel = vissim.Net.VehicleCompositions.ItemByKey(TRANSIT_COMP).VehCompRelFlows
        for rf in list(rel.GetAll()):
            if int(rf.AttValue("VehType")) not in wanted:
                rel.RemoveVehicleCompositionRelativeFlow(rf)

        got = read_composition(vissim)
        total = sum(v[1] for v in got.values())
        ok = set(got) == set(wanted)
        for vt, (dsd, share) in wanted.items():
            if vt in got:
                g_dsd, g_flow = got[vt]
                if g_dsd != dsd or abs(g_flow / total - share) > 1e-6:
                    ok = False
        if ok:
            log("    composition verified: " + ", ".join(
                f"type {vt}: distr {d}, share {f/total:.2f}"
                for vt, (d, f) in sorted(got.items())))
            return got
        log(f"    composition read-back MISMATCH (attempt {attempt+1}): "
            f"wanted {wanted}, got {got}")
    raise CompositionError(f"could not set composition for AS share {as_share}")


def set_demand(vissim, volumes):
    """Set Volume(1) of the listed vehicle inputs; returns all inputs as read back."""
    for input_no, vol in volumes.items():
        vi = vissim.Net.VehicleInputs.ItemByKey(input_no)
        vi.SetAttValue("Volume(1)", vol)
    return {int(vi.AttValue("No")): (int(vi.AttValue("Link")),
                                     float(vi.AttValue("Volume(1)")))
            for vi in vissim.Net.VehicleInputs.GetAll()}


def check_network_driver_flag(log=print):
    """The shuttle vehicle type must not use an external driver model
    (extDriver="false"): the COM driver sets the speed itself."""
    xml = open(NET_FILE, encoding="utf-8", errors="ignore").read()
    m = re.search(r'<vehicleType [^>]*no="%d"[^>]*>' % AS_TYPE, xml)
    if not m:
        raise RuntimeError(f"vehicle type {AS_TYPE} not found in {os.path.basename(NET_FILE)}")
    flag = re.search(r'extDriver="([^"]*)"', m.group(0))
    if flag and flag.group(1) != "false":
        raise RuntimeError(f"{os.path.basename(NET_FILE)}: vehicle type {AS_TYPE} has "
                           f"extDriver={flag.group(1)!r}; set it to false for the COM driver")
    log(f"    network {os.path.basename(NET_FILE)}: shuttle type {AS_TYPE}, external driver off")


def load_params(log=print):
    p = cid.read_params(IDM_PARAM_FILE)
    log(f"    IDM parameters ({os.path.basename(IDM_PARAM_FILE)}): " +
        ", ".join(f"{k}={v}" for k, v in p.items()))
    return p


def collect_outputs(net_dir, dest_dir, stem=None):
    """Vissim writes direct output (.fzp/.err/.mer/.rsr) next to the .inpx."""
    os.makedirs(dest_dir, exist_ok=True)
    stem = stem or NET_STEM
    pat = re.compile(r"^" + re.escape(stem) + r"_([0-9]+|exception)[.]", re.I)
    moved = 0
    for f in os.listdir(net_dir):
        if pat.match(f) and f.lower().endswith(OUTPUT_EXTS):
            shutil.move(os.path.join(net_dir, f), os.path.join(dest_dir, f))
            moved += 1
    return moved


def copy_run_db(dest_dir, log=None):
    """Copy the newest run database into the run's output folder.

    Vissim purges the stored result tables of previous runs when a later
    simulation starts after the network changed, so each run keeps its own
    copy next to its .fzp; extract_node_link.py prefers that copy."""
    dbs = glob.glob(os.path.join(RESULTS_DIR, "*.db"))
    if not dbs:
        return None
    newest = max(dbs, key=lambda q: int(os.path.basename(q)[:-3]))
    target = os.path.join(dest_dir, os.path.basename(newest))
    shutil.copy2(newest, target)
    if log:
        log(f"    run db {os.path.basename(newest)} copied to the run folder")
    return target


def keep_awake(on=True):
    """Tell Windows the system and display are in use (prevents Modern
    Standby while the batch runs; released automatically when the process
    exits). ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED."""
    try:
        import ctypes
        flags = 0x80000000 | (0x00000001 | 0x00000002 if on else 0)
        ctypes.windll.kernel32.SetThreadExecutionState(flags)
    except Exception:
        pass


def open_vissim(log=print):
    keep_awake(True)
    vissim = com.DispatchEx("Vissim.Vissim")  # new instance, never the open GUI
    vissim.LoadNet(NET_FILE)
    vissim.SuspendUpdateGUI()
    sim = vissim.Simulation
    sim.SetAttValue("NumRuns", 1)
    sim.SetAttValue("UseMaxSimSpeed", True)
    cid.forbid_as_lane_changes(vissim, log)
    return vissim, sim


def close_vissim(vissim):
    try:
        vissim.Exit()
    except Exception:
        pass


def run_one(vissim, sim, seed, params, log):
    """One simulation run with the COM-driven IDM; returns wall seconds."""
    sim.SetAttValue("RandSeed", seed)
    t0 = time.time()
    cid.drive(vissim, sim, params, int(sim.AttValue("SimPeriod")), log)
    return time.time() - t0


def as_summary(fzp):
    """Quick shuttle check from a .fzp: count, max speed, lane changes."""
    ids, vmax, lc, prev = set(), 0.0, 0, {}
    with open(fzp, errors="ignore") as f:
        for line in f:
            if not line[:1].isdigit():
                continue
            p = line.rstrip("\n").split(";")
            if len(p) < 15 or p[4] != str(AS_TYPE):
                continue
            ids.add(p[1])
            vmax = max(vmax, float(p[11]))
            active = p[14].strip() not in ("", "None")
            if active and not prev.get(p[1], False):
                lc += 1
            prev[p[1]] = active
    return len(ids), vmax, lc


def make_logger(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fh = open(path, "a")

    def log(msg):
        print(msg, flush=True)
        fh.write(msg + "\n")
        fh.flush()
    log.close = fh.close
    return log


def smoke():
    """COM mechanics + one short run: run this before any batch."""
    out = OUT_ROOT + "_smoke"
    log = make_logger(os.path.join(out, "smoke_log.txt"))
    log(f"=== smoke {time.strftime('%Y-%m-%d %H:%M:%S')} ===")
    log("    " + C.describe())
    check_network_driver_flag(log)
    params = load_params(log)
    vissim, sim = open_vissim(log)
    net_dir = os.path.dirname(NET_FILE)
    n = collect_outputs(net_dir, os.path.join(out, "_stale"))
    if n:
        log(f"moved {n} leftover output file(s) to {out}\\_stale")
    first_sc = C.SCENARIOS[0]
    inputs = set_demand(vissim, DEMAND[first_sc])
    log(f"    inputs after set_demand({first_sc}): " + ", ".join(
        f"{no}:link{lk}={vol:.0f}" for no, (lk, vol) in sorted(inputs.items())))
    log("composition transitions (COM only):")
    names = list(COMPOSITIONS)
    for name in names + [names[0], names[-1]]:
        log(f"  -> {name}")
        set_composition(vissim, COMPOSITIONS[name], log)
    log(f"one 600 s run with {names[-1]}, seed {SEEDS[0]}:")
    sim.SetAttValue("SimPeriod", 600)
    dur = run_one(vissim, sim, SEEDS[0], params, log)
    dest = os.path.join(out, "run_600s")
    n = collect_outputs(net_dir, dest)
    log(f"    run done in {dur:.0f}s, {n} output files -> {dest}")
    fzps = sorted(glob.glob(os.path.join(dest, "*.fzp")), key=os.path.getmtime)
    if fzps:
        n_as, vmax, lc = as_summary(fzps[-1])
        cap_kmh = params["cap"] * 3.6
        verdict = ("OK" if (n_as == 0 or (vmax <= cap_kmh + 0.5 and lc == 0))
                   else "FAIL: shuttle above cap or changed lanes")
        log(f"    shuttles {n_as}, max speed {vmax:.2f} km/h (cap {cap_kmh:.2f}), "
            f"lane changes {lc} -> {verdict}")
    else:
        log("    no .fzp produced: enable the vehicle record in the network "
            "(see docs/network_setup.md)")
    vissim.ResumeUpdateGUI()
    close_vissim(vissim)
    log("SMOKE DONE")
    log.close()


def main():
    if "--smoke" in sys.argv:
        smoke()
        return
    if any(os.path.isdir(os.path.join(OUT_ROOT, sc)) for sc in DEMAND):
        print(f"{OUT_ROOT} already holds scenario folders from an earlier batch. "
              "Rename that folder first so the new runs do not mix with the old "
              "ones (or delete the seed folders you want to re-run). Nothing was started.")
        raise SystemExit(1)
    log = make_logger(os.path.join(OUT_ROOT, "batch_log.txt"))
    total = len(DEMAND) * len(COMPOSITIONS) * len(SEEDS)
    log(f"=== batch start {time.strftime('%Y-%m-%d %H:%M:%S')}: {total} runs ===")
    log("    " + C.describe())
    check_network_driver_flag(log)
    params = load_params(log)
    vissim, sim = open_vissim(log)
    sim.SetAttValue("SimPeriod", SIM_PERIOD)
    net_dir = os.path.dirname(NET_FILE)
    n = collect_outputs(net_dir, os.path.join(OUT_ROOT, "_stale"))
    if n:
        log(f"moved {n} leftover output file(s) to _stale/")

    done = 0
    for sc, volumes in DEMAND.items():
        inputs = set_demand(vissim, volumes)
        log(f"{sc}: inputs " + ", ".join(
            f"{no}:link{lk}={vol:.0f}" for no, (lk, vol) in sorted(inputs.items())))
        for comp_name, as_share in COMPOSITIONS.items():
            set_composition(vissim, as_share, log)
            for seed in SEEDS:
                dest = os.path.join(OUT_ROOT, sc, comp_name, f"seed_{seed}")
                if glob.glob(os.path.join(dest, "*.fzp")):
                    log(f"skip (exists) {sc} {comp_name} seed {seed}")
                    continue
                dur = run_one(vissim, sim, seed, params, log)
                n = collect_outputs(net_dir, dest)
                copy_run_db(dest)
                done += 1
                log(f"[{done}/{total}] {sc} {comp_name} seed {seed} done in "
                    f"{dur:.0f}s ({n} files)")
    vissim.ResumeUpdateGUI()
    close_vissim(vissim)
    log(f"=== batch complete: {done} runs ===")
    log.close()


if __name__ == "__main__":
    main()
