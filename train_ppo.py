import os
# import hydra
import ray
# from hydra.core.config_store import ConfigStore
from ray import tune
from ray.rllib.models.catalog import ModelCatalog
from ray.tune.registry import register_env

from rl_interface.environment import TiramisuScheduleEnvironment
from rl_interface.model import TiramisuModelMult
from utils.environment_variables import configure_env_variables
from utils.global_ray_variables import Actor, GlobalVarActor
from utils.rl_autoscheduler_config import (RLAutoSchedulerConfig,
                                           dict_to_config, parse_yaml_file,
                                           read_yaml_file)


# @hydra.main(config_path="config", config_name="config")
def main(config: RLAutoSchedulerConfig):
    configure_env_variables(config)
    local_dir = os.path.join(config.ray.base_path, config.ray.log_directory)
    with ray.init(num_cpus=config.ray.ray_num_cpus):
        progs_list_registery = GlobalVarActor.remote(
            config.environment.programs_file,
            config.environment.dataset_path,
            num_workers=config.ray.num_workers)
        shared_variable_actor = Actor.remote(progs_list_registery)

        register_env(
            "Tiramisu_env_v1",
            lambda a: TiramisuScheduleEnvironment(
                config,
                shared_variable_actor
            ),
        )
        ModelCatalog.register_custom_model("tiramisu_model_v1",
                                           TiramisuModelMult)

        analysis = tune.run(
            "PPO",
            local_dir=local_dir,
            name=config.ray.name,
            stop={"training_iteration": config.ray.training_iteration},
            max_failures=0,
            checkpoint_freq=config.ray.checkpoint_freq,
            verbose=0,
            config={
                "env": "Tiramisu_env_v1",
                "num_workers": config.ray.num_workers,
                "batch_mode": "complete_episodes",
                "train_batch_size": config.training.train_batch_size,
                "sgd_minibatch_size": config.training.sgd_minibatch_size,
                "lr": config.training.lr,
                "num_sgd_iter": config.training.num_sgd_iter,
                "framework": "torch",
                "_disable_preprocessor_api": True,
                "model": {
                    "custom_model": "tiramisu_model_v1",
                    "custom_model_config": {
                        "layer_sizes": list(config.model.layer_sizes),
                        "drops": list(config.model.drops),
                    },
                },
            },
        )


if __name__ == "__main__":
    parsed_yaml_dict = parse_yaml_file(read_yaml_file("config.yaml"))
    config = dict_to_config(parsed_yaml_dict)
    main(config)
