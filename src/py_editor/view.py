"""Interactive view: pan, zoom, selection, and connection drags.

Graph rules live on ``CanvasController``. This class only tracks the pointer
and asks the controller to commit a move or a connection.
"""

from __future__ import annotations

import weakref
from collections.abc import Callable, Iterable

from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QDragEnterEvent,
    QDragLeaveEvent,
    QDragMoveEvent,
    QDropEvent,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPen,
    QPixmap,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsScene,
    QGraphicsView,
    QLineEdit,
    QMenu,
    QWidget,
)

from .controller import CanvasController
from .gesture import ConnectionGesture
from .graphics import NODE_WIDTH, CanvasEdgeItem, CanvasNodeItem
from .layout import arrange_nodes
from .models import EdgeData, NodeData, Viewport
from .policy import (
    ConnectResult,
    PortHit,
    nearest_port,
    preferred_port,
)
from .registry import CanvasRegistry

__all__ = ["CanvasView"]

_MIN_ZOOM = 0.05
_MAX_ZOOM = 2.0
_FIT_MAX_ZOOM = 1.25
_GRID_STEP = 24
_COMPACT_NODE_PX = 48.0
_OVERVIEW_LIMIT = 4096
_FLOW_CAP = 64
_FLOW_PERIOD = 12.0


