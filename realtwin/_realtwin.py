##############################################################################
# Copyright (c) 2024, Oak Ridge National Laboratory                          #
# All rights reserved.                                                       #
#                                                                            #
# This file is part of RealTwin and is distributed under a MIT               #
# license. For the licensing terms see the LICENSE file in the top-level     #
# directory.                                                                 #
#                                                                            #
# Contributors: ORNL Real-Twin Team                                          #
# Contact: realtwin@ornl.gov                                                 #
##############################################################################

"""The real-twin developed by ORNL Applied Research and Mobility System (ARMS) group"""
import os
import shutil
from pathlib import Path
import sys
import time
import pyufunc as pf
from rich.console import Console
from rich import print as rprint
# environment setup
from realtwin.util_lib.create_venv import venv_create, venv_delete
from realtwin.func_lib._a_install_simulator.inst_sumo import install_sumo

# input data loading
from realtwin.func_lib._b_load_inputs.loader_config import load_input_config

# scenario generation
# from realtwin.util_lib.download_elevation_tif import download_elevation_tif_by_bbox
from realtwin.util_lib.check_abstract_scenario_inputs import check_abstract_inputs
from realtwin.func_lib._c_abstract_scenario._abstractScenario import AbstractScenario
from realtwin.func_lib._c_abstract_scenario.rt_matchup_table_generation import generate_matchup_table
from realtwin.func_lib._c_abstract_scenario.rt_matchup_table_generation import format_junction_bearing
from realtwin.func_lib._c_abstract_scenario.rt_demand_generation import generate_turn_demand, update_matchup_table

from realtwin.func_lib._d_concrete_scenario._concreteScenario import ConcreteScenario

# simulation
from realtwin.func_lib._e_simulation._generate_simulation import SimPrep

# calibration
from realtwin.func_lib._f_calibration.calibration_sumo import cali_sumo
from realtwin.data_lib.data_lib_config import sel_behavior_routes as sel_behavior_routes_demo

console = Console()
# info: dim cyan, warning: magenta, danger: bold red


