"""Pointer interactions on a shown canvas. Offscreen-safe Qt events."""

from __future__ import annotations

from collections.abc import Iterator
from typing import cast

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QKeyEvent, QMouseEvent, QWheelEvent
from PySide6.QtWidgets import QApplication, QGraphicsItem, QGraphicsSceneMouseEvent

from py_editor import CanvasController, CanvasView
from py_editor.models import EdgeData, NodeData, Port


@pytest.fixture(scope="module")
def qapp() -> Iterator[QApplication]:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield cast(QApplication, app)


def test_controller_edit_updates_the_scene_without_an_extra_refresh(qapp: QApplication) -> None:
    view = _shown(_pair(), qapp)
    view.controller.remove_node("a")
    qapp.processEvents()
    assert "a" not in view._node_items
    assert "ab" not in view._edge_items
    assert view.controller.state.edges == {}
    view.controller.undo()
    qapp.processEvents()
    assert "a" in view._node_items
    assert "ab" in view._edge_items


def test_fit_does_not_zoom_past_the_wheel_minimum(qapp: QApplication) -> None:
    controller = CanvasController()
    controller.create_node("A", node_id="a", position=(0.0, 0.0), ports=[Port("out", "output")])
    controller.create_node("B", node_id="b", position=(6000.0, 4000.0), ports=[Port("in", "input")])
    view = CanvasView()
    view.resize(240, 180)
    view.show()
    qapp.processEvents()
    view.set_controller(controller)
    view.fit_to_contents()
    scale = view.transform().m11()
    assert scale + 1e-6 >= 0.05
    center = view.mapToScene(view.viewport().rect().center())
    assert 0.0 < center.x() < 6200.0
    assert qapp is not None


def test_wheel_zoom_stays_inside_the_limits(qapp: QApplication) -> None:
    view = _shown(_pair(), qapp)
    before = view.transform().m11()
    pos = QPointF(80, 80)
    view.wheelEvent(
        QWheelEvent(
            pos,
            view.mapToGlobal(pos.toPoint()),
            QPoint(0, 0),
            QPoint(0, 120),
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
            Qt.ScrollPhase.NoScrollPhase,
            False,
        )
    )
    assert view.transform().m11() > before
    for _ in range(40):
        view.wheelEvent(
            QWheelEvent(
                pos,
                view.mapToGlobal(pos.toPoint()),
                QPoint(0, 0),
                QPoint(0, -120),
                Qt.MouseButton.NoButton,
                Qt.KeyboardModifier.NoModifier,
                Qt.ScrollPhase.NoScrollPhase,
                False,
            )
        )
    assert view.transform().m11() + 1e-6 >= 0.05


def test_panning_blank_canvas_moves_the_viewport(qapp: QApplication) -> None:
    view = _shown(_pair(), qapp)
    origin = view.mapToScene(view.viewport().rect().center())
    blank = _blank_point(view)
    view.mousePressEvent(_mouse(QMouseEvent.Type.MouseButtonPress, QPointF(blank), view))
    moved = QPointF(blank.x() + 40, blank.y() + 25)
    view.mouseMoveEvent(_mouse(QMouseEvent.Type.MouseMove, moved, view))
    view.mouseReleaseEvent(_mouse(QMouseEvent.Type.MouseButtonRelease, moved, view))
    shifted = view.mapToScene(view.viewport().rect().center())
    assert abs(shifted.x() - origin.x()) > 10
    assert not view._panning


def test_shift_drag_selects_the_nodes_inside_the_band(qapp: QApplication) -> None:
    view = _shown(_pair(), qapp)
    view.controller.clear_selection()
    view.refresh()
    start = _blank_point(view)
    end = view.mapFromScene(QPointF(700.0, 220.0))
    view.mousePressEvent(
        _mouse(QMouseEvent.Type.MouseButtonPress, QPointF(start), view, Qt.KeyboardModifier.ShiftModifier)
    )
    view.mouseMoveEvent(
        _mouse(QMouseEvent.Type.MouseMove, QPointF(end), view, Qt.KeyboardModifier.ShiftModifier)
    )
    view.mouseReleaseEvent(
        _mouse(QMouseEvent.Type.MouseButtonRelease, QPointF(end), view, Qt.KeyboardModifier.ShiftModifier)
    )
    assert view._selecting is False
    assert view.dragMode() == CanvasView.DragMode.NoDrag
    assert {"a", "b"} <= set(view.controller.selected_nodes())


