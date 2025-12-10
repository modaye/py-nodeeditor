from __future__ import annotations

from collections.abc import Iterator
from typing import cast

import pytest
from PySide6.QtWidgets import QApplication

from py_editor import (
    CanvasController,
    CanvasView,
    ConnectionPreviewStyleOptions,
)
from py_editor.graphics import CanvasEdgeItem, CanvasNodeItem
from py_editor.registry import CanvasRegistry


@pytest.fixture(scope="module")
def qapp() -> Iterator[QApplication]:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield cast(QApplication, app)


class CustomNodeItem(CanvasNodeItem):
    def __init__(self, node):
        super().__init__(node)
        self.is_custom = True


class CustomEdgeItem(CanvasEdgeItem):
    def __init__(self, edge, source_item, target_item):
        super().__init__(edge, source_item, target_item)
        self.is_custom = True


def _node_factory(node, _view):
    return CustomNodeItem(node)


def _edge_factory(edge, source_item, target_item, _view):
    return CustomEdgeItem(edge, source_item, target_item)


def test_registry_resolves_custom_factories(qapp):
    registry = CanvasRegistry()
    registry.register_node_factory("special", _node_factory)
    registry.register_edge_factory("fancy", _edge_factory)

    controller = CanvasController()
    source = controller.create_node(
        "Source",
        node_type="special",
        outputs=["out"],
    )
    target = controller.create_node(
        "Target",
        node_type="special",
        inputs=["inp"],
    )
    edge = controller.create_edge(
        source=source.id,
        target=target.id,
        source_port="out",
        target_port="inp",
        edge_type="fancy",
    )

    view = CanvasView(registry=registry)
    view.set_controller(controller)

    assert isinstance(view.registry, CanvasRegistry)
    assert isinstance(view._node_items[source.id], CustomNodeItem)
    assert isinstance(view._node_items[target.id], CustomNodeItem)
    assert isinstance(view._edge_items[edge.id], CustomEdgeItem)


def test_registry_legacy_metadata_kind(qapp):
    registry = CanvasRegistry()
    registry.register_node_factory("legacy", _node_factory)
    registry.register_edge_factory("legacy-edge", _edge_factory)

    controller = CanvasController()
    node = controller.create_node(
        "Legacy",
        metadata={"canvas": {"kind": "legacy"}},
    )
    legacy_edge = controller.create_edge(
        source=node.id,
        target=node.id,
        metadata={"canvas": {"kind": "legacy-edge"}},
    )

    view = CanvasView(registry=registry)
    view.set_controller(controller)

    assert isinstance(view._node_items[node.id], CustomNodeItem)
    assert isinstance(view._edge_items[legacy_edge.id], CustomEdgeItem)


def test_duplicate_registration_requires_replace():
    registry = CanvasRegistry()
    registry.register_node_factory("special", _node_factory)
    with pytest.raises(ValueError):
        registry.register_node_factory("Special", _node_factory)
    registry.register_node_factory("Special", _node_factory, replace=True)


def test_connection_preview_style_helpers():
    registry = CanvasRegistry()
    style = ConnectionPreviewStyleOptions(
        color="#123456",
        width=2.2,
        dash_pattern=None,
    )
    registry.set_connection_preview_style(style)
    resolved = registry.connection_preview_style(
        direction="output",
        source=None,
        source_port="out",
        target=None,
        target_port=None,
        view=None,
    )
    assert resolved is style


def test_connection_preview_factory_overrides():
    registry = CanvasRegistry()
    style = ConnectionPreviewStyleOptions(color="#FF0000")
    captured: dict[str, object] = {}

    def factory(
        direction,
        source,
        source_port,
        target,
        target_port,
        view,
    ) -> ConnectionPreviewStyleOptions:
        captured.update(
            {
                "direction": direction,
                "source": source,
                "source_port": source_port,
                "target": target,
                "target_port": target_port,
                "view": view,
            }
        )
        return style

    registry.set_connection_preview_factory(factory)
    resolved = registry.connection_preview_style(
        direction="input",
        source=None,
        source_port=None,
        target=None,
        target_port="in",
        view=None,
    )
    assert resolved is style
    assert captured["direction"] == "input"
    assert captured["target_port"] == "in"
