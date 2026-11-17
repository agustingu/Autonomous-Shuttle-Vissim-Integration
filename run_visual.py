"""
run_visual.py -- ONE simulation run of ONE scenario with the Vissim GUI
visible (vehicles animated), shuttles driven by the same COM-IDM driver as
the batch (com_idm_driver.drive). For watching, not for the paper's numbers.

The run uses its own copy of the network, <stem>_visual.inpx (+ .layx),
refreshed from the configured network at every start, so it has its own run
numbering and its own <stem>_visual.results folder: the batch results in
<stem>.results are never touched. Outputs (.fzp, .err, run .db)
go to visual_results\\<SC>_<composition>_seed<seed>_<timestamp>\\.

USAGE
  python run_visual.py                          # first sensitivity scenario, all-AS fleet, first seed, 5x real time
  python run_visual.py SC-5 10AS_10Bus 43       # scenario, composition, seed (names from the config)
  python run_visual.py --speed 10               # 10 simulation seconds per wall second
  python run_visual.py --max                    # no pacing (as fast as the COM loop runs)
  python run_visual.py --period 900             # shorter run (s), default 3600
  python run_visual.py --3d                     # 3D view
  python run_visual.py --close                  # exit Vissim when the run ends
                                                # (default: Vissim stays open)
Give the shuttle vehicle type its own colour distribution in the network to
tell shuttles and buses apart. Never start this while a batch is running.

Pace: the pacer only ever slows the loop down. With the GUI live the COM
calls are about ten times slower than in the batch, and interacting with the
GUI (lists, dialogs) slows them further, so the real pace can fall below
--speed. Do not start another run from the GUI while the
script is collecting its files.
"""
import os
import sys
import glob
import shutil
import subprocess
import time

import importlib
import config as C
HERE = C.HERE
SRC_NET = C.NET_FILE
VIS_NET = os.path.splitext(SRC_NET)[0] + "_visual.inpx"
OUT_ROOT = os.path.join(HERE, "visual_results")

# the visual copy must be chosen BEFORE run_batch is imported (NET_FILE,
# NET_STEM and RESULTS_DIR are derived from AS_NET when config is loaded)
os.environ["AS_NET"] = VIS_NET
importlib.reload(C)
import win32com.client as com        # noqa: E402
import run_batch as rb               # noqa: E402
import com_idm_driver as cid         # noqa: E402


def parse_args(argv):
    opts = dict(sc=C.SENS_SCENARIOS[0], comp=C.FULL_COMP, seed=C.SEEDS[0], speed=5.0, max_speed=False,
                period=3600, three_d=False, close=False)
    pos = []
    it = iter(argv)
    for a in it:
        if a == "--speed":
            opts["speed"] = float(next(it))
        elif a == "--max":
            opts["max_speed"] = True
        elif a == "--period":
            opts["period"] = int(next(it))
        elif a == "--3d":
            opts["three_d"] = True
        elif a == "--close":
            opts["close"] = True
        elif a in ("-h", "--help"):
            print(__doc__)
            raise SystemExit(0)
        else:
            pos.append(a)
    if len(pos) > 0:
        opts["sc"] = pos[0].upper()
    if len(pos) > 1:
        opts["comp"] = pos[1]
    if len(pos) > 2:
        opts["seed"] = int(pos[2])
    if opts["sc"] not in rb.DEMAND:
        raise SystemExit(f"unknown scenario {opts['sc']!r}; choose from {list(rb.DEMAND)}")
    if opts["comp"] not in rb.COMPOSITIONS:
        raise SystemExit(f"unknown composition {opts['comp']!r}; choose from {list(rb.COMPOSITIONS)}")
    return opts


def vissim_pids():
    """PIDs of the Vissim processes currently running."""
    try:
        out = subprocess.check_output(
            ["tasklist", "/FI", "IMAGENAME eq Vissim260.exe", "/FO", "CSV", "/NH"],
            text=True, stderr=subprocess.DEVNULL)
    except Exception:
        return set()
    pids = set()
    for line in out.splitlines():
        parts = [p.strip().strip('"') for p in line.split('","')]
        if len(parts) >= 2 and parts[0].lower().startswith("vissim"):
            try:
                pids.add(int(parts[1]))
            except ValueError:
                pass
    return pids


def bring_to_front(pids):
    """Maximise and raise the main window(s) of the given Vissim PIDs."""
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        found = []

        @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
        def cb(hwnd, lparam):
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value in pids and user32.IsWindowVisible(hwnd):
                n = user32.GetWindowTextLengthW(hwnd)
                buf = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(hwnd, buf, n + 1)
                if "vissim" in buf.value.lower():
                    found.append(hwnd)
            return True

        user32.EnumWindows(cb, 0)
        for hwnd in found:
            user32.ShowWindow(hwnd, 3)          # SW_MAXIMIZE
            user32.SetForegroundWindow(hwnd)
        return len(found)
    except Exception:
        return 0


def make_pacer(speed, dt, log, period):
    """Keep the animation at `speed` sim seconds per wall second even if
    RunSingleStep does not honour SimSpeed; also logs progress every 5 min."""
    t0 = [None]
    every = int(round(300.0 / dt))

    def on_step(i):
        if t0[0] is None:
            t0[0] = time.perf_counter()
        if speed:
            due = t0[0] + (i + 1) * dt / speed
            now = time.perf_counter()
            if due > now:
                time.sleep(due - now)
        if (i + 1) % every == 0 or (i + 1) * dt >= period:
            sim_t = (i + 1) * dt
            log(f"    t = {sim_t:5.0f} s  (wall {time.perf_counter() - t0[0]:5.0f} s)")
    return on_step


