"""The text record — the plugin's primary output.

People paste this into a language model, a job file, or an email, and that is the
right place for analysis to happen: no API keys, no running cost, no data leaving
without a deliberate press of a button, nothing to rot as models change.

It leads with a TIMELINE, because that is what a person asked for on first real
use: "Bing Satellite, noticed at this time, CRS such-and-such" — every layer and
every change, however small, in the order it was seen. Then the current state of
each layer in detail.

Obligations a screen does not have:

  * self-describing — CRS names spelled out, not just codes
  * explicit unknowns — "not published", "could not be checked" as written words,
    never blanks, because a blank invites a guess
  * honest about time — these are times the plugin NOTICED things, not times they
    happened; it can only notice what happens while it is running
  * states its own blind spot, so the reader does not overclaim

Pure functions over dataclasses. No Qt, no PyQGIS.
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from .grading import crs_text, format_uncertainty, grade, summarise
from .model import ProjectState, Shift

#: Prose is never pre-wrapped. Wherever the text lands — an LLM, an email, the
#: Timeline tab — the reader wraps it to its own width; text wrapped at 72 was
#: wrapped a second time by a narrower dock and broke mid-sentence. Rules are
#: kept short for the same reason.
RULE = 44
TIME_COLUMN = 10            # "HH:MM:SS" plus two spaces

BLIND_SPOT = (
    "LIMITATION: this record describes how each layer is converted to the "
    "project CRS. It cannot detect a layer whose assigned CRS is simply wrong — "
    "in that case the conversion is valid and the premise is not, and everything "
    "below will look healthy."
)

UNCERTAINTY_NOTE = (
    "Uncertainty figures are the PUBLISHED accuracy of each operation as a "
    "method — not measured error at any given point."
)

TIME_NOTE = (
    "Times are when CRS Inspector noticed each thing, not necessarily when it "
    "happened. It only notices what happens while it is running."
)

_STATE_WORDS = {
    Shift.NO_CRS: "NO CRS SET",
    Shift.NO_SHIFT: "no datum shift needed",
    Shift.SHIFT: "datum shift applied",
    Shift.BALLPARK: "BALLPARK OPERATION OBSERVED (no datum shift applied)",
    Shift.CROSS_DATUM_UNKNOWN: "NO PUBLISHED ACCURACY ACROSS A DATUM BOUNDARY",
    Shift.TEMPORAL_UNASSESSED: "SAME DATUM; EPOCHS DIFFER, NOT CHECKED",
    Shift.UNKNOWN: "STATE UNKNOWN",
    Shift.NO_GEOMETRY: "table with no geometry (nothing to convert)",
}


def _state_words(shift) -> str:
    """Total by construction. A missing entry used to raise KeyError mid-export,
    losing the whole record over one unmapped state."""
    return _STATE_WORDS.get(shift, "UNMAPPED STATE ({})".format(getattr(shift, "value", shift)))


def _field(label: str, text: str) -> str:
    """A labelled field on one line, however long."""
    return "  {:<14} {}".format(label + ":", text)


def _crs_block(crs, role: str) -> List[str]:
    if not crs.is_valid:
        return ["  {:<14} none assigned".format(role + ":")]
    lines = [
        "  {:<14} {} — {}".format(role + ":", crs.authid or "custom", crs.description),
        "  {:<14} {}".format("datum:", crs.datum_key or "not reported"),
    ]
    flags = []
    if crs.is_geographic:
        flags.append("geographic")
    if crs.is_dynamic:
        flags.append("dynamic")
    flags.append("epoch " + ("not set" if crs.epoch is None else "{:g}".format(crs.epoch)))
    lines.append("  {:<14} {}".format("properties:", ", ".join(flags)))
    return lines


# --------------------------------------------------------------------------
# Timeline
# --------------------------------------------------------------------------
def _change_lines(change) -> List[str]:
    """One change, as a person would say it. Sub-lines indented four spaces."""
    # Before and after go on lines of their own: a dock is narrow, and two full
    # CRS names joined by an arrow ran well past the record's width.
    if change.kind == "project":
        if change.before is None:
            return ["Project CRS: {}".format(change.after_crs)]
        if change.before_crs != change.after_crs:
            return ["Project CRS changed",
                    "    from: " + change.before_crs,
                    "    to:   " + change.after_crs]
        return ["Project transformation settings changed "
                "(project CRS still {})".format(change.after_crs)]

    name = change.label
    if change.after is None:
        return ["Layer removed: {} (was {})".format(name, change.before_crs)]
    if change.before is None:
        prefix = "" if change.first_observation else "Layer added: "
        return ["{}{} — {}".format(prefix, name, change.after_crs),
                "    " + change.after_detail]
    if change.before_crs != change.after_crs:
        return ["Layer CRS changed: {}".format(name),
                "    from: " + change.before_crs,
                "    to:   " + change.after_crs,
                "    now:  " + change.after_detail]
    return ["{} — {}".format(name, change.after_crs),
            "    was: " + change.before_detail,
            "    now: " + change.after_detail]


def timeline_lines(history) -> List[str]:
    """Every change set, oldest first, one dated block per day."""
    sets = list(reversed(history.all())) if history is not None else []
    if not sets:
        return ["(nothing recorded in this session yet)"]

    out: List[str] = []
    day = None
    for change_set in sets:
        stamp = change_set.at
        if stamp.strftime("%Y-%m-%d") != day:
            day = stamp.strftime("%Y-%m-%d")
            if out:
                out.append("")
            out.append(day)

        body: List[str] = []
        if change_set.is_baseline:
            n = change_set.n_tracked_layers
            body.append("Started watching: {} layer{} already in the project.".format(
                n, "" if n == 1 else "s"))
        elif not change_set.locally_changed_inputs:
            body.append("A conversion changed with no edit seen "
                        "(for example, a grid file appeared or disappeared):")
        # The project first, then layers by name — the store orders by id,
        # which a reader cannot see.
        for change in sorted(change_set.changes,
                             key=lambda c: (c.kind != "project", c.label.lower())):
            body.extend(_change_lines(change))

        out.append("{:<{w}}{}".format(stamp.strftime("%H:%M:%S"), body[0],
                                      w=TIME_COLUMN))
        out.extend(" " * TIME_COLUMN + line for line in body[1:])
    return out


# --------------------------------------------------------------------------
# Current state
# --------------------------------------------------------------------------
def _layer_blocks(state: ProjectState, verdicts) -> List[str]:
    out: List[str] = []
    for layer_state, verdict in zip(state.layers, verdicts):
        out.append("-" * RULE)
        out.append("LAYER  {}".format(layer_state.layer_name))
        out.append("  {:<14} {}".format("state:", _state_words(verdict.shift)))
        if not layer_state.is_spatial:
            out.append("  {:<14} none — this layer has no coordinates".format("geometry:"))
        else:
            out.extend(_crs_block(layer_state.source, "source CRS"))

        op = layer_state.operation
        if op is not None:
            out.append("  {:<14} {}".format("operation:", op.name or "not reported"))
            out.append("  {:<14} {}".format(
                "uncertainty:",
                "not published" if verdict.shift is Shift.NO_SHIFT
                else format_uncertainty(verdict.uncertainty_m)))
            if op.grids:
                for g in op.grids:
                    out.append("  {:<14} {} — {}".format(
                        "grid file:", g.short_name,
                        "installed" if g.is_available else "NOT INSTALLED"))
            else:
                out.append("  {:<14} none required".format("grid file:"))
        elif layer_state.is_spatial:
            out.append("  {:<14} none reported".format("operation:"))

        if layer_state.probe_error:
            out.append("  {:<14} {}".format("probe error:", layer_state.probe_error))
        out.append(_field("reading", verdict.headline))
        if verdict.detail:
            out.append(_field("detail", verdict.detail))
        if verdict.action:
            out.append(_field("suggested", verdict.action))
        out.append("  {:<14} {}".format("tracked as:", layer_state.layer_id))
        out.append("")
    return out


def _footer() -> List[str]:
    return [
        "-" * RULE,
        "Generated by CRS Inspector. 'Ballpark' means PROJ found no operation "
        "between two datums and converted without shifting — such layers still "
        "line up on screen with each other, because they are all wrong in the "
        "same direction.",
    ]


def _header(state: ProjectState, project_name: str, verdicts) -> List[str]:
    return [
        "project:        {}".format(project_name or "<unsaved>"),
        "project CRS:    {}".format(crs_text(state.target)),
        "project datum:  {}".format(state.target.datum_key or "not reported"),
    ]


def to_text(state: ProjectState, project_name: str = "") -> str:
    """The current state alone, as plain text. Kept for callers without history."""
    verdicts = [grade(s) for s in state.layers]
    out = ["CRS INSPECTOR — COORDINATE RECORD", "=" * RULE]
    out += _header(state, project_name, verdicts)
    out += [
        "summary:        {}".format(summarise(verdicts)),
        "",
        "Every layer below is described by how it is converted to the project "
        "CRS above. " + UNCERTAINTY_NOTE,
        "",
        BLIND_SPOT,
        "",
    ]
    out += _layer_blocks(state, verdicts)
    out += _footer()
    return "\n".join(out)


def full_record(state: ProjectState, history=None, project_name: str = "",
                observed_at: Optional[datetime] = None, is_current: bool = True,
                freshness_note: str = "", made_at: Optional[datetime] = None) -> str:
    """Header, timeline, then each layer in detail. What "Copy" puts on the clipboard.

    Three times are kept distinct, because a pasted record is read later and
    elsewhere: when the state was observed, whether it was still current, and
    when this text was made. A stale observation says so in words.
    """
    verdicts = [grade(s) for s in state.layers]
    made_at = made_at or datetime.now()
    if observed_at is None:
        checked = "not checked"
    elif is_current:
        checked = "{} (current)".format(observed_at.strftime("%Y-%m-%d %H:%M:%S"))
    else:
        checked = "{} — NOT CURRENT: {}".format(
            observed_at.strftime("%Y-%m-%d %H:%M:%S"),
            freshness_note or "inputs changed since")

    out = ["CRS INSPECTOR — PROJECT RECORD", "=" * RULE]
    out += _header(state, project_name, verdicts)
    out += [
        "last checked:   {}".format(checked),
        "record made:    {}".format(made_at.strftime("%Y-%m-%d %H:%M:%S")),
        "summary:        {}".format(summarise(verdicts)),
        "",
        TIME_NOTE,
        "",
        "TIMELINE",
        "-" * RULE,
    ]
    out += timeline_lines(history)
    out += [
        "",
        "",
        "CURRENT STATE, LAYER BY LAYER",
        UNCERTAINTY_NOTE,
        "",
        BLIND_SPOT,
        "",
    ]
    out += _layer_blocks(state, verdicts)
    out += _footer()
    return "\n".join(out)
