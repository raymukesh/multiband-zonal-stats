"""Small dependency-free helpers shared by the UI and algorithm."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable


STATISTICS = ("count", "nodata", "min", "max", "sum", "mean", "stddev")
AREA_UNITS = ("pixels", "m2", "km2", "ha", "acres")


def parse_band_selection(text: str, band_count: int) -> list[int]:
    """Parse ``all`` or a comma-separated list containing ranges.

    Band indexes are one-based, matching QGIS and GDAL.
    """
    if band_count < 1:
        raise ValueError("The raster has no bands.")
    value = (text or "all").strip().lower()
    if value in {"all", "*"}:
        return list(range(1, band_count + 1))

    selected: set[int] = set()
    for token in value.split(","):
        token = token.strip()
        if not token:
            continue
        if "-" in token:
            parts = token.split("-")
            if len(parts) != 2:
                raise ValueError(f"Invalid band range: {token!r}")
            start, end = (int(part.strip()) for part in parts)
            if start > end:
                raise ValueError(f"Band range must be ascending: {token!r}")
            selected.update(range(start, end + 1))
        else:
            selected.add(int(token))

    if not selected:
        raise ValueError("Select at least one raster band.")
    invalid = sorted(b for b in selected if b < 1 or b > band_count)
    if invalid:
        raise ValueError(
            f"Band {invalid[0]} is outside the available range 1–{band_count}."
        )
    return sorted(selected)


def parse_statistics(values: Iterable[str] | str) -> list[str]:
    if isinstance(values, str):
        values = values.split(",")
    result = []
    for value in values:
        name = value.strip().lower()
        if name and name not in STATISTICS:
            raise ValueError(f"Unknown statistic: {name}")
        if name and name not in result:
            result.append(name)
    if not result:
        raise ValueError("Select at least one statistic.")
    return result


def safe_band_name(description: str | None, band_index: int) -> str:
    name = (description or "").strip()
    return name if name else f"band_{band_index:03d}"


def safe_output_path(path: str) -> str:
    cleaned = path.strip()
    if not cleaned:
        raise ValueError("Choose an output CSV file.")
    output = Path(cleaned).expanduser()
    if output.suffix.lower() != ".csv":
        output = output.with_suffix(".csv")
    return str(output)


def clean_column_name(value: str, fallback: str = "field") -> str:
    cleaned = re.sub(r"[^0-9A-Za-z_]+", "_", value.strip()).strip("_").lower()
    if not cleaned:
        cleaned = fallback
    if cleaned[0].isdigit():
        cleaned = f"_{cleaned}"
    return cleaned


def unique_names(names: Iterable[str]) -> list[str]:
    seen: dict[str, int] = {}
    result = []
    for raw_name in names:
        name = clean_column_name(raw_name)
        seen[name] = seen.get(name, 0) + 1
        result.append(name if seen[name] == 1 else f"{name}_{seen[name]}")
    return result

