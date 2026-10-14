"""The dock panel.

Shaped by what a person said on first real use: show the project CRS, put times
on things, use tabs, and stop sounding like a verdict. "Assessment" read as more
authority than operation metadata can carry, so the columns now say what
happens — *To project CRS* — and when it was noticed.

**Two tabs, one source.** *Layers* is for looking; *Timeline* is the plain text
that Copy puts on the clipboard, shown verbatim, so what you paste into your own
LLM is exactly what you saw. Nothing is summarised in one and missing in the
other.

**Structure carries identity, not colour.** Layers under one heading share a
source CRS. No palette, no cap on how many CRSs, no legend, and nothing to break
under a dark theme. Severity is a themed QGIS icon.

**Grouping is navigation, not proof.** Layers sharing a source CRS usually share
the whole route to the project CRS, but not always — one probe can fail while
another succeeds. So every layer states its own line, and a group whose members
disagree says *mixed* rather than speaking for all of them.

**Visibility filters the view, never the record.** Every layer is always
checked, and the copied text always includes all of them. "Visible only" hides
unticked layers and tables with no geometry from the tree, and the status line
counts what it hid, so a hidden problem never reads as no problem.

Imports go through `qgis.PyQt` so one codebase loads on Qt5 and Qt6.
"""

from __future__ import annotations

from datetime import datetime

