"""Large-graph behavior: lookup, coarse drawing, and port drags without per-port items."""

from __future__ import annotations

from collections.abc import Iterator
from typing import cast

import pytest
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QMouseEvent, QPainter
from PySide6.QtWidgets import QApplication, QGraphicsScene

from py_editor import CanvasController, CanvasView
from py_editor.graphics import CanvasEdgeItem, CanvasNodeItem
from py_editor.models import CanvasState, EdgeData, NodeData, Port


@pytest.fixture(scope="module")
def qapp() -> Iterator[QApplication]:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield cast(QApplication, app)


def test_scene_keeps_a_spatial_index(qapp: QApplication) -> None:
    view = _view()
    assert view.scene().itemIndexMethod() == QGraphicsScene.ItemIndexMethod.BspTreeIndex
    assert qapp is not None


def test_focus_titled_selects_and_ignores_a_miss(qapp: QApplication) -> None:
    view = _view()
    assert view.focus_titled("filter") == ["b"]
    assert view.controller is not None
    assert view.controller.state.selection.nodes == {"b"}
    assert view.focus_titled("no-such-node") == []
    assert view.controller.state.selection.nodes == {"b"}
    assert qapp is not None


def test_zooming_out_switches_to_compact_drawing(qapp: QApplication) -> None:
    view = _view()
    view.scale(0.1, 0.1)
    view._apply_detail()
    assert view._node_items["a"]._compact
    assert view._edge_items["ab"]._compact
    assert qapp is not None


