/*==========================================================================*/
/*  DriverModel.cpp                                  DLL Module for VISSIM  */
/*                                                                          */
/*  Interface module for external driver models.                            */
/*  Dummy version that simply sends back VISSIM's suggestions to VISSIM.    */
/*                                                                          */
/*  Version of 2010-03-02                                   Lukas Kautzsch  */
/*==========================================================================*/

#include "DriverModel.h"
#include <cstdio>
#include <ctime>
#include <fstream>
#include <iostream>
#include <list>
#include <map>
#include <math.h>
#include <set>
#include <stdio.h>
#include <string>

using namespace std;
/*==========================================================================*/
/*Default Parameters*/

double  desired_acceleration = 1.5; // It looks like this value is updated below with IDM model so, NOTE: It can be any value it won't make a difference?
double  desired_lane_angle   = 1.50;
long    active_lane_change   = 0;
long    rel_target_lane      = 0;
double  desired_velocity     = 16.3698;
long    turning_indicator    = 0;
long    vehicle_color        = RGB(225,225,225);

/*==========================================================================*/
/*Create Variables*/

double current_time;
long current_vehicle;
double  current_velocity;
double  current_acceleration;
double  vehicle_length;
double  vehicle_x_coord;
double  vehicle_y_coord;
long  vehicle_type;
int vehicle_current_link;
double  gross_distance;
double  speed_difference;
double  ahead_vehicle_acceleration;
double  signal_distance;
double  s_star;
double  distance = 0;
double stopline_x_coord;
double stopline_y_coord;
double car_following_acceleration;
double ahead_vehicle;
long phase;
//long category;


static int vehicle_count = 0;
static int veh = -1;
static int vehicle_to_stop = 0;

/*==========================================================================*/
/* IDM parameters and equation*/

//double a = 3.5;                            // maximum acceleration, constant a (m/s^2)
//double b = 2.0;                            // maximum deceleration, constant b (m/s^2)
//double IDM_desired_velocity = 16.3698;        // desired velocity uesd in IDM (m/s)
//double v0 = IDM_desired_velocity;
//double jam_distance = 0.6;                 // minimum gross distance (m)
//double S0 = jam_distance;
//double T = 0.6;                            // safe-time headway (s)

//================= IDM parameters (SI units) =================================
// DEFAULTS = the published calibration: Favero & Elefteriadou (2025), IEEE
// T-ITS 26(5), Table IV, converted from ft to m (2.76 ft/s2, 20.00 ft/s,
// 9.89 ft, 2.79 s, 24.58 ft/s2). These are the values reported in Table I of
// the shuttle-impact manuscript.
//
// The values can be OVERRIDDEN at run time through VISSIM's external-driver
// "parameter file" (vehicle type attribute ExtDriverParFile): VISSIM passes
// its path to DriverModelSetValue(DRIVER_DATA_PARAMETERFILE) before INIT.
// File format: one "key=value" per line, keys a, b, v0, s0, T, cap; '#' starts
// a comment. Every load is appended to "<paramfile>.loaded.log" so a batch can
// prove which parameter set each run used.
//
// Historical note: from May 2024 to Oct 2026 this file had a=0.375481599048,
// b=4.804760066, v0=5.489523468, s0=1.160702055528, T=0.70023604 compiled in
// ("RENAN VALUES", source undocumented). They are kept in
// idm_params_dll2024.txt for comparison runs only.
double a = 0.841;                                  // maximum acceleration (m/s^2)
double b = 7.492;                                  // desired deceleration (m/s^2)
double IDM_desired_velocity = 6.096;               // IDM free speed v0 (m/s)
double v0 = IDM_desired_velocity;
double jam_distance = 3.014;                       // jam distance s0 (m)
double S0 = jam_distance;
double T = 2.79;                                   // desired time gap (s)

// Operational speed cap for the AS (VehType 630): 12 mph = 19.3121 km/h.
// The IDM free term targets v0 above this cap, and in the frames where the
// IDM branch is skipped the vehicle falls back to VISSIM's own acceleration
// suggestion. Neither path hard-limits the actual speed, so without the cap
// the shuttle drifts above 12 mph on free road (observed up to ~22.8 km/h in
// the .fzp). AS_SPEED_CAP is enforced as a hard limit in
// DriverModelExecuteCommand below.
double AS_SPEED_CAP = 5.364474;                   // m/s  (12 mph = 19.3121 km/h)