class CanvasView(QGraphicsView):
    """Node canvas with React Flow style navigation and connecting.

    The wheel zooms toward the pointer. Dragging empty canvas pans. Shift-drag
    on empty canvas selects. Drag a port to connect; the wire snaps to a
    compatible port. Drag either end of an existing wire to reconnect it.
    ``Ctrl+L`` arranges left to right, ``Ctrl+Shift+L`` top to bottom.
    ``Ctrl+0`` frames every node. ``Ctrl+Shift+0`` frames the selection.
    """

    selection_changed = Signal(list, list)
    graph_changed = Signal()
    connection_finished = Signal(object)
    node_double_clicked = Signal(str)
    tool_dropped = Signal(str, float, float, str)
    node_menu = Signal(object, str)
    edge_menu = Signal(object, str)
    connection_hint = Signal(str)

    def __init__(
        self,
        parent=None,
        *,
        enable_default_shortcuts: bool = True,
        registry: CanvasRegistry | None = None,
        grid: bool = True,
        edge_animation: bool = False,
        drop_mime: str | None = None,
        connection_caption: Callable[[PortHit, PortHit | None, bool, str], str] | None = None,
        snap_to_grid: bool = False,
    ) -> None:
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self._scene.setSceneRect(-8000, -8000, 16000, 16000)
        # Most items stay put. The index makes pan, paint and hit-testing
        # depend on what is on screen, not on how many nodes the graph has.
        self._scene.setItemIndexMethod(QGraphicsScene.ItemIndexMethod.BspTreeIndex)
        self.setScene(self._scene)
        self._grid_enabled = False
        self.set_grid_enabled(grid)
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
        self._drop_mime = drop_mime
        self._caption_fn = connection_caption
        self._snap_to_grid = snap_to_grid
        self._registry = registry if registry is not None else CanvasRegistry()
        self._node_items: dict[str, CanvasNodeItem] = {}
        self._edge_items: dict[str, CanvasEdgeItem] = {}
        self._edges_by_node: dict[str, set[str]] = {}
        self._compact = False
        self._edges_dimmed = False
        self._emphasized_ids: set[str] = set()
        self._overview: QPixmap | None = None
        self._overview_rect = QRectF()
        self._overview_scale = 0.0
        self._rendering_overview = False
        self._flow_phase = 0.0
        self._flowing_ids: set[str] = set()
        self._edge_animation = False
        self._flow_timer = QTimer(self)
        self._flow_timer.setInterval(50)
        self._flow_timer.timeout.connect(self._advance_flow)
        self.set_edge_animation_enabled(edge_animation)
        self._minimap = _CanvasMinimap(self)
        self._find_box = QLineEdit(self)
        self._find_box.setPlaceholderText("查找节点")
        self._find_box.hide()
        self._find_box.returnPressed.connect(self._commit_find)
        self._find_box.installEventFilter(self)
        self._find_box.setStyleSheet(
            "QLineEdit { background: #FFFFFF; border: 1px solid #CBD5E1;"
            " border-radius: 6px; padding: 4px 8px; color: #0F172A; }"
        )
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
        self._refresh_posted = False
        self._model_listener = _model_listener(self)
        self._scene.selectionChanged.connect(self._handle_scene_selection_changed)
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)
        self._scene_drag = False

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # type: ignore[override]
        """Accept a tool dragged from a palette. Other drags keep the default."""

        if _accept_tool_drag(event, self._drop_mime):
            self._scene_drag = False
            return
        self._scene_drag = True
        super().dragEnterEvent(event)

    def dragLeaveEvent(self, event: QDragLeaveEvent) -> None:  # type: ignore[override]
        """Skip the base leave when this view accepted the drag itself.

        QGraphicsView warns if leave arrives without the enter it stored.
        """

        if not self._scene_drag:
            event.accept()
            return
        self._scene_drag = False
        super().dragLeaveEvent(event)

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:  # type: ignore[override]
        """Keep accepting the tool while it moves across the canvas."""

        if _accept_tool_drag(event, self._drop_mime):
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:  # type: ignore[override]
        """Place a dragged tool at the pointer, in scene coordinates."""

        self._scene_drag = False
        tool_id = _tool_id(event, self._drop_mime)
        if tool_id is None:
            super().dropEvent(event)
            return
        scene = self.mapToScene(event.position().toPoint())
        self.tool_dropped.emit(tool_id, scene.x(), scene.y(), self._edge_at(scene))
        event.setDropAction(Qt.DropAction.CopyAction)
        event.accept()

    def _edge_at(self, scene_pos) -> str:
        for item in self._scene.items(scene_pos):
            edge_id = getattr(item, "edge_id", "")
            if isinstance(edge_id, str) and edge_id in self._edge_items:
                return edge_id
        return ""

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
        changed = controller is not self._controller
        if changed:
            if self._controller is not None:
                self._controller.unsubscribe(self._model_listener)
            self._clear_scene_items()
            self._controller = controller
            if controller is not None:
                controller.subscribe(self._model_listener)
        self.refresh()
        if changed and controller is not None:
            self.apply_viewport(controller.state.viewport)
        if not self._initialized:
            self._initialized = True

    def apply_viewport(self, viewport: Viewport) -> None:
        """Center the view on ``viewport`` without writing it back."""

        current = self.transform().m11()
        zoom = min(_MAX_ZOOM, max(_MIN_ZOOM, viewport.zoom))
        if current > 0 and abs(zoom - current) > 1e-6:
            self.scale(zoom / current, zoom / current)
        self.centerOn(viewport.x, viewport.y)
        self._apply_detail()
        self._sync_minimap()

    def _store_viewport(self) -> None:
        if self._controller is None:
            return
        center = self.mapToScene(self.viewport().rect().center())
        zoom = abs(self.transform().m11()) or 1.0
        self._controller.state.viewport = Viewport(center.x(), center.y(), zoom)

    def _schedule_model_refresh(self) -> None:
        """Rebuild items once after the current event, even if several edits landed."""

        if self._refresh_posted:
            return
        self._refresh_posted = True
        QTimer.singleShot(0, self._flush_model_refresh)

    def _flush_model_refresh(self) -> None:
        self._refresh_posted = False
        if self._controller is None:
            return
        self.refresh()

    def set_default_shortcuts_enabled(self, enabled: bool) -> None:
        self._shortcuts = enabled

    def set_grid_enabled(self, enabled: bool) -> None:
        """Draw the scene grid, or a flat background when ``enabled`` is false."""

        if enabled == self._grid_enabled:
            return
        self._grid_enabled = enabled
        if enabled:
            self.setBackgroundBrush(_grid_brush())
            return
        self.setBackgroundBrush(QBrush(QColor("#F8FAFC")))

    def set_edge_animation_enabled(self, enabled: bool) -> None:
        """Flow a dash along on-screen wires. Off by default, and the timer stays stopped."""

        if enabled == self._edge_animation:
            return
        self._edge_animation = enabled
        if enabled:
            self._flow_timer.start()
            return
        self._flow_timer.stop()
        self._clear_flow()

    def refresh(self) -> None:
        """Rebuild items that were added or removed. Drags do not call this."""

        if self._controller is None:
            return
        self._gesture.cancel()
        self._restore_hidden_edge()
        state = self._controller.state
        self.setUpdatesEnabled(False)
        try:
            self._drop_missing_edges(set(state.edges))
            self._sync_nodes(state.nodes)
            self._sync_edges(state.edges)
            self._apply_selection(state.selection.nodes, state.selection.edges)
            self._expand_scene_rect()
        finally:
            self.setUpdatesEnabled(True)
        self._invalidate_overview()
        self._sync_minimap()
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
        self._invalidate_overview()

    def focus_nodes(self, node_ids: list[str]) -> None:
        """Select these nodes and notify listeners without rebuilding the scene."""

        if self._controller is None:
            return
        self._controller.set_selection(nodes=node_ids, edges=())
        selection = self._controller.state.selection
        self._apply_selection(selection.nodes, selection.edges)
        self._emit_selection()

    def focus_titled(self, query: str) -> list[str]:
        """Select every node whose title contains ``query``, then frame them.

        Matching ignores case. An empty query or no hit leaves the selection
        alone and returns an empty list.
        """

        needle = query.casefold().strip()
        if not needle or self._controller is None:
            return []
        matched = [
            node.id
            for node in self._controller.state.nodes.values()
            if needle in node.title.casefold()
        ]
        if not matched:
            return []
        self.focus_nodes(matched)
        self._fit_nodes(matched)
        return matched

    def add_node(self, node: NodeData) -> NodeData:
        if self._controller is None:
            raise RuntimeError("Canvas controller is not set")
        created = self._controller.add_node(node)
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

    def set_snap_to_grid(self, enabled: bool) -> None:
        """Snap a released node onto the same 24px grid the background draws.

        The card follows the pointer while it is dragged. Only the position
        stored by ``move_nodes`` is aligned. Undo returns to where the drag began.
        """

        self._snap_to_grid = bool(enabled)

    def focus_on_selection(self) -> None:
        """Frame every selected node and every selected edge."""

        self._fit_rect(self._selection_rect(), max_zoom=_FIT_MAX_ZOOM)

    def fit_to_contents(self) -> None:
        """Frame every node without stretching the view."""

        self._fit_rect(self._nodes_scene_rect(), max_zoom=_FIT_MAX_ZOOM)

    def arrange(
        self,
        direction: str = "horizontal",
        include: Callable[[NodeData], bool] | None = None,
    ) -> None:
        """Lay out nodes in one undo step.

        ``direction`` is ``horizontal`` (left to right) or ``vertical``
        (top to bottom). ``include`` drops nodes the caller wants to leave put.
        """

        if self._controller is None:
            return
        if direction not in ("horizontal", "vertical"):
            raise ValueError("direction must be 'horizontal' or 'vertical'")
        state = self._controller.state
        sizes: dict[str, tuple[float, float]] = {}
        origins: list[tuple[float, float]] = []
        for node_id, node in state.nodes.items():
            if include is not None and not include(node):
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
            self._apply_detail()
            self._sync_minimap()
            self._store_viewport()

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
            self._apply_detail()
            self._sync_minimap()
            self._store_viewport()
        event.accept()

    def paintEvent(self, event) -> None:  # type: ignore[override]
        """Blit one cached picture while zoomed out.

        A fitted graph puts every node on screen. Drawing each card from
        Python every frame is what makes that view stutter. The cache is
        rebuilt when the graph or the zoom changes, and panning only moves
        the picture.
        """

        if not self._overview_active():
            super().paintEvent(event)
            return
        pixmap = self._ensure_overview()
        if pixmap is None:
            super().paintEvent(event)
            return
        source = self.mapToScene(self.viewport().rect()).boundingRect()
        scale = self._overview_scale
        origin = self._overview_rect
        painter = QPainter(self.viewport())
        painter.fillRect(self.viewport().rect(), QColor("#F8FAFC"))
        painter.drawPixmap(
            QRectF(self.viewport().rect()),
            pixmap,
            QRectF(
                (source.left() - origin.left()) * scale,
                (source.top() - origin.top()) * scale,
                source.width() * scale,
                source.height() * scale,
            ),
        )
        painter.end()

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
            self.edge_menu.emit(menu, edge_id)
        else:
            menu.addAction("从左到右排列", lambda: self.arrange("horizontal"))
            menu.addAction("从上到下排列", lambda: self.arrange("vertical"))
            menu.addAction("适应画面", self.fit_to_contents)
        self._show_menu(menu, event.globalPos())
        event.accept()

    def _show_menu(self, menu: QMenu, global_pos) -> None:
        menu.exec(global_pos)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if not self._shortcuts:
            super().keyPressEvent(event)
            return
        modifiers = event.modifiers()
        ctrl = bool(modifiers & Qt.KeyboardModifier.ControlModifier)
        shift = bool(modifiers & Qt.KeyboardModifier.ShiftModifier)
        key = event.key()
        if ctrl and key == Qt.Key.Key_F:
            self._open_find()
            event.accept()
            return
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
            if shift:
                self.focus_on_selection()
            else:
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
        if self._caption_fn is not None:
            return self._caption_fn(origin, snapped, valid, reason)
        left = self._endpoint_text(origin.node_id, origin.port_id)
        if snapped is None:
            return f"{left}  →  拖到端口"
        right = self._endpoint_text(snapped.node_id, snapped.port_id)
        if valid:
            return f"{left}  →  {right}"
        detail = {
            "type": "类型不一致",
            "cycle": "会形成环",
            "self": "不能连接自身",
            "duplicate": "已经连接",
            "missing-port": "没有端口",
            "unknown-node": "找不到节点",
            "locked": "节点已锁定",
        }.get(reason, reason)
        return f"{left}  →  {right}（{detail}）"

    def _endpoint_text(self, node_id: str, port_id: str) -> str:
        if self._controller is None:
            return port_id
        node = self._controller.state.nodes.get(node_id)
        title = node.title if node is not None and node.title else node_id
        return f"{title} · {port_id}"

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
        self._store_viewport()

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
        elif 0 < scale < _MIN_ZOOM:
            self.scale(_MIN_ZOOM / scale, _MIN_ZOOM / scale)
        self._apply_detail()
        self._sync_minimap()
        self._store_viewport()

    def _selection_rect(self):
        if self._controller is None:
            return None
        rect = None
        selection = self._controller.state.selection
        for node_id in selection.nodes:
            item = self._node_items.get(node_id)
            if item is None:
                continue
            item_rect = item.mapToScene(item.boundingRect()).boundingRect()
            rect = item_rect if rect is None else rect.united(item_rect)
        for edge_id in selection.edges:
            item = self._edge_items.get(edge_id)
            if item is None:
                continue
            item_rect = item.sceneBoundingRect()
            rect = item_rect if rect is None else rect.united(item_rect)
        return rect

    def _snap_position(self, x: float, y: float) -> tuple[float, float]:
        if not self._snap_to_grid:
            return (x, y)
        step = _GRID_STEP
        return (round(x / step) * step, round(y / step) * step)

    def _place_item(self, item: CanvasNodeItem, x: float, y: float) -> None:
        blocked = item.blockSignals(True)
        item.setPos(x, y)
        item.blockSignals(blocked)
        item.commit_position(x, y)

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
            elif not item.same_visual(node):
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
                self._remember_edge(edge)
            else:
                item.update_from_edge(edge)

    def _remove_edge_item(self, edge_id: str) -> None:
        item = self._edge_items.pop(edge_id, None)
        if item is None:
            return
        if item is self._hidden_edge:
            self._hidden_edge = None
        self._forget_edge(item.edge_data)
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
        self._edges_by_node.clear()
        self._edges_dimmed = False
        self._emphasized_ids.clear()
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
            selected = node_id in node_ids
            if item.isSelected() == selected:
                continue
            blocked = item.blockSignals(True)
            item.setSelected(selected)
            item.blockSignals(blocked)
        for edge_id, item in self._edge_items.items():
            selected = edge_id in edge_ids
            if item.isSelected() != selected:
                item.setSelected(selected)
        self._syncing_selection = False
        self._emphasize_connected_edges(node_ids)
        self._invalidate_overview()

    def _handle_scene_selection_changed(self) -> None:
        if self._controller is None or self._syncing_selection:
            return
        self._controller.set_selection(
            nodes=list(self._selected_node_ids()),
            edges=list(self._selected_edge_ids()),
        )
        self._emphasize_connected_edges(set(self._controller.state.selection.nodes))
        self._invalidate_overview()
        self._emit_selection()

    def _emphasize_connected_edges(self, selected_nodes: set[str]) -> None:
        """Brighten wires that touch the selected nodes and fade the rest."""

        if not selected_nodes and not self._edges_dimmed:
            return
        self._edges_dimmed = bool(selected_nodes)
        for item in self._edge_items.values():
            if not selected_nodes:
                item.set_relation("normal")
            elif item.touches(selected_nodes):
                item.set_relation("linked")
            else:
                item.set_relation("dimmed")

    def _selected_node_ids(self) -> list[str]:
        return [
            item.node_id
            for item in self._scene.selectedItems()
            if isinstance(item, CanvasNodeItem)
        ]

    def _selected_edge_ids(self) -> list[str]:
        return [
            item.edge_id
            for item in self._scene.selectedItems()
            if isinstance(item, CanvasEdgeItem)
        ]

    def _create_node_item(self, node: NodeData) -> CanvasNodeItem:
        item = self._registry.create_node_item(self, node)
        if not isinstance(item, CanvasNodeItem):
            raise TypeError("Node factory must return a CanvasNodeItem instance")
        item.set_compact(self._compact)
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
        item.set_compact(self._compact)
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
        seen: set[str] = set()
        for node_id in dirty:
            for edge_id in self._edges_by_node.get(node_id, ()):
                if edge_id in seen:
                    continue
                seen.add(edge_id)
                item = self._edge_items.get(edge_id)
                if item is not None:
                    item.update_path()
        if seen:
            self._invalidate_overview()

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
            x, y = self._snap_position(current.x(), current.y())
            if abs(x - start[0]) < 0.5 and abs(y - start[1]) < 0.5:
                self._place_item(item, start[0], start[1])
                continue
            updates[node_id] = (x, y)
        self._drag_starts.clear()
        if not updates:
            return
        self._controller.move_nodes(updates)
        for node_id, position in updates.items():
            item = self._node_items.get(node_id)
            if item is not None:
                self._place_item(item, position[0], position[1])
        self._invalidate_overview()
        self.graph_changed.emit()

    def _begin_port_drag(
        self,
        node_id: str,
        direction: str,
        port_id: str,
        anchor: QPointF,
    ) -> None:
        node = self._controller.state.nodes.get(node_id) if self._controller is not None else None
        if node is None or not node.connectable:
            return
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
            self._ports_near(scene_pos.x(), scene_pos.y(), controller.policy.snap_radius),
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
        self._show_emphasis(origin, snapped, valid, scene_pos)

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

    def _ports_near(self, x: float, y: float, radius: float) -> list[PortHit]:
        """Ports on nodes that intersect a box around the pointer."""

        pad = radius + 4.0
        rect = QRectF(x - pad, y - pad, pad * 2, pad * 2)
        hits: list[PortHit] = []
        seen: set[str] = set()
        for item in self._scene.items(rect):
            node = item if isinstance(item, CanvasNodeItem) else item.parentItem()
            if not isinstance(node, CanvasNodeItem) or node.node_id in seen:
                continue
            seen.add(node.node_id)
            hits.extend(node.port_hits())
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
        scene_pos: QPointF,
    ) -> None:
        """Highlight the dragged port and nearby targets, not the whole graph."""

        opposite = "input" if origin.direction == "output" else "output"
        reach = 160.0
        if self._controller is not None:
            reach = max(self._controller.policy.snap_radius * 6, 96.0)
        active = {origin.node_id}
        marks_for: dict[str, dict[tuple[str, str], str]] = {
            origin.node_id: {(origin.direction, origin.port_id): "active"}
        }
        for item in self._nodes_near(scene_pos, reach):
            if item.node_id == origin.node_id:
                continue
            active.add(item.node_id)
            marks: dict[tuple[str, str], str] = {}
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
            marks_for[item.node_id] = marks
        for node_id in self._emphasized_ids - active:
            previous = self._node_items.get(node_id)
            if previous is not None:
                previous.set_handle_emphasis({})
        for node_id, marks in marks_for.items():
            item = self._node_items.get(node_id)
            if item is not None:
                item.set_handle_emphasis(marks)
        self._emphasized_ids = active

    def _clear_emphasis(self) -> None:
        for node_id in self._emphasized_ids:
            item = self._node_items.get(node_id)
            if item is not None:
                item.set_handle_emphasis({})
        self._emphasized_ids.clear()

    def _restore_hidden_edge(self) -> None:
        if self._hidden_edge is None:
            return
        self._hidden_edge.setOpacity(1.0)
        self._hidden_edge = None

    def _expand_scene_rect(self) -> None:
        rect = self._nodes_scene_rect()
        if rect is None:
            return
        self._scene.setSceneRect(
            self._scene.sceneRect().united(rect.adjusted(-2000, -2000, 2000, 2000))
        )

    def _nodes_scene_rect(self) -> QRectF | None:
        rect = None
        for item in self._node_items.values():
            item_rect = item.sceneBoundingRect()
            rect = item_rect if rect is None else rect.united(item_rect)
        return rect

    def _nodes_near(self, scene_pos: QPointF, radius: float) -> list[CanvasNodeItem]:
        rect = QRectF(scene_pos.x() - radius, scene_pos.y() - radius, radius * 2, radius * 2)
        nodes: list[CanvasNodeItem] = []
        seen: set[str] = set()
        for item in self._scene.items(rect):
            node = item if isinstance(item, CanvasNodeItem) else item.parentItem()
            if not isinstance(node, CanvasNodeItem) or node.node_id in seen:
                continue
            seen.add(node.node_id)
            nodes.append(node)
        return nodes

    def _remember_edge(self, edge: EdgeData) -> None:
        self._edges_by_node.setdefault(edge.source, set()).add(edge.id)
        if edge.target != edge.source:
            self._edges_by_node.setdefault(edge.target, set()).add(edge.id)

    def _forget_edge(self, edge: EdgeData) -> None:
        for node_id in {edge.source, edge.target}:
            bucket = self._edges_by_node.get(node_id)
            if bucket is None:
                continue
            bucket.discard(edge.id)
            if not bucket:
                del self._edges_by_node[node_id]

    def _apply_detail(self) -> None:
        """Switch cards and wires to blocks and hairlines when they are tiny."""

        compact = abs(self.transform().m11()) * NODE_WIDTH < _COMPACT_NODE_PX
        if compact == self._compact:
            return
        self._compact = compact
        if compact:
            self._clear_flow()
        self.setRenderHint(QPainter.RenderHint.Antialiasing, not compact)
        self.setRenderHint(QPainter.RenderHint.TextAntialiasing, not compact)
        for item in self._node_items.values():
            item.set_compact(compact)
        for item in self._edge_items.values():
            item.set_compact(compact)
        self._invalidate_overview()
        self._sync_minimap()

    def _overview_active(self) -> bool:
        return (
            self._compact
            and not self._rendering_overview
            and not self._drag_starts
            and not self._selecting
            and not self._gesture.active
        )

    def _invalidate_overview(self) -> None:
        if not self._rendering_overview:
            self._minimap.mark_dirty()
        if self._overview is None:
            return
        self._overview = None
        if not self._rendering_overview:
            self.viewport().update()

    def _advance_flow(self) -> None:
        """Move a dash along the wires that are actually on screen.

        A fitted graph can show thousands of wires. Updating every pen each
        tick would redraw the whole canvas, so the animation stops past a
        small on-screen count. Selected wires still move when the rest do not,
        unless the selection itself is past that count.
        """

        if not self._edge_animation or self._compact or self._rendering_overview:
            self._clear_flow()
            return
        self._flow_phase = (self._flow_phase - 0.7) % _FLOW_PERIOD
        rect = self.mapToScene(self.viewport().rect()).boundingRect()
        visible: list[CanvasEdgeItem] = []
        overflow = False
        for item in self._scene.items(rect):
            if not isinstance(item, CanvasEdgeItem):
                continue
            visible.append(item)
            if len(visible) > _FLOW_CAP:
                overflow = True
                break
        if overflow:
            visible = [
                item
                for item in visible
                if item.isSelected() or item._relation == "linked"
            ]
            if len(visible) > _FLOW_CAP:
                visible = []
        current = {item.edge_id for item in visible}
        for edge_id in self._flowing_ids - current:
            item = self._edge_items.get(edge_id)
            if item is not None:
                item.set_flow_offset(None)
        self._flowing_ids = current
        for item in visible:
            item.set_flow_offset(self._flow_phase)

    def _clear_flow(self) -> None:
        for edge_id in self._flowing_ids:
            item = self._edge_items.get(edge_id)
            if item is not None:
                item.set_flow_offset(None)
        self._flowing_ids.clear()

    def _sync_minimap(self) -> None:
        """Show the map only when the viewport cannot hold every node."""

        rect = self._nodes_scene_rect()
        if rect is None or self.width() < 420 or self.height() < 280:
            self._minimap.hide()
            return
        visible = self.mapToScene(self.viewport().rect()).boundingRect()
        if visible.contains(rect.adjusted(-8, -8, 8, 8)):
            self._minimap.hide()
            return
        self._minimap.show()
        self._minimap.move(
            self.width() - self._minimap.width() - 12,
            self.height() - self._minimap.height() - 12,
        )
        self._minimap.update()

    def _ensure_overview(self) -> QPixmap | None:
        """Rasterize the whole graph at the current zoom, if it fits in one image."""

        scale = abs(self.transform().m11())
        if (
            self._overview is not None
            and not self._overview.isNull()
            and abs(self._overview_scale - scale) < 1e-4
        ):
            return self._overview
        rect = self._nodes_scene_rect()
        if rect is None or scale <= 0:
            return None
        rect = rect.adjusted(-32, -32, 32, 32)
        width = int(rect.width() * scale) + 1
        height = int(rect.height() * scale) + 1
        if width > _OVERVIEW_LIMIT or height > _OVERVIEW_LIMIT or width < 2 or height < 2:
            return None
        pixmap = QPixmap(width, height)
        pixmap.fill(QColor("#F8FAFC"))
        painter = QPainter(pixmap)
        self._rendering_overview = True
        try:
            self._scene.render(
                painter,
                QRectF(0, 0, width, height),
                rect,
                Qt.AspectRatioMode.IgnoreAspectRatio,
            )
        finally:
            self._rendering_overview = False
            painter.end()
        self._overview = pixmap
        self._overview_rect = rect
        self._overview_scale = scale
        return pixmap

    def _open_find(self) -> None:
        self._place_find_box()
        self._find_box.show()
        self._find_box.raise_()
        self._find_box.setFocus()
        self._find_box.selectAll()

    def _commit_find(self) -> None:
        if self.focus_titled(self._find_box.text()):
            self._find_box.hide()
            self.setFocus()
            return
        self._find_box.selectAll()

    def _place_find_box(self) -> None:
        width = min(280, max(self.width() - 24, 120))
        self._find_box.setGeometry(12, 12, width, 32)

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._place_find_box()
        self._sync_minimap()

    def scrollContentsBy(self, dx: int, dy: int) -> None:
        super().scrollContentsBy(dx, dy)
        if self._minimap.isVisible():
            self._minimap.update()

    def eventFilter(self, watched, event) -> bool:  # type: ignore[override]
        if (
            watched is self._find_box
            and event.type() == QEvent.Type.KeyPress
            and event.key() == Qt.Key.Key_Escape
        ):
            self._find_box.hide()
            self.setFocus()
            return True
        return super().eventFilter(watched, event)

    def _emit_selection(self) -> None:
        if self._controller is None:
            return
        state = self._controller.state
        self.selection_changed.emit(
            list(state.selection.nodes),
            list(state.selection.edges),
        )


