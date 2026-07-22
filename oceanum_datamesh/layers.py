# Copyright 2026 Oceanum / Dave Johnson
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Turn a Datamesh query result into QGIS map layers.

Shared by the Browser (loading a saved connection) so the query -> file ->
QgsMapLayer pipeline lives in one place. The heavy ``engine.query`` /
``converters`` work runs on a background thread; ``add_layer_specs`` must run on
the main thread as it touches the project.
"""

from __future__ import annotations

from typing import Any

from qgis.core import (
    Qgis,
    QgsColorRampShader,
    QgsDateTimeRange,
    QgsProject,
    QgsRasterLayer,
    QgsRasterShader,
    QgsSingleBandPseudoColorRenderer,
    QgsStyle,
    QgsVectorLayer,
)

from . import converters
from .utils import session_dir, to_utc_qdatetime


def query_to_layer_specs(
    engine: Any,
    query: Any,
    name: str,
    coordinates: dict | None = None,
    variables: list | None = None,
) -> list:
    """Run *query* and convert the result to converter ``LayerSpec`` objects.

    Runs on a background thread. Raises ``RuntimeError`` if the query is empty.
    """
    result = engine.query(query)
    if result is None:
        raise RuntimeError("No data found for this query. Try a different time or area.")
    return converters.result_to_layers(
        result,
        session_dir(),
        name,
        coordinates=coordinates or {},
        variables=variables,
    )


def layer_from_spec(spec: Any) -> Any:
    """Build a QgsMapLayer from a converter ``LayerSpec`` (no project touch)."""
    if spec.kind == "raster":
        return QgsRasterLayer(spec.path, spec.name)
    uri = spec.path
    if spec.sublayer:
        uri = f"{spec.path}|layername={spec.sublayer}"
    return QgsVectorLayer(uri, spec.name, "ogr")


def add_layer_specs(specs: list) -> tuple[int, list[str]]:
    """Add layers to the current project; return (added_count, failed_names).

    Specs with a ``group`` are placed in a layer-tree group of that name (e.g.
    a variable's series of time-step rasters). Must run on the main thread.
    """
    project = QgsProject.instance()
    root = project.layerTreeRoot()
    groups: dict = {}  # resolve each group once, not per layer
    added = 0
    failed = []
    for spec in specs or []:
        layer = layer_from_spec(spec)
        if layer is None or not layer.isValid():
            failed.append(spec.name)
            continue
        apply_temporal(layer, spec)
        apply_shared_style(layer, spec)
        if spec.group:
            group = groups.get(spec.group)
            if group is None:
                group = root.findGroup(spec.group) or root.addGroup(spec.group)
                groups[spec.group] = group
            project.addMapLayer(layer, False)
            group.addLayer(layer)
        else:
            project.addMapLayer(layer)
        added += 1
    return added, failed


# --------------------------------------------------------------------------- #
# Temporal registration
# --------------------------------------------------------------------------- #
def apply_temporal(layer: Any, spec: Any) -> None:
    """Register a layer with the Temporal Controller from its spec metadata.

    A raster time step gets its fixed (begin, end) temporal range, so the
    series of single-band rasters plays back in sequence; vectors with a
    datetime field get instant-from-field mode. No-op when the spec carries no
    temporal metadata.
    """
    if spec.kind == "raster" and spec.time_range:
        _apply_raster_temporal(layer, spec.time_range)
    elif spec.kind == "vector" and spec.time_field:
        _apply_vector_temporal(layer, spec.time_field)


def _apply_raster_temporal(layer: Any, time_range: tuple) -> None:
    begin, end = (to_utc_qdatetime(t) for t in time_range)
    if not (begin.isValid() and end.isValid()):
        return
    # A single-step series has begin == end; a half-open range would then be
    # empty and the layer would never render under the Temporal Controller, so
    # include the end for a zero-length (instant) range.
    include_end = begin >= end
    props = layer.temporalProperties()
    props.setMode(Qgis.RasterTemporalMode.FixedTemporalRange)
    props.setFixedTemporalRange(QgsDateTimeRange(begin, end, True, include_end))
    props.setIsActive(True)


def _apply_vector_temporal(layer: Any, time_field: str) -> None:
    if time_field not in {f.name() for f in layer.fields()}:
        return
    props = layer.temporalProperties()
    props.setMode(Qgis.VectorTemporalMode.FeatureDateTimeInstantFromField)
    props.setStartField(time_field)
    props.setIsActive(True)


# --------------------------------------------------------------------------- #
# Shared symbology
# --------------------------------------------------------------------------- #
DEFAULT_RAMP = "Viridis"
_RAMP_PROTOTYPE: list = []  # lazy one-element cache of the ramp lookup


def _shader_enums():
    """(interpolation, classification) enums, tolerant of QGIS < 3.38.

    The ``Qgis.Shader*`` scoped enums exist from 3.38; older releases use the
    ``QgsColorRampShader`` member aliases.
    """
    try:
        return (
            Qgis.ShaderInterpolationMethod.Linear,
            Qgis.ShaderClassificationMethod.Continuous,
        )
    except AttributeError:  # pragma: no cover - only on QGIS < 3.38
        return (
            QgsColorRampShader.Type.Interpolated,
            QgsColorRampShader.ClassificationMode.Continuous,
        )


def _default_ramp():
    """A fresh clone of the default ramp, caching the (costly) style lookup."""
    if not _RAMP_PROTOTYPE:
        _RAMP_PROTOTYPE.append(QgsStyle.defaultStyle().colorRamp(DEFAULT_RAMP))
    ramp = _RAMP_PROTOTYPE[0]
    return ramp.clone() if ramp is not None else None


def apply_shared_style(layer: Any, spec: Any) -> None:
    """Style a raster time step on its variable's global colour scale.

    Every layer in a temporal series carries the same (min, max) so one colour
    means one value in every frame; without this each layer would stretch to
    its own extrema and the animation would 'pulse'. No-op for other specs.
    """
    if spec.kind != "raster" or not spec.value_range:
        return
    vmin, vmax = (float(v) for v in spec.value_range)
    ramp = _default_ramp()
    if ramp is None:  # pragma: no cover - Viridis ships with QGIS
        return
    interpolation, classification = _shader_enums()
    shader_fn = QgsColorRampShader(vmin, vmax, ramp, interpolation, classification)
    shader_fn.classifyColorRamp()
    shader = QgsRasterShader()
    shader.setRasterShaderFunction(shader_fn)
    renderer = QgsSingleBandPseudoColorRenderer(layer.dataProvider(), 1, shader)
    renderer.setClassificationMin(vmin)
    renderer.setClassificationMax(vmax)
    layer.setRenderer(renderer)