static std::string idm_param_file = "";

static std::string trim(const std::string& s)
{
	size_t b_ = s.find_first_not_of(" \t\r\n");
	if (b_ == std::string::npos) return "";
	size_t e_ = s.find_last_not_of(" \t\r\n");
	return s.substr(b_, e_ - b_ + 1);
}

// Load key=value parameters from the VISSIM parameter file. Unknown keys are
// ignored; missing keys keep the compiled defaults above.
static void load_idm_params(const char* path)
{
	if (path == NULL || path[0] == '\0') return;
	idm_param_file = path;
	std::ifstream in(path);
	if (!in.is_open()) return;
	std::string line;
	while (std::getline(in, line)) {
		size_t hash = line.find('#');
		if (hash != std::string::npos) line = line.substr(0, hash);
		size_t eq = line.find('=');
		if (eq == std::string::npos) continue;
		std::string key = trim(line.substr(0, eq));
		double val = atof(trim(line.substr(eq + 1)).c_str());
		if      (key == "a")   a = val;
		else if (key == "b")   b = val;
		else if (key == "v0")  { IDM_desired_velocity = val; v0 = val; }
		else if (key == "s0")  { jam_distance = val; S0 = val; }
		else if (key == "T")   T = val;
		else if (key == "cap") AS_SPEED_CAP = val;
	}
	in.close();
	std::ofstream log((idm_param_file + ".loaded.log").c_str(), std::ios::app);
	if (log.is_open()) {
		time_t now = time(NULL);
		char buf[64];
		strftime(buf, sizeof(buf), "%Y-%m-%d %H:%M:%S", localtime(&now));
		log << buf << "  a=" << a << " b=" << b << " v0=" << v0
		    << " s0=" << S0 << " T=" << T << " cap=" << AS_SPEED_CAP << "\n";
		log.close();
	}
}

double S_star(double v, double v_delta)
{
	double S_star = S0 + (v * T) + ((v * v_delta) / (float)(2 * sqrt(a*b)));
	return S_star;
}



double euclid_dist(double x1, double y1, double x2, double y2)
{
	double x_diff = x2 - x1;
	double y_diff = y2 - y1;
	double euclid_dist = sqrt(pow(x_diff, 2) + pow(y_diff, 2));
	return euclid_dist;
}


/*==========================================================================*/

static std::string dll_dir = "";

// Append one line to <dll dir>\DriverModel_activity.log. Lets a batch prove
// that VISSIM actually loaded and initialised this DLL in every run.
static void activity_log(const std::string& msg)
{
	if (dll_dir.empty()) return;
	std::ofstream log((dll_dir + "DriverModel_activity.log").c_str(), std::ios::app);
	if (!log.is_open()) return;
	time_t now = time(NULL);
	char buf[64];
	strftime(buf, sizeof(buf), "%Y-%m-%d %H:%M:%S", localtime(&now));
	log << buf << "  " << msg << "\n";
	log.close();
}

BOOL APIENTRY DllMain (HANDLE  hModule, 
                       DWORD   ul_reason_for_call, 
                       LPVOID  lpReserved)
{
  switch (ul_reason_for_call) {
      case DLL_PROCESS_ATTACH: {
         char path[MAX_PATH] = "";
         if (GetModuleFileNameA((HMODULE)hModule, path, MAX_PATH) > 0) {
            std::string sp(path);
            size_t cut = sp.find_last_of("\\/");
            dll_dir = (cut == std::string::npos) ? "" : sp.substr(0, cut + 1);
         }
         activity_log("PROCESS_ATTACH");
         break;
      }
      case DLL_THREAD_ATTACH:
      case DLL_THREAD_DETACH:
      case DLL_PROCESS_DETACH:
         break;
  }
  return TRUE;
}

/*==========================================================================*/