class _CanvasMinimap(QWidget):
    """Corner map of node positions. Clicking it pans the canvas.

    The picture is a few rectangles, not a second render of the scene.
    shortcut: positions update when a drag ends, not on each move.
    """

    def __init__(self, view: CanvasView) -> None:
        super().__init__(view)
        self._view = view
        self._dirty = True
        self._pixmap = QPixmap()
        self._bounds = QRectF()
        self._scale = 1.0
        self._origin = QPointF()
        self._dragging = False
        self.setFixedSize(180, 112)

    def mark_dirty(self) -> None:
        self._dirty = True
        self.update()

    def widget_point(self, scene_point: QPointF) -> QPointF:
        """Map a scene point into this widget. Used by tests and hit checks."""

        return QPointF(
            self._origin.x() + (scene_point.x() - self._bounds.left()) * self._scale,
            self._origin.y() + (scene_point.y() - self._bounds.top()) * self._scale,
        )

    def paintEvent(self, event) -> None:  # type: ignore[override]
        del event
        if self._dirty and not self._view._drag_starts:
            self._rebuild()
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#FFFFFF"))
        if not self._pixmap.isNull():
            painter.drawPixmap(0, 0, self._pixmap)
        visible = self._view.mapToScene(self._view.viewport().rect()).boundingRect()
        painter.setPen(QPen(QColor("#2563EB"), 1))
        painter.setBrush(QColor(37, 99, 235, 36))
        painter.drawRect(self._scene_rect_to_widget(visible))
        painter.setPen(QPen(QColor("#CBD5E1"), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(self.rect().adjusted(0, 0, -1, -1))
        painter.end()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._pan_to(event.position())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._dragging:
            self._pan_to(event.position())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = False
            self._view._store_viewport()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _pan_to(self, widget_pos: QPointF) -> None:
        if self._scale <= 0:
            return
        scene = QPointF(
            self._bounds.left() + (widget_pos.x() - self._origin.x()) / self._scale,
            self._bounds.top() + (widget_pos.y() - self._origin.y()) / self._scale,
        )
        self._view.centerOn(scene)
        self.update()

    def _scene_rect_to_widget(self, rect: QRectF) -> QRectF:
        top_left = self.widget_point(rect.topLeft())
        return QRectF(
            top_left.x(),
            top_left.y(),
            rect.width() * self._scale,
            rect.height() * self._scale,
        )

    def _rebuild(self) -> None:
        nodes = self._view._node_items
        self._dirty = False
        if not nodes:
            self._pixmap = QPixmap()
            return
        bounds = QRectF()
        for item in nodes.values():
            width, height = item.card_size
            card = QRectF(item.pos().x(), item.pos().y(), width, height)
            bounds = card if bounds.isNull() else bounds.united(card)
        bounds.adjust(-48, -48, 48, 48)
        self._bounds = bounds
        span = max(self.width() - 8, 1)
        scale = min(span / bounds.width(), (self.height() - 8) / bounds.height())
        self._scale = scale
        self._origin = QPointF(
            4 + (span - bounds.width() * scale) / 2,
            4 + ((self.height() - 8) - bounds.height() * scale) / 2,
        )
        pixmap = QPixmap(self.width(), self.height())
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        edges = self._view._edge_items
        if len(edges) <= 400:
            painter.setPen(QPen(QColor("#CBD5E1"), 1))
            for edge in edges.values():
                painter.drawLine(
                    self.widget_point(edge._source.pos()),
                    self.widget_point(edge._target.pos()),
                )
        painter.setPen(Qt.PenStyle.NoPen)
        for item in nodes.values():
            color = QColor("#2563EB") if item.isSelected() else QColor("#64748B")
            painter.setBrush(color)
            width, height = item.card_size
            top_left = self.widget_point(item.pos())
            painter.drawRect(
                QRectF(
                    top_left.x(),
                    top_left.y(),
                    max(1.0, width * scale),
                    max(1.0, height * scale),
                )
            )
        painter.end()
        self._pixmap = pixmap


def _model_listener(view: "CanvasView"):
    """Notify ``view`` without the controller holding it alive."""

    view_ref = weakref.ref(view)

    def listener() -> None:
        canvas = view_ref()
        if canvas is not None:
            canvas._schedule_model_refresh()

    return listener


def _grid_brush() -> QBrush:
    """Scene-space line grid. Qt tiles the pixmap, so panning does not redraw it in Python."""

    step = _GRID_STEP
    span = step * 5
    tile = QPixmap(span, span)
    tile.fill(QColor("#F8FAFC"))
    painter = QPainter(tile)
    painter.setPen(QPen(QColor("#E2E8F0")))
    for index in range(1, 5):
        painter.drawLine(index * step, 0, index * step, span)
        painter.drawLine(0, index * step, span, index * step)
    painter.setPen(QPen(QColor("#CBD5E1")))
    painter.drawLine(0, 0, 0, span)
    painter.drawLine(0, 0, span, 0)
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


def _tool_id(event, mime_type: str | None) -> str | None:
    if not mime_type:
        return None
    mime = event.mimeData()
    if mime is None or not mime.hasFormat(mime_type):
        return None
    text = bytes(mime.data(mime_type)).decode("utf-8").strip()
    return text or None


def _accept_tool_drag(event: QDragEnterEvent | QDragMoveEvent, mime_type: str | None) -> bool:
    """Accept a palette drag as a copy. Qt delivers these events to the view."""

    if _tool_id(event, mime_type) is None:
        return False
    event.setDropAction(Qt.DropAction.CopyAction)
    event.accept()
    return True