def collect_run(net_dir, dest, log):
    """Move this run's output files (<stem>_NNN.*) into dest. A file Vissim
    still holds open (a further run started from the GUI after the scripted
    one ended) is skipped with a warning instead of aborting.
    Returns (files moved, run number of the largest .fzp moved)."""
    import re
    pat = re.compile(r"^" + re.escape(rb.NET_STEM) + r"_([0-9]+|exception)[.]", re.I)
    moved, sizes = 0, {}
    for f in sorted(os.listdir(net_dir)):
        m = pat.match(f)
        if not m or not f.lower().endswith(rb.OUTPUT_EXTS):
            continue
        src, dst = os.path.join(net_dir, f), os.path.join(dest, f)
        for attempt in range(3):
            try:
                os.replace(src, dst)        # rename only, never a half copy
                moved += 1
                if f.lower().endswith(".fzp") and m.group(1).isdigit():
                    sizes[int(m.group(1))] = os.path.getsize(dst)
                break
            except PermissionError:
                if attempt < 2:
                    time.sleep(2)
                else:
                    log(f"    WARNING: {f} is still open in Vissim (another run "
                        f"started from the GUI?) -- left in place")
    run_no = max(sizes, key=sizes.get) if sizes else None
    return moved, run_no


def main():
    opts = parse_args(sys.argv[1:])
    stamp = time.strftime("%Y%m%d-%H%M")
    dest = os.path.join(OUT_ROOT, f"{opts['sc']}_{opts['comp']}_seed{opts['seed']}_{stamp}")
    os.makedirs(dest, exist_ok=True)
    log_f = open(os.path.join(dest, "visual_log.txt"), "a")

    def log(msg):
        print(msg, flush=True)
        log_f.write(msg + "\n")
        log_f.flush()

    # fresh copy of the batch network (+ layout) under the visual stem
    shutil.copy2(SRC_NET, VIS_NET)
    src_lay = SRC_NET[:-5] + ".layx"
    if os.path.exists(src_lay):
        shutil.copy2(src_lay, VIS_NET[:-5] + ".layx")
    log(f"=== visual run {stamp}: {opts['sc']} {opts['comp']} seed {opts['seed']}, "
        f"{opts['period']} s, " + ("max speed" if opts["max_speed"] else f"{opts['speed']:g}x") +
        f", {'3D' if opts['three_d'] else '2D'} ===")
    log(f"    network copy {os.path.basename(VIS_NET)} refreshed from {os.path.basename(SRC_NET)}")
    before = vissim_pids()
    if before:
        log(f"    note: {len(before)} Vissim instance(s) already open (left alone)")
    rb.check_network_driver_flag(log)
    params = rb.load_params(log)

    rb.keep_awake(True)
    vissim = com.DispatchEx("Vissim.Vissim")   # new instance, GUI visible
    vissim.LoadNet(VIS_NET)
    new_pids = vissim_pids() - before
    sim = vissim.Simulation
    sim.SetAttValue("NumRuns", 1)
    sim.SetAttValue("SimPeriod", opts["period"])
    if opts["max_speed"]:
        sim.SetAttValue("UseMaxSimSpeed", True)
    else:
        sim.SetAttValue("UseMaxSimSpeed", False)
        sim.SetAttValue("SimSpeed", opts["speed"])
    win = vissim.Graphics.CurrentNetworkWindow
    win.SetAttValue("QuickMode", 0)             # draw vehicles
    win.SetAttValue("3D", 1 if opts["three_d"] else 0)
    cid.forbid_as_lane_changes(vissim, log)
    inputs = rb.set_demand(vissim, rb.DEMAND[opts["sc"]])
    log(f"    {opts['sc']}: inputs " + ", ".join(
        f"{no}:link{lk}={vol:.0f}" for no, (lk, vol) in sorted(inputs.items())))
    rb.set_composition(vissim, rb.COMPOSITIONS[opts["comp"]], log)
    net_dir = os.path.dirname(VIS_NET)
    n = rb.collect_outputs(net_dir, os.path.join(OUT_ROOT, "_stale"))
    if n:
        log(f"    moved {n} leftover output file(s) to _stale/")
    n_win = bring_to_front(new_pids)
    log(f"    Vissim window(s) raised: {n_win} (pid {sorted(new_pids)})")

    sim.SetAttValue("RandSeed", opts["seed"])
    pacer = make_pacer(None if opts["max_speed"] else opts["speed"], cid.DT, log, opts["period"])
    log("    simulation running -- watch the Vissim window (shuttles = red cars)")
    t0 = time.time()
    cid.drive(vissim, sim, params, opts["period"], log, on_step=pacer)
    dur = time.time() - t0

    n, run_no = collect_run(net_dir, dest, log)
    if run_no is not None:
        db = os.path.join(rb.RESULTS_DIR, f"{run_no}.db")
        if os.path.exists(db):
            shutil.copy2(db, dest)
            log(f"    run db {run_no}.db copied to the run folder")
        fzp = os.path.join(dest, f"{rb.NET_STEM}_{run_no:03d}.fzp")
        if os.path.exists(fzp):
            n_as, vmax, lc = rb.as_summary(fzp)
            log(f"    shuttles in .fzp: {n_as}, max speed {vmax:.1f} km/h, lane changes {lc}")
    log(f"=== run done in {dur:.0f} s wall, {n} output file(s) in {dest} ===")
    if opts["close"]:
        rb.close_vissim(vissim)
        log("    Vissim closed")
    else:
        log("    Vissim left open for inspection (close it from its window, or rerun with --close)")
    log_f.close()


if __name__ == "__main__":
    main()
