import copy
import traceback
import numpy as np
from tiramisu_programs.optimization import OptimizationCommand
import time
import torch
import json
import math
from rl_interface.action import Action
from tiramisu_programs.surrogate_model_utils.json_to_tensor import get_schedule_representation
from tiramisu_programs.schedule_utils import *
from tiramisu_programs.surrogate_model_utils.modeling import Model_Recursive_LSTM_v2

global_dioph_sols_dict = dict()
EPSILON = 1e-6


class ScheduleController:

    def __init__(self,
                 schedule=None,
                 nb_executions=5,
                 scheds=None,
                 config=None):
        self.depth = 0
        self.schedule = []
        self.schedule_object = schedule
        self.scheds = scheds
        self.nb_executions = nb_executions
        self.speedup = 1.0
        self.steps = 0
        self.new_scheds = {}
        self.search_time = time.time()
        self.config = config
        if self.config.tiramisu.env_type == "cpu":
            self.measurement_env = self.schedule_object.prog.evaluate_schedule
        else:
            self.measurement_env = self.get_exec_time_by_model
        self.lc_total_time = 0
        self.schedule_list_model = []
        self.model = Model_Recursive_LSTM_v2()
        self.model.load_state_dict(
            torch.load(config.tiramisu.model_checkpoint, map_location="cpu"))

    def apply_action(self, action):
        exit = False
        done = False
        info = {}
        applied_exception = False
        skew_params_exception = False
        skew_unroll = False
        self.speedup = 1.0
        self.steps += 1
        #reward = 0
        first_comp = self.schedule_object.comps[0]
        if not action.id in range(44, 46):
            action_params = action.parameter()
            # print("action params first are", action_params)
        else:
            comp = list(self.schedule_object.it_dict.keys())[0]
            action_params = action.parameter(comp, self.schedule_object.prog)
        if action.id in range(28):

            if not self.schedule_object.is_interchaged:

                params = [
                    int(action_params["first_dim_index"]),
                    int(action_params["second_dim_index"])
                ]

                optim1 = OptimizationCommand("Interchange", params,
                                             self.schedule_object.comps)
                # print("got the optim cmd")
                self.schedule.append(optim1)

                if self.schedule_object.is_unrolled:
                    lc_check = self.schedule_object.prog.check_legality_of_schedule(
                        self.schedule, self.non_skewed_comps, first_comp)
                else:
                    lc_check = self.schedule_object.prog.check_legality_of_schedule(
                        self.schedule, first_comp=first_comp)

                # print("\n in interchange,  lc res: {}".format(lc_check))

                if lc_check == -1:
                    print("X: The action produced an error.")
                    self.schedule_object.repr["action_mask"][action.id] = 0
                    self.schedule.pop()
                    raise LCException

                if lc_check == 0:
                    print("X: Illegal action")
                    self.schedule.pop()
                    info = {"illegal_action": True}
                    done = False
                    return self.schedule_object.repr, self.speedup, done, info

                self.schedule_object.apply_interchange(action_params)
                print("O: Interchange applied")
                self.schedule_object.is_interchaged = True

            else:
                print("X: Interchange already applied execption")
                applied_exception = True
                raise IsInterchangedException
                #to expierment with the #reward in this case

        if action.id in range(28, 41):
            if not self.schedule_object.is_tiled:
                params = [
                    int(action_params["first_dim_index"]),
                    int(action_params["second_dim_index"])
                ]
                params.append(action_params["first_factor"])
                params.append(action_params["second_factor"])

                if action_params["tiling_depth"] == 3:
                    params.insert(2, action_params["third_dim_index"])
                    params.append(action_params["third_factor"])

                optim2 = OptimizationCommand("Tiling", params,
                                             self.schedule_object.comps)

                self.schedule.append(optim2)

                if self.schedule_object.is_unrolled:
                    lc_check = self.schedule_object.prog.check_legality_of_schedule(
                        self.schedule, self.non_skewed_comps, first_comp)
                else:
                    lc_check = self.schedule_object.prog.check_legality_of_schedule(
                        self.schedule, first_comp=first_comp)

                # print("\n in tiling,  lc res: {}".format(lc_check))

                if lc_check == -1:
                    print("X: This action produces an error")
                    self.schedule.pop()
                    raise LCException

                if lc_check == 0:
                    print("X: Illegal action")
                    self.schedule.pop()
                    info = {"illegal_action": True}
                    done = False
                    return self.schedule_object.repr, self.speedup, done, info

                self.schedule_object.apply_tiling(action_params)
                print("O: Tiling applied")

                self.schedule_object.is_tiled = True

                done = True
                exit = True
                self.schedule_object.schedule_str = ScheduleUtils.sched_str(
                    self.schedule_object.schedule_str, action.id,
                    action_params, self.schedule_object.comp_indic_dict)
            else:
                print("X: Tiling already applied exception")
                applied_exception = True
                raise IsTiledException

        if action.id in range(41, 44):
            params = {}
            if not self.schedule_object.is_unrolled:
                # print("action params of unrolling", action_params["dim_index"])
                # print("action params of unrolling", action_params["unrolling_factor"])

                #we don't apply unrolling on a level that's skewed, we get the tag to see if it's skewed or not
                self.non_skewed_comps = []
                for comp in self.schedule_object.comps:
                    it_skewed = "L" + self.schedule_object.it_dict[comp][
                        action_params[comp]
                        ["dim_index"]]["iterator"] + "Skewed"
                    if self.schedule_object.repr["representation"][
                            self.schedule_object.comp_indic_dict[comp]][
                                self.schedule_object.placeholders[comp]
                                [it_skewed]] != 1:
                        self.non_skewed_comps.append(comp)

                #for mult comps, unrolling returns a dict of parameters, each for each comp
                for comp in self.non_skewed_comps:
                    params[comp] = [
                        int(action_params[comp]["dim_index"]),
                        int(action_params[comp]["unrolling_factor"])
                    ]
                # print("\nLes paramètres sont:",params)

                if self.non_skewed_comps != []:
                    # print("it's not skewed")

                    optim3 = OptimizationCommand("Unrolling", params,
                                                 self.non_skewed_comps)
                    # print("obtained tiramisu code")
                    self.schedule.append(optim3)

                    start_time = time.time()

                    lc_check = self.schedule_object.prog.check_legality_of_schedule(
                        self.schedule, self.non_skewed_comps, first_comp)
                    l_time = time.time() - start_time
                    # print("\n unrollling lc check {} ".format(lc_check))
                    self.lc_total_time += l_time

                    if lc_check == -1:
                        print("X: This action produces an error")
                        self.schedule_object.repr["action_mask"][action.id] = 0
                        self.schedule.pop()
                        raise LCException

                    if lc_check == 0:
                        print("X: Illegal action")
                        self.schedule.pop()
                        ##reward = -1
                        info = {"illegal_action": True}
                        done = False
                        return self.schedule_object.repr, self.speedup, done, info

                    self.schedule_object.apply_unrolling(action_params)
                    print("O: Unrolling applied")
                    for i in range(41, 44):
                        self.schedule_object.repr["action_mask"][i] = 0
                    self.schedule_object.is_unrolled = True
                else:
                    ##reward=-1
                    lc_check = 0
                    info[
                        'error'] = "trying to apply unrolling after skewing in one of the computations"

            else:
                applied_exception = True
                print("X: Unrolling is already applied")
                raise IsUnrolledException

        if action.id in range(44, 46):

            if not self.schedule_object.is_skewed:

                if (action_params["first_factor"] != None
                        and action_params["second_factor"] != None):

                    # print("\nLes paramètres sont:")
                    # print("\nLe premier niveau de boucle:", action_params["first_dim_index"])
                    # print("\nLe deuxième niveau de boucle:", action_params["second_dim_index"])
                    # print("\nLe premier facteur:", action_params["first_factor"])
                    # print("\nLe deuxième facteur:", action_params["second_factor"])
                    non_inner_comps = []
                    for comp in self.schedule_object.comps:
                        if (action_params["first_dim_index"] !=
                                len(self.schedule_object.it_dict[comp]) - 1
                                and action_params["second_dim_index"] !=
                                len(self.schedule_object.it_dict[comp]) - 1
                            ) or (
                                (action_params["first_dim_index"]
                                 == len(self.schedule_object.it_dict[comp]) - 1
                                 or action_params["second_dim_index"]
                                 == len(self.schedule_object.it_dict[comp]) - 1
                                 and not self.schedule_object.is_unrolled)):
                            non_inner_comps.append(comp)

                    if non_inner_comps != []:

                        params = [
                            int(action_params["first_dim_index"]),
                            int(action_params["second_dim_index"])
                        ]
                        params.append(action_params["first_factor"])
                        params.append(action_params["second_factor"])

                        optim4 = OptimizationCommand("Skewing", params,
                                                     non_inner_comps)

                        self.schedule.append(optim4)

                        start_time = time.time()
                        if self.schedule_object.is_unrolled:
                            lc_check = self.schedule_object.prog.check_legality_of_schedule(
                                self.schedule, self.non_skewed_comps,
                                first_comp)
                        else:
                            lc_check = self.schedule_object.prog.check_legality_of_schedule(
                                self.schedule, first_comp=first_comp)
                        l_time = time.time() - start_time
                        # print("\n skewing lc check res {} ".format(lc_check))
                        self.lc_total_time += l_time

                        if lc_check == -1:
                            print("X: This action produces an error")
                            self.schedule_object.repr["action_mask"][
                                action.id] = 0
                            self.schedule.pop()
                            raise LCException
                        if lc_check == 0:
                            print("X: Illegal action")
                            self.schedule.pop()
                            ##reward = -1
                            info = {"illegal_action": True}
                            done = False
                            return self.schedule_object.repr, self.speedup, done, info

                        self.schedule_object.apply_skewing(action_params)
                        print("O: Skewing is applied")
                        self.schedule_object.is_skewed = True

                    else:
                        skew_unroll = True
                        raise SkewUnrollException

                else:
                    print("X: Skewing prams are null")
                    skew_params_exception = True
                    raise SkewParamsException

            else:
                print("X: Skewing is already applied")
                applied_exception = True
                raise IsSkewedException

        if action.id in range(46, 48):
            if not self.schedule_object.is_parallelized:
                # print("\nLes paramètres sont:")
                # print("\nLe niveau de boucle:", action_params["dim_index"])

                params = [int(action_params["dim_index"])]

                optim5 = OptimizationCommand("Parallelization", params,
                                             self.schedule_object.comps)

                self.schedule.append(optim5)

                start_time = time.time()
                if self.schedule_object.is_unrolled:
                    lc_check = self.schedule_object.prog.check_legality_of_schedule(
                        self.schedule, self.non_skewed_comps, first_comp)
                else:
                    lc_check = self.schedule_object.prog.check_legality_of_schedule(
                        self.schedule, first_comp=first_comp)

                l_time = time.time() - start_time
                # print("\n parallelzation lc check {}".format(lc_check))
                self.lc_total_time += l_time

                if lc_check == -1:
                    print("X: This action produces an error")
                    self.schedule.pop()
                    raise LCException

                if lc_check == 0:
                    print("X: Illegal action")
                    self.schedule.pop()
                    ##reward = -1
                    info = {"illegal_action": True}
                    done = False
                    return self.schedule_object.repr, self.speedup, done, info

                self.schedule_object.apply_parallelization(action_params)
                print("O: Parallelisation applied")
                self.schedule_object.is_parallelized = True
            else:
                applied_exception = True
                print("X: Parallelisation is already applied")
                raise IsParallelizedException

        if action.id in range(48, 56):

            if not self.schedule_object.is_reversed:
                # print("\nLes paramètres sont:")
                # print("\nLe niveau de boucle:", action_params["dim_index"])

                params = [int(action_params["dim_index"])]

                optim6 = OptimizationCommand("Reversal", params,
                                             self.schedule_object.comps)

                self.schedule.append(optim6)

                start_time = time.time()
                if self.schedule_object.is_unrolled:
                    lc_check = self.schedule_object.prog.check_legality_of_schedule(
                        self.schedule, self.non_skewed_comps, first_comp)
                else:
                    lc_check = self.schedule_object.prog.check_legality_of_schedule(
                        self.schedule, first_comp=first_comp)
                l_time = time.time() - start_time
                # print("loop reversal lc check {}".format(lc_check))
                self.lc_total_time += l_time

                if lc_check == -1:
                    print("X: This action produces am error")
                    self.schedule.pop()
                    self.schedule_object.repr["action_mask"][action.id] = 0
                    raise LCException

                if lc_check == 0:
                    print("X: Illegal action")
                    self.schedule.pop()
                    #self.schedule_object.repr["action_mask"][action.id]=0
                    ##reward = -1
                    info = {"illegal_action": True}
                    done = False
                    return self.schedule_object.repr, self.speedup, done, info

                self.schedule_object.apply_reversal(action_params)
                print("O: Loop reversal applied")
                self.schedule_object.is_reversed = True
            else:
                applied_exception = True

                print("X: Loop reversal already applied")

                raise IsReversedException

        if action.id in range(56, 61):
            params = [
                int(action_params["dim_index"]), action_params["fuse_comps"]
            ]

            # print("fuse params are", action_params["dim_index"], '\n', action_params["fuse_comps"])

            if action_params["fuse_comps"] != [] and len(
                    action_params["fuse_comps"]) != 1:

                optim7 = OptimizationCommand("Fusion", params,
                                             action_params["fuse_comps"])

                # print("fusion optim created")

                self.schedule.append(optim7)

                start_time = time.time()

                if self.schedule_object.is_unrolled:
                    lc_check = self.schedule_object.prog.check_legality_of_schedule(
                        self.schedule, self.non_skewed_comps, first_comp)
                else:
                    lc_check = self.schedule_object.prog.check_legality_of_schedule(
                        self.schedule, first_comp=first_comp)

                l_time = time.time() - start_time
                # print("loop fusion lc check {}".format(lc_check))
                self.lc_total_time += l_time

                if lc_check == -1:
                    print("X: This action produces an error")
                    self.schedule_object.repr["action_mask"][action.id] = 0
                    self.schedule.pop()
                    raise LCException

                if lc_check == 0:
                    print("X: Illegal action")
                    self.schedule.pop()
                    info = {"illegal_action": True}
                    done = False
                    return self.schedule_object.repr, self.speedup, done, info

                self.schedule_object.apply_fusion(action_params)
                print("O: Loop fusion applied")
                self.schedule_object.is_fused = True
            else:
                lc_check = 0
                print("X: Unable to fuse")
                ##reward=-1

        if action.id == Action.EXIT:
            # print("**** It's an exit action ****")
            done = True
            exit = True

        if (not exit and lc_check != 0) and not (action.id in range(
                41, 44) and self.schedule_object.is_skewed):
            # print("in the long cond after actions")
            self.schedule_object.schedule_str = ScheduleUtils.sched_str(
                self.schedule_object.schedule_str, action.id, action_params,
                self.schedule_object.comp_indic_dict)
            # print("the original iterators were:", self.schedule_object.it_dict)
            if not action.id in range(41, 44):
                self.schedule_object.it_dict = ScheduleUtils.update_iterators(
                    action.id, self.schedule_object.it_dict, action_params,
                    self.schedule_object.added_iterators,
                    self.schedule_object.comp_indic_dict)

            self.depth += 1

        return self.schedule_object.repr, self.speedup, done, info

    def test_additional_actions(self):
        info = dict()
        if self.schedule_object.is_unrolled:
            for optim in self.schedule:
                if optim.type == "Unrolling":
                    unroll_optimisation = optim

            new_unrolling_params = {}
            new_unrolling_optim_params = {}
            for comp in self.non_skewed_comps:
                unroll_factor = unroll_optimisation.params_list[comp][1]
                new_unrolling_params[comp] = {
                    "dim_index": len(self.schedule_object.it_dict[comp]) - 1,
                    "unrolling_factor": unroll_factor
                }
                new_unrolling_optim_params[comp] = [
                    len(self.schedule_object.it_dict[comp]) - 1, unroll_factor
                ]

            new_unrolling_optim = OptimizationCommand(
                "Unrolling", new_unrolling_optim_params, self.non_skewed_comps)
            new_unrolling_str = ""
            unrolling_str = ""

            for comp in self.non_skewed_comps:
                unroll_factor = unroll_optimisation.params_list[comp][1]
                new_unrolling_str += "U(L" + str(
                    len(self.schedule_object.it_dict[comp]) -
                    1) + "," + str(unroll_factor) + ",C" + str(
                        self.schedule_object.comp_indic_dict[comp]) + ")"
                unrolling_str += "U(L" + str(
                    unroll_optimisation.params_list[comp][0]) + "," + str(
                        unroll_factor) + ",C" + str(
                            self.schedule_object.comp_indic_dict[comp]) + ")"
            self.schedule_object.schedule_str = self.schedule_object.schedule_str.replace(
                unrolling_str, "") + new_unrolling_str
            self.schedule.remove(unroll_optimisation)
            self.schedule.append(new_unrolling_optim)
            self.schedule_object.apply_unrolling(new_unrolling_params)

        self.search_time = time.time() - self.search_time

        try:
            exec_time = 0
            exec_time = self.get_exec_time()

            if not self.schedule_object.is_parallelized:
                print("Testing if parallelization improves the performance...")
                action = Action(Action.PARALLELIZATION0,
                                self.schedule_object.it_dict,
                                self.schedule_object.common_it)
                action_params = action.parameter()

                params = [int(action_params["dim_index"])]

                optim5 = OptimizationCommand("Parallelization", params,
                                             self.schedule_object.comps)
                first_comp = list(self.schedule_object.it_dict.keys())[0]
                iterator = self.schedule_object.it_dict[first_comp][
                    action_params["dim_index"]]['iterator']
                self.schedule_object.schedule_dict[first_comp][
                    "parallelized_dim"] = iterator

                self.schedule.append(optim5)

                try:

                    self.schedule_object.schedule_str = ScheduleUtils.sched_str(
                        self.schedule_object.schedule_str, action.id,
                        action_params, self.schedule_object.comp_indic_dict)
                    parallelized_exec_time = self.get_exec_time()
                    parallelization_str = 'P(L' + str(
                        action_params["dim_index"]) + ')'
                except:
                    print("X: Illegal action")
                    self.schedule.remove(optim5)
                    self.schedule_object.schedule_str = self.schedule_object.schedule_str.replace(
                        parallelization_str, "")

                if parallelized_exec_time < exec_time and parallelized_exec_time != 0:
                    exec_time = parallelized_exec_time

                    self.schedule_object.apply_parallelization(action_params)
                    print("O: Parallelization improves the performance.")

                else:
                    self.schedule.remove(optim5)
                    self.new_scheds[self.schedule_object.prog.name].pop(
                        self.schedule_object.schedule_str)
                    self.schedule_object.schedule_str = self.schedule_object.schedule_str.replace(
                        parallelization_str, "")
                    self.schedule_object.schedule_dict[first_comp][
                        "parallelized_dim"] = None
                    print("X: Parallelization improves the performance")

        except:

            print("X: Error while measuring performance")
            print(f"failed to save schedule",
                  traceback.format_exc(),
                  flush=True)
            info = {"Internal execution error": True}
            return self.schedule_object.repr, self.speedup, True, info

        if exec_time != 0:
            print("\nThe final schedule is ",
                  self.schedule_object.schedule_str)
            self.speedup = (self.schedule_object.prog.initial_execution_time /
                            exec_time) + EPSILON
            print("The speedup is: ", self.speedup)
            start_time = time.time()
        info["depth"] = self.depth
        return self.schedule_object.repr, self.speedup, True, info

    def get_exec_time_by_model(self, optims_list, cmd_type, nb_executions,
                               initial_exec_time):
        self.schedule_list_model.append({
            "schedule_str":
            self.schedule_object.schedule_str,
            "schedule_dict":
            self.schedule_object.schedule_dict
        })
        # print(f"schedule={self.schedule_object.schedule_str};",end="")
        stat = dict()
        try:
            # print("Done saving")
            # print("Done saving")
            computations_tensor, loops_tensor = get_schedule_representation(
                self.schedule_object.annotations,
                self.schedule_object.schedule_dict,
                self.schedule_object.templates["comps_repr_templates_list"],
                self.schedule_object.templates["loops_repr_templates_list"],
                self.schedule_object.
                templates["comps_placeholders_indices_dict"],
                self.schedule_object.
                templates["loops_placeholders_indices_dict"],
                max_depth=self.schedule_object.MAX_DEPTH - 1)
            # print(computations_tensor.shape, loops_tensor.shape)
            tree_tensors = (self.schedule_object.templates["prog_tree"],
                            computations_tensor, loops_tensor)
            with torch.no_grad():
                predicted_speedup = self.model(
                    tree_tensors,
                    num_matrices=self.schedule_object.MAX_DEPTH - 1).item()
                stat[
                    "initial_execution_time"] = self.schedule_object.prog.initial_execution_time
                # print("initial_execution_time", self.schedule_object.prog.initial_execution_time)
                stat["predicted_speedup"] = predicted_speedup
                print(f"The predicted speedup is {predicted_speedup}")
                stat[
                    "predicted_execution_time"] = self.schedule_object.prog.initial_execution_time / predicted_speedup
                # print("predicted_execution_time", self.schedule_object.prog.initial_execution_time/predicted_speedup)
        except Exception:
            print("ERROR_MODEL", traceback.format_exc())
            # or
            print(sys.exc_info()[2])

        return stat["predicted_execution_time"]

    def get_exec_time(self):

        # print("in get_exec_time")

        prog_name = self.schedule_object.prog.name
        execution_time = 0
        if self.schedule_object.schedule_str != "" and self.schedule != []:
            if prog_name in self.scheds.keys():
                #print("Am in 1")

                if self.schedule_object.schedule_str in self.scheds[prog_name]:
                    #print("Am in 1.1")
                    # print("Prog in sched: True, sched in scheds: True")
                    execution_time = self.scheds[prog_name][
                        self.schedule_object.schedule_str][0]
                    # print("**out of ** Prog in sched: True, sched in scheds: False")

                else:
                    #print("Am in 1.2")

                    if prog_name in self.new_scheds.keys(
                    ) and self.schedule_object.schedule_str in self.new_scheds[
                            prog_name].keys():
                        #print("Am in 1.2.1")
                        # print("Prog in sched: True, sched in scheds: False, shced in new_scheds: True")
                        execution_time = self.new_scheds[prog_name][
                            self.schedule_object.schedule_str][1]
                        # print("**out of **Prog in sched: True, sched in scheds: False, shced in new_scheds: True")
                    else:
                        ## print("Am in 1.2.2")
                        curr_sched = copy.deepcopy(self.schedule)
                        # print("Prog in sched: True, sched in scheds: False, shced in new_scheds: False")
                        self.new_scheds[prog_name] = {}
                        execution_time = self.measurement_env(
                            self.schedule, 'sched_eval', self.nb_executions,
                            self.schedule_object.prog.initial_execution_time)
                        self.new_scheds[prog_name][
                            self.schedule_object.schedule_str] = (
                                curr_sched, execution_time, 0)
                        # print("**out of **Prog in sched: True, sched in scheds: False, shced in new_scheds: False")

            else:

                ## print("Am in 2")
                if prog_name in self.new_scheds.keys():
                    ## print("Am in 2.1")

                    if self.schedule_object.schedule_str in self.new_scheds[
                            prog_name].keys():
                        ## print("Am in 2.1.1")
                        # print("Prog in sched: False, sched in scheds: False Prog in new sched: True, sched in new scheds: True")
                        execution_time = self.new_scheds[prog_name][
                            self.schedule_object.schedule_str][1]
                        # print("** out of** Prog in sched: False, sched in scheds: False Prog in new sched: True, sched in new scheds: True")

                    else:
                        ## print("Am in 2.1.2")
                        curr_sched = copy.deepcopy(self.schedule)
                        # print("Prog in sched: False, sched in scheds: False Prog in new sched: True, sched in new scheds: False")
                        execution_time = self.measurement_env(
                            self.schedule, 'sched_eval', self.nb_executions,
                            self.schedule_object.prog.initial_execution_time)
                        self.new_scheds[prog_name][
                            self.schedule_object.schedule_str] = (
                                curr_sched, execution_time, 0)
                        # print("** out of** Prog in sched: False, sched in scheds: False Prog in new sched: True, sched in new scheds: False")

                else:
                    ## print("Am in 2.2")
                    curr_sched = copy.deepcopy(self.schedule)
                    # print("Prog in sched: False, sched in scheds: False Prog in new sched: False")
                    self.new_scheds[prog_name] = {}
                    start_time = time.time()
                    execution_time = self.measurement_env(
                        self.schedule, 'sched_eval', self.nb_executions,
                        self.schedule_object.prog.initial_execution_time)
                    sched_time = time.time() - start_time
                    # self.codegen_total_time+=sched_time

                    self.new_scheds[prog_name][
                        self.schedule_object.schedule_str] = (curr_sched,
                                                              execution_time,
                                                              0)
                    # print("**out of **Prog in sched: True, sched in scheds: False, shced in new_scheds: False")

        else:
            execution_time = self.schedule_object.prog.initial_execution_time

        # print("get_exec_time returned {} for the function {}".format(execution_time,self.schedule_object.prog.name))
        return execution_time
