"""Qt5/Qt6 and QGIS 3/4 compatibility shims.

QGIS 4 moves to Qt6, where unscoped enum access (``Qt.AlignTop``) is gone and
``QAction`` lives in QtGui rather than QtWidgets. QGIS 4 also retires several
class-scoped enums in favour of the central ``Qgis`` namespace.

Every lookup here tries the modern spelling first and falls back to the legacy
one, so a single code path runs on QGIS 3.34 through QGIS 4.x. Nothing in this
module raises at import time unless a name is genuinely unavailable in both.
"""

from __future__ import annotations

from qgis.core import Qgis

# QAction moved from QtWidgets to QtGui in Qt6.
try:  # Qt6
    from qgis.PyQt.QtGui import QAction
except ImportError:  # Qt5
    from qgis.PyQt.QtWidgets import QAction  # noqa: F401


_MISSING = object()


def _lookup(root, path):
    """Resolve a dotted attribute path, returning ``_MISSING`` if any part fails."""
    current = root
    for part in path.split("."):
        current = getattr(current, part, _MISSING)
        if current is _MISSING:
            return _MISSING
    return current


def resolve(description: str, *candidates):
    """Return the first ``(root, "dotted.path")`` candidate that resolves.

    ``description`` only appears in the error raised when every candidate fails,
    which means the host is a QGIS version this plugin has not been taught about.
    """
    for root, path in candidates:
        if root is _MISSING or root is None:
            continue
        value = _lookup(root, path)
        if value is not _MISSING:
            return value
    raise ImportError(
        f"This QGIS build exposes no known spelling for {description}. "
        f"Please report the QGIS version ({Qgis.QGIS_VERSION})."
    )


def qt_enum(owner, scope: str, name: str):
    """Return a Qt enum value under both PyQt5 (unscoped) and PyQt6 (scoped) rules."""
    return resolve(f"{owner.__name__}.{name}", (owner, name), (owner, f"{scope}.{name}"))


def _optional_import(module_name: str, attribute: str):
    """Import an attribute that may not exist in every QGIS generation."""
    try:
        module = __import__(module_name, fromlist=[attribute])
    except ImportError:
        return _MISSING
    return getattr(module, attribute, _MISSING)


# --- Processing enums -------------------------------------------------------

_QgsProcessing = _optional_import("qgis.core", "QgsProcessing")
_QgsProcessingParameterNumber = _optional_import("qgis.core", "QgsProcessingParameterNumber")
_QgsProcessingParameterDefinition = _optional_import("qgis.core", "QgsProcessingParameterDefinition")
_QgsWkbTypes = _optional_import("qgis.core", "QgsWkbTypes")

SOURCE_TYPE_VECTOR_POLYGON = resolve(
    "the polygon Processing source type",
    (Qgis, "ProcessingSourceType.VectorPolygon"),
    (_QgsProcessing, "TypeVectorPolygon"),
)

GEOMETRY_TYPE_POLYGON = resolve(
    "the polygon geometry type",
    (Qgis, "GeometryType.Polygon"),
    (_QgsWkbTypes, "GeometryType.PolygonGeometry"),
    (_QgsWkbTypes, "PolygonGeometry"),
)

PARAMETER_NUMBER_INTEGER = resolve(
    "the integer Processing parameter type",
    (Qgis, "ProcessingNumberParameterType.Integer"),
    (_QgsProcessingParameterNumber, "Type.Integer"),
    (_QgsProcessingParameterNumber, "Integer"),
)

PARAMETER_FLAG_ADVANCED = resolve(
    "the advanced Processing parameter flag",
    (Qgis, "ProcessingParameterFlag.Advanced"),
    (_QgsProcessingParameterDefinition, "Flag.FlagAdvanced"),
    (_QgsProcessingParameterDefinition, "FlagAdvanced"),
)


# --- GUI enums --------------------------------------------------------------
# Imported lazily by name so that headless use (qgis_process) never touches
# qgis.gui, which is unavailable in some server and CI builds.

def layer_filter(name: str):
    """Return a QgsMapLayerComboBox filter such as ``RasterLayer``."""
    proxy_model = _optional_import("qgis.core", "QgsMapLayerProxyModel")
    return resolve(
        f"the {name} layer filter",
        (Qgis, f"LayerFilter.{name}"),
        (proxy_model, f"Filter.{name}"),
        (proxy_model, name),
    )


def file_widget_storage_mode(name: str):
    """Return a QgsFileWidget storage mode such as ``SaveFile``."""
    file_widget = _optional_import("qgis.gui", "QgsFileWidget")
    return resolve(
        f"the {name} file widget storage mode",
        (Qgis, f"FileWidgetStorageMode.{name}"),
        (file_widget, f"StorageMode.{name}"),
        (file_widget, name),
    )


# --- Field values -----------------------------------------------------------

_NULL = _optional_import("qgis.core", "NULL")


def is_null(value) -> bool:
    """Report whether a feature attribute is unset under Qt5 or Qt6 bindings.

    Qt5 returns a null ``QVariant`` for unset fields while Qt6 bindings return
    ``None``; both spellings have to be treated as missing.
    """
    if value is None:
        return True
    if _NULL is not _MISSING:
        try:
            if value == _NULL:
                return True
        except (TypeError, ValueError):
            pass
    is_null_method = getattr(value, "isNull", None)
    if callable(is_null_method):
        try:
            return bool(is_null_method())
        except TypeError:
            return False
    return False