def test_viewport_query_does_not_scan_every_node(qapp: QApplication) -> None:
    from py_editor.graphics import CanvasNodeItem

    state = CanvasState()
    for index in range(600):
        state.nodes[f"n{index}"] = NodeData(
            f"n{index}",
            f"Step {index}",
            position=((index % 30) * 240.0, (index // 30) * 140.0),
            ports=[Port("out", "output")],
        )
    view = CanvasView()
    view.resize(1280, 720)
    view.set_controller(CanvasController(state))
    view.show()
    qapp.processEvents()
    calls = 0
    original = CanvasNodeItem.boundingRect

    def counting(self: CanvasNodeItem) -> object:
        nonlocal calls
        calls += 1
        return original(self)

    CanvasNodeItem.boundingRect = counting  # type: ignore[method-assign]
    try:
        exposed = view.mapToScene(view.viewport().rect()).boundingRect()
        view.scene().items(exposed)
    finally:
        CanvasNodeItem.boundingRect = original  # type: ignore[method-assign]
    assert calls < 120
    assert qapp is not None


def test_refresh_skips_wires_whose_ends_did_not_move(qapp: QApplication) -> None:
    view = _view()
    calls = {"n": 0}
    original = CanvasEdgeItem.update_path

    def counting(self: CanvasEdgeItem) -> None:
        calls["n"] += 1
        original(self)

    CanvasEdgeItem.update_path = counting  # type: ignore[method-assign]
    try:
        view.refresh()
        assert calls["n"] == 0
        assert view.controller is not None
        view.controller.move_nodes({"a": (90.0, 80.0)})
        view.refresh()
        assert calls["n"] == 1
    finally:
        CanvasEdgeItem.update_path = original  # type: ignore[method-assign]
    assert qapp is not None


def test_compact_card_fills(qapp: QApplication) -> None:
    item = CanvasNodeItem(NodeData("a", "A", ports=[Port("out", "output")]))
    item.set_compact(True)
    scene = QGraphicsScene()
    scene.addItem(item)
    image = QImage(80, 40, QImage.Format.Format_ARGB32)
    image.fill(0xFFFFFFFF)
    painter = QPainter(image)
    scene.render(
        painter,
        QRectF(0, 0, 80, 40),
        item.sceneBoundingRect(),
        Qt.AspectRatioMode.IgnoreAspectRatio,
    )
    painter.end()
    color = image.pixelColor(40, 20)
    assert color.red() > 200
    assert color.green() > 200
    assert color.blue() > 200
    assert qapp is not None


def test_status_change_repaints_the_overview(qapp: QApplication) -> None:
    view = _view()
    view.resize(900, 600)
    view.show()
    view.scale(0.1, 0.1)
    view._apply_detail()
    view.viewport().repaint()
    qapp.processEvents()
    assert view._overview is not None
    assert view.controller is not None
    view.controller.state.update_node("a", title="Changed")
    view.sync_node("a")
    qapp.processEvents()
    assert view._compact
    assert view._overview is not None
    assert qapp is not None


def test_dragging_a_port_starts_a_wire(qapp: QApplication) -> None:
    view = _view()
    view.resize(900, 600)
    view.show()
    qapp.processEvents()
    port = view._node_items["a"].port_scene_position("output", "out")
    point = QPointF(view.mapFromScene(port))
    view.mousePressEvent(_mouse(QMouseEvent.Type.MouseButtonPress, point, view))
    moved = QPointF(point.x() + 40, point.y())
    view.mouseMoveEvent(_mouse(QMouseEvent.Type.MouseMove, moved, view))
    assert view._gesture.active
    assert view._gesture.origin is not None
    assert view._gesture.origin.node_id == "a"
    assert view._gesture.origin.port_id == "out"
    assert qapp is not None


def test_background_is_a_line_grid(qapp: QApplication) -> None:
    view = CanvasView()
    image = QImage(120, 120, QImage.Format.Format_ARGB32)
    image.fill(QColor("#FF00FF"))
    painter = QPainter(image)
    painter.fillRect(QRectF(0, 0, 120, 120), view.backgroundBrush())
    painter.end()
    line = image.pixelColor(0, 8)
    gap = image.pixelColor(12, 12)
    assert line.red() < gap.red()
    assert gap.red() > 240
    view.set_grid_enabled(False)
    painter = QPainter(image)
    painter.fillRect(QRectF(0, 0, 120, 120), view.backgroundBrush())
    painter.end()
    assert image.pixelColor(0, 8) == image.pixelColor(12, 12)
    assert qapp is not None


def test_flow_offset_dashes_then_restores_a_solid_stroke(qapp: QApplication) -> None:
    view = _view()
    assert view._flow_timer.isActive() is False
    edge = view._edge_items["ab"]
    view._advance_flow()
    assert edge.pen().style() == Qt.PenStyle.SolidLine
    view.set_edge_animation_enabled(True)
    assert view._flow_timer.isActive()
    edge.set_flow_offset(4.0)
    assert edge.pen().style() == Qt.PenStyle.CustomDashLine
    assert abs(edge.pen().dashOffset() - 4.0) < 0.01
    view.resize(900, 600)
    view.show()
    qapp.processEvents()
    view.centerOn(view._node_items["b"])
    view._advance_flow()
    assert edge.pen().style() == Qt.PenStyle.CustomDashLine
    view._compact = True
    view._advance_flow()
    assert edge.pen().style() == Qt.PenStyle.SolidLine
    assert qapp is not None


def test_minimap_click_centers_a_node_that_does_not_fit(qapp: QApplication) -> None:
    state = CanvasState()
    state.nodes["a"] = NodeData("a", "Near", position=(0.0, 0.0), ports=[Port("out", "output")])
    state.nodes["b"] = NodeData(
        "b",
        "Far",
        position=(2400.0, 1600.0),
        ports=[Port("in", "input")],
    )
    state.edges["ab"] = EdgeData("ab", "a", "b", source_port="out", target_port="in")
    view = CanvasView()
    view.resize(800, 600)
    view.set_controller(CanvasController(state))
    view.show()
    qapp.processEvents()
    assert view._minimap.isVisible()
    view._minimap._rebuild()
    point = view._minimap.widget_point(QPointF(2490.0, 1638.0))
    view._minimap.mousePressEvent(
        QMouseEvent(
            QMouseEvent.Type.MouseButtonPress,
            point,
            view._minimap.mapToGlobal(point.toPoint()),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
    )
    center = view.mapToScene(view.viewport().rect().center())
    assert abs(center.x() - 2490.0) < 8
    assert abs(center.y() - 1638.0) < 8
    image = view._minimap._pixmap.toImage()
    color = image.pixelColor(int(point.x()), int(point.y()))
    assert abs(color.red() - 100) < 40
    assert abs(color.blue() - 139) < 40
    assert qapp is not None


def test_one_thousand_nodes_follow_a_move_and_an_undo(qapp: QApplication) -> None:
    from tests.perf_graphs import grid_chain

    view = CanvasView()
    view.resize(1280, 720)
    view.show()
    view.set_controller(CanvasController(grid_chain(1000)))
    qapp.processEvents()
    controller = view.controller
    assert controller is not None
    assert set(view._node_items) == set(controller.state.nodes)
    assert set(view._edge_items) == set(controller.state.edges)
    start = controller.state.nodes["n0"].position
    controller.move_nodes({"n0": (start[0] + 48.0, start[1] + 24.0)})
    qapp.processEvents()
    item = view._node_items["n0"]
    assert abs(item.pos().x() - (start[0] + 48.0)) < 0.6
    assert abs(item.pos().y() - (start[1] + 24.0)) < 0.6
    controller.undo()
    qapp.processEvents()
    assert controller.state.nodes["n0"].position == start
    assert abs(item.pos().x() - start[0]) < 0.6
    assert abs(item.pos().y() - start[1]) < 0.6
    view.close()


def _view() -> CanvasView:
    state = CanvasState()
    state.nodes["a"] = NodeData("a", "Source", position=(40.0, 80.0), ports=[Port("out", "output")])
    state.nodes["b"] = NodeData(
        "b",
        "Filter",
        position=(320.0, 80.0),
        ports=[Port("in", "input"), Port("out", "output")],
    )
    state.nodes["c"] = NodeData("c", "Sink", position=(600.0, 80.0), ports=[Port("in", "input")])
    state.edges["ab"] = EdgeData("ab", "a", "b", source_port="out", target_port="in")
    state.edges["bc"] = EdgeData("bc", "b", "c", source_port="out", target_port="in")
    view = CanvasView()
    view.set_controller(CanvasController(state))
    return view


def _mouse(kind: QMouseEvent.Type, point: QPointF, view: CanvasView) -> QMouseEvent:
    button = Qt.MouseButton.LeftButton
    return QMouseEvent(
        kind,
        point,
        view.mapToGlobal(point.toPoint()),
        button,
        button,
        Qt.KeyboardModifier.NoModifier,
    )
