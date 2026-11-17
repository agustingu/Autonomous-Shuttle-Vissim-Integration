"""
config.py -- the only place where the scripts learn about YOUR network.

Everything network-specific (object numbers, demand scenarios, fleet
compositions, seeds, file names) lives in a JSON file; the scripts import
this module and never hard-code a Vissim object number.

    default file : config/network_config.json  (the Archer Road example)
    override     : set the environment variable AS_CONFIG=<path to your json>
    network      : "network_file" in the json, or the environment variable
                   AS_NET=<path to .inpx> (used to run a second worker on a
                   copy of the network, which then has its own run numbering
                   and its own <stem>.results folder)

Relative paths in the json are resolved against the folder that holds the
json file.  See docs/network_setup.md for the meaning of every key.
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
CFG_FILE = os.path.abspath(os.environ.get("AS_CONFIG",
                                          os.path.join(HERE, "config", "network_config.json")))
CFG_DIR = os.path.dirname(CFG_FILE)

with open(CFG_FILE, encoding="utf-8") as _fh:
    cfg = json.load(_fh)


def _resolve(p, base=CFG_DIR):
    return os.path.normpath(p if os.path.isabs(p) else os.path.join(base, p))


# ---------------------------------------------------------------- simulation
NET_FILE = _resolve(os.environ.get("AS_NET", cfg["network_file"]))
NET_STEM = os.path.splitext(os.path.basename(NET_FILE))[0]
RESULTS_DIR = os.path.join(os.path.dirname(NET_FILE), NET_STEM + ".results")
OUT_ROOT = _resolve(cfg.get("output_root", "batch_results"), HERE)
SENS_ROOT = _resolve(cfg.get("sensitivity_output_root", "sensitivity_results"), HERE)
TABLES_DIR = _resolve(cfg.get("tables_dir", "tables"), HERE)
FIGURES_DIR = _resolve(cfg.get("figures_dir", "figures"), HERE)
SEEDS = [int(s) for s in cfg.get("seeds", [42, 43, 44, 45, 46])]
SIM_PERIOD = int(cfg.get("sim_period_s", 3600))
DT = float(cfg.get("step_s", 0.2))
SIM_COMPLETE_MIN_S = float(cfg.get("sim_complete_min_s", SIM_PERIOD - 10))

# ---------------------------------------------------------------- vehicles
_sh = cfg["shuttle"]
AS_TYPE = int(_sh["vehicle_type"])
AS_CLASS = int(_sh["vehicle_class"])
AS_DSD = int(_sh["speed_distribution"])
IDM_PARAM_FILE = _resolve(_sh.get("idm_params_file", "idm_params_published.txt"))
IDM_ALT_PARAM_FILE = (_resolve(_sh["alternative_params_file"])
                      if _sh.get("alternative_params_file") else None)
LOOKAHEAD_M = float(_sh.get("lookahead_m", 250.0))
_bus = cfg["bus"]
BUS_TYPE = int(_bus["vehicle_type"])
BUS_DSD = int(_bus["speed_distribution"])
TRANSIT_COMP = int(cfg["transit_composition"])
# vehicle-class codes as they appear in the node-evaluation result tables
NODE_CLASSES = {"all": "ALL",
                "bus": str(cfg.get("node_class_codes", {}).get("bus", "")),
                "shuttle": str(cfg.get("node_class_codes", {}).get("shuttle", AS_CLASS))}

# ---------------------------------------------------------------- experiment
SCENARIOS = list(cfg["demand_scenarios"])                       # ordered, low -> high
DEMAND = {sc: {int(k): float(v) for k, v in d["inputs"].items()}
          for sc, d in cfg["demand_scenarios"].items()}
DEMAND_TOTAL = {sc: float(d.get("total_vph") or sum(d["inputs"].values()))
                for sc, d in cfg["demand_scenarios"].items()}

COMPS = list(cfg["compositions"])                               # ordered, 0 % AS -> 100 % AS
SHARE = {c: float(d["as_share"]) for c, d in cfg["compositions"].items()}
RATIO = {c: d.get("label", c) for c, d in cfg["compositions"].items()}
NSHUT = {c: d.get("n_shuttles") for c, d in cfg["compositions"].items()}
_MARKS = ["o", "v", "p", "*", "+", "s", "D", "^", "x"]
MARKER = {c: d.get("marker", _MARKS[i % len(_MARKS)])
          for i, (c, d) in enumerate(cfg["compositions"].items())}
TRANSIT_PER_HOUR = int(cfg.get("transit_vehicles_per_hour", 0))
if any(v is None for v in NSHUT.values()):
    NSHUT = {c: int(round(SHARE[c] * TRANSIT_PER_HOUR)) for c in COMPS}
BASE_COMP = COMPS[0]                                            # no shuttles
FULL_COMP = COMPS[-1]                                           # all shuttles
AS_COMPS = [c for c in COMPS if SHARE[c] > 0]                   # compositions with shuttles
FIRST_AS_COMP = AS_COMPS[0] if AS_COMPS else None
SHARE_STEP = min((SHARE[c] for c in AS_COMPS), default=1.0)     # "per N shuttles" unit
NSHUT_STEP = int(round(SHARE_STEP * TRANSIT_PER_HOUR)) if TRANSIT_PER_HOUR else None

# ---------------------------------------------------------------- sensitivity
_se = cfg.get("sensitivity", {})
SENS_SCENARIOS = list(_se.get("scenarios", SCENARIOS[:2]))
SENS_DECISIONS = [int(x) for x in _se.get("mainline_routing_decisions", [])]
SENS_DRIVING_BEHAVIOR = int(_se.get("driving_behavior", 1))
SENS_IDM_PARAMS = list(_se.get("idm_parameters", ["a", "T", "s0", "b"]))
SENS_IDM_FACTORS = [float(f) for f in _se.get("idm_factors", [0.75, 1.25])]
SENS_TURN_FACTORS = [float(f) for f in _se.get("turn_factors", [0.5, 1.5])]
SENS_LC_VARIANTS = _se.get("lane_change_variants", {})


def describe():
    """One-line summary used by the scripts' logs."""
    return (f"config {os.path.relpath(CFG_FILE, HERE)}: network {os.path.basename(NET_FILE)}, "
            f"{len(SCENARIOS)} demand scenarios x {len(COMPS)} compositions x {len(SEEDS)} seeds, "
            f"shuttle type {AS_TYPE} (class {AS_CLASS}), bus type {BUS_TYPE}, "
            f"transit composition {TRANSIT_COMP}")


if __name__ == "__main__":
    print(describe())
    print("network file :", NET_FILE)
    print("results db   :", RESULTS_DIR)
    print("outputs      :", OUT_ROOT)
    print("IDM params   :", IDM_PARAM_FILE)
    print("scenarios    :", {sc: DEMAND_TOTAL[sc] for sc in SCENARIOS})
    print("compositions :", {c: SHARE[c] for c in COMPS})
