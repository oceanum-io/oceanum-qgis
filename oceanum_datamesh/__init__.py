# Copyright 2026 Oceanum / Dave Johnson
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
"""Oceanum Datamesh QGIS plugin.

Entry point required by QGIS. QGIS calls :func:`classFactory` with the
plugin interface (``iface``) to instantiate the plugin.
"""


def classFactory(iface):  # noqa: N802 (name mandated by QGIS)
    """Load the OceanumDatameshPlugin class.

    :param iface: A QGIS interface instance.
    :type iface: qgis.gui.QgisInterface
    """
    from .plugin import OceanumDatameshPlugin

    return OceanumDatameshPlugin(iface)