def test_ctrl_click_adds_a_node_without_clearing_the_first(qapp: QApplication) -> None:
    view = _shown(_pair(), qapp)
    view.focus_nodes(["a"])
    point = QPointF(view.mapFromScene(view._node_items["b"].sceneBoundingRect().center()))
    view.mousePressEvent(
        _mouse(QMouseEvent.Type.MouseButtonPress, point, view, Qt.KeyboardModifier.ControlModifier)
    )
    view.mouseReleaseEvent(
        _mouse(QMouseEvent.Type.MouseButtonRelease, point, view, Qt.KeyboardModifier.ControlModifier)
    )
    assert set(view.controller.selected_nodes()) == {"a", "b"}


def test_dragging_one_selected_node_moves_the_other_selected_node(qapp: QApplication) -> None:
    view = _shown(_pair(), qapp)
    view.focus_nodes(["a", "b"])
    start_a = view.controller.state.nodes["a"].position
    start_b = view.controller.state.nodes["b"].position
    point = QPointF(view.mapFromScene(view._node_items["a"].sceneBoundingRect().center()))
    view.mousePressEvent(_mouse(QMouseEvent.Type.MouseButtonPress, point, view))
    moved = QPointF(point.x() + 36, point.y() + 18)
    view.mouseMoveEvent(_mouse(QMouseEvent.Type.MouseMove, moved, view))
    view.mouseReleaseEvent(_mouse(QMouseEvent.Type.MouseButtonRelease, moved, view))
    qapp.processEvents()
    end_a = view.controller.state.nodes["a"].position
    end_b = view.controller.state.nodes["b"].position
    assert abs(end_a[0] - start_a[0] - 36) < 2
    assert abs(end_a[1] - start_a[1] - 18) < 2
    assert abs((end_b[0] - start_b[0]) - (end_a[0] - start_a[0])) < 2
    assert abs((end_b[1] - start_b[1]) - (end_a[1] - start_a[1])) < 2
    edge = view._edge_items["ab"]
    port = view._node_items["b"].port_scene_position("input", "in")
    last = edge.path().elementAt(edge.path().elementCount() - 1)
    assert abs(last.x - port.x()) < 1.5
    assert abs(last.y - port.y()) < 1.5


def test_adding_a_port_moves_the_existing_wire_to_the_new_anchor(qapp: QApplication) -> None:
    view = _shown(_pair(), qapp)
    before = view._node_items["b"].port_scene_position("input", "in")
    view.controller.update_node(
        "b",
        ports=[Port("in", "input"), Port("extra", "input")],
    )
    qapp.processEvents()
    after = view._node_items["b"].port_scene_position("input", "in")
    assert abs(after.y() - before.y()) > 2
    last = view._edge_items["ab"].path().elementAt(view._edge_items["ab"].path().elementCount() - 1)
    assert abs(last.x - after.x()) < 1.5
    assert abs(last.y - after.y()) < 1.5


def test_releasing_a_wire_on_empty_space_cancels(qapp: QApplication) -> None:
    view = _shown(_pair(), qapp)
    _drag_port(view, "a", "out", QPointF(30, 30))
    assert not view._gesture.active
    assert set(view.controller.state.edges) == {"ab"}


def test_escape_cancels_a_connection_drag(qapp: QApplication) -> None:
    view = _shown(_pair(), qapp)
    port = view._node_items["a"].port_scene_position("output", "out")
    point = QPointF(view.mapFromScene(port))
    view.mousePressEvent(_mouse(QMouseEvent.Type.MouseButtonPress, point, view))
    view.mouseMoveEvent(_mouse(QMouseEvent.Type.MouseMove, QPointF(point.x() + 40, point.y()), view))
    assert view._gesture.active
    view.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier))
    assert not view._gesture.active
    assert view._emphasized_ids == set()
    assert set(view.controller.state.edges) == {"ab"}


def test_a_locked_node_stays_put_when_dragged(qapp: QApplication) -> None:
    controller = CanvasController()
    controller.create_node(
        "Fixed",
        node_id="a",
        position=(40.0, 80.0),
        ports=[Port("out", "output")],
        draggable=False,
        selectable=False,
    )
    view = _shown(controller, qapp)
    view.controller.clear_selection()
    qapp.processEvents()
    item = view._node_items["a"]
    flags = item.flags()
    assert not flags & QGraphicsItem.GraphicsItemFlag.ItemIsMovable
    assert not flags & QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
    steps = len(view.controller.commands._undo_stack)
    point = QPointF(view.mapFromScene(item.sceneBoundingRect().center()))
    view.mousePressEvent(_mouse(QMouseEvent.Type.MouseButtonPress, point, view))
    moved = QPointF(point.x() + 36, point.y() + 20)
    view.mouseMoveEvent(_mouse(QMouseEvent.Type.MouseMove, moved, view))
    view.mouseReleaseEvent(_mouse(QMouseEvent.Type.MouseButtonRelease, moved, view))
    assert view.controller.state.nodes["a"].position == (40.0, 80.0)
    assert abs(item.pos().x() - 40.0) < 0.6
    assert len(view.controller.commands._undo_stack) == steps
    assert "a" not in view.controller.state.selection.nodes