DRIVERMODEL_API  int  DriverModelSetValue (long   type, 
                                           long   index1,
                                           long   index2,
                                           long   long_value,
                                           double double_value,
                                           char   *string_value)
{
  /* Sets the value of a data object of type <type>, selected by <index1> */
  /* and possibly <index2>, to <long_value>, <double_value> or            */
  /* <*string_value> (object and value selection depending on <type>).    */
  /* Return value is 1 on success, otherwise 0.                           */
	
  switch (type) {
    case DRIVER_DATA_PARAMETERFILE          :
		load_idm_params(string_value);
		return 1;
    case DRIVER_DATA_PATH                   :
    case DRIVER_DATA_TIMESTEP               :
    case DRIVER_DATA_TIME                   :
		current_time = double_value;
		return 1;
    case DRIVER_DATA_VEH_ID                 :
		current_vehicle = long_value;
		return 1;
    case DRIVER_DATA_VEH_LANE               :
    case DRIVER_DATA_VEH_ODOMETER           :
    case DRIVER_DATA_VEH_LANE_ANGLE         :
    case DRIVER_DATA_VEH_LATERAL_POSITION   :
    case DRIVER_DATA_VEH_VELOCITY           :
		current_velocity = double_value;
		return 1;
    case DRIVER_DATA_VEH_ACCELERATION       :
		current_acceleration = double_value;
		return 1;
    case DRIVER_DATA_VEH_LENGTH             :
		vehicle_length = double_value;
		return 1;
    case DRIVER_DATA_VEH_WIDTH              :
    case DRIVER_DATA_VEH_WEIGHT             :
    case DRIVER_DATA_VEH_MAX_ACCELERATION   :
		return 1;
    case DRIVER_DATA_VEH_TURNING_INDICATOR  :
		turning_indicator = long_value;
		return 1;
    case DRIVER_DATA_VEH_CATEGORY           :
		//category = long_value;
		//return 1;
    case DRIVER_DATA_VEH_PREFERRED_REL_LANE :
    case DRIVER_DATA_VEH_USE_PREFERRED_LANE :
		return 1;
    case DRIVER_DATA_VEH_DESIRED_VELOCITY   :
		desired_velocity = double_value;
		return 1;
    case DRIVER_DATA_VEH_X_COORDINATE       :
		vehicle_x_coord = double_value;
		return 1;
    case DRIVER_DATA_VEH_Y_COORDINATE       :
		vehicle_y_coord = double_value;
		return 1;
    case DRIVER_DATA_VEH_TYPE               :
		vehicle_type = long_value;
		return 1;
    case DRIVER_DATA_VEH_COLOR              :
		vehicle_color = long_value;
		return 1;
    case DRIVER_DATA_VEH_CURRENT_LINK       :
		vehicle_current_link = long_value;
		return 1; /* (To avoid getting sent lots of DRIVER_DATA_VEH_NEXT_LINKS messages) */
                /* Must return 1 if these messages are to be sent from VISSIM!         */
    case DRIVER_DATA_VEH_NEXT_LINKS         :
    case DRIVER_DATA_VEH_ACTIVE_LANE_CHANGE :
    case DRIVER_DATA_VEH_REL_TARGET_LANE    :
    case DRIVER_DATA_NVEH_ID                :
		if (index1 == 0 && index2 == 1) {
			ahead_vehicle = long_value;
		}
		//adj_vehicle[0][1] = long_value;
		return 1;
    case DRIVER_DATA_NVEH_LANE_ANGLE        :
    case DRIVER_DATA_NVEH_LATERAL_POSITION  :
    case DRIVER_DATA_NVEH_DISTANCE          :
		if (index1 == 0 && index2 == 1)
		{
			gross_distance = double_value;
		}
		return 1;
    case DRIVER_DATA_NVEH_REL_VELOCITY      :
		if (index1 == 0 && index2 == 1)
		{
			speed_difference = double_value;
		}
		return 1;
    case DRIVER_DATA_NVEH_ACCELERATION      :
		if (index1 == 0 && index2 == 1)
		{
			ahead_vehicle_acceleration = double_value;
		}
		return 1;
    case DRIVER_DATA_NVEH_LENGTH            :
    case DRIVER_DATA_NVEH_WIDTH             :
    case DRIVER_DATA_NVEH_WEIGHT            :
    case DRIVER_DATA_NVEH_TURNING_INDICATOR :
    case DRIVER_DATA_NVEH_CATEGORY          :
    case DRIVER_DATA_NVEH_LANE_CHANGE       :
    case DRIVER_DATA_NO_OF_LANES            :
    case DRIVER_DATA_LANE_WIDTH             :
    case DRIVER_DATA_LANE_END_DISTANCE      :
    case DRIVER_DATA_RADIUS                 :
    case DRIVER_DATA_MIN_RADIUS             :
    case DRIVER_DATA_DIST_TO_MIN_RADIUS     :
    case DRIVER_DATA_SLOPE                  :
    case DRIVER_DATA_SLOPE_AHEAD            :
    case DRIVER_DATA_SIGNAL_DISTANCE        :
		signal_distance = double_value;
		return 1;
    case DRIVER_DATA_SIGNAL_STATE           :
		phase = long_value;
		return 1;
    case DRIVER_DATA_SIGNAL_STATE_START     :
    case DRIVER_DATA_SPEED_LIMIT_DISTANCE   :
    case DRIVER_DATA_SPEED_LIMIT_VALUE      :
		return 1;
    case DRIVER_DATA_DESIRED_ACCELERATION :
		desired_acceleration = double_value;
		return 1;
    case DRIVER_DATA_DESIRED_LANE_ANGLE : // set this to 0 to avoid lane change (both should be zero, see https://rosap.ntl.bts.gov/view/dot/57280)
		desired_lane_angle = 0;
		return 1;
    case DRIVER_DATA_ACTIVE_LANE_CHANGE : // set this to 0 to avoid lane change (both should be zero see prject report D,)
		active_lane_change = 0;
		return 1;
    case DRIVER_DATA_REL_TARGET_LANE :
		rel_target_lane = long_value;
		return 1;
    default :
      return 0;
  }
}


