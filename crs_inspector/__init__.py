"""CRS Inspector — a QGIS plugin for datum-transformation provenance.

Copyright (C) 2026 Carter Gant

This program is free software; you can redistribute it and/or modify it
under the terms of the GNU General Public License as published by the Free
Software Foundation; either version 2 of the Licence, or (at your option)
any later version. See the LICENSE file distributed alongside this
plugin.
"""


def classFactory(iface):  # noqa: N802  (QGIS requires this exact name)
    from .plugin import CrsInspectorPlugin
    return CrsInspectorPlugin(iface)