def test_dragging_a_wire_end_reconnects_it(qapp: QApplication) -> None:
    controller = CanvasController()
    controller.create_node("A", node_id="a", position=(40.0, 80.0), ports=[Port("out", "output")])
    controller.create_node("B", node_id="b", position=(320.0, 80.0), ports=[Port("in", "input")])
    controller.create_node("C", node_id="c", position=(320.0, 240.0), ports=[Port("in", "input")])
    made = controller.connect("a", "b", source_port="out", target_port="in")
    assert made.edge_id is not None
    view = _shown(controller, qapp)
    wire = view._edge_items[made.edge_id]
    end = wire._target.port_scene_position("input", "in")
    away = QPointF(end.x() - 40, end.y() + 30)
    landing = view._node_items["c"].port_scene_position("input", "in")
    wire.mousePressEvent(_scene_mouse(QEvent.Type.GraphicsSceneMousePress, end, held=True))
    wire.mouseMoveEvent(_scene_mouse(QEvent.Type.GraphicsSceneMouseMove, away, held=True))
    wire.mouseMoveEvent(_scene_mouse(QEvent.Type.GraphicsSceneMouseMove, landing, held=True))
    wire.mouseReleaseEvent(_scene_mouse(QEvent.Type.GraphicsSceneMouseRelease, landing, held=False))
    qapp.processEvents()
    assert made.edge_id not in view.controller.state.edges
    moved = next(iter(view.controller.state.edges.values()))
    assert (moved.source, moved.target) == ("a", "c")
    view.controller.undo()
    qapp.processEvents()
    assert made.edge_id in view.controller.state.edges
    assert view.controller.state.edges[made.edge_id].target == "b"


def test_dropping_a_wire_on_an_incompatible_port_keeps_the_old_graph(qapp: QApplication) -> None:
    controller = CanvasController()
    controller.create_node(
        "Picture",
        node_id="a",
        position=(40.0, 80.0),
        ports=[Port("out", "output", kind="image")],
    )
    controller.create_node(
        "Table",
        node_id="b",
        position=(320.0, 80.0),
        ports=[Port("in", "input", kind="table")],
    )
    view = _shown(controller, qapp)
    target = view._node_items["b"].port_scene_position("input", "in")
    _drag_port(view, "a", "out", QPointF(view.mapFromScene(target)))
    assert view.controller.state.edges == {}
    assert not view._gesture.active


def _pair() -> CanvasController:
    controller = CanvasController()
    controller.create_node("A", node_id="a", position=(40.0, 80.0), ports=[Port("out", "output")])
    controller.create_node("B", node_id="b", position=(320.0, 80.0), ports=[Port("in", "input")])
    edge = EdgeData("ab", "a", "b", source_port="out", target_port="in")
    controller.state.edges[edge.id] = edge
    return controller


def _shown(controller: CanvasController, qapp: QApplication) -> CanvasView:
    view = CanvasView()
    view.resize(900, 600)
    view.show()
    view.set_controller(controller)
    qapp.processEvents()
    return view


def _blank_point(view: CanvasView) -> QPoint:
    for y in range(8, view.height() - 8, 16):
        for x in range(8, view.width() - 8, 16):
            if view.itemAt(QPoint(x, y)) is None:
                return QPoint(x, y)
    raise AssertionError("expected an empty spot on the canvas")


def _drag_port(view: CanvasView, node_id: str, port_id: str, release_at: QPointF) -> None:
    port = view._node_items[node_id].port_scene_position("output", port_id)
    point = QPointF(view.mapFromScene(port))
    view.mousePressEvent(_mouse(QMouseEvent.Type.MouseButtonPress, point, view))
    view.mouseMoveEvent(_mouse(QMouseEvent.Type.MouseMove, release_at, view))
    view.mouseReleaseEvent(_mouse(QMouseEvent.Type.MouseButtonRelease, release_at, view))


def _scene_mouse(kind: QEvent.Type, scene_pos: QPointF, *, held: bool) -> QGraphicsSceneMouseEvent:
    event = QGraphicsSceneMouseEvent(kind)
    event.setScenePos(scene_pos)
    event.setButton(Qt.MouseButton.LeftButton)
    event.setButtons(Qt.MouseButton.LeftButton if held else Qt.MouseButton.NoButton)
    return event


def _mouse(
    kind: QMouseEvent.Type,
    point: QPointF,
    view: CanvasView,
    modifiers: Qt.KeyboardModifier = Qt.KeyboardModifier.NoModifier,
) -> QMouseEvent:
    button = Qt.MouseButton.LeftButton
    return QMouseEvent(kind, point, view.mapToGlobal(point.toPoint()), button, button, modifiers)
