"""
com_idm_driver.py -- drive the autonomous shuttles (the shuttle vehicle type of
config/network_config.json) with the calibrated IDM through Vissim's COM
interface, one simulation step at a time. No External Driver Model add-on is
needed; COM is part of the base license.

Rule applied at every step boundary t (dt = 0.2 s), for every shuttle:

    v_AS(t) = min( v_Vissim(t),  v_IDM(t),  v_cap )

  * v_Vissim(t): the speed Vissim's own model produced for this step. It
    handles signals, conflict areas, stop lines, routing and lane keeping, so
    a shuttle can never run a red light or hit a leader because of the IDM.
  * v_IDM(t) = v_AS(t-dt) + a_IDM(t-dt) * dt, the FULL IDM: with a vehicle
    leader in the same lane (net gap s, speed difference dv) the interaction
    term is active; without one, the free-road term a*(1 - v/v0) alone. The
    shuttle therefore never accelerates harder than the calibrated maximum
    acceleration a, in following or in free flow.
  * v_cap: the 12 mph operational limit of the deployment.
  * A stopped shuttle (v = 0) is released by Vissim (signal, queue) and then
    accelerates under the IDM.

Lane changes: shuttles are forbidden to change lanes through the lane
attributes NoLnChL/RVehClasses = <shuttle class> (forbid_as_lane_changes),
which reproduces the predefined-route operation of current deployments.

Leader detection: Vissim's "leading target" when it is a vehicle (exact net
gap from FollowDistNet); otherwise the next vehicle ahead in the same lane of
the same link (net gap = position difference minus the leader length), within
LOOKAHEAD_M. Signal heads, conflict areas and reduced-speed areas are left to
Vissim.

Parameters come from a key=value text file (config/idm_params_published.txt):
a, b, v0, s0, T in SI units and cap, the operational speed limit in m/s.
The former External Driver Model DLL (legacy_edm_dll/) handed the shuttle back
to Vissim's car acceleration when no vehicle was within the look-ahead; here
the IDM free term applies instead, which is the model as written.

API
    drive(vissim, sim, params, sim_period, log, on_step=None) -> dict(stats)
The driver is called by run_batch.py, run_sensitivity.py and run_visual.py.
"""
import math
import time

import config as C

AS_TYPE = C.AS_TYPE
DT = C.DT
LOOKAHEAD_M = C.LOOKAHEAD_M      # leader search distance (Vissim default 250 m)
ATTRS = ("No", "VehType", "Speed", "LeadTargNo", "LeadTargType", "FollowDistNet",
         "Lane\\Link\\No", "Lane\\Index", "Pos", "Length")


def read_params(path):
    out = {}
    for line in open(path):
        line = line.split("#")[0].strip()
        if "=" in line:
            k, val = line.split("=", 1)
            out[k.strip()] = float(val)
    return out


def idm_accel(v, s, dv, p):
    """IDM acceleration (m/s2), delta = 1 as in the calibrated model.
    s = None -> free road (interaction term off)."""
    free = p["a"] * (1.0 - v / p["v0"])
    if s is None:
        return free
    s_star = p["s0"] + max(0.0, v * p["T"] + v * dv / (2.0 * math.sqrt(p["a"] * p["b"])))
    return free - p["a"] * (s_star / max(s, 0.1)) ** 2


AS_CLASS = C.AS_CLASS            # vehicle class of the shuttles


def forbid_as_lane_changes(vissim, log=None):
    """Shuttles never change lanes: set the lane attributes NoLnChL/RVehClasses
    = shuttle class on every lane of every link (connectors excluded). Other
    classes are unaffected. Verified by read-back."""
    n = 0
    for lk in vissim.Net.Links.GetAll():
        if lk.AttValue("IsConn"):
            continue
        for ln in lk.Lanes.GetAll():
            ln.SetAttValue("NoLnChLVehClasses", str(AS_CLASS))
            ln.SetAttValue("NoLnChRVehClasses", str(AS_CLASS))
            if (str(ln.AttValue("NoLnChLVehClasses")) != str(AS_CLASS) or
                    str(ln.AttValue("NoLnChRVehClasses")) != str(AS_CLASS)):
                raise RuntimeError(f"lane-change restriction not applied on link {lk.AttValue('No')}")
            n += 1
    if log:
        log(f"    lane changes forbidden for vehicle class {AS_CLASS} on {n} lanes")
    return n


