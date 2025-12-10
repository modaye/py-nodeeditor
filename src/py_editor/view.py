from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from PySide6.QtCore import QPoint, QPointF, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QWheelEvent,
)
from PySide6.QtWidgets import QGraphicsPathItem, QGraphicsScene, QGraphicsView

from .controller import CanvasController
from .graphics import (
    CanvasConnectionHandle,
    CanvasEdgeItem,
    CanvasNodeItem,
)
from .registry import (
    CanvasRegistry,
    ConnectionPreviewStyleOptions,
    canvas_registry,
)
from .models import EdgeData, NodeData

__all__ = ["CanvasView"]


class CanvasView(QGraphicsView):
    selection_changed = Signal(list, list)
    nodes_changed = Signal(list, list, list)
    edges_changed = Signal(list, list, list)

    node_clicked = Signal(str, Qt.MouseButton, QPointF)
    node_double_clicked = Signal(str)
    node_context_menu_requested = Signal(str, QPoint, QPointF)
    node_mouse_enter = Signal(str, QPointF)
    node_mouse_move = Signal(str, QPointF)
    node_mouse_leave = Signal(str)
    node_drag_started = Signal(str, QPointF)
    node_dragged = Signal(str, QPointF)
    node_drag_finished = Signal(str, QPointF)

    selection_drag_started = Signal(object)
    selection_dragged = Signal(object)
    selection_drag_finished = Signal(object)

    edge_clicked = Signal(str, Qt.MouseButton, QPointF)
    edge_double_clicked = Signal(str, QPointF)
    edge_context_menu_requested = Signal(str, QPoint, QPointF)
    edge_mouse_enter = Signal(str)
    edge_mouse_leave = Signal(str)

    connection_started = Signal(object)
    connection_updated = Signal(object)
    connection_finished = Signal(object)
    connection_succeeded = Signal(object)
    connection_failed = Signal(object)

    viewport_move_started = Signal(object)
    viewport_moved = Signal(object)
    viewport_move_finished = Signal(object)
    viewport_zoomed = Signal(object)

    pane_clicked = Signal(object)
    pane_context_menu_requested = Signal(object)
    pane_scrolled = Signal(object)

    initialized = Signal()

    def __init__(
        self,
        parent=None,
        *,
        enable_default_shortcuts: bool = True,
        registry: CanvasRegistry | None = None,
    ) -> None:
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        self.setBackgroundBrush(QColor("#F4F4F5"))
        self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        self.setTransformationAnchor(
            QGraphicsView.ViewportAnchor.AnchorUnderMouse
        )

        self._controller: CanvasController | None = None
        self._node_items: Dict[str, CanvasNodeItem] = {}
        self._edge_items: Dict[str, CanvasEdgeItem] = {}
        self._syncing_selection = False
        self._drag_starts: Dict[str, Tuple[float, float]] = {}
        self._panning = False
        self._pan_start = QPoint()
        self._pan_start_scene = QPointF()
        self._default_shortcuts_enabled = enable_default_shortcuts
        self._connection_source: str | None = None
        self._connection_source_port: str | None = None
        self._connection_target: str | None = None
        self._connection_target_port: str | None = None
        self._connection_direction: str | None = None
        self._connection_anchor = QPointF()
        self._connection_path: QGraphicsPathItem | None = None
        self._initialized = False
        self._registry = registry or canvas_registry

        self._scene.selectionChanged.connect(
            self._handle_scene_selection_changed
        )

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
        self._controller = controller
        self.refresh()
        if not self._initialized:
            self._initialized = True
            self.initialized.emit()

    def set_default_shortcuts_enabled(self, enabled: bool) -> None:
        self._default_shortcuts_enabled = enabled

    def refresh(self) -> None:
        if self._controller is None:
            return
        self._clear_connection_preview()
        state = self._controller.state
        self._sync_nodes(state.nodes)
        self._sync_edges(state.edges)
        self._apply_selection(
            state.selection.nodes,
            state.selection.edges,
        )
        self._update_scene_rect()
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

    def wheelEvent(self, event: QWheelEvent) -> None:
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            angle = event.angleDelta().y()
            if angle == 0:
                event.accept()
                return
            factor = 1.15 if angle > 0 else 1 / 1.15
            self.scale(factor, factor)
            anchor = self.mapToScene(event.position().toPoint())
            self.pane_scrolled.emit(
                {
                    "angle_delta": angle,
                    "scene_pos": anchor,
                }
            )
            self.viewport_zoomed.emit(
                {
                    "factor": factor,
                    "angle_delta": angle,
                    "scene_pos": anchor,
                    "scale": self.transform().m11(),
                }
            )
            event.accept()
            return
        super().wheelEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        clicked_item = self.itemAt(event.pos())
        if event.button() == Qt.MouseButton.MiddleButton:
            self._panning = True
            self._pan_start = event.pos()
            self._pan_start_scene = self.mapToScene(event.pos())
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            self.viewport_move_started.emit(
                {
                    "scene_pos": self._pan_start_scene,
                    "screen_pos": event.globalPosition(),
                }
            )
            event.accept()
            return
        if (
            event.button() == Qt.MouseButton.LeftButton
            and self._controller is not None
        ):
            self._drag_starts.clear()
            for node_id in self._selected_node_ids():
                node = self._controller.state.nodes.get(node_id)
                if node is None:
                    continue
                self._drag_starts[node_id] = node.position
        super().mousePressEvent(event)
        if (
            event.button() == Qt.MouseButton.LeftButton
            and clicked_item is None
        ):
            self.pane_clicked.emit(
                {
                    "scene_pos": self.mapToScene(event.pos()),
                    "screen_pos": event.globalPosition(),
                    "button": event.button(),
                }
            )

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.MiddleButton and self._panning:
            self._panning = False
            self.setCursor(Qt.CursorShape.ArrowCursor)
            self.viewport_move_finished.emit(
                {
                    "scene_pos": self.mapToScene(event.pos()),
                    "screen_pos": event.globalPosition(),
                }
            )
            event.accept()
            return
        super().mouseReleaseEvent(event)
        if (
            event.button() == Qt.MouseButton.LeftButton
            and self._controller is not None
        ):
            self._apply_move_commands()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._panning:
            delta = event.pos() - self._pan_start
            self._pan_start = event.pos()
            self.horizontalScrollBar().setValue(
                self.horizontalScrollBar().value() - delta.x()
            )
            self.verticalScrollBar().setValue(
                self.verticalScrollBar().value() - delta.y()
            )
            self.viewport_moved.emit(
                {
                    "scene_pos": self.mapToScene(event.pos()),
                    "delta": QPointF(-delta.x(), -delta.y()),
                }
            )
            event.accept()
            return
        super().mouseMoveEvent(event)

    def contextMenuEvent(self, event) -> None:  # type: ignore[override]
        item = self.itemAt(event.pos())
        if item is None:
            self.pane_context_menu_requested.emit(
                {
                    "scene_pos": self.mapToScene(event.pos()),
                    "screen_pos": event.globalPos(),
                }
            )
        super().contextMenuEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if self._default_shortcuts_enabled:
            modifiers = event.modifiers()
            ctrl = modifiers & Qt.KeyboardModifier.ControlModifier
            shift = modifiers & Qt.KeyboardModifier.ShiftModifier
            key = event.key()

            if key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
                self.delete_selection()
                event.accept()
                return
            if ctrl and key == Qt.Key.Key_A:
                self.select_all()
                event.accept()
                return
            if key == Qt.Key.Key_Escape:
                if self._controller is not None:
                    self._controller.clear_selection()
                    self.refresh()
                event.accept()
                return
            if ctrl and key == Qt.Key.Key_Z and not shift:
                self.undo()
                event.accept()
                return
            if ctrl and (
                (key == Qt.Key.Key_Z and shift)
                or key == Qt.Key.Key_Y
            ):
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
        super().keyPressEvent(event)

    def delete_selection(self) -> None:
        if self._controller is None:
            return
        state = self._controller.state
        selected_nodes = list(state.selection.nodes)
        selected_edges = list(state.selection.edges)
        if not selected_nodes and not selected_edges:
            return
        edges_to_remove: list[str] = []
        if selected_edges:
            for edge_id in selected_edges:
                edge = state.edges.get(edge_id)
                if edge is None:
                    continue
                if (
                    edge.source in selected_nodes
                    or edge.target in selected_nodes
                ):
                    continue
                edges_to_remove.append(edge_id)
        if selected_nodes:
            self._controller.remove_nodes(selected_nodes)
        if edges_to_remove:
            self._controller.remove_edges(edges_to_remove)
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
        if self._controller is None:
            return
        selection = list(self._controller.state.selection.nodes)
        if not selection:
            return
        rect = None
        for node_id in selection:
            item = self._node_items.get(node_id)
            if item is None:
                continue
            item_rect = item.mapToScene(item.boundingRect()).boundingRect()
            rect = item_rect if rect is None else rect.united(item_rect)
        if rect is not None:
            expanded = rect.adjusted(-80, -80, 80, 80)
            self.fitInView(
                expanded,
                Qt.AspectRatioMode.IgnoreAspectRatio,
            )

    def fit_to_contents(self) -> None:
        if self._scene.items():
            rect = self._scene.itemsBoundingRect()
            self.fitInView(
                rect.adjusted(-120, -120, 120, 120),
                Qt.AspectRatioMode.IgnoreAspectRatio,
            )

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
        if self._controller is None:
            return
        self._controller.copy_selection()

    def cut_selection(self) -> None:
        if self._controller is None:
            return
        self._controller.cut_selection()
        self.refresh()

    def paste_selection(self) -> None:
        if self._controller is None:
            return
        payload = self._controller.paste()
        if payload is None:
            return
        self.refresh()

    def _sync_nodes(self, nodes: Mapping[str, NodeData]) -> None:
        removed_nodes: List[NodeData] = []
        for node_id in list(self._node_items.keys()):
            if node_id not in nodes:
                item = self._node_items.pop(node_id)
                removed_nodes.append(item.node_data)
                self._scene.removeItem(item)
        added_nodes: List[NodeData] = []
        updated_nodes: List[NodeData] = []
        for node_id, node in nodes.items():
            item = self._node_items.get(node_id)
            if item is None:
                item = self._create_node_item(node)
                self._node_items[node_id] = item
                self._scene.addItem(item)
                added_nodes.append(node.clone())
            else:
                if item.node_data != node:
                    updated_nodes.append(node.clone())
                item.update_from_node(node)
        if added_nodes or updated_nodes or removed_nodes:
            self.nodes_changed.emit(
                added_nodes,
                updated_nodes,
                removed_nodes,
            )

    def _sync_edges(self, edges: Mapping[str, EdgeData]) -> None:
        removed_edges: List[EdgeData] = []
        for edge_id in list(self._edge_items.keys()):
            if edge_id not in edges:
                item = self._edge_items.pop(edge_id)
                removed_edges.append(item.edge_data)
                self._scene.removeItem(item)
        added_edges: List[EdgeData] = []
        updated_edges: List[EdgeData] = []
        for edge_id, edge in edges.items():
            item = self._edge_items.get(edge_id)
            if item is None:
                source = self._node_items.get(edge.source)
                target = self._node_items.get(edge.target)
                if source is None or target is None:
                    continue
                item = self._create_edge_item(edge, source, target)
                self._edge_items[edge_id] = item
                self._scene.addItem(item)
                added_edges.append(edge.clone())
            else:
                if item.edge_data != edge:
                    updated_edges.append(edge.clone())
                item.update_from_edge(edge)
        if added_edges or updated_edges or removed_edges:
            self.edges_changed.emit(
                added_edges,
                updated_edges,
                removed_edges,
            )

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

    def _handle_scene_selection_changed(self) -> None:
        if self._controller is None or self._syncing_selection:
            return
        selected_nodes = list(self._selected_node_ids())
        selected_edges = list(self._selected_edge_ids())
        self._controller.set_selection(
            nodes=selected_nodes,
            edges=selected_edges,
        )
        self._emit_selection()

    def _selected_node_ids(self) -> Iterable[str]:
        for node_id, item in self._node_items.items():
            if item.isSelected():
                yield node_id

    def _selected_edge_ids(self) -> Iterable[str]:
        for edge_id, item in self._edge_items.items():
            if item.isSelected():
                yield edge_id

    def _create_node_item(self, node: NodeData) -> CanvasNodeItem:
        item = self._registry.create_node_item(self, node)
        if not isinstance(item, CanvasNodeItem):  # defensive guard
            raise TypeError(
                "Node factory must return a CanvasNodeItem instance"
            )
        self._configure_node_item(item)
        return item

    def _configure_node_item(self, item: CanvasNodeItem) -> None:
        item.position_changed.connect(self._handle_node_position_changed)
        item.double_clicked.connect(self.node_double_clicked.emit)
        item.clicked.connect(self._handle_node_clicked)
        item.context_menu_requested.connect(
            self._handle_node_context_menu
        )
        item.hover_entered.connect(self._handle_node_hover_entered)
        item.hover_left.connect(self._handle_node_hover_left)
        item.hover_moved.connect(self._handle_node_hover_moved)
        item.drag_started.connect(self._handle_node_drag_started)
        item.drag_moved.connect(self._handle_node_drag_moved)
        item.drag_finished.connect(self._handle_node_drag_finished)
        item.connection_drag_started.connect(
            self._handle_connection_drag_started
        )
        item.connection_drag_moved.connect(
            self._handle_connection_drag_moved
        )
        item.connection_drag_finished.connect(
            self._handle_connection_drag_finished
        )

    def _create_edge_item(
        self,
        edge: EdgeData,
        source_item: CanvasNodeItem,
        target_item: CanvasNodeItem,
    ) -> CanvasEdgeItem:
        item = self._registry.create_edge_item(
            self,
            edge,
            source_item,
            target_item,
        )
        if not isinstance(item, CanvasEdgeItem):
            raise TypeError(
                "Edge factory must return a CanvasEdgeItem instance"
            )
        self._configure_edge_item(item)
        return item

    def _configure_edge_item(self, item: CanvasEdgeItem) -> None:
        item.clicked.connect(self._handle_edge_clicked)
        item.double_clicked.connect(self._handle_edge_double_clicked)
        item.context_menu_requested.connect(
            self._handle_edge_context_menu
        )
        item.hover_entered.connect(self._handle_edge_hover_entered)
        item.hover_left.connect(self._handle_edge_hover_left)

    def _handle_node_clicked(
        self,
        node_id: str,
        button: Qt.MouseButton,
        scene_pos: QPointF,
    ) -> None:
        self.node_clicked.emit(node_id, button, scene_pos)

    def _handle_node_context_menu(
        self,
        node_id: str,
        screen_pos: QPoint,
        scene_pos: QPointF,
    ) -> None:
        self.node_context_menu_requested.emit(node_id, screen_pos, scene_pos)

    def _handle_node_hover_entered(
        self,
        node_id: str,
        scene_pos: QPointF,
    ) -> None:
        self.node_mouse_enter.emit(node_id, scene_pos)

    def _handle_node_hover_left(self, node_id: str) -> None:
        self.node_mouse_leave.emit(node_id)

    def _handle_node_hover_moved(
        self,
        node_id: str,
        scene_pos: QPointF,
    ) -> None:
        self.node_mouse_move.emit(node_id, scene_pos)

    def _handle_node_drag_started(
        self,
        node_id: str,
        scene_pos: QPointF,
    ) -> None:
        self.node_drag_started.emit(node_id, scene_pos)
        if self._drag_starts:
            snapshot = {
                key: (value[0], value[1])
                for key, value in self._drag_starts.items()
            }
        else:
            snapshot = self._selection_positions()
        if snapshot:
            self.selection_drag_started.emit(snapshot)

    def _handle_node_drag_moved(
        self,
        node_id: str,
        scene_pos: QPointF,
    ) -> None:
        self.node_dragged.emit(node_id, scene_pos)
        positions = self._selection_positions()
        if positions:
            self.selection_dragged.emit(positions)

    def _handle_node_drag_finished(
        self,
        node_id: str,
        scene_pos: QPointF,
    ) -> None:
        self.node_drag_finished.emit(node_id, scene_pos)

    def _selection_positions(self) -> Dict[str, Tuple[float, float]]:
        positions: Dict[str, Tuple[float, float]] = {}
        for node_id in self._selected_node_ids():
            item = self._node_items.get(node_id)
            if item is None:
                continue
            pos = item.pos()
            positions[node_id] = (pos.x(), pos.y())
        return positions

    def _handle_edge_clicked(
        self,
        edge_id: str,
        button: Qt.MouseButton,
        scene_pos: QPointF,
    ) -> None:
        self.edge_clicked.emit(edge_id, button, scene_pos)

    def _handle_edge_double_clicked(
        self,
        edge_id: str,
        scene_pos: QPointF,
    ) -> None:
        self.edge_double_clicked.emit(edge_id, scene_pos)

    def _handle_edge_context_menu(
        self,
        edge_id: str,
        screen_pos: QPoint,
        scene_pos: QPointF,
    ) -> None:
        self.edge_context_menu_requested.emit(edge_id, screen_pos, scene_pos)

    def _handle_edge_hover_entered(self, edge_id: str) -> None:
        self.edge_mouse_enter.emit(edge_id)

    def _handle_edge_hover_left(self, edge_id: str) -> None:
        self.edge_mouse_leave.emit(edge_id)

    def _handle_connection_drag_started(
        self,
        node_id: str,
        direction: str,
        port_id: str,
        anchor: QPointF,
    ) -> None:
        self._clear_connection_preview()
        if self._controller is None:
            return
        self._connection_direction = direction
        if direction == "output":
            self._connection_source = node_id
            self._connection_source_port = port_id
            self._connection_target = None
            self._connection_target_port = None
        else:
            self._connection_source = None
            self._connection_target = node_id
            self._connection_source_port = None
            self._connection_target_port = port_id
        self._connection_anchor = anchor
        self._ensure_connection_path()
        self._apply_connection_preview_style(
            node_id=node_id,
            direction=direction,
            port_id=port_id,
        )
        self._update_connection_preview(anchor)
        self.connection_started.emit(
            {
                "node": node_id,
                "direction": direction,
                "port": port_id,
                "scene_pos": anchor,
                "source": self._connection_source,
                "source_port": self._connection_source_port,
                "target": self._connection_target,
                "target_port": self._connection_target_port,
            }
        )

    def _handle_connection_drag_moved(
        self,
        _node_id: str,
        _direction: str,
        _port_id: str,
        scene_pos: QPointF,
    ) -> None:
        if self._connection_direction is None:
            return
        self._ensure_connection_path()
        self._apply_connection_preview_style(
            node_id=_node_id,
            direction=_direction,
            port_id=_port_id,
        )
        self._update_connection_preview(scene_pos)
        self.connection_updated.emit(
            {
                "direction": self._connection_direction,
                "scene_pos": scene_pos,
                "source": self._connection_source,
                "source_port": self._connection_source_port,
                "target": self._connection_target,
                "target_port": self._connection_target_port,
            }
        )

    def _handle_connection_drag_finished(
        self,
        _node_id: str,
        _direction: str,
        _port_id: str,
        scene_pos: QPointF,
    ) -> None:
        result = self._complete_connection_drag(scene_pos)
        self.connection_finished.emit(result)
        if result.get("success"):
            self.connection_succeeded.emit(result)
        else:
            self.connection_failed.emit(result)
        self._clear_connection_preview()

    def _complete_connection_drag(
        self,
        scene_pos: QPointF,
    ) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "success": False,
            "scene_pos": scene_pos,
            "direction": self._connection_direction,
            "source": self._connection_source,
            "source_port": self._connection_source_port,
            "target": self._connection_target,
            "target_port": self._connection_target_port,
        }
        if self._controller is None or self._connection_direction is None:
            result["reason"] = "unavailable"
            return result
        source_id = self._connection_source
        source_port = self._connection_source_port
        target_id = self._connection_target
        target_port = self._connection_target_port
        if self._connection_direction == "output":
            if source_id is None:
                result["reason"] = "missing-source"
                return result
            drop = self._resolve_drop_target(
                scene_pos,
                prefer="input",
                exclude={source_id},
            )
            if drop is None:
                result["reason"] = "no-target"
                return result
            target_id, target_port = drop
            if target_port is None:
                result["reason"] = "missing-target-port"
                return result
        else:
            if target_id is None:
                result["reason"] = "missing-target"
                return result
            drop = self._resolve_drop_target(
                scene_pos,
                prefer="output",
                exclude={target_id},
            )
            if drop is None:
                result["reason"] = "no-source"
                return result
            source_id, source_port = drop
            if source_port is None:
                result["reason"] = "missing-source-port"
                return result
        if not source_id or not target_id or source_id == target_id:
            result["reason"] = "invalid-pair"
            return result
        result["source"] = source_id
        result["source_port"] = source_port
        result["target"] = target_id
        result["target_port"] = target_port
        # Avoid creating duplicate edges between the same pair of nodes.
        for edge in self._controller.state.edges.values():
            if (
                edge.source == source_id
                and edge.target == target_id
                and edge.source_port == source_port
                and edge.target_port == target_port
            ):
                result["reason"] = "duplicate"
                return result
        try:
            created = self._controller.create_edge(
                source_id,
                target_id,
                source_port=source_port,
                target_port=target_port,
            )
        except KeyError:
            result["reason"] = "invalid-nodes"
            return result
        result["success"] = True
        result["edge_id"] = created.id
        result["edge"] = created
        self.refresh()
        return result

    def _ensure_connection_path(self) -> None:
        if self._connection_path is not None:
            return
        path_item = QGraphicsPathItem()
        path_item.setZValue(25.0)
        self._scene.addItem(path_item)
        self._connection_path = path_item
        # Apply default styling immediately
        self._apply_connection_preview_style(
            node_id=self._connection_source or self._connection_target or "",
            direction=self._connection_direction or "output",
            port_id=(
                self._connection_source_port
                if self._connection_direction == "output"
                else self._connection_target_port
            ),
        )

    def _update_connection_preview(self, scene_pos: QPointF) -> None:
        if self._connection_path is None or self._connection_direction is None:
            return
        start = self._connection_anchor
        end = scene_pos
        if self._connection_direction == "input":
            start, end = end, start
        path = QPainterPath(start)
        delta_x = (end.x() - start.x()) * 0.5
        control_1 = QPointF(start.x() + delta_x, start.y())
        control_2 = QPointF(end.x() - delta_x, end.y())
        path.cubicTo(control_1, control_2, end)
        self._connection_path.setPath(path)

    def _resolve_drop_target(
        self,
        scene_pos: QPointF,
        *,
        prefer: str,
        exclude: set[str],
    ) -> tuple[str, str | None] | None:
        items = self._scene.items(scene_pos)
        for item in items:
            if (
                isinstance(item, CanvasConnectionHandle)
                and item.direction == prefer
                and item.node_id not in exclude
            ):
                return item.node_id, item.port_id
        for item in items:
            if (
                isinstance(item, CanvasConnectionHandle)
                and item.node_id not in exclude
            ):
                return item.node_id, item.port_id
        return None

    def _clear_connection_preview(self) -> None:
        if self._connection_path is not None:
            self._scene.removeItem(self._connection_path)
            self._connection_path = None
        self._connection_source = None
        self._connection_source_port = None
        self._connection_target = None
        self._connection_target_port = None
        self._connection_direction = None
        self._connection_anchor = QPointF()

    def _apply_connection_preview_style(
        self,
        *,
        node_id: str,
        direction: str,
        port_id: str | None,
    ) -> None:
        if self._connection_path is None:
            return
        style = self._resolve_connection_preview_style(
            node_id=node_id,
            direction=direction,
            port_id=port_id,
        )
        pen = QPen(QColor(style.color), style.width)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        if style.dash_pattern:
            pen.setDashPattern(list(style.dash_pattern))
        else:
            pen.setStyle(Qt.PenStyle.SolidLine)
        self._connection_path.setPen(pen)

    def _resolve_connection_preview_style(
        self,
        *,
        node_id: str,
        direction: str,
        port_id: str | None,
    ) -> ConnectionPreviewStyleOptions:
        controller = self._controller
        if controller is None:
            source_port = port_id if direction == "output" else None
            target_port = port_id if direction == "input" else None
            return self._registry.connection_preview_style(
                direction=direction,
                source=None,
                source_port=source_port,
                target=None,
                target_port=target_port,
                view=self,
            )
        state = controller.state
        source_node: Optional[NodeData] = None
        target_node: Optional[NodeData] = None
        source_port: Optional[str] = self._connection_source_port
        target_port: Optional[str] = self._connection_target_port
        if direction == "output":
            source_node = state.nodes.get(node_id)
            source_port = port_id
            target_node = (
                state.nodes.get(self._connection_target)
                if self._connection_target
                else None
            )
        else:
            target_node = state.nodes.get(node_id)
            target_port = port_id
            source_node = (
                state.nodes.get(self._connection_source)
                if self._connection_source
                else None
            )
        return self._registry.connection_preview_style(
            direction=direction,
            source=source_node,
            source_port=source_port,
            target=target_node,
            target_port=target_port,
            view=self,
        )

    def _handle_node_position_changed(
        self,
        node_id: str,
        x: float,
        y: float,
    ) -> None:
        if self._controller is None:
            return
        if node_id not in self._drag_starts:
            node = self._controller.state.nodes.get(node_id)
            if node is None:
                return
            self._drag_starts[node_id] = node.position

    def _apply_move_commands(self) -> None:
        if self._controller is None or not self._drag_starts:
            self._drag_starts.clear()
            return
        updates: Dict[str, Tuple[float, float]] = {}
        for node_id, start in self._drag_starts.items():
            item = self._node_items.get(node_id)
            if item is None:
                continue
            current = item.pos()
            if (current.x(), current.y()) != start:
                updates[node_id] = (current.x(), current.y())
        self._drag_starts.clear()
        if not updates:
            return
        self._controller.move_nodes(updates)
        self.selection_drag_finished.emit(dict(updates))
        self.refresh()

    def _update_scene_rect(self) -> None:
        rect = self._scene.itemsBoundingRect()
        if rect.isNull():
            rect = self.viewport().rect().adjusted(-400, -400, 400, 400)
        expanded = rect.adjusted(-200, -200, 200, 200)
        self._scene.setSceneRect(expanded)

    def _emit_selection(self) -> None:
        if self._controller is None:
            return
        state = self._controller.state
        self.selection_changed.emit(
            list(state.selection.nodes),
            list(state.selection.edges),
        )