/*--------------------------------------------------------------------------*/

DRIVERMODEL_API  int  DriverModelGetValue (long   type, 
                                           long   index1,
                                           long   index2,
                                           long   *long_value,
                                           double *double_value,
                                           char   **string_value)
{
  /* Gets the value of a data object of type <type>, selected by <index1> */
  /* and possibly <index2>, and writes that value to <*double_value>,     */
  /* <*float_value> or <**string_value> (object and value selection       */
  /* depending on <type>).                                                */
  /* Return value is 1 on success, otherwise 0.                           */

  switch (type) {
    case DRIVER_DATA_STATUS :
      *long_value = 0;
      return 1;
	case DRIVER_DATA_TIME:
		*double_value = current_time;
		 return 1;
	case DRIVER_DATA_VEH_ID:
		*long_value = current_vehicle;
		return 1;
	case DRIVER_DATA_VEH_VELOCITY:
		*double_value = current_velocity;
		return 1;
	case DRIVER_DATA_VEH_ACCELERATION:
		*double_value = current_acceleration;
		return 1;
	case DRIVER_DATA_VEH_LENGTH:
		*double_value = vehicle_length;
		return 1;
    case DRIVER_DATA_VEH_TURNING_INDICATOR :
      *long_value = turning_indicator;
      return 1;
    case DRIVER_DATA_VEH_DESIRED_VELOCITY   :
      *double_value = desired_velocity;
      return 1;
	case DRIVER_DATA_VEH_X_COORDINATE:
		*double_value = vehicle_x_coord;
		return 1;
	case DRIVER_DATA_VEH_Y_COORDINATE:
		*double_value = vehicle_y_coord;
		return 1;
	case DRIVER_DATA_VEH_TYPE:
		*long_value = vehicle_type;
		return 1;
    case DRIVER_DATA_VEH_COLOR :
      *long_value = vehicle_color;
      return 1;
	case DRIVER_DATA_VEH_CURRENT_LINK:
		*long_value = vehicle_current_link;
		return 1;
	//case DRIVER_DATA_NVEH_ID:
		//*long_value = adj_vehicle[0][1];
		//return 1;
	case DRIVER_DATA_NVEH_DISTANCE:
		*double_value = gross_distance;
		return 1;
	case DRIVER_DATA_NVEH_REL_VELOCITY:
		*double_value = speed_difference;
		return 1;
	//case DRIVER_DATA_NVEH_ACCELERATION:
	//	*double_value = ahead_vehicle_acceleration;
	//	return 1;
	case DRIVER_DATA_SIGNAL_DISTANCE:
		*double_value = signal_distance;
		return 1;
    case DRIVER_DATA_WANTS_SUGGESTION :
      *long_value = 1;
      return 1;
    case DRIVER_DATA_DESIRED_ACCELERATION :
      *double_value = desired_acceleration;
      return 1;
    case DRIVER_DATA_DESIRED_LANE_ANGLE :
      *double_value = 0;
      return 1;
	case DRIVER_DATA_USE_INTERNAL_MODEL: /* this is what agustin change according to xi duan document*/
		*long_value = 0; /* must be set to 0 if external model is to be applied */
		return 1;

	case DRIVER_DATA_SIGNAL_STATE:
		*long_value = phase; /* status of phase*/ 
		return 1;

    case DRIVER_DATA_ACTIVE_LANE_CHANGE :
      *long_value = 0;
      return 1;
    case DRIVER_DATA_REL_TARGET_LANE :
      *long_value = rel_target_lane;
      return 1;
    case DRIVER_DATA_SIMPLE_LANECHANGE :
      *long_value = 0;
      return 1;
    default :
      return 0;
  }
}



