import yaml
import importlib
import logging
import os

logger = logging.getLogger(__name__)

def load_class(path: str):
    module_path, class_name = path.rsplit(".", 1)
    module = importlib.import_module(module_path)
    cls = getattr(module, class_name)
    return cls


def _load_config():
    try:
        project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
        with open(os.path.join(project_root, "config", "strategies.yaml"), encoding="utf-8") as f:
            return yaml.safe_load(f)
    except FileNotFoundError:
        logger.warning("config/strategies.yaml not found; strategy registry will be empty.")
        return {"strategies": {}}


def _configured_execution_modes() -> dict[str, str]:
    """Read per-asset modes without triggering credential initialization."""
    modes = {}
    for entry in filter(
        None,
        (item.strip() for item in os.getenv("ORBIT_ASSET_EXECUTION_MODES", "").split(",")),
    ):
        try:
            symbol, mode = (part.strip() for part in entry.split(":", 1))
        except ValueError:
            continue
        modes[symbol.upper()] = mode.lower()
    return modes


class _LazyStrategyRegistry(dict):
    """A dict-like registry that defers importing strategy classes until first access."""

    def __init__(self, config: dict):
        super().__init__()
        execution_modes = _configured_execution_modes()
        # Store raw config entries (strings), not the imported classes
        self._raw = {
            symbol: item["strategy"]
            for symbol, item in config.get("strategies", {}).items()
            if not item.get("execution_modes")
            or execution_modes.get(symbol.upper(), "paper")
            in item["execution_modes"]
        }

    def _resolve(self, symbol: str):
        path = self._raw[symbol]
        try:
            cls = load_class(path)
            # Cache the resolved class so we only import once
            super().__setitem__(symbol, cls)
            return cls
        except (ModuleNotFoundError, AttributeError) as exc:
            logger.error(
                "Could not load strategy '%s' for symbol '%s': %s", path, symbol, exc
            )
            raise

    # --- dict protocol overrides for lazy resolution ---

    def __getitem__(self, symbol):
        if symbol not in self._raw:
            raise KeyError(symbol)

        if not super().__contains__(symbol):
            self._resolve(symbol)

        return super().__getitem__(symbol)

    def __contains__(self, symbol):
        return symbol in self._raw

    def keys(self):
        return self._raw.keys()

    def items(self):
        for symbol in self._raw:
            yield symbol, self[symbol]

    def values(self):
        for symbol in self._raw:
            yield self[symbol]

    def get(self, symbol, default=None):
        if symbol in self._raw:
            try:
                return self[symbol]
            except (ModuleNotFoundError, AttributeError):
                return default
        return default


STRATEGY_REGISTRY = _LazyStrategyRegistry(_load_config())
