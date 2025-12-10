from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, Optional, TYPE_CHECKING

from .graphics import (
    CanvasEdgeItem,
    CanvasNodeItem,
)
from .models import EdgeData, NodeData

if TYPE_CHECKING:  # pragma: no cover - imported for type checking only
    from .view import CanvasView

NodeItemFactory = Callable[[NodeData, "CanvasView"], CanvasNodeItem]
EdgeItemFactory = Callable[
    [EdgeData, CanvasNodeItem, CanvasNodeItem, "CanvasView"],
    CanvasEdgeItem,
]


@dataclass(slots=True)
class ConnectionPreviewStyleOptions:
    color: str = "#38BDF8"
    width: float = 1.8
    dash_pattern: tuple[float, ...] | None = (6.0, 4.0)


ConnectionPreviewFactory = Callable[
    [
        str,
        Optional[NodeData],
        Optional[str],
        Optional[NodeData],
        Optional[str],
        Optional["CanvasView"],
    ],
    ConnectionPreviewStyleOptions,
]


def _default_node_factory(
    node: NodeData,
    _view: "CanvasView",
) -> CanvasNodeItem:
    return CanvasNodeItem(node)


def _default_edge_factory(
    edge: EdgeData,
    source_item: CanvasNodeItem,
    target_item: CanvasNodeItem,
    _view: "CanvasView",
) -> CanvasEdgeItem:
    return CanvasEdgeItem(edge, source_item, target_item)


class CanvasRegistry:
    """Registry for customizing canvas node, edge, and connection behaviors."""

    def __init__(
        self,
        *,
        default_node_factory: NodeItemFactory | None = None,
        default_edge_factory: EdgeItemFactory | None = None,
        default_connection_preview_factory: ConnectionPreviewFactory
        | None = None,
    ) -> None:
        self._node_factories: Dict[str, NodeItemFactory] = {}
        self._edge_factories: Dict[str, EdgeItemFactory] = {}
        self._default_node_factory = (
            default_node_factory or _default_node_factory
        )
        self._default_edge_factory = (
            default_edge_factory or _default_edge_factory
        )
        self._connection_preview_factory: ConnectionPreviewFactory = (
            default_connection_preview_factory
            or _default_connection_preview_factory
        )

    def register_node_factory(
        self,
        kind: str,
        factory: NodeItemFactory,
        *,
        replace: bool = False,
    ) -> None:
        key = self._normalize_kind(kind)
        if not replace and key in self._node_factories:
            raise ValueError(
                f"Node factory already registered for kind '{kind}'"
            )
        self._node_factories[key] = factory

    def unregister_node_factory(self, kind: str) -> None:
        key = self._normalize_kind(kind)
        self._node_factories.pop(key, None)

    def register_edge_factory(
        self,
        kind: str,
        factory: EdgeItemFactory,
        *,
        replace: bool = False,
    ) -> None:
        key = self._normalize_kind(kind)
        if not replace and key in self._edge_factories:
            raise ValueError(
                f"Edge factory already registered for kind '{kind}'"
            )
        self._edge_factories[key] = factory

    def unregister_edge_factory(self, kind: str) -> None:
        key = self._normalize_kind(kind)
        self._edge_factories.pop(key, None)

    def set_default_node_factory(self, factory: NodeItemFactory) -> None:
        self._default_node_factory = factory

    def set_default_edge_factory(self, factory: EdgeItemFactory) -> None:
        self._default_edge_factory = factory

    def set_connection_preview_factory(
        self,
        factory: ConnectionPreviewFactory,
    ) -> None:
        self._connection_preview_factory = factory

    def set_connection_preview_style(
        self,
        style: ConnectionPreviewStyleOptions,
    ) -> None:
        self._connection_preview_factory = lambda *_args: style

    def connection_preview_style(
        self,
        *,
        direction: str,
        source: Optional[NodeData],
        source_port: Optional[str],
        target: Optional[NodeData],
        target_port: Optional[str],
        view: Optional["CanvasView"] = None,
    ) -> ConnectionPreviewStyleOptions:
        return self._connection_preview_factory(
            direction,
            source,
            source_port,
            target,
            target_port,
            view,
        )

    def create_node_item(
        self,
        view: "CanvasView",
        node: NodeData,
    ) -> CanvasNodeItem:
        key = self._normalize_kind(self._resolve_node_kind(node))
        factory = self._node_factories.get(key, self._default_node_factory)
        return factory(node, view)

    def create_edge_item(
        self,
        view: "CanvasView",
        edge: EdgeData,
        source_item: CanvasNodeItem,
        target_item: CanvasNodeItem,
    ) -> CanvasEdgeItem:
        factory = self._edge_factories.get(
            self._normalize_kind(self._resolve_edge_kind(edge)),
            self._default_edge_factory,
        )
        return factory(edge, source_item, target_item, view)

    def _resolve_node_kind(self, node: NodeData) -> str:
        node_type = getattr(node, "node_type", "default")
        return node_type

    def _resolve_edge_kind(self, edge: EdgeData) -> str:
        edge_type = getattr(edge, "edge_type", "default")
        if edge_type and edge_type != "default":
            return edge_type
        metadata = edge.metadata or {}
        if isinstance(metadata, dict):
            canvas_meta = metadata.get("canvas")
            if isinstance(canvas_meta, dict):
                value = self._first_non_empty(
                    canvas_meta,
                    ("kind", "type", "style", "edge"),
                )
                if value:
                    return value
            value = self._first_non_empty(
                metadata,
                ("canvas_kind", "ui_kind", "kind"),
            )
            if value:
                return value
        return "default"

    def _normalize_kind(self, kind: str) -> str:
        text = str(kind).strip()
        return text.casefold() if text else "default"

    def _first_non_empty(
        self,
        mapping: Dict[str, object],
        keys: tuple[str, ...],
    ) -> Optional[str]:
        for key in keys:
            value = mapping.get(key)
            if isinstance(value, str):
                normalized = value.strip()
                if normalized:
                    return normalized
        return None


_DEFAULT_CONNECTION_PREVIEW_STYLE = ConnectionPreviewStyleOptions()


def _default_connection_preview_factory(
    direction: str,
    source: Optional[NodeData],
    source_port: Optional[str],
    target: Optional[NodeData],
    target_port: Optional[str],
    view: Optional["CanvasView"],
) -> ConnectionPreviewStyleOptions:
    return _DEFAULT_CONNECTION_PREVIEW_STYLE


canvas_registry = CanvasRegistry()

__all__ = [
    "CanvasRegistry",
    "ConnectionPreviewStyleOptions",
    "EdgeItemFactory",
    "NodeItemFactory",
    "canvas_registry",
]
