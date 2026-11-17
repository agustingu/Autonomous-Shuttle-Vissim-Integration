# Preparing your Vissim network and the configuration file

The scripts never hard-code a Vissim object number. Everything they need to
know about the network is in one JSON file (default `config/network_config.json`,
or the file named by the environment variable `AS_CONFIG`). This page lists what
the network must contain and what each key of the JSON means.

## 1. What the network (.inpx) must contain

| Element | Why the scripts need it | Config key |
|---|---|---|
| A **vehicle type** for the shuttle (any number, e.g. 630) with `extDriver="false"` (no external driver model) | The COM driver sets the speed of every vehicle of this type at each step | `shuttle.vehicle_type` |
| A **vehicle class** that contains only the shuttle type | Lane changes are forbidden per class (`NoLnChLVehClasses` / `NoLnChRVehClasses` on every lane); the node evaluation reports per class | `shuttle.vehicle_class`, `node_class_codes.shuttle` |
| A **desired speed distribution** for the shuttle at or below its operational cap (12 mph in the example) | Assigned to the shuttle's row of the transit composition | `shuttle.speed_distribution` |
| A **vehicle type** and a **class** for the conventional bus, with its own desired speed distribution | The fleet compositions replace buses by shuttles | `bus.vehicle_type`, `bus.speed_distribution`, `node_class_codes.bus` |
| A **vehicle composition** used by the transit input only | `run_batch.py` rebuilds it for each AS/bus share and verifies it by reading it back | `transit_composition` |
| A **vehicle input** for the transit vehicles (fixed volume, this composition) and the vehicle inputs of the general traffic | The demand scenarios set `Volume(1)` of the listed inputs | `demand_scenarios.*.inputs` |
| **Nodes** around the intersections with the **node evaluation** switched on, results kept per run (`keepPrevResults = KEEPALL`) | Intersection delay and stops per vehicle class come from the run's result database `<stem>.results/<runNo>.db` | `node_class_codes` |
| The **vehicle record** (direct output, `.fzp`) at 1 s resolution with these attributes, in this order: `SIMSEC; NO; LANE\LINK\NO; LANE\INDEX; VEHTYPE; POS; POSLAT; SPACING; DESSPEED; COORDFRONT; COORDREAR; SPEED; ACCELERATION; LEADTARGNO; LNCHG` | All trajectory-level measures (trapped vehicles, follower gaps, lane changes near a shuttle, shuttle service) and the link speeds are computed from it | fixed column order, see `extract_metrics.py` |
| **Static routing decisions** on the mainline with a through route and turning routes (optional, for the turning-movement sensitivity) | `run_sensitivity.py turns` scales the non-through shares | `sensitivity.mainline_routing_decisions` |
| The **driving behavior** used by the arterial links (optional, for the lane-changing sensitivity) | `run_sensitivity.py lanechange` changes its attributes | `sensitivity.driving_behavior`, `sensitivity.lane_change_variants` |

Notes
- Simulation resolution is forced to `1/step_s` steps per second (5 for 0.2 s).
- Give the shuttle type its own colour distribution if you want to watch runs with `run_visual.py`.
- Reduced speed areas, signal heads, conflict areas, priority rules and routing stay with Vissim; the driver only ever lowers the speed Vissim proposes for a shuttle.
- Keep the network file out of git (the `.gitignore` already excludes `*.inpx`). Put it in `network/` or anywhere and point `network_file` at it.

## 2. The configuration keys

