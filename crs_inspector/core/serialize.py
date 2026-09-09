"""The text record — the plugin's primary output.

People will paste this into a language model, and that is the right place for the
analysis to happen: no API keys, no running cost, no data leaving without a
deliberate press of a button, nothing to rot as models change.

That makes this format load-bearing rather than a convenience, with obligations
a screen does not have:

  * self-describing — datum names spelled out, not just authority codes
  * explicit unknowns — "unbounded" and "not published" as written values, never
    a blank, because a blank is an invitation to guess
  * carries what a human would skip — epochs, dynamic flags, grid availability
  * states its own blind spot, so the reader does not overclaim on the user's
    behalf

Pure functions over dataclasses. No Qt, no PyQGIS.
"""

from __future__ import annotations

import textwrap
from typing import Iterable

from .grading import format_uncertainty, grade, summarise
from .model import ProjectState, Shift

WIDTH = 72
LABEL = 16


def _wrap(label: str, text: str) -> Iterable[str]:
    """A labelled field, wrapped and hanging-indented to the label column."""
    body = textwrap.wrap(str(text), WIDTH - LABEL) or [""]
    head = "  {:<14} {}".format(label + ":", body[0])
    return [head] + ["  {}{}".format(" " * 14, line) for line in body[1:]]

BLIND_SPOT = (
    "LIMITATION: this record describes the shift between each layer and the "
    "project CRS. It cannot detect a layer whose assigned CRS is simply wrong — "
    "in that case the transformation is valid and the premise is not, and "
    "everything below will look healthy."
)

_STATE_WORDS = {
    Shift.NO_CRS: "NO CRS SET",
    Shift.NO_SHIFT: "no datum shift needed",
    Shift.SHIFT: "datum shift applied",
    Shift.BALLPARK: "BALLPARK OPERATION OBSERVED (no datum shift applied)",
    Shift.CROSS_DATUM_UNKNOWN: "NO PUBLISHED ACCURACY ACROSS A DATUM BOUNDARY",
    Shift.TEMPORAL_UNASSESSED: "SAME DATUM; TEMPORAL REFERENCE NOT ASSESSED",
    Shift.UNKNOWN: "STATE UNKNOWN",
}


def _state_words(shift) -> str:
    """Total by construction. A missing entry used to raise KeyError mid-export,
    losing the whole record over one unmapped state."""
    return _STATE_WORDS.get(shift, "UNMAPPED STATE ({})".format(getattr(shift, "value", shift)))


def _crs_block(crs, role: str) -> Iterable[str]:
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


def to_text(state: ProjectState, project_name: str = "") -> str:
    """Render the whole project state as the plain-text record."""
    verdicts = [grade(s) for s in state.layers]
    out = [
        "DATUM LOG — COORDINATE PROVENANCE RECORD",
        "=" * 72,
        "project:        {}".format(project_name or "<unsaved>"),
        "project CRS:    {} — {}".format(
            state.target.authid or "custom", state.target.description),
        "project datum:  {}".format(state.target.datum_key or "not reported"),
        "summary:        {}".format(summarise(verdicts)),
        "",
        "Every layer below is described by the shift required to bring it into the",
        "project CRS above. Uncertainty figures are the PUBLISHED accuracy of the",
        "operation as a method — they are not measured error at any given point.",
        "",
        textwrap.fill(BLIND_SPOT, WIDTH),
        "",
    ]

    for layer_state, verdict in zip(state.layers, verdicts):
        out.append("-" * 72)
        out.append("LAYER  {}".format(layer_state.layer_name))
        out.append("  {:<14} {}".format("state:", _state_words(verdict.shift)))
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
        else:
            out.append("  {:<14} none reported".format("operation:"))

        if layer_state.probe_error:
            out.append("  {:<14} {}".format("probe error:", layer_state.probe_error))
        out.extend(_wrap("reading", verdict.headline))
        if verdict.detail:
            out.extend(_wrap("detail", verdict.detail))
        if verdict.action:
            out.extend(_wrap("suggested", verdict.action))
        out.append("  {:<14} {}".format("tracked as:", layer_state.layer_id))
        out.append("")

    out.append("-" * 72)
    out.append("Generated by CRS Inspector. 'Ballpark' means PROJ found no operation "
               "between two")
    out.append("datums and reprojected without shifting — such layers still align on "
               "screen")
    out.append("with each other, because they are all wrong in the same direction.")
    return "\n".join(out)
