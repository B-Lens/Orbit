import yaml
import importlib
from orbit.strategies.strategies_base import Strategy as BaseStrategy

def load_class(path: str):
    module_path, class_name = path.rsplit(".", 1)
    module = importlib.import_module(module_path)
    cls = getattr(module, class_name)

    if not issubclass(cls, BaseStrategy):
        raise TypeError(f"{path} is not a BaseStrategy")

    return cls

with open("config/strategies.yaml") as f:
    config = yaml.safe_load(f)

STRATEGY_REGISTRY = {
    symbol: load_class(item["strategy"])
    for symbol, item in config["strategies"].items()
}
