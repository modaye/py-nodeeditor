"""The file format and the public editing rules after the compatibility break."""

from __future__ import annotations

import re
from pathlib import Path

from py_editor.controller import CanvasController
from py_editor.models import Port
from py_editor.policy import CanvasPolicy, ConnectResult
from py_editor.view import CanvasView

_BANNED = re.compile(
    r"output_fields|output_columns|run_state|run_label|\bannotation\b"
)


def test_library_source_has_no_host_vocabulary() -> None:
    root = Path(__file__).resolve().parents[1] / "src" / "py_editor"
    for path in root.rglob("*.py"):
        assert _BANNED.search(path.read_text(encoding="utf-8")) is None, path.name


def test_public_edge_writers_are_gone() -> None:
    assert not hasattr(CanvasController, "create_edge")
    assert not hasattr(CanvasController, "add_edge")
    assert not hasattr(CanvasView, "add_edge")


def test_locked_node_refuses_a_wire_and_delete_skips_a_fixed_node() -> None:
    controller = CanvasController()
    controller.create_node(
        "A",
        node_id="a",
        ports=[Port("out", "output")],
        connectable=False,
    )
    controller.create_node(
        "B",
        node_id="b",
        ports=[Port("in", "input")],
        deletable=False,
    )
    assert controller.connect("a", "b").reason == "locked"
    controller.delete(["b"])
    assert "b" in controller.state.nodes


def test_extra_rule_can_reject_after_the_built_in_checks() -> None:
    def deny(_state, request):
        return ConnectResult(
            accepted=False,
            reason="cancelled",
            source=request.source,
            target=request.target,
        )

    controller = CanvasController(policy=CanvasPolicy(extra_rules=(deny,)))
    controller.create_node("A", node_id="a", ports=[Port("out", "output")])
    controller.create_node("B", node_id="b", ports=[Port("in", "input")])
    assert controller.connect("a", "b").reason == "cancelled"
    assert controller.state.edges == {}