/*==========================================================================*/

DRIVERMODEL_API  int  DriverModelExecuteCommand (long number)
{
  /* Executes the command <number> if that is available in the driver */
  /* module. Return value is 1 on success, otherwise 0.               */
  fstream myFile;
  switch (number) {
    case DRIVER_COMMAND_INIT : {
      char line[512];
      sprintf_s(line, sizeof(line),
                "INIT  a=%.4f b=%.4f v0=%.4f s0=%.4f T=%.4f cap=%.6f  paramfile=%s",
                a, b, v0, S0, T, AS_SPEED_CAP,
                idm_param_file.empty() ? "(none passed by VISSIM; compiled defaults)"
                                       : idm_param_file.c_str());
      activity_log(line);
      return 1;
    }
    case DRIVER_COMMAND_CREATE_DRIVER :
      return 1;
    case DRIVER_COMMAND_KILL_DRIVER :
      return 1;
    case DRIVER_COMMAND_MOVE_DRIVER :
		
		if (vehicle_type == 630)
		{
			if (current_vehicle != vehicle_to_stop && current_velocity != 0 && phase != 1 && signal_distance > 0.5) { //

				double S = gross_distance - vehicle_length;
				double v = current_velocity;
				double v_delta = speed_difference;
				double s_star = S_star(current_velocity, speed_difference);
				// here is where Renan input his model
				car_following_acceleration = a * (1 - pow((v / (float)(v0)), 1) - pow((s_star / (float)S), 2));

				if (gross_distance > jam_distance)
				{
					desired_acceleration = car_following_acceleration;// setting the IDM acceleration to the desired acceleration if gross distance is greater than the min gross distance
				}
			}

			if (ahead_vehicle < 0) {
				vehicle_to_stop = current_vehicle;
			}

			// --- Hard speed cap (12 mph) -------------------------------------
			// Applies to every type-630 frame, including the ones where the IDM
			// branch above is skipped (no leader, restart from stop, near a
			// signal) and desired_acceleration still holds VISSIM's suggestion.
			// If the shuttle is over the cap, force the desired acceleration to
			// at most (cap - v), i.e. a gentle deceleration back to the cap;
			// if it is at the cap and the model still wants to accelerate,
			// (cap - v) is ~0 so it is held at the cap. This never adds
			// acceleration and never touches behavior below the cap, so
			// Renan's calibrated car-following/stopping is preserved.
			if (current_velocity > AS_SPEED_CAP &&
			    desired_acceleration > (AS_SPEED_CAP - current_velocity)) {
				desired_acceleration = AS_SPEED_CAP - current_velocity;
			}

			//================= This code below works for writing to the txt file what's going on with the IDM above
			//================= I should use this to compare with the vissim output (fzp)
			
			//fstream myfile;
			//myfile.open("saldina.txt", ios::app);
			//if (myfile.is_open()) {
				//myfile << desired_acceleration;
				//myfile << current_vehicle <<" < veh cur," << current_time << "," << current_velocity <<"," << desired_velocity << "," << current_acceleration <<"," << desired_acceleration <<", vtstop " << vehicle_to_stop << "\n";
				//myfile.close();


			//}

			
		}
      return 1;
    default :
      return 0;
  }
}

/*==========================================================================*/
/*  Ende of DriverModel.cpp                                                 */
/*==========================================================================*/