```json
{
  "network_file": "../network/MyCorridor.inpx",   // relative to this json, or absolute; AS_NET overrides
  "output_root": "batch_results",                 // relative to the repository root
  "sensitivity_output_root": "sensitivity_results",
  "tables_dir": "tables", "figures_dir": "figures",
  "sim_period_s": 3600, "step_s": 0.2, "seeds": [42, 43, 44, 45, 46],

  "shuttle": {"vehicle_type": 630, "vehicle_class": 90, "speed_distribution": 1049,
              "idm_params_file": "idm_params_published.txt",      // relative to this json
              "alternative_params_file": "idm_params_lowaccel.txt", // optional contrast set
              "lookahead_m": 250},
  "bus": {"vehicle_type": 300, "speed_distribution": 1047},
  "transit_composition": 2, "transit_vehicles_per_hour": 20,
  "node_class_codes": {"bus": "30", "shuttle": "90"},      // ARG_VEHICLECLASS codes in the node result table

  "demand_scenarios": {"SC-1": {"inputs": {"1": 613, "3": 201}, "total_vph": 1115}, ...},
  "compositions": {"00AS_20Bus": {"as_share": 0.0, "label": "0/20", "n_shuttles": 0, "marker": "o"}, ...},

  "sensitivity": {"scenarios": ["SC-2", "SC-5"],
                  "idm_parameters": ["a", "T", "s0", "b"], "idm_factors": [0.75, 1.25],
                  "mainline_routing_decisions": [14, 2, 4, 3], "turn_factors": [0.5, 1.5],
                  "driving_behavior": 1,
                  "lane_change_variants": {"lc_safdist_0.4": {"label": "...", "attributes": {"SafDistFactLnChg": 0.4}}}}
}
```

- `demand_scenarios` is ordered from low to high demand; the order is used on the x axis of the figures and as the level order of the ANOVA. Inputs not listed keep the value stored in the network (the transit input, for instance). `total_vph` is only a label for the figures; if omitted, the sum of the listed inputs is used.
- `compositions` is ordered from no shuttles to all shuttles; `as_share` is the share of the transit composition given to the shuttle type. The first entry must be the no-shuttle fleet and the last the all-shuttle fleet. `n_shuttles` and `label` are used in tables and legends; `marker` in the figures.
- `seeds`: the same seeds are reused in every cell, which is why the statistics treat the seed as a blocking factor.
- `sim_complete_min_s` (optional, default `sim_period_s - 10`): runs that end earlier are treated as truncated and excluded from the cell means.

## 3. The IDM parameter file

Plain `key=value` lines, SI units; `#` starts a comment:

```
a=0.841      # maximum acceleration, m/s2
b=7.492      # comfortable deceleration, m/s2
v0=6.096     # desired speed, m/s
s0=3.014     # jam distance, m
T=2.79       # desired time gap, s
cap=5.364474 # operational speed limit of the deployment, m/s (independent of v0)
```

The values shipped in `config/idm_params_published.txt` were calibrated with a
genetic algorithm and validated on field trajectories of a NAVYA shuttle
following a conventional car in mixed traffic (Favero and Elefteriadou, 2025).
Replace them with your own calibration; keep `cap` at the operational limit of
the deployment you simulate.

## 4. Run numbering, outputs and the result database

- Vissim writes the direct outputs next to the `.inpx` as `<stem>_<runNo>.fzp`, `.err`, ...; `run_batch.py` moves them into `<output_root>/<scenario>/<composition>/seed_<N>/` after each run.
- The node evaluation of run `<runNo>` is stored in `<stem>.results/<runNo>.db` (SQLite). Vissim can purge the stored tables of earlier runs when a new simulation starts after the network changed, so `run_batch.py` copies the run's database into the run folder immediately; `extract_node_link.py` reads that copy first.
- A second worker on the same machine must use its own copy of the network (`AS_NET=<copy.inpx>`), which gives it its own run numbering, its own `.results` folder and its own output file stem.
- A run shorter than `sim_complete_min_s` (a Vissim crash, for instance) leaves a truncated `.fzp`; the extraction scripts flag or exclude it.
- The scripts keep the machine awake while they run, but they cannot stop a Windows Update restart: pause updates before an overnight batch.
- Never start a simulation from the Vissim GUI while a batch is running on the same network.
