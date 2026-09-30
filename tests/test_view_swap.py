"""Replacing the graph must not leave wires attached to the previous nodes."""

from __future__ import annotations

from collections.abc import Iterator
from typing import cast

import pytest
from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QApplication

from py_editor import CanvasController, CanvasView
from py_editor.models import CanvasState, EdgeData, NodeData


@pytest.fixture(scope="module")
def qapp() -> Iterator[QApplication]:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield cast(QApplication, app)


def test_swapping_controllers_redraws_wires_on_the_new_nodes(qapp: QApplication) -> None:
    view = CanvasView()
    view.set_controller(CanvasController(_graph("a", "b", 0.0, 900.0)))
    view.set_controller(CanvasController(_graph("left", "right", 40.0, 80.0)))

    edge = view._edge_items["edge-0"]
    assert edge.follows(view._node_items["left"], view._node_items["right"])
    start = edge._source.port_scene_position("output", "out")
    element = edge.path().elementAt(0)
    assert abs(element.x - start.x()) < 1.0
    assert abs(element.y - start.y()) < 1.0
    assert qapp is not None


def test_selecting_a_node_highlights_only_its_wires(qapp: QApplication) -> None:
    view = CanvasView()
    state = CanvasState()
    state.nodes["a"] = NodeData("a", "A", position=(0.0, 0.0), outputs=["out"])
    state.nodes["b"] = NodeData("b", "B", position=(240.0, 0.0), inputs=["in"])
    state.nodes["c"] = NodeData("c", "C", position=(0.0, 180.0), outputs=["out"])
    state.nodes["d"] = NodeData("d", "D", position=(240.0, 180.0), inputs=["in"])
    state.edges["ab"] = EdgeData("ab", "a", "b", source_port="out", target_port="in")
    state.edges["cd"] = EdgeData("cd", "c", "d", source_port="out", target_port="in")
    view.set_controller(CanvasController(state))
    view.focus_nodes(["a"])

    assert view._edge_items["ab"]._relation == "linked"
    assert view._edge_items["cd"]._relation == "dimmed"
    assert view._edge_items["ab"].pen().color().name() == "#2563eb"
    assert view._edge_items["cd"].pen().color().name() == "#cbd5e1"
    assert "ab" not in view.controller.state.selection.edges

    view.controller.clear_selection()
    view.refresh()
    assert view._edge_items["ab"]._relation == "normal"
    assert view._edge_items["cd"]._relation == "normal"
    assert qapp is not None


def test_right_click_target_reads_the_node_property(qapp: QApplication) -> None:
    view = CanvasView()
    view.resize(900, 600)
    view.show()
    view.set_controller(CanvasController(_graph("left", "right", 40.0, 80.0)))
    item = view._node_items["left"]
    point = view.mapFromScene(item.sceneBoundingRect().center())
    assert view._target_at(QPoint(point.x(), point.y())) == ("left", None)
    assert qapp is not None


def _graph(source: str, target: str, x: float, y: float) -> CanvasState:
    state = CanvasState()
    state.nodes[source] = NodeData(
        id=source,
        title=source,
        position=(x, y),
        outputs=["out"],
    )
    state.nodes[target] = NodeData(
        id=target,
        title=target,
        position=(x + 280.0, y),
        inputs=["in"],
    )
    state.edges["edge-0"] = EdgeData(
        id="edge-0",
        source=source,
        target=target,
        source_port="out",
        target_port="in",
    )
    return state
