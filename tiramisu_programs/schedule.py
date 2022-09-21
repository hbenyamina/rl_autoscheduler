import traceback
import numpy as np
from rl_interface.action import Action
from tiramisu_programs.surrogate_model_utils.json_to_tensor import get_tree_structure, get_sched_rep
from tiramisu_programs.schedule_utils import *

global_dioph_sols_dict = dict()
EPSILON = 1e-6

class Schedule:
    MAX_DEPTH = 6
    def __init__(self, program):
        self.depth = 0
        self.schedule_str = ""
        self.is_interchaged=False
        self.is_tiled=False
        self.is_unrolled=False
        self.is_skewed=False
        self.is_parallelized=False
        self.is_reversed=False
        self.prog = program
        self.comps = list(self.prog.comp_name)
        self.annotations=self.prog.get_program_annotations()
        self.repr = None

    def get_representation(self):
        if self.repr is not None: return self.repr 
        self.prog_rep, self.comps_placeholders, self.comp_indic_dict = ScheduleUtils.get_representation(self.annotations)

        # print("the length is", len(prog_rep[0]))

        for comp_rep in self.prog_rep:
            if len(comp_rep) != 1052:
                raise RepresentationLengthException
        
        
        if len(self.comps)!= 1:
            # print("more than one comp")
            self.comps_it = []
            for comp in self.comps:
                self.comps_it.append(self.annotations["computations"][comp]["iterators"])
            
            ## print("got the comp it", self.comps_it)

            self.common_it = self.comps_it[0]

            for comp_it in self.comps_it[1:]:
                ## print("common it is ", self.common_it)
                self.common_it = [it for it in comp_it if it in self.common_it]

            # print("the common iterators are", self.common_it)

        elif len(self.comps)>5: # To avoid IndexError in self.repr["representation"]
            raise IndexError

        else:
            # print("one comp, no need for common iterators")
            self.common_it= self.annotations["computations"][self.comps[0]]["iterators"]


        # print("The initial execution time is", self.prog.initial_execution_time)
        self.schedule_dict = dict()
        self.schedule_dict["fusions"] = None
        for comp in self.comps:
            dim = len(self.annotations['computations'][comp]['iterators'])
            self.schedule_dict[comp] = dict()
            self.schedule_dict[comp]["dim"] = dim
            self.schedule_dict[comp]["transformation_matrix"] = np.eye(dim,dim)
            self.schedule_dict[comp]["transformation_matrices"] = [np.eye(dim,dim)]
            self.schedule_dict[comp]['parallelized_dim'] = None
            self.schedule_dict[comp]['unrolling_factor'] = None
            self.schedule_dict[comp]['tiling'] = None
        self.schedule_dict['tree_structure'] = get_tree_structure(self.annotations)
        
        self.templates = dict()
        (self.templates["prog_tree"],
            self.templates["comps_repr_templates_list"],
            self.templates["loops_repr_templates_list"],
            self.templates["comps_placeholders_indices_dict"],
            self.templates["loops_placeholders_indices_dict"]) = get_sched_rep(self.annotations, self.schedule_dict, max_depth=self.MAX_DEPTH-1)
        self.schedule_dict["fusions"] = []
        self.placeholders = self.comps_placeholders
        self.added_iterators=[]   

        self.repr={}
        self.repr["representation"] = np.empty((0,1052),np.float32)
        self.repr["loops_representation"]=np.empty((0,26),np.float32)
        self.repr['child_list']=np.empty((0,11),np.float32)
        self.repr['has_comps']=np.empty((0,12),np.float32)
        self.repr['computations_indices']=np.empty((0,5),np.float32)

        for i in range (5):
            if i>=len(self.prog_rep):
                self.repr["representation"]=np.vstack([self.repr["representation"], np.zeros(1052)])
            else:
                self.repr["representation"]=np.vstack([self.repr["representation"], np.array([self.prog_rep[i]],dtype=np.float32)])

        #print("\nLa représentation vectorielle initiale de ce programme est:", self.repr["representation"] )
        
        # print("\nLes niveaux de boucles de ce programme sont:")
        self.it_dict={}
        for comp in self.comps:        
            comp_it_dict={}
            iterators=list(self.annotations["computations"][comp]["iterators"])
            
            for i in range (len(iterators)):
                comp_it_dict[i]={}
                comp_it_dict[i]['iterator']=iterators[i]
                comp_it_dict[i]['lower_bound']=self.annotations['iterators'][iterators[i]]['lower_bound']
                comp_it_dict[i]['upper_bound']=self.annotations['iterators'][iterators[i]]['upper_bound']

            self.it_dict[comp]=comp_it_dict
        # print(self.it_dict)

        iterators=list(self.annotations["iterators"].keys())

        for i in range(len(iterators)):
        
            loop_repr=[]
            loop_repr.append(self.annotations['iterators'][iterators[i]]['lower_bound'])
            loop_repr.append(self.annotations['iterators'][iterators[i]]['upper_bound'])
            loop_repr.extend([0,0,0,0,0,0,0,0,0,0,0])
            loop_log_rep = list(np.log1p(loop_repr))
            loop_repr.extend(loop_log_rep)
            self.repr["loops_representation"]=np.vstack([self.repr["loops_representation"],np.array([loop_repr])])

            childs_indexes=[iterators.index(child) for child in self.annotations['iterators'][iterators[i]]['child_iterators']]
            if len(childs_indexes)!=11:
                for j in range(11-len(childs_indexes)):
                    childs_indexes.append(-1)
            self.repr["child_list"]=np.vstack([self.repr["child_list"], np.array([childs_indexes])])
            
            if self.annotations['iterators'][iterators[i]]['computations_list']!=[]:
                self.repr['has_comps']=np.append(self.repr['has_comps'],1)
            else:
                self.repr['has_comps']=np.append(self.repr['has_comps'],0)

            computations_list=list(self.annotations['computations'].keys())
            loop_comps=[computations_list.index(comp) for comp in self.annotations['iterators'][iterators[i]]['computations_list']]
            if len(loop_comps)!=5:
                for j in range(5-len(loop_comps)):
                    loop_comps.append(-1)
            self.repr["computations_indices"]=np.vstack([self.repr["computations_indices"],np.array([loop_comps])])
        

        #Add null vectors if needed to avoid mismatching error of env.observation's type and reset_obs's type              
        for i in range(15-len(self.annotations["iterators"])):
            loop_repr=np.full(26,-1)
            self.repr["loops_representation"]=np.vstack([self.repr["loops_representation"],loop_repr])
        
        for i in range(12-len(self.annotations["iterators"])):
            self.repr["child_list"]=np.vstack([self.repr["child_list"], np.full(11,-1)])
            self.repr['has_comps']=np.append(self.repr['has_comps'],0)
            self.repr["computations_indices"]=np.vstack([self.repr["computations_indices"],np.full(5,-1)])

        
        if len(self.common_it) == 5:
            self.repr["action_mask"] = np.array([1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1], dtype=np.float32)
        else:
            if len(self.common_it) == 4:
                self.repr["action_mask"] = np.array([1, 1, 1, 0, 0, 0, 0, 1, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 0, 0, 0, 0, 1, 1, 0, 0, 0, 0, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 0, 0, 0, 0, 1, 1, 1, 1, 1, 1], dtype=np.float32)
            else: 
                if len(self.common_it) == 3:
                    self.repr["action_mask"] = np.array([1, 1, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0, 1, 1, 1, 1, 1, 1], dtype=np.float32)
                else: 
                    if len(self.common_it) == 2:
                        self.repr["action_mask"] = np.array([1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 0, 1, 1, 1, 1, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 1, 1], dtype=np.float32)
                    else:
                        if len(self.common_it) == 1:
                            self.repr["action_mask"] = np.array([0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 0, 0, 1, 0, 1, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 1, 1], dtype=np.float32)
    
        if len(self.comps)==1:
            np.put(self.repr["action_mask"],[56,57,58,59,60],[0, 0, 0, 0, 0])  
        return self.repr

    def apply_interchange(self, params):
        for comp in self.comps:
            l_code = "L" + self.it_dict[comp][params["first_dim_index"]]['iterator']
            self.repr["representation"][self.comp_indic_dict[comp]][self.placeholders[comp][l_code + "Interchanged"]] = 1
            l_code = "L" + self.it_dict[comp][params["second_dim_index"]]['iterator']
            self.repr["representation"][self.comp_indic_dict[comp]][self.placeholders[comp][l_code + "Interchanged"]] = 1

        iterators=list(self.annotations["iterators"].keys())
        if self.it_dict[comp][params["first_dim_index"]]['iterator'] in iterators:
            loop_1=iterators.index(self.it_dict[comp][params["first_dim_index"]]['iterator'])
        elif self.it_dict[comp][params["first_dim_index"]]['iterator'] in self.added_iterators:
            loop_1=len(self.annotations['iterators'])+ self.added_iterators.index(self.it_dict[comp][params["first_dim_index"]]['iterator'])  
        self.repr["loops_representation"][loop_1][2]=1
        
        if self.it_dict[comp][params["second_dim_index"]]['iterator'] in iterators:
            loop_2=iterators.index(self.it_dict[comp][params["second_dim_index"]]['iterator'])
        elif self.it_dict[comp][params["second_dim_index"]]['iterator'] in self.added_iterators:
            loop_2=len(self.annotations['iterators'])+ self.added_iterators.index(self.it_dict[comp][params["second_dim_index"]]['iterator'])  
        self.repr["loops_representation"][loop_2][2]=1

        for i in range(28):
            self.repr["action_mask"][i]=0
        for i in range(56,61):
            self.repr["action_mask"][i]=0
        
        for comp in self.comps:
            dim = self.schedule_dict[comp]["dim"]
            interchange_matrix = np.eye(dim,dim)
            first_iter_index = params["first_dim_index"]
            second_iter_index = params["second_dim_index"]
            interchange_matrix[first_iter_index, first_iter_index] = 0
            interchange_matrix[second_iter_index, second_iter_index] = 0
            interchange_matrix[first_iter_index, second_iter_index] = 1
            interchange_matrix[second_iter_index, first_iter_index] = 1
            self.schedule_dict[comp]["transformation_matrices"].append(interchange_matrix)
            self.schedule_dict[comp]["transformation_matrix"] =  interchange_matrix @ self.schedule_dict[comp]["transformation_matrix"]

    def apply_tiling(self, params):
        for comp in self.comps:
            comp_index=self.comp_indic_dict[comp]
       
            first_dim_index=params["first_dim_index"]
            second_dim_index=params["second_dim_index"]
            self.schedule_dict[comp]['tiling']= {'tiling_depth': params["tiling_depth"],
                                        'tiling_dims': [self.it_dict[comp][first_dim_index]['iterator'], self.it_dict[comp][second_dim_index]['iterator']],
                                        'tiling_factors': [params["first_factor"], params["second_factor"]]}
            l_code = "L" + self.it_dict[comp][first_dim_index]['iterator']
            self.repr["representation"][self.comp_indic_dict[comp]][self.placeholders[comp][l_code + "Tiled"]] = 1
            self.repr["representation"][self.comp_indic_dict[comp]][self.placeholders[comp][l_code + "TileFactor"]] = params[
                "first_factor"
            ]

            #update the loop bounds if tiling is applied on loop 1
            if params["tiling_loop_1"]:
                # print("inside loop tiling 1")
                new_upper_bound_1=self.repr["representation"][self.comp_indic_dict[comp]][first_dim_index*20+1]/params["first_factor"]
                self.repr["representation"][self.comp_indic_dict[comp]][first_dim_index*20+1]=new_upper_bound_1
                new_inner_upper_bound_1=params["first_factor"]
                self.repr["representation"][self.comp_indic_dict[comp]][first_dim_index*20+10]=new_inner_upper_bound_1
                # print("after loop tiling 1")
                #Add the loop representation of the newly added iterator
                loop_added="{}_1".format(self.it_dict[comp][first_dim_index]['iterator'])
                self.added_iterators.append(loop_added)
                loop_index=len(self.annotations['iterators']) + self.added_iterators.index(loop_added)
                #Initialize lower and upper bounds
                loop_repr=[]
                if self.repr["representation"][comp_index][self.placeholders[comp][l_code + "Reversed"]]==1:
                    lower_bound=self.repr["representation"][comp_index][second_dim_index*20+1]
                else:
                    lower_bound=self.repr["representation"][comp_index][second_dim_index*20]                
                loop_repr.extend([lower_bound, params["first_factor"]])
                #Initialize the different tags
                loop_repr.extend([0,0,0,0,0,0,0,0,0,0,0])
                loop_log_rep = list(np.log1p(loop_repr))
                loop_repr.extend(loop_log_rep)
                self.repr["loops_representation"][loop_index]=loop_repr

            l_code = "L" + self.it_dict[comp][second_dim_index]['iterator']
            self.repr["representation"][self.comp_indic_dict[comp]][self.placeholders[comp][l_code + "Tiled"]] = 1
            self.repr["representation"][self.comp_indic_dict[comp]][self.placeholders[comp][l_code + "TileFactor"]] = params[
                "second_factor"
            ]
            #update the loop bounds if tiling is applied on loop 2
            if params["tiling_loop_2"]:
                # print("inside loop tiling 2")
                new_upper_bound_2=self.repr["representation"][self.comp_indic_dict[comp]][second_dim_index*20+1]/params["second_factor"]
                self.repr["representation"][self.comp_indic_dict[comp]][second_dim_index*20+1]=new_upper_bound_2
                new_inner_upper_bound_2=params["second_factor"]
                self.repr["representation"][self.comp_indic_dict[comp]][second_dim_index*20+10]=new_inner_upper_bound_2
                # print("after loop tiling 2")

                #Add the loop representation of the newly added iterator
                loop_added="{}_1".format(self.it_dict[comp][second_dim_index]['iterator'])
                self.added_iterators.append(loop_added)
                loop_index=len(self.annotations['iterators']) + self.added_iterators.index(loop_added)
                #Initialize lower and upper bounds
                loop_repr=[]

                if self.repr["representation"][comp_index][self.placeholders[comp][l_code + "Reversed"]]==1:
                    lower_bound=self.repr["representation"][comp_index][second_dim_index*20+1]
                else:
                    lower_bound=self.repr["representation"][comp_index][second_dim_index*20]
                loop_repr.extend([lower_bound, params["second_factor"]])

                #Initialize the different tags
                loop_repr.extend([0,0,0,0,0,0,0,0,0,0,0])
                loop_log_rep = list(np.log1p(loop_repr))
                loop_repr.extend(loop_log_rep)
                self.repr["loops_representation"][loop_index]=loop_repr

            if params["tiling_depth"] == 3:
                third_dim_index=params["third_dim_index"]
                self.schedule_dict[comp]['tiling']= {'tiling_depth': params["tiling_depth"],
                                        'tiling_dims': [self.it_dict[comp][first_dim_index]['iterator'],
                                                        self.it_dict[comp][second_dim_index]['iterator'],
                                                        self.it_dict[comp][third_dim_index]['iterator']],
                                        'tiling_factors': [params["first_factor"],
                                                            params["second_factor"],
                                                            params["third_factor"]]}
                l_code = "L" + self.it_dict[comp][third_dim_index]['iterator']
                self.repr["representation"][self.comp_indic_dict[comp]][self.placeholders[comp][l_code + "Tiled"]] = 1
                self.repr["representation"][self.comp_indic_dict[comp]][self.placeholders[comp][l_code + "TileFactor"]] = params[
                    "third_factor"
                ]
                #update the loop bounds if tiling is applied on loop 3
                if params["tiling_loop_3"]:
                    # print("inside loop tiling 3")
                    new_upper_bound_3=self.repr["representation"][self.comp_indic_dict[comp]][third_dim_index*20+1]/params["third_factor"]
                    self.repr["representation"][self.comp_indic_dict[comp]][third_dim_index*20+1]=new_upper_bound_3
                    new_inner_upper_bound_3=params["third_factor"]
                    self.repr["representation"][self.comp_indic_dict[comp]][third_dim_index*20+10]=new_inner_upper_bound_3
                    # print("after loop tiling 3")

                    #Add the loop representation of the newly added iterator
                    loop_added="{}_1".format(self.it_dict[comp][third_dim_index]['iterator'])
                    self.added_iterators.append(loop_added)
                    loop_index=len(self.annotations['iterators']) + self.added_iterators.index(loop_added)
                    #Initialize lower and upper bounds
                    loop_repr=[]
                    if self.repr["representation"][comp_index][self.placeholders[comp][l_code + "Reversed"]]==1:
                        lower_bound=self.repr["representation"][comp_index][third_dim_index*20+1]
                    else:
                        lower_bound=self.repr["representation"][comp_index][third_dim_index*20]

                    loop_repr.extend([lower_bound,params["third_factor"]])
                    #Initialize the different tags
                    loop_repr.extend([0,0,0,0,0,0,0,0,0,0,0])
                    loop_log_rep = list(np.log1p(loop_repr))
                    loop_repr.extend(loop_log_rep)
                    self.repr["loops_representation"][loop_index]=loop_repr

        #Update the loops representation
        iterators=list(self.annotations["iterators"].keys())

        if self.it_dict[comp][first_dim_index]['iterator'] in iterators:
            loop_1=iterators.index(self.it_dict[comp][first_dim_index]['iterator'])
        elif self.it_dict[comp][first_dim_index]['iterator'] in self.added_iterators:
            loop_1=len(self.annotations['iterators'])+ self.added_iterators.index(self.it_dict[comp][first_dim_index]['iterator'])

        self.repr["loops_representation"][loop_1][3]=1
        self.repr["loops_representation"][loop_1][4]=params['first_factor']

        if self.it_dict[comp][second_dim_index]['iterator'] in iterators:
            loop_2=iterators.index(self.it_dict[comp][second_dim_index]['iterator'])
        elif self.it_dict[comp][second_dim_index]['iterator'] in self.added_iterators:
            loop_2=len(self.annotations['iterators'])+ self.added_iterators.index(self.it_dict[comp][second_dim_index]['iterator'])  

        self.repr["loops_representation"][loop_2][3]=1
        self.repr["loops_representation"][loop_2][4]=params['second_factor']

        #Update the loop representation
        if params["tiling_depth"] == 3:

            if self.it_dict[comp][third_dim_index]['iterator'] in iterators:
                loop_3=iterators.index(self.it_dict[comp][third_dim_index]['iterator'])
            elif self.it_dict[comp][third_dim_index]['iterator'] in self.added_iterators:
                loop_3=len(self.annotations['iterators'])+ self.added_iterators.index(self.it_dict[comp][third_dim_index]['iterator'])  

            self.repr["loops_representation"][loop_3][3]=1
            self.repr["loops_representation"][loop_3][4]=params['third_factor']
            
            
            if self.is_interchaged == False:

                if len(self.common_it) == 5:
                    if params["tiling_loop_1"] and params["tiling_loop_2"] and params["tiling_loop_3"]:
                        self.repr["action_mask"][[Action.INTERCHANGE05, Action.INTERCHANGE06, Action.INTERCHANGE07, Action.INTERCHANGE15, Action.INTERCHANGE16, Action.INTERCHANGE17, 
                        Action.INTERCHANGE25, Action.INTERCHANGE26, Action.INTERCHANGE27, Action.INTERCHANGE35, Action.INTERCHANGE36, Action.INTERCHANGE37, 
                        Action.INTERCHANGE45, Action.INTERCHANGE46, Action.INTERCHANGE47,Action.INTERCHANGE56,Action.INTERCHANGE57, Action.INTERCHANGE67]]=1
                    elif params["tiling_loop_1"] and params["tiling_loop_2"] or params["tiling_loop_2"] and params["tiling_loop_3"] or params["tiling_loop_1"] and params["tiling_loop_3"]:
                        self.repr["action_mask"][[Action.INTERCHANGE05, Action.INTERCHANGE06, Action.INTERCHANGE15, Action.INTERCHANGE16, Action.INTERCHANGE25, Action.INTERCHANGE26, 
                        Action.INTERCHANGE35, Action.INTERCHANGE36, Action.INTERCHANGE45, Action.INTERCHANGE46, Action.INTERCHANGE56]]=1
                    elif params["tiling_loop_1"] or params["tiling_loop_2"] or params["tiling_loop_3"] :
                        self.repr["action_mask"][[Action.INTERCHANGE05, Action.INTERCHANGE15, Action.INTERCHANGE25,Action.INTERCHANGE35, Action.INTERCHANGE45]]=1

                if len(self.common_it) == 4:
                    if params["tiling_loop_1"] and params["tiling_loop_2"] and params["tiling_loop_3"]:
                        self.repr["action_mask"][[Action.INTERCHANGE04, Action.INTERCHANGE05, Action.INTERCHANGE06, Action.INTERCHANGE14, Action.INTERCHANGE15, Action.INTERCHANGE16, 
                        Action.INTERCHANGE24, Action.INTERCHANGE25, Action.INTERCHANGE26, Action.INTERCHANGE34, Action.INTERCHANGE35, Action.INTERCHANGE36, 
                        Action.INTERCHANGE45, Action.INTERCHANGE46, Action.INTERCHANGE56]]=1
                    elif params["tiling_loop_1"] and params["tiling_loop_2"] or params["tiling_loop_2"] and params["tiling_loop_3"] or params["tiling_loop_1"] and params["tiling_loop_3"]:
                        self.repr["action_mask"][[Action.INTERCHANGE04, Action.INTERCHANGE05, Action.INTERCHANGE14, Action.INTERCHANGE15,
                        Action.INTERCHANGE24, Action.INTERCHANGE25, Action.INTERCHANGE34, Action.INTERCHANGE35, Action.INTERCHANGE45]]=1
                    elif params["tiling_loop_1"] or params["tiling_loop_2"] or params["tiling_loop_3"] :
                        self.repr["action_mask"][[Action.INTERCHANGE04, Action.INTERCHANGE14, Action.INTERCHANGE24, Action.INTERCHANGE34]]=1    

                if len(self.common_it) == 3:
                    if params["tiling_loop_1"] and params["tiling_loop_2"] and params["tiling_loop_3"]:
                        self.repr["action_mask"][[Action.INTERCHANGE03, Action.INTERCHANGE04, Action.INTERCHANGE05, Action.INTERCHANGE13, Action.INTERCHANGE14, Action.INTERCHANGE15, 
                        Action.INTERCHANGE23, Action.INTERCHANGE24, Action.INTERCHANGE25, Action.INTERCHANGE34, Action.INTERCHANGE35, 
                        Action.INTERCHANGE45]]=1    
                    elif params["tiling_loop_1"] and params["tiling_loop_2"] or params["tiling_loop_2"] and params["tiling_loop_3"] or params["tiling_loop_1"] and params["tiling_loop_3"]:
                        self.repr["action_mask"][[Action.INTERCHANGE03, Action.INTERCHANGE04, Action.INTERCHANGE13, Action.INTERCHANGE14,
                        Action.INTERCHANGE23, Action.INTERCHANGE24, Action.INTERCHANGE34]]=1 
                    elif params["tiling_loop_1"] or params["tiling_loop_2"] or params["tiling_loop_3"] :
                        self.repr["action_mask"][[Action.INTERCHANGE03, Action.INTERCHANGE13, Action.INTERCHANGE23]]=1 
                
                if len(self.common_it) == 2:
                    if params["tiling_loop_1"] and params["tiling_loop_2"] and params["tiling_loop_3"]:
                        self.repr["action_mask"][[Action.INTERCHANGE02, Action.INTERCHANGE03, Action.INTERCHANGE04, Action.INTERCHANGE12, Action.INTERCHANGE13, Action.INTERCHANGE14, 
                        Action.INTERCHANGE23, Action.INTERCHANGE24, Action.INTERCHANGE34]]=1    
                    elif params["tiling_loop_1"] and params["tiling_loop_2"] or params["tiling_loop_2"] and params["tiling_loop_3"] or params["tiling_loop_1"] and params["tiling_loop_3"]:
                        self.repr["action_mask"][[Action.INTERCHANGE02, Action.INTERCHANGE03, Action.INTERCHANGE12, Action.INTERCHANGE13, Action.INTERCHANGE23]]=1 
                    elif params["tiling_loop_1"] or params["tiling_loop_2"] or params["tiling_loop_3"] :
                        self.repr["action_mask"][[Action.INTERCHANGE02, Action.INTERCHANGE12]]=1 

                if len(self.common_it) == 1:
                    if params["tiling_loop_1"] and params["tiling_loop_2"] and params["tiling_loop_3"]:
                        self.repr["action_mask"][[Action.INTERCHANGE01, Action.INTERCHANGE02, Action.INTERCHANGE03, Action.INTERCHANGE12, Action.INTERCHANGE13, Action.INTERCHANGE23]]=1    
                    elif params["tiling_loop_1"] and params["tiling_loop_2"] or params["tiling_loop_2"] and params["tiling_loop_3"] or params["tiling_loop_1"] and params["tiling_loop_3"]:
                        self.repr["action_mask"][[Action.INTERCHANGE01, Action.INTERCHANGE02, Action.INTERCHANGE12, Action.INTERCHANGE13]]=1    
                    elif params["tiling_loop_1"] or params["tiling_loop_2"] or params["tiling_loop_3"] :
                        self.repr["action_mask"][[Action.INTERCHANGE01]]=1  

            if self.is_reversed == False:
                if len(self.common_it) == 5:
                    if params["tiling_loop_1"] and params["tiling_loop_2"] and params["tiling_loop_3"]:
                        self.repr["action_mask"][[Action.REVERSAL5,Action.REVERSAL6, Action.REVERSAL7]]=1
                    elif params["tiling_loop_1"] and params["tiling_loop_2"] or params["tiling_loop_2"] and params["tiling_loop_3"] or params["tiling_loop_1"] and params["tiling_loop_3"]:
                        self.repr["action_mask"][[Action.REVERSAL5,Action.REVERSAL6]]=1
                    elif params["tiling_loop_1"] or params["tiling_loop_2"] or params["tiling_loop_3"] :
                        self.repr["action_mask"][Action.REVERSAL5]=1

                elif len(self.common_it) == 4:
                    if params["tiling_loop_1"] and params["tiling_loop_2"] and params["tiling_loop_3"]:
                        self.repr["action_mask"][[Action.REVERSAL4,Action.REVERSAL5, Action.REVERSAL6]]=1
                    elif params["tiling_loop_1"] and params["tiling_loop_2"] or params["tiling_loop_2"] and params["tiling_loop_3"] or params["tiling_loop_1"] and params["tiling_loop_3"]:
                        self.repr["action_mask"][[Action.REVERSAL4,Action.REVERSAL5]]=1
                    elif params["tiling_loop_1"] or params["tiling_loop_2"] or params["tiling_loop_3"] :
                        self.repr["action_mask"][Action.REVERSAL4]=1

                elif len(self.common_it) == 3:
                    if params["tiling_loop_1"] and params["tiling_loop_2"] and params["tiling_loop_3"]:
                        self.repr["action_mask"][[Action.REVERSAL3,Action.REVERSAL4, Action.REVERSAL5]]=1
                    elif params["tiling_loop_1"] and params["tiling_loop_2"] or params["tiling_loop_2"] and params["tiling_loop_3"] or params["tiling_loop_1"] and params["tiling_loop_3"]:
                        self.repr["action_mask"][[Action.REVERSAL3,Action.REVERSAL4]]=1
                    elif params["tiling_loop_1"] or params["tiling_loop_2"] or params["tiling_loop_3"] :
                        self.repr["action_mask"][Action.REVERSAL3]=1

                elif len(self.common_it) == 2:
                    if params["tiling_loop_1"] and params["tiling_loop_2"] and params["tiling_loop_3"]:
                        self.repr["action_mask"][[Action.REVERSAL2,Action.REVERSAL3, Action.REVERSAL4]]=1
                    elif params["tiling_loop_1"] and params["tiling_loop_2"] or params["tiling_loop_2"] and params["tiling_loop_3"] or params["tiling_loop_1"] and params["tiling_loop_3"]:
                        self.repr["action_mask"][[Action.REVERSAL2,Action.REVERSAL3]]=1
                    elif params["tiling_loop_1"] or params["tiling_loop_2"] or params["tiling_loop_3"] :
                        self.repr["action_mask"][Action.REVERSAL2]=1

                elif len(self.common_it) == 1:
                    if params["tiling_loop_1"] and params["tiling_loop_2"] and params["tiling_loop_3"]:
                        self.repr["action_mask"][[Action.REVERSAL1,Action.REVERSAL2, Action.REVERSAL3]]=1
                    elif params["tiling_loop_1"] and params["tiling_loop_2"] or params["tiling_loop_2"] and params["tiling_loop_3"] or params["tiling_loop_1"] and params["tiling_loop_3"]:
                        self.repr["action_mask"][[Action.REVERSAL1,Action.REVERSAL2]]=1
                    elif params["tiling_loop_1"] or params["tiling_loop_2"] or params["tiling_loop_3"] :
                        self.repr["action_mask"][Action.REVERSAL1]=1
        
        for i in range(28,41):
            self.repr["action_mask"][i]=0

        for i in range(56,61):
            self.repr["action_mask"][i]=0

    def apply_unrolling(self, params):

        for comp in self.comps:
            # print(comp)
            self.repr["representation"][self.comp_indic_dict[comp]][self.placeholders[comp]["Unrolled"]] = 1
            self.repr["representation"][self.comp_indic_dict[comp]][self.placeholders[comp]["UnrollFactor"]] = params[comp]["unrolling_factor"]

            l_code = "L" + self.it_dict[comp][params[comp]["dim_index"]]['iterator']
            index_upper_bound=self.placeholders[comp][l_code+'Interchanged']-1
            self.repr["representation"][self.comp_indic_dict[comp]][index_upper_bound]=self.repr["representation"][self.comp_indic_dict[comp]][index_upper_bound]/params[comp]["unrolling_factor"]

            #Update the loop representation
            iterators=list(self.annotations["iterators"].keys())
            if self.it_dict[comp][params[comp]["dim_index"]]['iterator'] in iterators:
                loop_index=iterators.index(self.it_dict[comp][params[comp]["dim_index"]]['iterator'])
            elif self.it_dict[comp][params[comp]["dim_index"]]['iterator'] in self.added_iterators:
                loop_index=len(self.annotations['iterators'])+ self.added_iterators.index(self.it_dict[comp][params[comp]["dim_index"]]['iterator'])           
            self.repr["loops_representation"][loop_index][5]=1
            self.repr["loops_representation"][loop_index][6]=params[comp]['unrolling_factor']

        for i in range(41,44):
            self.repr["action_mask"][i]=0
        for i in range(56,61):
            self.repr["action_mask"][i]=0
        
        # print("1.6")
        try:
            for comp in self.comps:
                self.schedule_dict[comp]["unrolling_factor"] = params[comp]["unrolling_factor"]
        except Exception:
            print("ERROR_MODEL",traceback.format_exc())

        # print("1.7")

    def apply_skewing(self, params):
        dim_1=params["first_dim_index"]
        dim_2=params["second_dim_index"]

        for comp in self.comps:
            l1_code = "L" + self.it_dict[comp][dim_1]['iterator']
            l2_code = "L" + self.it_dict[comp][dim_2]['iterator']

            #to get the start of the iterator in the representation template (just after the bounds)
            index1_upper_bound=self.placeholders[comp][l1_code+'Interchanged']-1
            index1_lower_bound=self.placeholders[comp][l1_code+'Interchanged']-2
            index2_upper_bound=self.placeholders[comp][l2_code+'Interchanged']-1
            index2_lower_bound=self.placeholders[comp][l2_code+'Interchanged']-2

            l1_lower_bound=self.repr["representation"][self.comp_indic_dict[comp]][index1_lower_bound]
            l1_upper_bound=self.repr["representation"][self.comp_indic_dict[comp]][index1_upper_bound]
            l2_lower_bound=self.repr["representation"][self.comp_indic_dict[comp]][index2_lower_bound]
            l2_upper_bound=self.repr["representation"][self.comp_indic_dict[comp]][index2_upper_bound]

            l1_extent = l1_upper_bound - l1_lower_bound
            l2_extent = l2_upper_bound - l2_lower_bound

            skew_factor = params["first_factor"]
            self.repr["representation"][self.comp_indic_dict[comp]][self.placeholders[comp][l1_code + "Skewed"]] = 1
            self.repr["representation"][self.comp_indic_dict[comp]][self.placeholders[comp][l1_code + "SkewFactor"]] = skew_factor
            self.repr["representation"][self.comp_indic_dict[comp]][index1_lower_bound]= abs(params["first_factor"]) * l1_lower_bound
            self.repr["representation"][self.comp_indic_dict[comp]][index1_upper_bound]= l1_lower_bound + abs(params["first_factor"]) * l1_extent + abs(params["second_factor"]) * l2_extent

            skew_factor = params["second_factor"]
            self.repr["representation"][self.comp_indic_dict[comp]][self.placeholders[comp][l2_code + "Skewed"]] = 1
            self.repr["representation"][self.comp_indic_dict[comp]][self.placeholders[comp][l2_code + "SkewFactor"]] = skew_factor
            self.repr["representation"][self.comp_indic_dict[comp]][index2_lower_bound]= 0
            self.repr["representation"][self.comp_indic_dict[comp]][index2_upper_bound]=(l2_extent) + 1

        #Update the loop representation
        iterators=list(self.annotations["iterators"].keys())
        if self.it_dict[comp][dim_1]['iterator'] in iterators:
            loop_1=iterators.index(self.it_dict[comp][dim_1]['iterator'])
        elif self.it_dict[comp][dim_1]['iterator'] in self.added_iterators:
            loop_1=len(self.annotations['iterators'])+ self.added_iterators.index(self.it_dict[comp][dim_1]['iterator'])        
        self.repr["loops_representation"][loop_1][7]=1
        self.repr["loops_representation"][loop_1][8]=params['first_factor']
        #Skewing is applied on common loop levels so loop bounds are equal for all computations
        self.repr["loops_representation"][loop_1][9]=self.repr["representation"][0][index1_upper_bound]-self.repr["representation"][0][index1_lower_bound]

        if self.it_dict[comp][dim_2]['iterator'] in iterators:
            loop_2=iterators.index(self.it_dict[comp][dim_2]['iterator'])
        elif self.it_dict[comp][dim_2]['iterator'] in self.added_iterators:
            loop_2=len(self.annotations['iterators'])+ self.added_iterators.index(self.it_dict[comp][dim_2]['iterator']) 
        self.repr["loops_representation"][loop_2][7]=1
        self.repr["loops_representation"][loop_2][8]=params['second_factor']
        self.repr["loops_representation"][loop_2][9]=self.repr["representation"][0][index2_upper_bound]-self.repr["representation"][0][index2_lower_bound]

        self.repr["action_mask"][44]=0
        self.repr["action_mask"][45]=0
        for i in range(56,61):
            self.repr["action_mask"][i]=0
        
        for comp in self.comps:
            dim = self.schedule_dict[comp]["dim"]
            skewing_matrix = np.eye(dim,dim)
            first_iter_index = params["first_dim_index"]
            second_iter_index = params["second_dim_index"]
            first_factor = params["first_factor"]
            second_factor = params["second_factor"]
            if (first_factor, second_factor) in global_dioph_sols_dict:
                a, b = global_dioph_sols_dict[(first_factor, second_factor)]
            else:
                a, b = ScheduleUtils.linear_diophantine_default(first_factor, second_factor)

            skewing_matrix[first_iter_index, first_iter_index] = first_factor
            skewing_matrix[first_iter_index, second_iter_index] = second_factor
            skewing_matrix[second_iter_index, first_iter_index] = a
            skewing_matrix[second_iter_index, second_iter_index] = b
            self.schedule_dict[comp]["transformation_matrices"].append(skewing_matrix)
            self.schedule_dict[comp]["transformation_matrix"] = skewing_matrix @ self.schedule_dict[comp]["transformation_matrix"]

    def apply_parallelization(self, params):
        first_comp=list(self.it_dict.keys())[0]
        iterator = self.it_dict[first_comp][params["dim_index"]]['iterator']
        self.schedule_dict[first_comp]["parallelized_dim"] = iterator
        l_code = "L" + iterator

        self.repr["representation"][0][self.placeholders[first_comp][l_code + "Parallelized"]] = 1

        #Update the loop representation
        iterators=list(self.annotations["iterators"].keys())
        if self.it_dict[first_comp][params["dim_index"]]['iterator'] in iterators:
            loop_index=iterators.index(self.it_dict[first_comp][params["dim_index"]]['iterator'])
        elif self.it_dict[first_comp][params["dim_index"]]['iterator'] in self.added_iterators:
            loop_index=len(self.annotations['iterators'])+ self.added_iterators.index(self.it_dict[first_comp][params["dim_index"]]['iterator'])
        self.repr["loops_representation"][loop_index][10]=1
        #Update the action mask
        self.repr["action_mask"][46]=0
        self.repr["action_mask"][47]=0
        for i in range(56,61):
            self.repr["action_mask"][i]=0
        # print("The first comp is ", first_comp)
        # print("The result is ", self.schedule_dict[first_comp]["parallelized_dim"])

    def apply_reversal(self, params):
        for comp in self.comps:
            l_code = "L" + self.it_dict[comp][params["dim_index"]]['iterator']

            index_upper_bound=self.placeholders[comp][l_code+'Interchanged']-1
            index_lower_bound=self.placeholders[comp][l_code+'Interchanged']-2

            self.repr["representation"][self.comp_indic_dict[comp]][self.placeholders[comp][l_code + "Reversed"]] = 1

            tmp=self.repr["representation"][self.comp_indic_dict[comp]][index_lower_bound]
            self.repr["representation"][self.comp_indic_dict[comp]][index_lower_bound]=self.repr["representation"][self.comp_indic_dict[comp]][index_upper_bound]
            self.repr["representation"][self.comp_indic_dict[comp]][index_upper_bound]=tmp 

        #Update the loop representation
        iterators=list(self.annotations["iterators"].keys())
        if self.it_dict[comp][params["dim_index"]]['iterator'] in iterators:
            loop_index=iterators.index(self.it_dict[comp][params["dim_index"]]['iterator'])
        elif self.it_dict[comp][params["dim_index"]]['iterator'] in self.added_iterators:
            loop_index=len(self.annotations['iterators'])+ self.added_iterators.index(self.it_dict[comp][params["dim_index"]]['iterator'])        
        self.repr["loops_representation"][loop_index][11]=1

        for i in range(48,56):
            self.repr["action_mask"][i]=0
        for i in range(56,61):
            self.repr["action_mask"][i]=0
        
        for comp in self.comps:
            dim = self.schedule_dict[comp]["dim"]
            reversal_matrix = np.eye(dim,dim)
            dim_index = params["dim_index"]
            reversal_matrix[dim_index, dim_index] = -1
            self.schedule_dict[comp]["transformation_matrices"].append(reversal_matrix)
            self.schedule_dict[comp]["transformation_matrix"] = reversal_matrix @ self.schedule_dict[comp]["transformation_matrix"]
    
    def apply_fusion(self, params):
        fusion = []
        for comp in params["fuse_comps"]:
            fusion.append(comp)
            l_code = "L" + self.it_dict[comp][params["dim_index"]]['iterator']
            self.repr["representation"][self.comp_indic_dict[comp]][self.placeholders[comp][l_code + "Fused"]] = 1
        fusion.append(params["dim_index"])
        self.schedule_dict["fusions"].append(fusion)
        #Update the loop representation
        iterators=list(self.annotations["iterators"].keys())
        if self.it_dict[comp][params["dim_index"]]['iterator'] in iterators:
            loop_index=iterators.index(self.it_dict[comp][params["dim_index"]]['iterator'])
        elif self.it_dict[comp][params["dim_index"]]['iterator'] in self.added_iterators:
            loop_index=len(self.annotations['iterators'])+ self.added_iterators.index(self.it_dict[comp][params["dim_index"]]['iterator'])        
        self.repr["loops_representation"][loop_index][12]=1

        for i in range(56,61):
            self.repr["action_mask"][i]=0
