"""Create an installable QGIS plugin ZIP from the repository root."""

from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_NAME = "fast_multiband_zonal_stats"
FILES = (
    "__init__.py",
    "algorithm.py",
    "CHANGELOG.md",
    "dialog.py",
    "engine.py",
    "icon.svg",
    "LICENSE",
    "metadata.txt",
    "plugin.py",
    "provider.py",
    "README.md",
    "utils.py",
)


def main():
    destination = ROOT / "dist" / f"{PLUGIN_NAME}-0.1.1.zip"
    destination.parent.mkdir(exist_ok=True)
    with ZipFile(destination, "w", compression=ZIP_DEFLATED, compresslevel=9) as archive:
        for relative in FILES:
            archive.write(ROOT / relative, f"{PLUGIN_NAME}/{relative}")
    print(destination)


if __name__ == "__main__":
    main()
