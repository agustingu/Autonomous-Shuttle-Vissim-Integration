# Legacy: External Driver Model DLL (superseded)

This folder keeps the first implementation of the shuttle driver, a C++
External Driver Model (EDM) DLL based on the `DriverModel` example that ships
with PTV Vissim. It is kept for reference only; the current implementation is
the COM driver in the repository root (`com_idm_driver.py`), which needs no
add-on license and executes the same model.

What the DLL did
- At every time step Vissim called `DriverModelExecuteCommand(DRIVER_COMMAND_MOVE_DRIVER)`
  for each vehicle of the shuttle type; the DLL computed the IDM acceleration
  from the leader data supplied by Vissim and returned the desired acceleration,
  capped so that the speed never exceeded the operational limit (12 mph).
- Lane changes were suppressed by holding the lane-change angle at zero.
- IDM parameters were compiled as defaults and could be overridden by the
  `key=value` file named in the vehicle type's `extDriverParFile` attribute
  (same file format as `config/idm_params_*.txt`); each load was logged.
- When no vehicle leader was within Vissim's look-ahead, the DLL returned
  Vissim's own acceleration suggestion instead of the IDM free-road term.
  The COM driver applies the IDM free-road term in that case, which is the
  model as written; in a validation run on the same network and seed both
  implementations produced the same number of shuttles, trapped vehicles
  (187 vs 189), lane changes (1163 vs 1159) and shuttle travel time (280 vs
  288 s), the COM shuttle being slower in free flow (12.7 vs 14.9 km/h mean)
  for that reason.

Files
- `DriverModel.cpp`, `DriverModel.h`: the modified driver model (IDM, cap, parameter file).
- `DriverModelExample.cpp`, `DriverModel_changes.txt`, `Interface_Description.pdf`: PTV's example and API description, unchanged.
- `DriverModel.sln`, `DriverModel.vcxproj`, `CppProperties.json`: Visual Studio project (x64).

Requirements to use it: the External Driver Model add-on in the Vissim license,
Visual Studio (x64 build), and `extDriver="true"` plus `extDriverDLLFile` /
`extDriverParFile` on the shuttle vehicle type. Build artifacts are not
committed.