class RealTwin:
    """The real-twin developed by ORNL Applied Research and Mobility System (ARMS) group that
    enables the simulation of twin-structured cities.
    """

    def __init__(self, input_config_file: str = "", **kwargs):
        """Initialize the REALTWIN object.

        Args:
            input_config_file (str): The directory containing the input files.
            kwargs: Additional keyword arguments. Will be used in the future.
        """

        # initialize the input directory
        if not input_config_file:
            raise Exception(
                "\n  :Input configuration file is not provided."
                "\n  :RealTwin requires a configuration file to be provided.")

        self.input_config = load_input_config(input_config_file)

        # add venv_create and delete as object methods
        self.venv_create = venv_create
        self.venv_delete = venv_delete
        self._venv_name = "venv_rt"
        self._proj_dir = os.getcwd()  # get current working directory

        # extract data from kwargs
        self.verbose = kwargs["verbose"] if "verbose" in kwargs else False

    def env_setup(self,
                  *,
                  sel_sim: list = None,
                  sel_dir: list = None,
                  strict_sumo_version: str = None,
                  strict_vissim_version: str = None,
                  strict_aimsun_version: str = None,
                  **kwargs) -> bool:
        """Check and set up the environment for the simulation

        Args:
            sel_sim (list): select simulator to be set up. Default is None.
                Currently available options are ["SUMO", "VISSIM", "AIMSUN"].
            sel_dir (list): A list of directories to search for the executables. Defaults to None.
            strict_sumo_version (str): Whether to strictly check the version is installed.
                if specified, will check and install the version. Default is None.
            strict_vissim_version (str): Whether to strictly check the version is installed.
                if specified, will check and install the version. Default is False.
            strict_aimsun_version (str): Whether to strictly check the version is installed.
                if specified, will check and install the version. Default is False.
            kwargs: Additional keyword arguments.

        Examples:
            >>> import realtwin as rt
            >>> twin = rt.REALTWIN(input_config_file="config.yaml", verbose=True)

            check simulator is installed or not, default to SUMO, optional: VISSIM, AIMSUN
            >>> twin.env_setup(sel_sim=["SUMO"])

            add additional directories to search for the executables
            >>> additional_dir = [r"path-to-your-local-installed-sumo-bin"]
            >>> twin.env_setup(sel_sim=["SUMO"], sel_dir=additional_dir)

            strict version check: will install the required version if not found
            >>> twin.env_setup(sel_sim=["SUMO"], sumo_version="1.21.0", strict_sumo_version=True)

            or with additional directories
            >>> twin.env_setup(sel_sim=["SUMO"], sel_dir=additional_dir,
            >>>                sumo_version="1.21.0", strict_sumo_version=True)

        Returns:
            bool: True if the environment is set up successfully, False otherwise.
        """

        # 0. Check if the sim_env is selected,
        #    default to SUMO, case insensitive and add self.sel_sim as a class attribute
        sel_sim = ["sumo"] if not sel_sim else [sim.lower() for sim in sel_sim]

        # 1. Check simulator installation - mapping function
        simulator_installation = {
            "sumo": install_sumo,
            "vissim": None,
            "aimsun": None,
        }

        # 2. check if the simulator is installed, if not, install it
        console.print("\n[bold green]Check / Install the selected simulators:")

        kwargs['sel_dir'] = sel_dir
        kwargs['strict_sumo_version'] = strict_sumo_version
        kwargs['strict_vissim_version'] = strict_vissim_version
        kwargs['strict_aimsun_version'] = strict_aimsun_version
        kwargs['verbose'] = self.verbose

        invalid_sim = []
        for simulator in sel_sim:
            try:
                sim_status = simulator_installation.get(simulator)(**kwargs)
                if not sim_status:
                    invalid_sim.append(simulator)
            except Exception:
                invalid_sim.append(simulator)
                rprint(f"  :[bold magenta]Could not install {simulator} (strict version) on your operation system", end="")

        sel_sim_ = list(set(sel_sim) - set(invalid_sim))

        if not sel_sim_:
            raise Exception("  :Error: No simulator is available (strict version). Please select available version(s).")
        self.sel_sim = sel_sim_

        return True

    def generate_inputs(self, *, incl_sumo_net: str = None):
        """ Generate user inputs, such as MatchUp table, Control and Traffic data

        Args:
            incl_sumo_net (str): The path to the updated SUMO network file (.net.xml) provided by the user.
                If provided, the OpenDrive network will be generated based on this SUMO network.
                If not provided, the OpenDrive network will be generated based on the vertices from the config file.

        See Also:
            - How to create configuration file
            - How to create/update MatchUp table
            - How to create/prepare Control and Traffic data
            - How to download elevation tif data from network BBOX
        """
        with console.status("[bold cyan]Generating inputs...", spinner="dots"):
            console.print("\n[bold green]Check / Create input files and folders for user:")
            path_input = pf.path2linux(Path(self.input_config.get("input_dir")))

            # check if Control folder exists in the input directory
            path_control = pf.path2linux(Path(path_input) / "Control")
            if not os.path.exists(path_control):
                os.makedirs(path_control)
            # check if the Control folder is empty
            elif not os.listdir(path_control):
                console.print(f"[dim cyan]Control folder is empty: {path_control}.")

            console.print(f"  [dim cyan]:Control folder exists: {path_control}.[/dim cyan]\n"
                          "  :NOTICE: [bold red]Please include Synchro UTDF file (signal) inside Control folder\n")

            # check if Traffic folder exists in the input directory
            path_traffic = pf.path2linux(Path(path_input) / "Traffic")
            if not os.path.exists(path_traffic):
                os.makedirs(path_traffic)
            # check if the Traffic folder is empty
            elif not os.listdir(path_traffic):
                console.print(f"  [magenta]:Traffic folder is empty: {path_traffic}.")

            console.print(f"  [dim cyan]:Traffic folder exists: {path_traffic}.[/dim cyan]\n"
                          "  :NOTICE: [bold red]Please include turn movement file for each intersection "
                          "inside Traffic folder and add the file names to the MatchupTable.xlsx "
                          "(You will notice the generated MatchupTable.xlsx inside your input folder)."
                          " For how to fill the MatchupTable.xlsx, please refer to official documentation\n",
                          soft_wrap=True, no_wrap=False)

            # check if SUMO net file generated (in OpenDrive folder), if not, create the net.
            net_name = self.input_config["Network"]["NetworkName"]
            path_sumo_net = pf.path2linux(Path(self.input_config.get("output_dir")) / f"OpenDrive/{net_name}.net.xml")
            # generate abstract scenario if sumo net file does not exist
            self.abstract_scenario = AbstractScenario(self.input_config)
            if not os.path.exists(path_sumo_net):
                # Create original SUMO network from vertices from config file
                self.abstract_scenario.create_SUMO_network()

                # crate OpenDrive network from SUMO network, and then rewrite sumo network based on OpenDrive network
                self.abstract_scenario.create_OpenDrive_network()

                rprint("  :INFO: OpenDrive network is generated.\n", end="")

            # Update SUMO Network before generating OpenDrive network
            if demo_data := self.input_config["demo_data"]:
                # demo mode is enabled, use the updated SUMO network from demo data
                incl_sumo_net = pf.path2linux(Path(self.input_config["input_dir"]) / f"updated_net/{demo_data}.net.xml")

            if incl_sumo_net:
                # check if the file exists and end with .net.xml
                if incl_sumo_net.endswith(".net.xml") and os.path.exists(incl_sumo_net):
                    self.input_config["incl_sumo_net"] = incl_sumo_net

                    # Copy user updated net file to the OpenDrive folder
                    incl_sumo_net = pf.path2linux(Path(incl_sumo_net))  # ensure it's absolute path
                    if incl_sumo_net != path_sumo_net:
                        shutil.copy(incl_sumo_net, path_sumo_net)
                    console.print(f"  [dim cyan]:INFO: SUMO network is copied to {path_sumo_net}.\n"
                                  f"  [dim cyan]:Using updated SUMO network provide by user: {incl_sumo_net} "
                                  "to generate OpenDrive network.\n", soft_wrap=True, no_wrap=False)

                    # create opendrive net from updated sumo net, and rewrite sumo net based on OpenDrive net
                    # self.abstract_scenario.create_OpenDrive_network()
                    rprint("  [dim cyan]:INFO: OpenDrive network is generated.", end="")
                else:
                    console.print("  [magenta]:NOTE: incl_sumo_net is not exist or not with .net.xml extension.\n"
                                  "  :Please provide a valid SUMO file with .net.xml extension or leave it empty.",
                                  soft_wrap=True, no_wrap=False)
            else:
                #  let user know they can use their own SUMO network by using incl_sumo_net
                rprint("  [dim cyan]:INFO: You can use your own SUMO network by providing the path "
                       "to the incl_sumo_net parameter. The path should be a .net.xml file. \n",
                       end="")

            # create matchup table for user
            path_matchup = pf.path2linux(Path(self.input_config.get("input_dir")) / "MatchupTable.xlsx")

            # check if sumo net file exists
            if not os.path.exists(path_sumo_net):
                raise Exception(f"  :Error: SUMO net file does not exist: {path_sumo_net},"
                                "please check input configuration file and re-run the script."
                                "For details please refer to the documentation: ")
            df_matchup_table = format_junction_bearing(path_sumo_net)
            generate_matchup_table(df_matchup_table, path_matchup)
            console.print(f"  [dim cyan]:NOTE: Matchup table is generated and saved to {path_matchup}.[/dim cyan]\n"
                          "  :NOTICE: [bold red]Please update the Matchup table from input folder"
                          " and then run generate_abstract_scenario()."
                          " For details please refer to official documentation: \n", soft_wrap=True, no_wrap=False)

            console.rule("[bold green]Program stopped. Please prepare the Control and Traffic data and "
                         "fill in the Matchup Table before proceeding.\n"
                         "[bold red]Please run generate_abstract_scenario() "
                         "after preparing the input data.\n")
            time.sleep(2)  # wait for 2 seconds before exiting
            # sys.exit(0)
            # Stop the program to let user update the Matchup table
            #
            usr_input = False
            while not usr_input:
                usr_input = console.input(":warning: [bold magenta]Please update the generated Matchup table from "
                                          "input folder before pressing Enter or type 'y' / 'yes' to continue")
                if usr_input == "" or usr_input in ["y", "Y", "yes", "Yes"]:
                    console.print("  [dim cyan]:INFO: User confirmed to continue (Matchup Table Updated).")
                    usr_input = True

    def generate_abstract_scenario(self):
        """Generate the abstract scenario: create OpenDrive files
        """

        # check abstract scenario inputs
        check_abstract_inputs(self.input_config.get("input_dir"))

        # 1. Generate the abstract scenario based on the input data
        # self.abstract_scenario = AbstractScenario(self.input_config)
        if not hasattr(self, 'abstract_scenario'):
            raise Exception("  :Error: Abstract Scenario is not generated yet. "
                            "Please run generate_inputs() first.")

        # update traffic and signal
        path_matchup = pf.path2linux(Path(self.input_config.get("input_dir")) / "MatchupTable.xlsx")
        control_dir = pf.path2linux(Path(self.input_config.get("input_dir")) / "Control")
        traffic_dir = pf.path2linux(Path(self.input_config.get("input_dir")) / "Traffic")

        # Auto-fill matchup table, save to matchup table : MatchupTable_UserInput
        _ = update_matchup_table(path_matchup_table=path_matchup,
                                 control_dir=control_dir,
                                 traffic_dir=traffic_dir)
        # Tell user to manually check correctness of the Matchup Table
        console.input(":warning: [bold magenta]In the Matchup Table, please check if the turn movement in the "
                      "demand and control data match with bearings in the network data. Enter any key to continue...")

        df_volume, df_vol_lookup = generate_turn_demand(path_matchup_table=path_matchup,
                                                        control_dir=control_dir,
                                                        traffic_dir=traffic_dir,)

        self.abstract_scenario.Traffic.VolumeLookupTable = df_vol_lookup

        self.abstract_scenario.update_AbstractScenario_from_input(df_volume=df_volume)
        console.print("\n[bold green]Abstract Scenario successfully generated.")

    def generate_concrete_scenario(self):
        """Generate the concrete scenario: generate unified scenario from abstract scenario
        """

        # 1. Generate the concrete scenario based on abstract scenario
        # 2. Save the concrete scenario to the output directory

        if not hasattr(self, 'abstract_scenario'):
            console.print("  [magenta]:Warning: Abstract Scenario is not generated yet. "
                          "Please run generate_abstract_scenario() first.")
            return

        self.concrete_scenario = ConcreteScenario()
        self.concrete_scenario.get_unified_scenario(self.abstract_scenario)
        console.print("\n[bold green]Concrete Scenario successfully generated.")

    def prepare_simulation(self,
                           start_time: float = 3600 * 8,
                           end_time: float = 3600 * 10,
                           seed: list | int = 812,
                           step_length: float = 0.1) -> bool:
        """Simulate the concrete scenario: generate simulation files for the selected simulator

        Args:
            start_time (float): The start time of the simulation. Default is 3600 * 8.
            end_time (float): The end time of the simulation. Default is 3600 * 10.
            seed (list or int): The seed for the simulation. Default is [101].
            step_length (float): The simulation step size. Default is 0.1.

        Examples:
            import realtwin package
            >>> import realtwin as rt

            load the input configuration file
            >>> twin = rt.REALTWIN(input_config_file="config.yaml", verbose=True)

            check simulator is installed or not, default to SUMO
            >>> twin.env_setup(sel_sim=["SUMO"])

            generate abstract scenario and concrete scenario
            >>> twin.generate_abstract_scenario()
            >>> twin.generate_concrete_scenario()

            prepare simulation with start time, end time, seed, and step size
            >>> twin.prepare_simulation(start_time=3600 * 8, end_time=3600 * 10, seed=[101], step_length=0.1)

        Returns:
            bool: True if the simulation is prepared successfully, False otherwise.
        """

        # 1. prepare Simulate docs from the concrete scenario
        # 2. Save results to the output directory

        sim_prep = {
            "sumo": SimPrep().create_sumo_sim,
            "vissim": SimPrep().create_vissim_sim,
            "aimsun": SimPrep().create_aimsun_sim,
        }

        # TODO according sel_sim to run different simulators
        self.sim = SimPrep()
        for simulator in self.sel_sim:
            sim_prep.get(simulator)(self.concrete_scenario,
                                    start_time=start_time,
                                    end_time=end_time,
                                    seed=seed,
                                    step_length=step_length)
            console.print(f"\n[bold green]{simulator.upper()} simulation successfully Prepared.")
        return True

    def calibrate(self, *, sel_algo: dict = None,
                  sel_behavior_routes: dict = None,
                  update_turn_flow_algo: dict = None,
                  update_behavior_algo: dict = None) -> bool:
        """Calibrate the turn and inflow, and behavioral parameters using the selected algorithms.

        Args:
            sel_algo (dict): The dictionary of algorithms to be used for calibration.
                Default is None, will use genetic algorithm. e.g. {"turn_inflow": "ga", "behavior": "ga"}.
            sel_behavior_routes (dict): The dictionary of behavior route parameters to be used for calibration.
                Default is None. time (in seconds) is ground truth travel time.
                e.g. sel_behavior_routes = {"route_1": {"time": 20, "edge_list": ["edge_id_1", "edge_d_2", ...]},
                                           "route_2" {"time": 40, "edge_list":["edge_id_1", "edge_d_2", ...]}
                                           ...}.
            update_turn_flow_algo (dict): The dictionary of algorithms to be used for updating turn flow.
                Default is None, will use genetic algorithm.
                Please refer to input configuration file for keys for each algorithm.
                e.g. update_turn_flow_algo = {"ga_config": {}, "sa_config":{}, "ts_config":{}}.
            update_behavior_algo (dict): The dictionary of algorithms to be used for updating behavior.
                Default is None, will use genetic algorithm.
                Please refer to input configuration file for keys for each algorithm.
                e.g. update_behavior_algo = {"ga_config":{}, "sa_config":{}, "ts_config": {}}.
        """
        # TDD
        print()
        if sel_algo is None:  # default to genetic algorithm
            sel_algo = {"turn_inflow": "ga", "behavior": "ga"}
            console.print(f"  [dim cyan]:sel_algo not specified, use default value: {sel_algo}")

        if not isinstance(sel_algo, dict):
            sel_algo = {"turn_inflow": "ga", "behavior": "ga"}
            console.print("  [bold red]:Error: parameter sel_algo must be a dict with"
                          " keys of 'turn_inflow' and 'behavior', using"
                          f" default values: {sel_algo}")

        # check if the selected algorithm is supported within the package
        # convert the algorithm to lower case
        sel_algo = {key: value.lower() for key, value in sel_algo.items()}
        if (algo := sel_algo["turn_inflow"]) not in ["ga", "sa", "ts"]:
            console.print(f"  [dim cyan]:Selected algorithms are {sel_algo}")
            console.print(f"  [dim cyan]:{algo} for turn and inflow calibration is not supported. "
                          "Must be one of ['ga', 'sa', 'ts']")
            return False

        if (algo := sel_algo["behavior"]) not in ["ga", "sa", "ts"]:
            console.print(f"  [dim cyan]:Selected algorithms are {sel_algo}")
            console.print(f"  [div cyan]:{algo} for behavior calibration is not supported. "
                          "Must be one of ['ga', 'sa', 'ts']")
            return False

        # parse user additional parameters for calibration
        user_kwargs = {}
        if sel_behavior_routes:
            # use user defined behavior route, if not provided, automatically select two routes from the network
            user_kwargs["sel_behavior_routes"] = sel_behavior_routes
        if self.input_config["demo_data"] and sel_behavior_routes_demo.get(self.input_config["demo_data"]):
            # use predefined behavior routes for demo data
            user_kwargs["sel_behavior_routes"] = sel_behavior_routes_demo.get(self.input_config["demo_data"])
        if update_turn_flow_algo:
            user_kwargs["update_turn_flow_algo"] = update_turn_flow_algo
        if update_behavior_algo:
            user_kwargs["update_behavior_algo"] = update_behavior_algo

        # run calibration based on the selected algorithm
        if "sumo" in self.sel_sim:
            cali_sumo(sel_algo=sel_algo, input_config=self.input_config, verbose=self.verbose, **user_kwargs)

        if "vissim" in self.sel_sim:
            pass

        if "aimsun" in self.sel_sim:
            pass

        console.print("[bold green]Calibration successfully completed.\n")

        return True

    def post_process(self):
        """Post-process the simulation results.
        """

        # 1. Post-process the simulation results
        # 2. Save the post-processed results to the output directory

    def visualize(self):
        """Visualize the simulation results.
        """

        # 1. Visualize the simulation results
        # 2. Save the visualization results to the output directory
