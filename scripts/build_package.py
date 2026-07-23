"""Create an installable QGIS plugin ZIP from the repository root.

The file list is derived from the working tree rather than hand-maintained, so a
new module cannot silently be left out of a release, and the version is read
from metadata.txt so it cannot drift from what QGIS reports.
"""

import configparser
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_NAME = "fast_multiband_zonal_stats"

# Everything QGIS needs at runtime. Tests, packaging scripts and VCS metadata
# are deliberately excluded.
EXTRA_FILES = ("metadata.txt", "icon.svg", "LICENSE", "README.md", "CHANGELOG.md")
EXCLUDED_DIRECTORIES = {"tests", "scripts", "dist", ".git", "__pycache__", ".venv"}


def read_version() -> str:
    parser = configparser.ConfigParser()
    # QGIS metadata uses ';' comments and non-lowercased keys.
    parser.optionxform = str
    parser.read(ROOT / "metadata.txt", encoding="utf-8")
    return parser["general"]["version"].strip()


def collect_files() -> list[Path]:
    files = []
    for path in sorted(ROOT.rglob("*.py")):
        relative = path.relative_to(ROOT)
        if EXCLUDED_DIRECTORIES.intersection(relative.parts):
            continue
        files.append(relative)
    for name in EXTRA_FILES:
        candidate = ROOT / name
        if candidate.exists():
            files.append(Path(name))
        else:
            raise SystemExit(f"Required file is missing from the plugin: {name}")
    return files


def main():
    version = read_version()
    files = collect_files()
    destination = ROOT / "dist" / f"{PLUGIN_NAME}-{version}.zip"
    destination.parent.mkdir(exist_ok=True)
    with ZipFile(destination, "w", compression=ZIP_DEFLATED, compresslevel=9) as archive:
        for relative in files:
            archive.write(ROOT / relative, f"{PLUGIN_NAME}/{relative.as_posix()}")
    print(f"{destination}  ({len(files)} files, version {version})")


if __name__ == "__main__":
    main()
