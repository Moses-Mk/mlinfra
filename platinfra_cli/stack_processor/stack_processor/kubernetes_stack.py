import json
import yaml
from platinfra_cli.stack_processor.stack_processor.stack import (
    AbstractStack,
)
from platinfra_cli.enums.provider import Provider
from platinfra_cli.enums.deployment_type import DeploymentType
from platinfra_cli.utils.constants import TF_PATH, CREATE_VPC_DB_SUBNET


class KubernetesStack(AbstractStack):
    """
    The KubernetesStack class is responsible for generating the
    Terraform configuration files for the Kubernetes stack.
    """

    def __init__(
        self,
        state_file_name: str,
        region: str,
        account_id: str,
        provider: Provider,
        deployment_type: DeploymentType,
        stacks: yaml,
    ):
        """
        Constructor for the KubernetesStack class.

        :param provider: The cloud provider
        :type provider: Provider
        :param stacks: The stacks configuration
        :type stacks: yaml
        """
        super().__init__(
            state_file_name=state_file_name,
            region=region,
            account_id=account_id,
            provider=provider,
            deployment_type=deployment_type,
            stacks=stacks,
        )
        self.output = {"output": []}

    def process_stack_config(self):
        pass

    def process_stack_modules(self):
        create_vpc_database_subnets: bool = False

        for module in self.stacks:
            # extracting stack type
            stack_type = [key for key in module.keys()][0]

            if "name" in module[stack_type]:
                name = module[stack_type]["name"]
            else:
                raise KeyError(f"No application assigned to the stack: {stack_type}")

            json_module = {"module": {name: {}}}

            # TODO: pickup right module source based on the deployment type

            json_module["module"][name][
                "source"
            ] = f"../modules/applications/{stack_type}/{name}/tf_module"

            # Reading inputs and outputs from the config file
            # placed in the applications/application folder and
            # adding them to json config for the application module
            application_config = self._read_config_file(
                stack_type=stack_type, application_name=name
            )

            if application_config is not None:
                if "inputs" in application_config:
                    inputs = {}
                    for input in application_config["inputs"]:
                        if not input["user_facing"]:
                            input_name = input["name"]
                            # handle the input from the yaml config
                            # if the input value is not a string
                            input_value = (
                                input["default"]
                                if input["default"] != "None"
                                else "${ %s }" % f"{input['value']}"
                            )
                            inputs.update({input_name: input_value})
                        json_module["module"][name].update(inputs)

                if "outputs" in application_config:
                    for output in application_config["outputs"]:
                        if output["export"]:
                            output_val = "${ %s }" % f"module.{name}.{output['name']}"

                            self.output["output"].append(
                                {output["name"]: {"value": output_val}}
                            )

            # Checks if there are params in the config file which can be
            # passed to the application module. Params are checked against
            # the application module yaml file
            if "params" in module[stack_type]:
                # TODO: throw error for a param not existing in the yaml config
                params = {}
                for key, value in module[stack_type]["params"].items():
                    for input in application_config["inputs"]:
                        if key == input["name"]:
                            if input["user_facing"]:
                                params.update({key: value})

                                # check if configuration exists to create
                                # vpc database subnets. This currently handles
                                # the case where the key is boolean and value
                                # is true. This is a temporary solution and
                                # will be updated in the future.
                                if key in CREATE_VPC_DB_SUBNET and value:
                                    create_vpc_database_subnets = True
                            else:
                                raise KeyError(f"{key} is not a user facing parameter")
                json_module["module"][name].update(params)

            with open(
                f"./{TF_PATH}/stack_{stack_type}.tf.json", "w", encoding="utf-8"
            ) as tf_json:
                json.dump(json_module, tf_json, ensure_ascii=False, indent=2)

        # inject vpc module
        if self.provider == Provider.AWS:
            name = "vpc"
            json_module = {"module": {name: {}}}
            # json_module["module"][name]["name"] = f"{self.stack_name}-vpc"
            json_module["module"][name]["source"] = f"../modules/cloud/aws/{name}"
            json_module["module"][name][
                "create_database_subnets"
            ] = create_vpc_database_subnets

            with open(f"./{TF_PATH}/{name}.tf.json", "w", encoding="utf-8") as tf_json:
                json.dump(json_module, tf_json, ensure_ascii=False, indent=2)
        elif self.provider == Provider.GCP:
            pass

    def get_eks_module_refs(self, k8s_module_name: str = None) -> dict:
        module_source = (
            f"module.{k8s_module_name}"
            if k8s_module_name is not None and len(k8s_module_name) != 0
            else "data.terraform_remote_state.parent.outputs"
        )
        if self.provider == "aws":
            return {
                "host": f"${{{module_source}.k8s_endpoint}}",
                "token": "${data.aws_eks_cluster_auth.k8s.token}",
                "cluster_ca_certificate": f"${{base64decode({module_source}.k8s_ca_data)}}",
            }

    def _get_k8s_module_name(self, return_key: str = "cluster_name") -> str:
        k8s_module = [
            _module
            for _module in self.stack_config["modules"]
            if _module["type"] == "eks"
        ]
        k8s_module_name = k8s_module[0][return_key] if len(k8s_module) != 0 else None
        return k8s_module_name

    def process_stack_outputs(self):
        self.output["output"].append({"state_storage": {"value": self.state_file_name}})
        self.output["output"].append(
            {
                "providers": {
                    "value": {
                        "aws": {
                            "region": self.region,
                            "account_id": self.account_id,
                        }
                    }
                }
            }
        )
        with open(f"./{TF_PATH}/output.tf.json", "w", encoding="utf-8") as tf_json:
            json.dump(self.output, tf_json, ensure_ascii=False, indent=2)

    def process_stack_inputs(self):
        pass

    def _common_service_input(self) -> any:
        return {
            "variable": [
                {"region": [{"type": "string", "default": self.region}]},
                {"account_id": [{"type": "string", "default": self.account_id}]},
            ]
        }

    def _user_input(self) -> any:
        user_input = []
        for v in self.stack_config["input_variables"]:
            input = {v["name"]: [{"type": "string"}]}
            if "default" in v:
                input[v["name"]][0]["default"] = v["default"]
            user_input.append(input)
        return user_input

    def _default_config_input(self) -> any:
        user_input = []
        if "environments" in self.stack_config:
            for env in self.stack_config["environments"]:
                if self._is_env_match(env["name"]):
                    if "variables" in env:
                        for k in env["variables"]:
                            user_input.append(
                                {
                                    k: [
                                        {
                                            "type": "string",
                                            "default": env["variables"][k],
                                        }
                                    ]
                                }
                            )
                    break
        return user_input

    def prepare_input(self):
        if "input_variables" in self.stack_config:
            json_output = {"variable": []}
            if self.is_stack_component:
                json_output = self._common_service_input()

            json_output["variable"].extend(self._user_input())
            json_output["variable"].extend(self._default_config_input())

            with open(
                f"./{TF_PATH}/variable.tf.json", "w", encoding="utf-8"
            ) as tf_json:
                json.dump(json_output, tf_json, ensure_ascii=False, indent=2)

    def generate(self):
        # logger.info("Processing Kubernetes Infrastructure")
        self.process_stack_config()
        self.process_stack_inputs()
        self.process_stack_modules()
        self.process_stack_outputs()


if __name__ == "__main__":
    pass