def drive(vissim, sim, params, sim_period, log=None, dt=DT, on_step=None):
    """Run one simulation with the COM-driven IDM. SimPeriod, RandSeed etc.
    must already be set on `sim`; SimRes is forced to 1/dt.
    on_step(i): optional callback after every step (run_visual.py uses it to
    pace the animation); None in the batch."""
    sim.SetAttValue("SimRes", int(round(1.0 / dt)))
    vehs = vissim.Net.Vehicles
    n_steps = int(round(sim_period / dt))
    a_min = -params["b"] * 1.5
    prev = {}                      # AS no -> (v_corrected, s, dv) at previous boundary
    n_override = n_follow = n_free = 0
    as_seen = set()
    lead_types = set()
    t0 = time.time()
    t_com = 0.0
    for i_step in range(n_steps):
        sim.RunSingleStep()
        tc = time.time()
        rows = vehs.GetMultipleAttributes(ATTRS)
        byno = {}
        by_lane = {}
        for r in rows:
            byno[r[0]] = r
            by_lane.setdefault((r[6], r[7]), []).append((float(r[8]), r[0]))
        cur = {}
        for r in rows:
            no, vt, spd, lead, ltype, gap, link, lane, pos, length = r
            if int(vt) != AS_TYPE:
                continue
            as_seen.add(no)
            lead_types.add(str(ltype))
            v_vis = float(spd) / 3.6
            target = min(v_vis, params["cap"])
            # ---- IDM step from the previous boundary state ------------------
            if no in prev:
                v_prev, s_prev, dv_prev = prev[no]
                if v_prev > 0.0:
                    a = max(idm_accel(v_prev, s_prev, dv_prev, params), a_min)
                    v_idm = max(0.0, v_prev + a * dt)
                    target = min(target, v_idm)
                    if s_prev is None:
                        n_free += 1
                    else:
                        n_follow += 1
            if target < v_vis - 1e-6:
                vehs.ItemByKey(no).SetAttValue("Speed", target * 3.6)
                n_override += 1
            # ---- leader state at this boundary, for the next step -----------
            s = dv = None
            if (lead is not None and gap is not None and int(lead) > 0
                    and str(ltype).upper().startswith("VEH") and lead in byno):
                s = float(gap)
                dv = target - float(byno[lead][2]) / 3.6
            else:
                best = None
                for p_, n_ in by_lane.get((link, lane), ()):
                    if n_ != no and p_ > float(pos) and (best is None or p_ < best[0]):
                        best = (p_, n_)
                if best is not None:
                    lr = byno[best[1]]
                    s_net = best[0] - float(pos) - float(lr[9])
                    if 0.0 < s_net <= LOOKAHEAD_M:
                        s = s_net
                        dv = target - float(lr[2]) / 3.6
            cur[no] = (target, s, dv)
        prev = cur
        t_com += time.time() - tc
        if on_step is not None:
            on_step(i_step)
    stats = dict(steps=n_steps, wall_s=time.time() - t0, com_s=t_com,
                 as_count=len(as_seen), follow_steps=n_follow, free_steps=n_free,
                 overrides=n_override)
    if log:
        log(f"    COM-IDM: {n_steps} steps in {stats['wall_s']:.0f}s "
            f"(COM {t_com:.0f}s), {len(as_seen)} AS, IDM following {n_follow} "
            f"AS-steps, IDM free {n_free} AS-steps, speed overridden {n_override} "
            f"times; LeadTargType seen: {sorted(lead_types)}")
    return stats



if __name__ == "__main__":
    print(__doc__)
