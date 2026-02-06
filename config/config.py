import os
import json
from typing import Any
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ARTIFACTS_DIR = os.path.join(PROJECT_ROOT, "artifacts")

DATA_PATH = os.path.join(PROJECT_ROOT, 'data')

def load_config() -> dict[str, Any]:
    # Construct the path to config.json
    config_path = os.path.join(
        os.path.dirname(__file__), "../config", "config.json"
    )
    with open(config_path, "r", encoding="utf-8") as config_file:
        return json.load(config_file)  # type: ignore[no-any-return]