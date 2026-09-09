"""Qt5 / Qt6 enum compatibility.

`qgis.PyQt` papers over *which* Qt is present, so imports work unchanged on the
3.44 LTR (Qt5) and on 4.x (Qt6). It does not paper over **enum scoping**, and
that difference stays invisible until a widget is actually constructed:

    Qt5   QAbstractItemView.SelectRows
    Qt6   QAbstractItemView.SelectionBehavior.SelectRows

Qt6 removed the flat spelling. The scope names are also not guessable — the
setter is ``setEditTriggers`` but the enum type is ``EditTrigger``, singular —
so `resolve` searches the owner's nested enum types rather than trusting a name
supplied by hand. A `hint` is accepted for readability and speed, but being
wrong about it costs nothing.

Both facts were found by constructing the panel offscreen. An import-only check
passed happily right up until a widget existed.
"""

from __future__ import annotations

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import QAbstractItemView, QHeaderView


def resolve(owner, name: str, hint: str = ""):
    """Return an enum member by name, whatever Qt calls its scope.

    Order: flat (Qt5), the hinted scope, then any nested scope that has it.
    """
    flat = getattr(owner, name, None)
    if flat is not None and not isinstance(flat, type):
        return flat

    if hint:
        scope = getattr(owner, hint, None)
        member = getattr(scope, name, None) if scope is not None else None
        if member is not None:
            return member

    for attr in dir(owner):
        if attr.startswith("__"):
            continue
        scope = getattr(owner, attr, None)
        if isinstance(scope, type):
            member = getattr(scope, name, None)
            if member is not None and not isinstance(member, type):
                return member

    raise AttributeError(
        "{} exposes no enum member named {!r} (flat or scoped)".format(
            getattr(owner, "__name__", owner), name))


SELECT_ROWS = resolve(QAbstractItemView, "SelectRows", "SelectionBehavior")
NO_EDIT_TRIGGERS = resolve(QAbstractItemView, "NoEditTriggers", "EditTrigger")
STRETCH = resolve(QHeaderView, "Stretch", "ResizeMode")
RIGHT_DOCK_AREA = resolve(Qt, "RightDockWidgetArea", "DockWidgetArea")
ELIDE_RIGHT = resolve(Qt, "ElideRight", "TextElideMode")
TO_CONTENTS = resolve(QHeaderView, "ResizeToContents", "ResizeMode")
SELECTABLE_TEXT = resolve(Qt, "TextSelectableByMouse", "TextInteractionFlag")
USER_ROLE = resolve(Qt, "UserRole", "ItemDataRole")
