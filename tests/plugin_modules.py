"""Load the plugin's QGIS-free modules under a synthetic package name.

The repository directory name is not a valid Python identifier, and the modules
use relative imports, so they are bound into a throwaway package here.
"""

import importlib.util
import sys
import types
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_NAME = "fast_zonal_testpkg"


def load():
    """Import ``utils`` and ``engine`` once and return them."""
    if PACKAGE_NAME not in sys.modules:
        package = types.ModuleType(PACKAGE_NAME)
        package.__path__ = [str(ROOT)]
        sys.modules[PACKAGE_NAME] = package
    for module_name in ("utils", "engine"):
        full_name = f"{PACKAGE_NAME}.{module_name}"
        if full_name in sys.modules:
            continue
        spec = importlib.util.spec_from_file_location(full_name, ROOT / f"{module_name}.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[full_name] = module
        spec.loader.exec_module(module)
    return sys.modules[f"{PACKAGE_NAME}.utils"], sys.modules[f"{PACKAGE_NAME}.engine"]
