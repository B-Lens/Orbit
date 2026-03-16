import yaml
import importlib
import logging

logger = logging.getLogger(__name__)

def load_class(path: str):
    module_path, class_name = path.rsplit(".", 1)
    module = importlib.import_module(module_path)
    cls = getattr(module, class_name)
    return cls


def _load_config():
    try:
        with open("config/strategies.yaml") as f:
            return yaml.safe_load(f)
    except FileNotFoundError:
        logger.warning("config/strategies.yaml not found; strategy registry will be empty.")
        return {"strategies": {}}


class _LazyStrategyRegistry(dict):
    """A dict-like registry that defers importing strategy classes until first access."""

    def __init__(self, config: dict):
        super().__init__()
        # Store raw config entries (strings), not the imported classes
        self._raw = {
            symbol: item["strategy"]
            for symbol, item in config.get("strategies", {}).items()
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
        if symbol not in self and symbol in self._raw:
            return self._resolve(symbol)
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
