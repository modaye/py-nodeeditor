"""Interactive view: pan, zoom, selection, and connection drags.

Graph rules live on ``CanvasController``. This class only tracks the pointer
and asks the controller to commit a move or a connection.
"""

from __future__ import annotations

from typing import Iterable

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QDragEnterEvent,
    QDragMoveEvent,
    QDropEvent,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPixmap,
    QWheelEvent,
)
from PySide6.QtWidgets import QFrame, QGraphicsScene, QGraphicsView, QMenu

from .controller import CanvasController
from .gesture import ConnectionGesture
from .graphics import CanvasEdgeItem, CanvasNodeItem
from .layout import arrange_nodes
from .models import EdgeData, NodeData
from .policy import (
    ConnectResult,
    PortHit,
    nearest_port,
    preferred_port,
)
from .registry import CanvasRegistry, canvas_registry

__all__ = ["CanvasView", "TOOL_DROP_MIME"]

_MIN_ZOOM = 0.2
_MAX_ZOOM = 2.0
_FIT_MAX_ZOOM = 1.25
_DOT_SPACING = 24
TOOL_DROP_MIME = "application/x-alteryx-flow-tool"


class CanvasView(QGraphicsView):
    """Node canvas with React Flow style navigation and connecting.

    The wheel zooms toward the pointer. Dragging empty canvas pans. Shift-drag
    on empty canvas selects. Drag a port to connect; the wire snaps to a
    compatible port. Drag either end of an existing wire to reconnect it.
    ``Ctrl+L`` arranges left to right, ``Ctrl+Shift+L`` top to bottom.
    """

    selection_changed = Signal(list, list)
    graph_changed = Signal()
    connection_finished = Signal(object)
    node_double_clicked = Signal(str)
    tool_dropped = Signal(str, float, float)
    node_menu = Signal(object, str)
    connection_hint = Signal(str)

    def __init__(
        self,
        parent=None,
        *,
        enable_default_shortcuts: bool = True,
        registry: CanvasRegistry | None = None,
    ) -> None:
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self._scene.setSceneRect(-8000, -8000, 16000, 16000)
        # A moving node editor is faster without a spatial index: the index is
        # rebuilt on every drag, and these graphs are far below where it pays off.
        self._scene.setItemIndexMethod(QGraphicsScene.ItemIndexMethod.NoIndex)
        self.setScene(self._scene)
        self.setBackgroundBrush(_dot_brush())
        self.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setRubberBandSelectionMode(Qt.ItemSelectionMode.IntersectsItemShape)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)

        self._controller: CanvasController | None = None
        self._registry = registry or canvas_registry
        self._node_items: dict[str, CanvasNodeItem] = {}
        self._edge_items: dict[str, CanvasEdgeItem] = {}
        self._gesture = ConnectionGesture(self._scene)
        self._hidden_edge: CanvasEdgeItem | None = None
        self._drag_starts: dict[str, tuple[float, float]] = {}
        self._dirty_nodes: set[str] = set()
        self._edge_update_posted = False
        self._syncing_selection = False
        self._panning = False
        self._pan_moved = False
        self._selecting = False
        self._pan_start = QPoint()
        self._shortcuts = enable_default_shortcuts
        self._initialized = False
        self._scene.selectionChanged.connect(self._handle_scene_selection_changed)
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # type: ignore[override]
        """Accept a tool dragged from a palette. Other drags keep the default."""

        if _accept_tool_drag(event):
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:  # type: ignore[override]
        """Keep accepting the tool while it moves across the canvas."""

        if _accept_tool_drag(event):
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:  # type: ignore[override]
        """Place a dragged tool at the pointer, in scene coordinates."""

        tool_id = _tool_id(event)
        if tool_id is None:
            super().dropEvent(event)
            return
        scene = self.mapToScene(event.position().toPoint())
        self.tool_dropped.emit(tool_id, scene.x(), scene.y())
        event.setDropAction(Qt.DropAction.CopyAction)
        event.accept()

    @property
    def controller(self) -> CanvasController | None:
        return self._controller

    @property
    def registry(self) -> CanvasRegistry:
        return self._registry

    def set_registry(self, registry: CanvasRegistry) -> None:
        if registry is self._registry:
            return
        self._registry = registry
        self.refresh()

    def set_controller(self, controller: CanvasController) -> None:
        if controller is not self._controller:
            self._clear_scene_items()
        self._controller = controller
        self.refresh()
        if not self._initialized:
            self._initialized = True

    def set_default_shortcuts_enabled(self, enabled: bool) -> None:
        self._shortcuts = enabled

    def refresh(self) -> None:
        """Rebuild items that were added or removed. Drags do not call this."""

        if self._controller is None:
            return
        self._gesture.cancel()
        self._restore_hidden_edge()
        state = self._controller.state
        self._drop_missing_edges(set(state.edges))
        self._sync_nodes(state.nodes)
        self._sync_edges(state.edges)
        self._apply_selection(state.selection.nodes, state.selection.edges)
        self._expand_scene_rect()
        self._emit_selection()
        self.graph_changed.emit()

    def sync_node(self, node_id: str) -> None:
        """Repaint one node from the controller after its metadata changes."""

        if self._controller is None:
            return
        node = self._controller.state.nodes.get(node_id)
        item = self._node_items.get(node_id)
        if node is None or item is None:
            return
        item.update_from_node(node)

    def focus_nodes(self, node_ids: list[str]) -> None:
        """Select these nodes and notify listeners without rebuilding the scene."""

        if self._controller is None:
            return
        self._controller.set_selection(nodes=node_ids, edges=())
        selection = self._controller.state.selection
        self._apply_selection(selection.nodes, selection.edges)
        self._emit_selection()

    def add_node(self, node: NodeData) -> NodeData:
        if self._controller is None:
            raise RuntimeError("Canvas controller is not set")
        created = self._controller.add_node(node)
        self.refresh()
        return created

    def add_edge(self, edge: EdgeData) -> EdgeData:
        if self._controller is None:
            raise RuntimeError("Canvas controller is not set")
        created = self._controller.add_edge(edge)
        self.refresh()
        return created

    def delete_selection(self) -> None:
        if self._controller is None:
            return
        state = self._controller.state
        self._controller.delete(state.selection.nodes, state.selection.edges)
        self.refresh()

    def select_all(self) -> None:
        if self._controller is None:
            return
        state = self._controller.state
        self._controller.set_selection(
            nodes=state.node_ids(),
            edges=state.edge_ids(),
            append=False,
        )
        self.refresh()

    def focus_on_selection(self) -> None:
        self._fit_rect(self._selection_rect(), max_zoom=_FIT_MAX_ZOOM)

    def fit_to_contents(self) -> None:
        """Frame every item without stretching the view."""

        rect = self._scene.itemsBoundingRect()
        if rect.isNull():
            return
        self._fit_rect(rect, max_zoom=_FIT_MAX_ZOOM)

    def arrange(self, direction: str = "horizontal") -> None:
        """Lay out connected tools and leave annotation nodes where they are.

        ``direction`` is ``horizontal`` (left to right) or ``vertical``
        (top to bottom). The move is one undo step.
        """

        if self._controller is None:
            return
        if direction not in ("horizontal", "vertical"):
            raise ValueError("direction must be 'horizontal' or 'vertical'")
        state = self._controller.state
        sizes: dict[str, tuple[float, float]] = {}
        origins: list[tuple[float, float]] = []
        for node_id, node in state.nodes.items():
            if node.node_type == "annotation":
                continue
            item = self._node_items.get(node_id)
            if item is None:
                continue
            sizes[node_id] = item.card_size
            origins.append(node.position)
        if not sizes:
            return
        positions = arrange_nodes(
            sizes,
            (
                (edge.source, edge.target)
                for edge in state.edges.values()
            ),
            direction=direction,
        )
        min_x = min(point[0] for point in origins)
        min_y = min(point[1] for point in origins)
        laid_min_x = min(point[0] for point in positions.values())
        laid_min_y = min(point[1] for point in positions.values())
        updates = {
            node_id: (
                point[0] - laid_min_x + min_x,
                point[1] - laid_min_y + min_y,
            )
            for node_id, point in positions.items()
        }
        self._controller.move_nodes(updates)
        self.refresh()
        self._fit_nodes(list(sizes))

    def undo(self) -> None:
        if self._controller is None:
            return
        self._controller.undo()
        self.refresh()

    def redo(self) -> None:
        if self._controller is None:
            return
        self._controller.redo()
        self.refresh()

    def copy_selection(self) -> None:
        if self._controller is not None:
            self._controller.copy_selection()

    def cut_selection(self) -> None:
        if self._controller is None:
            return
        self._controller.cut_selection()
        self.refresh()

    def duplicate_selection(self) -> None:
        """Copy the selection and paste it one step down and to the right."""

        self.copy_selection()
        self.paste_selection()

    def disconnect_node(self, node_id: str) -> None:
        """Remove every wire touching ``node_id`` in one undo step."""

        if self._controller is None:
            return
        edge_ids = [
            edge.id
            for edge in self._controller.state.edges.values()
            if edge.source == node_id or edge.target == node_id
        ]
        self._controller.delete([], edge_ids)
        self.refresh()

    def zoom_by(self, factor: float) -> None:
        """Scale the view around its current transform, clamped to the zoom limits."""

        current = self.transform().m11()
        if current <= 0:
            return
        target = min(_MAX_ZOOM, max(_MIN_ZOOM, current * factor))
        applied = target / current
        if abs(applied - 1.0) > 1e-6:
            self.scale(applied, applied)

    def paste_selection(self) -> None:
        if self._controller is None:
            return
        if self._controller.paste() is not None:
            self.refresh()

    def wheelEvent(self, event: QWheelEvent) -> None:
        factor = _zoom_factor(event)
        if factor == 1.0:
            event.accept()
            return
        current = self.transform().m11()
        target = min(_MAX_ZOOM, max(_MIN_ZOOM, current * factor))
        applied = target / current
        if abs(applied - 1.0) > 1e-6:
            self.scale(applied, applied)
        event.accept()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._blank(event.pos()):
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                self._selecting = True
                self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
                super().mousePressEvent(event)
                return
            self._panning = True
            self._pan_moved = False
            self._pan_start = event.pos()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton and self._controller is not None:
            self._drag_starts.clear()
            for node_id in self._selected_node_ids():
                node = self._controller.state.nodes.get(node_id)
                if node is not None:
                    self._drag_starts[node_id] = node.position
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._panning:
            delta = event.pos() - self._pan_start
            if delta.manhattanLength() > 3:
                self._pan_moved = True
            self._pan_start = event.pos()
            self.horizontalScrollBar().setValue(
                self.horizontalScrollBar().value() - delta.x()
            )
            self.verticalScrollBar().setValue(
                self.verticalScrollBar().value() - delta.y()
            )
            event.accept()
            return
        super().mouseMoveEvent(event)
        if self._dirty_nodes:
            self._update_dirty_edges()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._panning:
            moved = self._pan_moved
            self._stop_pan()
            if not moved:
                self._clear_selection_on_canvas()
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton and self._selecting:
            super().mouseReleaseEvent(event)
            self._selecting = False
            self.setDragMode(QGraphicsView.DragMode.NoDrag)
            event.accept()
            return
        super().mouseReleaseEvent(event)
        if event.button() == Qt.MouseButton.LeftButton:
            self._commit_moves()

    def contextMenuEvent(self, event) -> None:  # type: ignore[override]
        if self._controller is None:
            super().contextMenuEvent(event)
            return
        node_id, edge_id = self._target_at(event.pos())
        menu = QMenu(self)
        if node_id is not None:
            self._controller.set_selection(nodes=[node_id], edges=[])
            self.refresh()
            menu.addAction("复制一份", self.duplicate_selection)
            menu.addAction("复制", self.copy_selection)
            menu.addAction("删除", self.delete_selection)
            menu.addAction("断开连线", lambda node_id=node_id: self.disconnect_node(node_id))
            self.node_menu.emit(menu, node_id)
        elif edge_id is not None:
            self._controller.set_selection(nodes=[], edges=[edge_id])
            self.refresh()
            menu.addAction("删除连线", self.delete_selection)
        else:
            menu.addAction("从左到右排列", lambda: self.arrange("horizontal"))
            menu.addAction("从上到下排列", lambda: self.arrange("vertical"))
            menu.addAction("适应画面", self.fit_to_contents)
        menu.exec(event.globalPos())
        event.accept()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if not self._shortcuts:
            super().keyPressEvent(event)
            return
        modifiers = event.modifiers()
        ctrl = bool(modifiers & Qt.KeyboardModifier.ControlModifier)
        shift = bool(modifiers & Qt.KeyboardModifier.ShiftModifier)
        key = event.key()
        if key == Qt.Key.Key_Escape and self._gesture.active:
            self._cancel_gesture()
            event.accept()
            return
        if key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.delete_selection()
            event.accept()
            return
        if ctrl and key == Qt.Key.Key_A:
            self.select_all()
            event.accept()
            return
        if key == Qt.Key.Key_Escape and self._controller is not None:
            self._controller.clear_selection()
            self.refresh()
            event.accept()
            return
        if ctrl and key == Qt.Key.Key_Z and not shift:
            self.undo()
            event.accept()
            return
        if ctrl and ((key == Qt.Key.Key_Z and shift) or key == Qt.Key.Key_Y):
            self.redo()
            event.accept()
            return
        if ctrl and key == Qt.Key.Key_C:
            self.copy_selection()
            event.accept()
            return
        if ctrl and key == Qt.Key.Key_X:
            self.cut_selection()
            event.accept()
            return
        if ctrl and key == Qt.Key.Key_V:
            self.paste_selection()
            event.accept()
            return
        if ctrl and key == Qt.Key.Key_D:
            self.duplicate_selection()
            event.accept()
            return
        if ctrl and key in (Qt.Key.Key_Plus, Qt.Key.Key_Equal):
            self.zoom_by(1.15)
            event.accept()
            return
        if ctrl and key == Qt.Key.Key_Minus:
            self.zoom_by(1 / 1.15)
            event.accept()
            return
        if ctrl and key == Qt.Key.Key_0:
            self.fit_to_contents()
            event.accept()
            return
        if ctrl and key == Qt.Key.Key_L:
            self.arrange("vertical" if shift else "horizontal")
            event.accept()
            return
        super().keyPressEvent(event)

    def _blank(self, pos: QPoint) -> bool:
        return self.itemAt(pos) is None

    def _connection_caption(self, origin: PortHit, snapped: PortHit | None, valid: bool, reason: str) -> str:
        left = self._endpoint_text(origin.node_id, origin.port_id, origin.direction)
        if snapped is None:
            return f"{left}  →  拖到端口"
        right = self._endpoint_text(snapped.node_id, snapped.port_id, snapped.direction)
        if valid:
            return f"{left}  →  {right}"
        detail = {
            "type": "类型不一致",
            "cycle": "会形成环",
            "self": "不能连接自身",
            "duplicate": "已经连接",
            "missing-port": "没有端口",
            "unknown-node": "找不到节点",
        }.get(reason, reason)
        return f"{left}  →  {right}（{detail}）"

    def _endpoint_text(self, node_id: str, port_id: str, direction: str) -> str:
        if self._controller is None:
            return port_id
        node = self._controller.state.nodes.get(node_id)
        title = node.title if node is not None and node.title else node_id
        columns = self._metadata_columns(node_id, port_id, direction)
        if not columns:
            return f"{title} · {port_id}"
        shown = ", ".join(columns[:4])
        if len(columns) > 4:
            shown += "…"
        return f"{title} · {port_id}（{shown}）"

    def _metadata_columns(self, node_id: str, port_id: str, direction: str) -> list[str]:
        if self._controller is None:
            return []
        if direction == "input":
            names: list[str] = []
            for edge in self._controller.state.edges.values():
                if edge.target == node_id and edge.target_port == port_id:
                    names.extend(self._stored_columns(edge.source, edge.source_port or ""))
            return names
        return self._stored_columns(node_id, port_id)

    def _stored_columns(self, node_id: str, port_id: str) -> list[str]:
        if self._controller is None:
            return []
        node = self._controller.state.nodes.get(node_id)
        if node is None:
            return []
        fields = node.metadata.get("output_fields")
        if isinstance(fields, dict):
            raw = fields.get(port_id) or fields.get("Output") or ()
            names = [str(item.get("name")) for item in raw if isinstance(item, dict) and item.get("name")]
            if names:
                return names
        stored = node.metadata.get("output_columns")
        if isinstance(stored, dict):
            raw_names = stored.get(port_id) or stored.get("Output") or ()
            if isinstance(raw_names, (list, tuple)):
                return [str(name) for name in raw_names if str(name)]
        return []

    def _target_at(self, pos: QPoint) -> tuple[str | None, str | None]:
        item = self.itemAt(pos)
        while item is not None:
            if isinstance(item, CanvasNodeItem):
                return item.node_data.id, None
            if isinstance(item, CanvasEdgeItem):
                return None, item.edge_data.id
            item = item.parentItem()
        return None, None

    def _clear_selection_on_canvas(self) -> None:
        if self._controller is None:
            return
        self._controller.clear_selection()
        self._apply_selection((), ())
        self._emit_selection()

    def _stop_pan(self) -> None:
        self._panning = False
        self._pan_moved = False
        self.setCursor(Qt.CursorShape.OpenHandCursor)

    def _fit_nodes(self, node_ids: list[str]) -> None:
        rect = None
        for node_id in node_ids:
            item = self._node_items.get(node_id)
            if item is None:
                continue
            item_rect = item.mapToScene(item.boundingRect()).boundingRect()
            rect = item_rect if rect is None else rect.united(item_rect)
        self._fit_rect(rect, max_zoom=_FIT_MAX_ZOOM)

    def _fit_rect(self, rect, *, max_zoom: float) -> None:
        if rect is None or rect.isNull():
            return
        self.fitInView(rect.adjusted(-80, -80, 80, 80), Qt.AspectRatioMode.KeepAspectRatio)
        scale = self.transform().m11()
        if scale > max_zoom:
            self.scale(max_zoom / scale, max_zoom / scale)

    def _selection_rect(self):
        if self._controller is None:
            return None
        rect = None
        for node_id in self._controller.state.selection.nodes:
            item = self._node_items.get(node_id)
            if item is None:
                continue
            item_rect = item.mapToScene(item.boundingRect()).boundingRect()
            rect = item_rect if rect is None else rect.united(item_rect)
        return rect

    def _sync_nodes(self, nodes) -> None:
        for node_id in list(self._node_items):
            if node_id not in nodes:
                item = self._node_items.pop(node_id)
                self._scene.removeItem(item)
        for node_id, node in nodes.items():
            item = self._node_items.get(node_id)
            if item is None:
                item = self._create_node_item(node)
                self._node_items[node_id] = item
                self._scene.addItem(item)
            else:
                item.update_from_node(node)

    def _drop_missing_edges(self, edge_ids: set[str]) -> None:
        for edge_id in list(self._edge_items):
            if edge_id not in edge_ids:
                self._remove_edge_item(edge_id)

    def _sync_edges(self, edges) -> None:
        for edge_id, edge in edges.items():
            source = self._node_items.get(edge.source)
            target = self._node_items.get(edge.target)
            item = self._edge_items.get(edge_id)
            if item is not None and not item.follows(source, target):
                self._remove_edge_item(edge_id)
                item = None
            if item is None:
                if source is None or target is None:
                    continue
                item = self._create_edge_item(edge, source, target)
                self._edge_items[edge_id] = item
                self._scene.addItem(item)
            else:
                item.update_from_edge(edge)

    def _remove_edge_item(self, edge_id: str) -> None:
        item = self._edge_items.pop(edge_id, None)
        if item is None:
            return
        if item is self._hidden_edge:
            self._hidden_edge = None
        item.detach()
        self._scene.removeItem(item)

    def _clear_scene_items(self) -> None:
        """Drop every node and wire before another graph is shown.

        Edge ids are reused across workflows. Keeping the old wire would leave
        it attached to nodes that have already left the scene.
        """

        self._gesture.cancel()
        self._restore_hidden_edge()
        for edge_id in list(self._edge_items):
            self._remove_edge_item(edge_id)
        for item in self._node_items.values():
            self._scene.removeItem(item)
        self._node_items.clear()
        self._drag_starts.clear()
        self._dirty_nodes.clear()

    def _apply_selection(
        self,
        selected_nodes: Iterable[str],
        selected_edges: Iterable[str],
    ) -> None:
        self._syncing_selection = True
        node_ids = set(selected_nodes)
        edge_ids = set(selected_edges)
        for node_id, item in self._node_items.items():
            blocked = item.blockSignals(True)
            item.setSelected(node_id in node_ids)
            item.blockSignals(blocked)
        for edge_id, item in self._edge_items.items():
            item.setSelected(edge_id in edge_ids)
        self._syncing_selection = False
        self._emphasize_connected_edges(node_ids)

    def _handle_scene_selection_changed(self) -> None:
        if self._controller is None or self._syncing_selection:
            return
        self._controller.set_selection(
            nodes=list(self._selected_node_ids()),
            edges=list(self._selected_edge_ids()),
        )
        self._emphasize_connected_edges(set(self._controller.state.selection.nodes))
        self._emit_selection()

    def _emphasize_connected_edges(self, selected_nodes: set[str]) -> None:
        """Brighten wires that touch the selected nodes and fade the rest."""

        for item in self._edge_items.values():
            if not selected_nodes:
                item.set_relation("normal")
            elif item.touches(selected_nodes):
                item.set_relation("linked")
            else:
                item.set_relation("dimmed")

    def _selected_node_ids(self) -> list[str]:
        return [node_id for node_id, item in self._node_items.items() if item.isSelected()]

    def _selected_edge_ids(self) -> list[str]:
        return [edge_id for edge_id, item in self._edge_items.items() if item.isSelected()]

    def _create_node_item(self, node: NodeData) -> CanvasNodeItem:
        item = self._registry.create_node_item(self, node)
        if not isinstance(item, CanvasNodeItem):
            raise TypeError("Node factory must return a CanvasNodeItem instance")
        item.double_clicked.connect(self.node_double_clicked.emit)
        item.position_changed.connect(self._remember_drag_origin)
        item.connection_drag_started.connect(self._begin_port_drag)
        item.connection_drag_moved.connect(self._move_port_drag)
        item.connection_drag_finished.connect(self._finish_port_drag)
        return item

    def _create_edge_item(
        self,
        edge: EdgeData,
        source_item: CanvasNodeItem,
        target_item: CanvasNodeItem,
    ) -> CanvasEdgeItem:
        item = self._registry.create_edge_item(self, edge, source_item, target_item)
        if not isinstance(item, CanvasEdgeItem):
            raise TypeError("Edge factory must return a CanvasEdgeItem instance")
        item.endpoint_drag_started.connect(self._begin_reconnect)
        item.endpoint_drag_moved.connect(self._move_port_drag)
        item.endpoint_drag_finished.connect(self._finish_port_drag)
        return item

    def _remember_drag_origin(self, node_id: str, _x: float, _y: float) -> None:
        if self._controller is not None and node_id not in self._drag_starts:
            node = self._controller.state.nodes.get(node_id)
            if node is not None:
                self._drag_starts[node_id] = node.position
        self._schedule_edge_update(node_id)

    def _schedule_edge_update(self, node_id: str) -> None:
        """Redraw wires after the scene finishes moving the node.

        Updating a path inside ``itemChange`` forces the spatial index to
        rebuild while the drag is still in progress, which is what makes
        dragging feel sticky.
        """

        self._dirty_nodes.add(node_id)
        if self._edge_update_posted:
            return
        self._edge_update_posted = True
        QTimer.singleShot(0, self._update_dirty_edges)

    def _update_dirty_edges(self) -> None:
        self._edge_update_posted = False
        dirty = self._dirty_nodes
        self._dirty_nodes = set()
        for item in self._edge_items.values():
            if item.touches(dirty):
                item.update_path()

    def _commit_moves(self) -> None:
        if self._controller is None or not self._drag_starts:
            self._drag_starts.clear()
            return
        updates: dict[str, tuple[float, float]] = {}
        for node_id, start in self._drag_starts.items():
            item = self._node_items.get(node_id)
            if item is None:
                continue
            current = item.pos()
            if abs(current.x() - start[0]) < 0.5 and abs(current.y() - start[1]) < 0.5:
                continue
            updates[node_id] = (current.x(), current.y())
        self._drag_starts.clear()
        if not updates:
            return
        self._controller.move_nodes(updates)
        for node_id, position in updates.items():
            item = self._node_items.get(node_id)
            if item is not None:
                item.commit_position(position[0], position[1])
        self.graph_changed.emit()

    def _begin_port_drag(
        self,
        node_id: str,
        direction: str,
        port_id: str,
        anchor: QPointF,
    ) -> None:
        origin = PortHit(node_id, direction, port_id, anchor.x(), anchor.y())
        self._gesture.begin(origin)
        self._follow_pointer(anchor)

    def _begin_reconnect(self, edge_id: str, endpoint: str, scene_pos: QPointF) -> None:
        item = self._edge_items.get(edge_id)
        if item is None or self._controller is None:
            return
        edge = item.edge_data
        origin = item.anchored_end(endpoint)
        if not origin.port_id:
            return
        item.setOpacity(0.0)
        self._hidden_edge = item
        self._gesture.begin(origin, (edge.id,))
        self._follow_pointer(scene_pos)

    def _move_port_drag(self, *_args) -> None:
        if not self._gesture.active:
            return
        scene_pos = self.mapToScene(self.mapFromGlobal(self.cursor().pos()))
        if len(_args) >= 1 and isinstance(_args[-1], QPointF):
            scene_pos = _args[-1]
        self._follow_pointer(scene_pos)

    def _finish_port_drag(self, *_args) -> None:
        if not self._gesture.active or self._controller is None:
            return
        request = self._gesture.request_for(self._gesture.snapped)
        replace_edges = self._gesture.replace_ids
        self._clear_emphasis()
        self._gesture.cancel()
        self.connection_hint.emit("")
        if request is None:
            self._restore_hidden_edge()
            self.connection_finished.emit(
                ConnectResult(accepted=False, reason="cancelled")
            )
            return
        result = self._controller.connect(
            request.source,
            request.target,
            source_port=request.source_port,
            target_port=request.target_port,
            replace_edges=replace_edges,
        )
        if result.accepted:
            self._hidden_edge = None
        else:
            self._restore_hidden_edge()
        self.refresh()
        self.connection_finished.emit(result)

    def _cancel_gesture(self) -> None:
        self._clear_emphasis()
        self._gesture.cancel()
        self._restore_hidden_edge()
        self.connection_hint.emit("")

    def _follow_pointer(self, scene_pos: QPointF) -> None:
        origin = self._gesture.origin
        controller = self._controller
        if origin is None or controller is None:
            return
        opposite = "input" if origin.direction == "output" else "output"
        snapped = nearest_port(
            self._port_hits(),
            scene_pos.x(),
            scene_pos.y(),
            radius=controller.policy.snap_radius,
            direction=opposite,
            exclude_nodes={origin.node_id},
        )
        if snapped is None:
            snapped = self._port_on_node_body(scene_pos, opposite, origin.node_id)
        request = self._gesture.request_for(snapped)
        valid = False
        reason = ""
        if request is not None:
            decision = controller.policy.evaluate(
                controller.state,
                request,
                ignore=self._gesture.replace_ids,
            )
            valid = decision.accepted or decision.reason == "duplicate"
            reason = decision.reason
        start = self._live_origin(origin)
        end = scene_pos if snapped is None else QPointF(snapped.x, snapped.y)
        if snapped is not None and not valid:
            color = "#DC2626"
            dashed = False
        elif snapped is None:
            color = "#94A3B8"
            dashed = True
        else:
            color = "#2563EB"
            dashed = False
        caption = self._connection_caption(origin, snapped, valid, reason)
        self._gesture.update(start, end, snapped, color=color, dashed=dashed, caption=caption)
        self.connection_hint.emit(caption)
        self._show_emphasis(origin, snapped, valid)

    def _port_on_node_body(
        self,
        scene_pos: QPointF,
        direction: str,
        exclude_node: str,
    ) -> PortHit | None:
        if self._controller is None:
            return None
        for item in self._scene.items(scene_pos):
            node = item if isinstance(item, CanvasNodeItem) else item.parentItem()
            if not isinstance(node, CanvasNodeItem) or node.node_id == exclude_node:
                continue
            port = preferred_port(
                self._controller.state,
                node.node_data,
                direction,
                self._controller.policy,
            )
            if port is None:
                return None
            for hit in node.port_hits():
                if hit.direction == direction and hit.port_id == port:
                    return hit
            return None
        return None

    def _port_hits(self) -> list[PortHit]:
        hits: list[PortHit] = []
        for item in self._node_items.values():
            hits.extend(item.port_hits())
        return hits

    def _live_origin(self, origin: PortHit) -> QPointF:
        item = self._node_items.get(origin.node_id)
        if item is None:
            return QPointF(origin.x, origin.y)
        return item.port_scene_position(origin.direction, origin.port_id)

    def _show_emphasis(
        self,
        origin: PortHit,
        snapped: PortHit | None,
        valid: bool,
    ) -> None:
        opposite = "input" if origin.direction == "output" else "output"
        for item in self._node_items.values():
            marks: dict[tuple[str, str], str] = {}
            if item.node_id == origin.node_id:
                marks[(origin.direction, origin.port_id)] = "active"
            else:
                for hit in item.port_hits():
                    if hit.direction != opposite:
                        continue
                    if (
                        snapped is not None
                        and hit.node_id == snapped.node_id
                        and hit.port_id == snapped.port_id
                    ):
                        marks[(hit.direction, hit.port_id)] = "active" if valid else "invalid"
                    else:
                        marks[(hit.direction, hit.port_id)] = "compatible"
            item.set_handle_emphasis(marks)

    def _clear_emphasis(self) -> None:
        for item in self._node_items.values():
            item.set_handle_emphasis({})

    def _restore_hidden_edge(self) -> None:
        if self._hidden_edge is None:
            return
        self._hidden_edge.setOpacity(1.0)
        self._hidden_edge = None

    def _expand_scene_rect(self) -> None:
        rect = self._scene.itemsBoundingRect().adjusted(-2000, -2000, 2000, 2000)
        self._scene.setSceneRect(self._scene.sceneRect().united(rect))

    def _emit_selection(self) -> None:
        if self._controller is None:
            return
        state = self._controller.state
        self.selection_changed.emit(
            list(state.selection.nodes),
            list(state.selection.edges),
        )


