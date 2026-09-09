"""The dock panel.

Two things shape this and both were learned the hard way.

**Structure carries identity, not colour.** An earlier version tinted each
layer's reference frame with a categorical hue. That capped the design at three
datums (the comparison task is all-pairs, so a fourth hue would not validate),
left a fourth-and-beyond falling back to grey, needed a legend to mean anything,
and hardcoded hex that breaks under QGIS's dark themes. Grouping says the same
thing better: two layers under one heading are in the same reference frame. No
palette, no cap, no legend.

**The assessment belongs to the group, not the row.** Layers sharing a source
CRS share the whole route to the project CRS, so they share one assessment.
That is the "quality is a property of a use, not of a layer" reframe made
structural — the group *is* the use, and the verdict sits on it once instead of
being repeated down every row.

Everything else is subtraction. A dock is narrow: five columns produced a
horizontal scrollbar and pushed two of them off-screen entirely. Two columns
fit. Severity is a themed QGIS icon rather than a colour, so it survives Night
Mapping. Imports go through `qgis.PyQt` so one codebase loads on Qt5 and Qt6.
"""

from __future__ import annotations

from qgis.core import QgsApplication
from qgis.PyQt.QtWidgets import (
    QCheckBox,
    QDockWidget,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .qt_compat import (
    ELIDE_RIGHT, NO_EDIT_TRIGGERS, SELECTABLE_TEXT, SELECT_ROWS, STRETCH,
    TO_CONTENTS, USER_ROLE,
)
from ..core.grading import grade, indicator
from ..core.model import Shift
from ..core.observation import Observation, Observer, freshness_line
from ..core.probe import probe_project
from ..core.serialize import to_text

#: Severity as a themed icon, never as hardcoded colour — these follow whatever
#: QGIS theme the user runs, including the dark ones.
_ICONS = {
    Shift.BALLPARK: "/mIconCritical.svg",
    Shift.NO_CRS: "/mIconCritical.svg",
    Shift.CROSS_DATUM_UNKNOWN: "/mIconWarning.svg",
    Shift.UNKNOWN: "/mIconWarning.svg",
    Shift.SHIFT: "/mIconProjectionEnabled.svg",
    Shift.NO_SHIFT: "/mIconSuccess.svg",
}

#: Short forms for the group's second column. The long wording lives in the
#: tooltip and the copied record; a dock column has no room to argue a case.
_SHORT = {
    Shift.BALLPARK: "no datum shift available",
    Shift.CROSS_DATUM_UNKNOWN: "accuracy not published",
    Shift.NO_CRS: "no CRS set",
    Shift.UNKNOWN: "not checked",
    Shift.NO_SHIFT: "no shift needed",
}


def _assessment(verdict) -> str:
    """One lowercase phrase per group, so the column reads as a set."""
    if verdict.shift is Shift.SHIFT:
        from ..core.grading import format_uncertainty
        return "shifted, {}".format(format_uncertainty(verdict.uncertainty_m))
    return _SHORT.get(verdict.shift, "assessed")


def _icon(name):
    try:
        return QgsApplication.getThemeIcon(name)
    except Exception:
        return None


class CrsInspectorPanel(QDockWidget):
    """Layers grouped by the reference frame they are read from."""

    def __init__(self, iface, parent=None):
        super().__init__("CRS Inspector", parent)
        self.iface = iface
        self.setObjectName("CrsInspectorPanel")
        self._state = None
        self._observer = Observer()
        self._history = None
        self._trace = None

        body = QWidget()
        outer = QVBoxLayout(body)
        outer.setContentsMargins(4, 4, 4, 4)
        outer.setSpacing(4)

        # -- status: one line, never a paragraph --------------------------
        status = QHBoxLayout()
        status.setSpacing(5)
        self.status_icon = QLabel()
        self.status_icon.setFixedWidth(18)
        self.status = QLabel("Not checked.")
        self.status.setTextInteractionFlags(SELECTABLE_TEXT)
        status.addWidget(self.status_icon)
        status.addWidget(self.status, 1)
        outer.addLayout(status)

        # -- controls ------------------------------------------------------
        controls = QHBoxLayout()
        controls.setSpacing(4)
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Filter layers…")
        self.filter.setClearButtonEnabled(True)
        self.filter.textChanged.connect(self._apply_filter)
        controls.addWidget(self.filter, 1)

        self.visible_only = QCheckBox("Visible only")
        self.visible_only.setToolTip(
            "Hidden layers still ship in your export. What is excluded stays "
            "counted in the status line.")
        self.visible_only.stateChanged.connect(lambda _: self.refresh())
        controls.addWidget(self.visible_only)

        self.refresh_button = self._tool_button(
            "/mActionRefresh.svg", "Re-check", self.refresh)
        controls.addWidget(self.refresh_button)
        self.copy_button = self._tool_button(
            "/mActionEditCopy.svg", "Copy the full text record", self.copy_record)
        controls.addWidget(self.copy_button)
        outer.addLayout(controls)

        # -- the tree ------------------------------------------------------
        self.tree = QTreeWidget()
        self.tree.setColumnCount(2)
        self.tree.setHeaderLabels(["Reference frame", "Assessment"])
        self.tree.setSelectionBehavior(SELECT_ROWS)
        self.tree.setEditTriggers(NO_EDIT_TRIGGERS)
        self.tree.setRootIsDecorated(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setTextElideMode(ELIDE_RIGHT)
        header = self.tree.header()
        header.setSectionResizeMode(0, STRETCH)
        header.setSectionResizeMode(1, TO_CONTENTS)
        header.setStretchLastSection(False)
        outer.addWidget(self.tree, 1)

        self.setWidget(body)
        self.refresh()

    def _tool_button(self, icon_name, tip, slot):
        button = QToolButton()
        button.setAutoRaise(True)
        button.setToolTip(tip)
        icon = _icon(icon_name)
        if icon is not None and not icon.isNull():
            button.setIcon(icon)
        else:
            button.setText(tip.split()[0])
        button.clicked.connect(slot)
        return button

    # ------------------------------------------------------------------
    def refresh(self, observer=None, history=None, trace=None):
        """Assess, publish, and render only what the evidence currently supports.

        A failed probe does not leave the previous result standing as current —
        it stays visible, labelled as the last successful check.
        """
        from datetime import datetime
        import time

        if observer is not None:
            self._observer = observer
        observer = self._observer
        if history is not None:
            self._history = history
        if trace is not None:
            self._trace = trace

        # Tokens are taken BEFORE the work, so a result computed against inputs
        # that have since moved is rejected rather than published.
        capture = observer.begin()
        started = time.monotonic()
        assessed, changed, stage = 0, 0, ""
        try:
            probed = probe_project(visible_only=self.visible_only.isChecked())
            assessed = len(probed.layers)
            accepted = observer.publish(
                Observation(capture, datetime.now(), True, state=probed))
            if accepted and self._history is not None:
                recorded = self._history.record(probed)
                changed = len(recorded.changes) if recorded else 0
            publication = "accepted" if accepted else "discarded"
        except Exception as exc:
            stage = type(exc).__name__          # the stage, never the payload
            accepted = observer.publish(
                Observation(capture, datetime.now(), False, error=str(exc)))
            publication = "failed" if accepted else "discarded"

        if self._trace is not None:
            self._trace.record(
                session=capture.session, generation=capture.generation,
                assessed=assessed, changed=changed, publication=publication,
                elapsed_ms=(time.monotonic() - started) * 1000, stage=stage)

        self._render(observer.presentation)

    # ------------------------------------------------------------------
    def _render(self, shown):
        self.tree.clear()
        if not shown.has_evidence:
            self._set_status("/mIconInfo.svg", shown.note or "Not checked.")
            self._state = None
            return

        self._state = state = shown.state
        verdicts = [grade(s) for s in state.layers]

        # One assessment per source CRS: layers sharing a source CRS share the
        # whole route, so they share the verdict. The group is the unit.
        groups = {}
        for layer_state, verdict in zip(state.layers, verdicts):
            key = (layer_state.source.authid, layer_state.source.description,
                   layer_state.source.datum_key)
            groups.setdefault(key, (verdict, []))[1].append(layer_state)

        # Flagged first, then by datum so same-frame groups sit together.
        ordered = sorted(
            groups.items(),
            key=lambda kv: (not kv[1][0].is_alarming, kv[0][2], kv[0][1]))

        for (authid, description, datum), (verdict, layers) in ordered:
            node = QTreeWidgetItem(self.tree)
            label = description or "no CRS set"
            if authid:
                label = "{}  ({})".format(label, authid)
            node.setText(0, label)
            node.setText(1, _assessment(verdict))
            icon = _icon(_ICONS.get(verdict.shift, "/mIconInfo.svg"))
            if icon is not None and not icon.isNull():
                node.setIcon(0, icon)
            tip = "\n".join(filter(None, [
                "Datum: {}".format(datum or "not reported"),
                verdict.detail,
                ("Suggested: " + verdict.action) if verdict.action else "",
                "Assessed for display in the project CRS.",
            ]))
            node.setToolTip(0, tip)
            node.setToolTip(1, tip)

            for layer_state in sorted(layers, key=lambda s: s.layer_name.lower()):
                child = QTreeWidgetItem(node)
                child.setText(0, layer_state.layer_name)
                child.setToolTip(0, "tracked as {}".format(layer_state.layer_id))
                child.setData(0, USER_ROLE, layer_state.layer_name.lower())
            node.setExpanded(True)

        self.tree.resizeColumnToContents(1)
        flagged = _ICONS.get(
            next((v.shift for v in verdicts if v.is_alarming), Shift.NO_SHIFT))
        self._set_status(flagged, "{} · {}".format(
            indicator(verdicts), freshness_line(shown)))
        if not shown.is_current:
            self.status.setToolTip(shown.note)
        self._apply_filter(self.filter.text())

    def _set_status(self, icon_name, text):
        icon = _icon(icon_name)
        if icon is not None and not icon.isNull():
            self.status_icon.setPixmap(icon.pixmap(16, 16))
        else:
            self.status_icon.clear()
        self.status.setText(text)
        self.status.setToolTip(text)

    def _apply_filter(self, needle):
        needle = (needle or "").strip().lower()
        for i in range(self.tree.topLevelItemCount()):
            group = self.tree.topLevelItem(i)
            shown = 0
            for j in range(group.childCount()):
                child = group.child(j)
                match = not needle or needle in (child.text(0) or "").lower()
                child.setHidden(not match)
                shown += int(match)
            group.setHidden(shown == 0 and bool(needle))

    # ------------------------------------------------------------------
    def copy_record(self):
        if self._state is None:
            return
        try:
            from qgis.core import QgsProject
            name = QgsProject.instance().fileName()
        except Exception:
            name = ""
        text = to_text(self._state, name)
        try:
            from qgis.PyQt.QtGui import QGuiApplication
            QGuiApplication.clipboard().setText(text)
        except Exception:
            return
        self.iface.messageBar().pushInfo(
            "CRS Inspector",
            "Coordinate provenance record copied — {} lines.".format(
                len(text.splitlines())))