from qgis.core import QgsApplication
from qgis.PyQt.QtGui import QFontDatabase
from qgis.PyQt.QtWidgets import (
    QCheckBox,
    QDockWidget,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QTabWidget,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .qt_compat import (
    ELIDE_RIGHT, FIXED_FONT, NO_EDIT_TRIGGERS, SELECTABLE_TEXT,
    SELECT_ROWS, STRETCH, TO_CONTENTS, USER_ROLE, WIDGET_WIDTH,
)
from ..core.grading import crs_text, format_uncertainty, grade, indicator, plain_detail
from ..core.model import Shift
from ..core.observation import Observation, Observer, freshness_line
from ..core.probe import probe_project, visible_layer_ids
from ..core.serialize import full_record

#: Severity as a themed icon, never as hardcoded colour — these follow whatever
#: QGIS theme the user runs, including the dark ones.
_ICONS = {
    Shift.BALLPARK: "/mIconCritical.svg",
    Shift.NO_CRS: "/mIconCritical.svg",
    Shift.CROSS_DATUM_UNKNOWN: "/mIconWarning.svg",
    Shift.UNKNOWN: "/mIconWarning.svg",
    Shift.TEMPORAL_UNASSESSED: "/mIconWarning.svg",
    Shift.SHIFT: "/mIconProjectionEnabled.svg",
    Shift.NO_SHIFT: "/mIconSuccess.svg",
    Shift.NO_GEOMETRY: "/mIconTableLayer.svg",
}

#: The "To project CRS" column, short. The full sentence lives in the tooltip
#: and the copied text; a dock column has no room to argue a case.
_SHORT = {
    Shift.BALLPARK: "no datum shift available",
    Shift.CROSS_DATUM_UNKNOWN: "accuracy not published",
    Shift.NO_CRS: "no CRS set",
    Shift.UNKNOWN: "could not check",
    Shift.TEMPORAL_UNASSESSED: "epoch not checked",
    Shift.NO_GEOMETRY: "no geometry",
}

COLUMNS = ["Layer / CRS", "To project CRS", "Noticed"]


def _short(layer_state, verdict) -> str:
    """One lowercase phrase per row, so the column reads as a set."""
    if verdict.shift is Shift.SHIFT:
        # ± marks the figure as published accuracy, not distance moved.
        return "datum shift, ±{}".format(format_uncertainty(verdict.uncertainty_m))
    if verdict.shift is Shift.NO_SHIFT:
        return "same CRS" if layer_state.same_crs() else "same datum, reprojected"
    return _SHORT.get(verdict.shift, verdict.shift.value)


def _clock(at, now=None) -> str:
    """HH:MM today; with the date otherwise. Seconds live in the tooltip."""
    if at is None:
        return ""
    now = now or datetime.now()
    if at.date() == now.date():
        return at.strftime("%H:%M")
    return at.strftime("%d %b %H:%M")


def _icon(name):
    try:
        return QgsApplication.getThemeIcon(name)
    except Exception:
        return None


def _set_icon(item, name):
    icon = _icon(name)
    if icon is not None and not icon.isNull():
        item.setIcon(0, icon)


class CrsInspectorPanel(QDockWidget):
    """The project CRS, every layer's route to it, and when each was noticed."""

    def __init__(self, iface, observer=None, history=None, trace=None,
                 parent=None):
        """Collaborators are supplied, never manufactured.

        The panel used to build its own Observer and assess in its constructor,
        before the plugin handed it the authoritative one. The first visible
        evidence and the first history baseline then came from a different
        observer than every later assessment. Ownership is established before
        the first probe now, and the owner drives that probe.
        """
        super().__init__("CRS Inspector", parent)
        self.iface = iface
        self.setObjectName("CrsInspectorPanel")
        self._state = None
        self._shown = None
        self._observer = observer if observer is not None else Observer()
        self._history = history
        self._trace = trace

        body = QWidget()
        outer = QVBoxLayout(body)
        outer.setContentsMargins(4, 4, 4, 4)
        outer.setSpacing(4)

        # -- the project CRS: the thing every row is measured against ------
        self.project_crs = QLabel("Project CRS: not checked yet")
        self.project_crs.setTextInteractionFlags(SELECTABLE_TEXT)
        self.project_crs.setWordWrap(True)
        font = self.project_crs.font()
        font.setBold(True)
        self.project_crs.setFont(font)
        outer.addWidget(self.project_crs)

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
            "Show only layers ticked in the Layers panel; tables with no geometry "
            "are hidden too. The copied text still includes every layer, and the "
            "status line counts what is hidden here.")
        self.visible_only.stateChanged.connect(lambda _: self.render_current())
        controls.addWidget(self.visible_only)

        self.refresh_button = self._tool_button(
            "/mActionRefresh.svg", "Re-check", self._on_refresh_clicked)
        controls.addWidget(self.refresh_button)
        self.copy_button = self._tool_button(
            "/mActionEditCopy.svg",
            "Copy the timeline and every layer's details as plain text — "
            "ready to paste into your own LLM, a job file or an email",
            self.copy_record)
        controls.addWidget(self.copy_button)
        outer.addLayout(controls)

        # -- tabs ----------------------------------------------------------
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(len(COLUMNS))
        self.tree.setHeaderLabels(COLUMNS)
        self.tree.setSelectionBehavior(SELECT_ROWS)
        self.tree.setEditTriggers(NO_EDIT_TRIGGERS)
        self.tree.setRootIsDecorated(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setTextElideMode(ELIDE_RIGHT)
        header = self.tree.header()
        header.setSectionResizeMode(0, STRETCH)
        header.setSectionResizeMode(1, TO_CONTENTS)
        header.setSectionResizeMode(2, TO_CONTENTS)
        header.setStretchLastSection(False)
        self.tabs.addTab(self.tree, "Layers")

        # Exactly the copied text. Monospaced so the record's columns line up
        # as they will wherever it is pasted; wrapped on screen only, because
        # unwrapped it hid half of every line in a normal-width dock.
        self.timeline = QPlainTextEdit()
        self.timeline.setReadOnly(True)
        self.timeline.setLineWrapMode(WIDGET_WIDTH)
        try:
            self.timeline.setFont(QFontDatabase.systemFont(FIXED_FONT))
        except Exception:
            pass
        self.tabs.addTab(self.timeline, "Timeline")
        outer.addWidget(self.tabs, 1)

        self.setWidget(body)
        self._watch_layer_tree()
        # Deliberately no assessment here: the owner connects its notifications
        # first, then drives the first probe, so nothing is observed before the
        # wiring that would notice a change to it.
        self._render(self._observer.presentation)

    def _tool_button(self, icon_name, tip, slot):
        button = QToolButton()
        button.setAutoRaise(True)
        button.setToolTip(tip)
        icon = _icon(icon_name)
        if icon is not None and not icon.isNull():
            button.setIcon(icon)
        else:
            button.setText(tip.split()[0])
        # Qt's clicked signal carries `checked: bool`. Connected straight to
        # refresh(), that bool arrived as `observer`, replaced the real one with
        # False, and then failed at begin() outside the exception handler. UI
        # slots take no arguments; dependency injection never travels a signal.
        button.clicked.connect(lambda _checked=False: slot())
        return button

    def _watch_layer_tree(self):
        """Redraw when a layer is ticked or unticked, if the view depends on it.

        Visibility is not an input to any conversion, so it is not the plugin's
        business to invalidate or re-check anything here — only to redraw. The
        slot is a bound method of this widget so Qt drops the connection when
        the panel is destroyed.
        """
        try:
            from qgis.core import QgsProject
            QgsProject.instance().layerTreeRoot().visibilityChanged.connect(
                self._on_tree_visibility)
        except Exception:
            pass

    def _on_tree_visibility(self, *_):
        if self.visible_only.isChecked():
            self.render_current()

    def _on_refresh_clicked(self):
        """A UI slot: no arguments, so no signal payload can reach refresh()."""
        self.refresh()

    def render_current(self):
        """Redraw from what the observer already knows. No probing.

        Invalidation must reach the screen immediately: debounce the expensive
        reassessment, never the loss of currency. Otherwise the user keeps
        looking at a result labelled current while its inputs have moved, for as
        long as the timer takes — or forever, if the probe later fails.
        """
        self._render(self._observer.presentation)

    def needs_assessment(self) -> bool:
        """True when what is displayed is not a current successful observation."""
        return not self._observer.presentation.is_current

    # ------------------------------------------------------------------
    def bind_session(self, observer, history=None, trace=None):
        """Adopt the owner's collaborators, synchronously.

        Rebinding used to happen as a side effect of refresh(observer, history,
        trace), which the owner only called from the debounced recompute. Between
        a session starting and that timer firing, the plugin held the new history
        and the panel still held the old one — and the Re-check button calls
        refresh() with no arguments, so a manual assessment in that window was
        recorded into the abandoned project's history while the observer belonged
        to the new one. Binding is now explicit and immediate, and refresh() no
        longer replaces dependencies.
        """
        self._observer = observer
        self._history = history
        self._trace = trace

    def refresh(self):
        """Check, publish, and render only what the evidence currently supports.

        A failed probe does not leave the previous result standing as current —
        it stays visible, labelled as the last successful check.
        """
        import time

        observer = self._observer

        # Tokens are taken BEFORE the work, so a result computed against inputs
        # that have since moved is rejected rather than published.
        capture = observer.begin()
        started = time.monotonic()
        assessed, changed, stage = 0, 0, ""
        try:
            # ALWAYS assess complete membership. Passing the visibility filter
            # into the probe meant hidden layers vanished from the snapshot, and
            # diff() reads an absent subject as removed — so toggling a checkbox
            # wrote removal and re-addition records. Filtering is presentation.
            probed = probe_project()
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
    def _noticed(self, layer_id):
        """When the layer's current state was noticed, and in what way."""
        if self._history is None:
            return None, ""
        try:
            found = self._history.for_object(layer_id)
        except Exception:
            return None, ""
        if not found:
            return None, ""
        change_set, change = found[0]
        stamp = change_set.at.strftime("%Y-%m-%d %H:%M:%S")
        if change.before is None and change.first_observation:
            how = "Already in the project when CRS Inspector started watching ({})."
        elif change.before is None:
            how = "Added — noticed {}."
        else:
            how = "Last changed — noticed {}. Was: " + (change.before_detail
                                                       or change.before or "?")
        return change_set.at, how.format(stamp)

    def _render(self, shown):
        self._shown = shown
        self.tree.clear()
        self._render_timeline()
        if not shown.has_evidence:
            self.project_crs.setText("Project CRS: not checked yet")
            self.project_crs.setToolTip("")
            self._set_status("/mIconInfo.svg", shown.note or "Not checked.")
            self._state = None
            return

        self._state = state = shown.state
        self.project_crs.setText("Project CRS: {}".format(crs_text(state.target)))
        self.project_crs.setToolTip(
            "Datum: {}\nEvery layer below is converted to this CRS for display.".format(
                state.target.datum_key or "not reported"))
        verdicts = [grade(s) for s in state.layers]

        # Everything is checked; only the view is filtered. What is hidden is
        # counted and reported, because a hidden problem must never read as no
        # problem. Tables with no geometry have no checkbox in the layer tree,
        # so they always read as "visible" — they are filtered by kind instead.
        visible = visible_layer_ids() if self.visible_only.isChecked() else None

        def shown_here(layer_state):
            if visible is None:
                return True
            return layer_state.is_spatial and layer_state.layer_id in visible

        pairs = [(s, v) for s, v in zip(state.layers, verdicts) if shown_here(s)]
        hidden = len(state.layers) - len(pairs)

        # Shared CRS identity does not prove shared evidence: one layer's probe
        # can fail while another with the same CRS succeeds, and PROJ can select
        # different operations for different coordinates. Grouping is navigation,
        # not a claim — so members keep their own lines and the group reports
        # agreement or says it is mixed.
        groups = {}
        for layer_state, verdict in pairs:
            key = (layer_state.is_spatial, layer_state.source.authid,
                   layer_state.source.description, layer_state.source.datum_key)
            groups.setdefault(key, []).append((layer_state, verdict))

        def worst(members):
            return max((v for _, v in members),
                       key=lambda v: (v.is_alarming, v.shift is Shift.UNKNOWN))

        # Flagged first, tables last, then by datum so same-frame groups sit
        # together.
        ordered = sorted(
            groups.items(),
            key=lambda kv: (not worst(kv[1]).is_alarming, not kv[0][0],
                            kv[0][3], kv[0][2]))

        for (spatial, authid, description, datum), members in ordered:
            phrases = {_short(s, v) for s, v in members}
            operations = {(s.operation.name if s.operation else None)
                          for s, _ in members}
            mixed = len(phrases) > 1 or len(operations) > 1
            verdict = worst(members)
            lead = next(s for s, v in members if v is verdict)

            node = QTreeWidgetItem(self.tree)
            if not spatial:
                label = "Tables with no geometry"
            else:
                label = crs_text(lead.source)
            node.setText(0, label)
            node.setText(1, "mixed — see layers" if mixed else _short(lead, verdict))
            _set_icon(node, _ICONS.get(verdict.shift, "/mIconInfo.svg"))
            tip = "\n".join(filter(None, [
                "Datum: {}".format(datum or "not reported") if spatial else "",
                plain_detail(lead, verdict) if not mixed
                else "Layers in this group are converted differently; see each one.",
                ("Suggested: " + verdict.action) if verdict.action and not mixed else "",
            ]))
            for column in range(len(COLUMNS)):
                node.setToolTip(column, tip)

            for layer_state, layer_verdict in sorted(
                    members, key=lambda m: m[0].layer_name.lower()):
                child = QTreeWidgetItem(node)
                child.setText(0, layer_state.layer_name)
                # Each layer states its own line; the group never speaks for a
                # member whose evidence differs.
                child.setText(1, _short(layer_state, layer_verdict))
                at, how = self._noticed(layer_state.layer_id)
                child.setText(2, _clock(at))
                detail = plain_detail(layer_state, layer_verdict)
                child.setToolTip(0, "\n".join(filter(None, [
                    detail, "tracked as {}".format(layer_state.layer_id)])))
                child.setToolTip(1, "\n".join(filter(None, [
                    detail,
                    ("Suggested: " + layer_verdict.action)
                    if layer_verdict.action else ""])))
                child.setToolTip(2, how or "Not recorded in this session.")
                if layer_verdict.is_alarming or layer_verdict.shift is Shift.UNKNOWN:
                    _set_icon(child, _ICONS.get(layer_verdict.shift, "/mIconInfo.svg"))
                child.setData(0, USER_ROLE, layer_state.layer_name.lower())
            node.setExpanded(True)

        for column in (1, 2):
            self.tree.resizeColumnToContents(column)
        flagged = _ICONS.get(
            next((v.shift for v in verdicts if v.is_alarming), Shift.NO_SHIFT))
        line = "{} · {}".format(indicator(verdicts), freshness_line(shown))
        if hidden:
            line += " · {} hidden here".format(hidden)
        self._set_status(flagged, line)
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
    def _project_name(self) -> str:
        try:
            from qgis.core import QgsProject
            return QgsProject.instance().fileName()
        except Exception:
            return ""

    def record_text(self) -> str:
        """The full plain-text record, or "" when nothing has been checked."""
        shown = self._shown
        if shown is None or not shown.has_evidence:
            return ""
        return full_record(shown.state, self._history, self._project_name(),
                           observed_at=shown.observed_at,
                           is_current=shown.is_current,
                           freshness_note=shown.note)

    def _render_timeline(self, text=None):
        text = text or self.record_text() or (
            "Nothing checked yet.\n\nPress Re-check, or edit the project, and "
            "the timeline will start here.")
        bar = self.timeline.verticalScrollBar()
        position = bar.value()
        self.timeline.setPlainText(text)
        bar.setValue(min(position, bar.maximum()))

    def copy_record(self):
        """Copy the record made at this moment, and show that same text."""
        text = self.record_text()
        if not text:
            return
        self._render_timeline(text)
        try:
            from qgis.PyQt.QtGui import QGuiApplication
            QGuiApplication.clipboard().setText(text)
        except Exception:
            return
        self.iface.messageBar().pushInfo(
            "CRS Inspector",
            "Copied {} lines — the timeline and every layer's details.".format(
                len(text.splitlines())))