def _dot_brush() -> QBrush:
    """Scene-space dot grid painted by Qt, not by a Python loop each frame."""

    tile = QPixmap(_DOT_SPACING, _DOT_SPACING)
    tile.fill(QColor("#F8FAFC"))
    painter = QPainter(tile)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#CBD5E1"))
    painter.drawEllipse(QRectF(0.0, 0.0, 2.0, 2.0))
    painter.end()
    return QBrush(tile)


def _zoom_factor(event: QWheelEvent) -> float:
    angle = event.angleDelta().y()
    if angle > 0:
        return 1.12
    if angle < 0:
        return 1 / 1.12
    pixel = event.pixelDelta().y()
    if pixel == 0:
        return 1.0
    return pow(1.0015, pixel)


def _tool_id(event) -> str | None:
    mime = event.mimeData()
    if mime is None or not mime.hasFormat(TOOL_DROP_MIME):
        return None
    text = bytes(mime.data(TOOL_DROP_MIME)).decode("utf-8").strip()
    return text or None


def _accept_tool_drag(event: QDragEnterEvent | QDragMoveEvent) -> bool:
    """Accept a palette drag as a copy. Qt delivers these events to the view."""

    if _tool_id(event) is None:
        return False
    event.setDropAction(Qt.DropAction.CopyAction)
    event.accept()
    return True
