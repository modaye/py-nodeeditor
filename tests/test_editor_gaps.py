"""Grid snap, selection framing, the edge menu, and the public examples."""

from __future__ import annotations

import importlib.util
from collections.abc import Iterator
from pathlib import Path
from typing import cast

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QContextMenuEvent, QKeyEvent
from PySide6.QtWidgets import QApplication

from py_editor import CanvasController, CanvasView, Port


def _example(name: str):
    path = Path(__file__).resolve().parents[1] / "examples" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def qapp() -> Iterator[QApplication]:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield cast(QApplication, app)


def test_snap_on_release_lands_on_the_grid_and_undo_restores_the_press(qapp: QApplication) -> None:
    controller = CanvasController()
    controller.create_node("A", node_id="a", position=(0.0, 0.0), ports=[Port("out", "output")])
    view = CanvasView(snap_to_grid=True)
    view.resize(900, 600)
    view.show()
    view.set_controller(controller)
    qapp.processEvents()
    item = view._node_items["a"]
    view._drag_starts["a"] = (0.0, 0.0)
    item.setPos(10.0, 30.0)
    view._commit_moves()
    assert controller.state.nodes["a"].position == (0.0, 24.0)
    controller.undo()
    assert controller.state.nodes["a"].position == (0.0, 0.0)
    assert qapp is not None


def test_snap_stays_off_unless_requested(qapp: QApplication) -> None:
    controller = CanvasController()
    controller.create_node("A", node_id="a", position=(0.0, 0.0))
    view = CanvasView()
    view.resize(400, 300)
    view.show()
    view.set_controller(controller)
    qapp.processEvents()
    view._drag_starts["a"] = (0.0, 0.0)
    view._node_items["a"].setPos(10.0, 30.0)
    view._commit_moves()
    assert controller.state.nodes["a"].position == (10.0, 30.0)


def test_focus_on_selection_includes_a_selected_edge(qapp: QApplication) -> None:
    controller, view = _example("basic_editor").build()
    controller.create_node("Far", position=(4000.0, 3000.0), ports=[Port("out", "output")])
    view.resize(900, 600)
    view.show()
    qapp.processEvents()
    edge_id = next(iter(controller.state.edges))
    controller.set_selection(nodes=[], edges=[edge_id])
    assert view._selection_rect() is not None
    view.fit_to_contents()
    wide = view.transform().m11()
    event = QKeyEvent(
        QKeyEvent.Type.KeyPress,
        Qt.Key.Key_0,
        Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier,
    )
    view.keyPressEvent(event)
    assert view.transform().m11() > wide


def test_edge_menu_reports_the_edge_id(qapp: QApplication, monkeypatch: pytest.MonkeyPatch) -> None:
    controller, view = _example("basic_editor").build()
    view.resize(900, 600)
    view.show()
    qapp.processEvents()
    edge_id = next(iter(controller.state.edges))
    seen: list[str] = []
    view.edge_menu.connect(lambda _menu, reported: seen.append(reported))
    monkeypatch.setattr(CanvasView, "_show_menu", lambda *_args, **_kwargs: None)
    edge = view._edge_items[edge_id]
    mid = edge.path().pointAtPercent(0.5)
    local = view.mapFromScene(mid)
    view.contextMenuEvent(
        QContextMenuEvent(
            QContextMenuEvent.Reason.Mouse,
            local,
            view.mapToGlobal(local),
        )
    )
    assert seen == [edge_id]


def test_examples_use_the_public_editor(qapp: QApplication) -> None:
    _controller, view = _example("basic_editor").build()
    assert len(_controller.state.edges) == 1
    assert view.controller is _controller

    custom_example = _example("custom_node")
    controller, custom = custom_example.build()
    custom.resize(900, 600)
    custom.show()
    qapp.processEvents()
    source = next(node for node in controller.state.nodes.values() if node.title == "Source")
    item = custom._node_items[source.id]
    assert isinstance(item, custom_example.AccentNode)
    assert "port_anchor" not in custom_example.AccentNode.__dict__
    edge = next(iter(custom._edge_items.values()))
    start = edge.path().elementAt(0)
    anchor = item.mapToScene(item.port_anchor("output", "out"))
    assert abs(start.x - anchor.x()) < 1.5
    assert abs(start.y - anchor.y()) < 1.5

    ruled, _ruled_view = _example("connection_rule").build()
    assert ruled.state.edges == {}

    saved = _example("save_restore")
    restored = saved.load(saved.dump(saved.build()))
    assert set(restored.state.nodes) == {"source", "sink"}
    edge_data = next(iter(restored.state.edges.values()))
    assert (edge_data.source, edge_data.target) == ("source", "sink")
    assert restored.state.viewport.x == 220.0
    assert restored.state.nodes["source"].ports[0].kind == "table"


def test_restored_viewport_is_applied(qapp: QApplication) -> None:
    saved = _example("save_restore")
    controller = saved.load(saved.dump(saved.build()))
    view = CanvasView()
    view.resize(900, 600)
    view.show()
    view.set_controller(controller)
    qapp.processEvents()
    center = view.mapToScene(view.viewport().rect().center())
    assert abs(center.x() - 220.0) < 2
    assert abs(center.y() - 180.0) < 2
    assert abs(view.transform().m11() - 1.0) < 0.01
