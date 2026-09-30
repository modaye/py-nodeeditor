"""Painted nodes, ports, and wires for the canvas.

Nodes stay graphics items. Tool settings belong in a side panel fed by
``metadata``, not in widgets embedded in the scene.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QPainter,
    QPainterPath,
    QPainterPathStroker,
    QPen,
)
from PySide6.QtWidgets import QGraphicsObject, QGraphicsPathItem, QStyle

from .models import EdgeData, NodeData
from .policy import PortHit

__all__ = [
    "CanvasConnectionHandle",
    "CanvasEdgeItem",
    "CanvasNodeItem",
    "node_height",
]

NODE_WIDTH = 180.0
MIN_NODE_HEIGHT = 76.0
HEADER_HEIGHT = 28.0
PORT_PITCH = 22.0
HANDLE_HIT_RADIUS = 14.0
HANDLE_OFFSET = 12.0
_ENDPOINT_RADIUS = 18.0


def node_height(port_rows: int) -> float:
    """Return a node height that keeps ports from overlapping the title."""

    rows = max(port_rows, 1)
    return max(MIN_NODE_HEIGHT, HEADER_HEIGHT + 16.0 + rows * PORT_PITCH)


class CanvasConnectionHandle(QGraphicsObject):
    """Round port. The hit target is larger than the painted dot."""

    drag_started = Signal(str, str, str, QPointF)
    drag_moved = Signal(str, str, str, QPointF)
    drag_finished = Signal(str, str, str, QPointF)

    def __init__(
        self,
        node_id: str,
        direction: str,
        port_id: str,
        parent: QGraphicsObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.node_id = node_id
        self.direction = direction
        self.port_id = port_id
        self._hovered = False
        self._dragging = False
        self._emphasis = "idle"
        self._press_pos = QPointF()
        self.setAcceptHoverEvents(True)
        self.setAcceptedMouseButtons(Qt.MouseButton.LeftButton)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setZValue(20.0)

    @property
    def emphasis(self) -> str:
        return self._emphasis

    def set_emphasis(self, emphasis: str) -> None:
        if emphasis == self._emphasis:
            return
        self._emphasis = emphasis
        self.update()

    def boundingRect(self) -> QRectF:  # type: ignore[override]
        radius = HANDLE_HIT_RADIUS
        return QRectF(-radius, -radius, radius * 2, radius * 2)

    def paint(self, painter: QPainter, option, widget=None) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        color = QColor("#7C3AED" if self.direction == "input" else "#2563EB")
        radius = 5.0
        if self._emphasis == "active":
            color = QColor("#1D4ED8")
            radius = 6.5
        elif self._emphasis == "invalid":
            color = QColor("#DC2626")
            radius = 6.5
        elif self._emphasis == "compatible":
            color = QColor("#93C5FD")
        elif self._hovered or self._dragging:
            color = color.lighter(120)
        painter.setBrush(color)
        painter.setPen(QPen(QColor("#FFFFFF"), 1.5))
        painter.drawEllipse(QRectF(-radius, -radius, radius * 2, radius * 2))

    def hoverEnterEvent(self, event) -> None:  # type: ignore[override]
        self._hovered = True
        self.update()
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event) -> None:  # type: ignore[override]
        self._hovered = False
        self.update()
        super().hoverLeaveEvent(event)

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_pos = event.pos()
            self._dragging = False
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # type: ignore[override]
        if not (event.buttons() & Qt.MouseButton.LeftButton):
            super().mouseMoveEvent(event)
            return
        if not self._dragging:
            if (event.pos() - self._press_pos).manhattanLength() >= 4.0:
                self._dragging = True
                self.drag_started.emit(
                    self.node_id,
                    self.direction,
                    self.port_id,
                    self.scenePos(),
                )
        if self._dragging:
            self.drag_moved.emit(
                self.node_id,
                self.direction,
                self.port_id,
                event.scenePos(),
            )
            event.accept()
            self.update()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton and self._dragging:
            self.drag_finished.emit(
                self.node_id,
                self.direction,
                self.port_id,
                event.scenePos(),
            )
            self._dragging = False
            self.update()
            event.accept()
            return
        super().mouseReleaseEvent(event)


class CanvasNodeItem(QGraphicsObject):
    """Rounded node card whose height follows the number of ports."""

    position_changed = Signal(str, float, float)
    double_clicked = Signal(str)
    connection_drag_started = Signal(str, str, str, QPointF)
    connection_drag_moved = Signal(str, str, str, QPointF)
    connection_drag_finished = Signal(str, str, str, QPointF)
    ports_changed = Signal(str)

    def __init__(self, node: NodeData, parent=None) -> None:
        super().__init__(parent)
        self._node = node.clone()
        self.node_id = node.id
        self._hovered = False
        self._width = NODE_WIDTH
        self._height = node_height(max(len(node.inputs), len(node.outputs)))
        self.setFlag(self.GraphicsItemFlag.ItemIsMovable, True)
        self.setFlag(self.GraphicsItemFlag.ItemIsSelectable, True)
        self.setFlag(self.GraphicsItemFlag.ItemSendsGeometryChanges, True)
        self.setZValue(10.0)
        self.setPos(node.position[0], node.position[1])
        self.setAcceptHoverEvents(True)
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self._input_handles: dict[str, CanvasConnectionHandle] = {}
        self._output_handles: dict[str, CanvasConnectionHandle] = {}
        self._rebuild_handles_for_direction("input", self._node.inputs)
        self._rebuild_handles_for_direction("output", self._node.outputs)
        self._position_handles()

    @property
    def card_size(self) -> tuple[float, float]:
        """Width and height of the node card, without the port hit margin."""

        return (self._width, self._height)

    def boundingRect(self) -> QRectF:  # type: ignore[override]
        margin = HANDLE_OFFSET + HANDLE_HIT_RADIUS
        return QRectF(-margin, -4.0, self._width + margin * 2, self._height + 8.0)

    def shape(self) -> QPainterPath:  # type: ignore[override]
        path = QPainterPath()
        path.addRoundedRect(QRectF(0.0, 0.0, self._width, self._height), 8.0, 8.0)
        for handle in (*self._input_handles.values(), *self._output_handles.values()):
            path.addEllipse(handle.pos(), 8.0, 8.0)
        return path

    def paint(self, painter: QPainter, option, widget=None) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if option.state & QStyle.StateFlag.State_Selected:
            option.state &= ~QStyle.StateFlag.State_Selected
        rect = QRectF(0.0, 0.0, self._width, self._height)
        radius = 8.0
        border = _run_color(self)
        disabled = bool(self._node.metadata.get("disabled"))
        if border is None and disabled:
            border = QColor("#64748B")
        elif border is None:
            border = QColor("#2563EB") if self.isSelected() else QColor("#CBD5E1")
            if not self.isSelected() and self._hovered:
                border = QColor("#94A3B8")
        painter.setPen(QPen(border, 2.2 if _run_state(self) else (1.6 if self.isSelected() else 1.2)))
        painter.setBrush(QColor("#FFFFFF"))
        painter.drawRoundedRect(rect, radius, radius)
        accent = _run_color(self)
        if accent is not None:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(accent)
            painter.drawRoundedRect(QRectF(0.0, 0.0, 4.0, self._height), 2.0, 2.0)

        clip = QPainterPath()
        clip.addRoundedRect(rect, radius, radius)
        painter.save()
        painter.setClipPath(clip)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#F8FAFC"))
        painter.drawRect(QRectF(0.0, 0.0, self._width, HEADER_HEIGHT))
        painter.restore()

        painter.setPen(QColor("#0F172A"))
        label = str(self._node.metadata.get("run_label") or "")
        if not label and disabled:
            label = "禁用"
        metrics = painter.fontMetrics()
        label_width = 0.0
        if label:
            label_width = min(metrics.horizontalAdvance(label) + 4.0, self._width * 0.46)
        title_width = max(self._width - 28.0 - label_width, 24.0)
        painter.drawText(
            QRectF(12.0, 0.0, title_width, HEADER_HEIGHT),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            metrics.elidedText(self._node.title, Qt.TextElideMode.ElideRight, int(title_width)),
        )
        if label:
            painter.setPen(_run_color(self) or QColor("#64748B"))
            painter.drawText(
                QRectF(self._width - 10.0 - label_width, 0.0, label_width, HEADER_HEIGHT),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                label,
            )
        painter.setPen(QColor("#64748B"))
        self._draw_port_labels(painter)

    def update_from_node(self, node: NodeData) -> None:
        """Apply model changes without rebuilding the item."""

        previous_inputs = list(self._node.inputs)
        previous_outputs = list(self._node.outputs)
        self._node = node.clone()
        height = node_height(max(len(node.inputs), len(node.outputs)))
        if height != self._height:
            self.prepareGeometryChange()
            self._height = height
        blocked = self.blockSignals(True)
        self.setPos(node.position[0], node.position[1])
        self.blockSignals(blocked)
        self.update()
        inputs_changed = previous_inputs != self._node.inputs
        outputs_changed = previous_outputs != self._node.outputs
        if inputs_changed:
            self._rebuild_handles_for_direction("input", self._node.inputs)
        if outputs_changed:
            self._rebuild_handles_for_direction("output", self._node.outputs)
        self._position_handles()
        if inputs_changed or outputs_changed:
            self.ports_changed.emit(self.node_id)

    def commit_position(self, x: float, y: float) -> None:
        """Store a position the item already shows, without moving it again."""

        self._node = self._node.with_position(x, y)

    def port_hits(self) -> list[PortHit]:
        """Scene positions of every port, used for magnetic snapping."""

        hits: list[PortHit] = []
        groups = (
            ("input", self._input_handles),
            ("output", self._output_handles),
        )
        for direction, handles in groups:
            for port_id, handle in handles.items():
                pos = handle.scenePos()
                hits.append(
                    PortHit(self.node_id, direction, port_id, pos.x(), pos.y())
                )
        return hits

    def set_handle_emphasis(self, emphasis: dict[tuple[str, str], str]) -> None:
        """Mark ports as ``idle``, ``compatible``, ``active``, or ``invalid``."""

        for handle in (*self._input_handles.values(), *self._output_handles.values()):
            mode = emphasis.get((handle.direction, handle.port_id), "idle")
            handle.set_emphasis(mode)

    def port_scene_position(self, direction: str, port_id: str | None) -> QPointF:
        handle = None
        if direction == "input" and port_id is not None:
            handle = self._input_handles.get(port_id)
        elif direction == "output" and port_id is not None:
            handle = self._output_handles.get(port_id)
        if handle is not None:
            return handle.scenePos()
        rect = QRectF(0.0, 0.0, self._width, self._height)
        return self.mapToScene(rect.center())

    def itemChange(self, change, value):  # type: ignore[override]
        if (
            change == self.GraphicsItemChange.ItemPositionHasChanged
            and not self.signalsBlocked()
        ):
            point: QPointF = value
            self.position_changed.emit(self.node_id, point.x(), point.y())
        return super().itemChange(change, value)

    def mouseDoubleClickEvent(self, event) -> None:  # type: ignore[override]
        self.double_clicked.emit(self.node_id)
        event.accept()

    def hoverEnterEvent(self, event) -> None:  # type: ignore[override]
        self._hovered = True
        self.update()
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event) -> None:  # type: ignore[override]
        self._hovered = False
        self.update()
        super().hoverLeaveEvent(event)

    def _position_handles(self) -> None:
        self._position_port_group(self._input_handles, -HANDLE_OFFSET)
        self._position_port_group(self._output_handles, self._width + HANDLE_OFFSET)

    def _position_port_group(
        self,
        handles: dict[str, CanvasConnectionHandle],
        x: float,
    ) -> None:
        count = len(handles)
        if count == 0:
            return
        top = HEADER_HEIGHT + 8.0
        bottom = self._height - 12.0
        for index, handle in enumerate(handles.values()):
            if count == 1:
                y = (top + bottom) / 2.0
            else:
                y = top + (bottom - top) * index / (count - 1)
            handle.setPos(x, y)

    def _rebuild_handles_for_direction(self, direction: str, ports: list[str]) -> None:
        mapping = self._input_handles if direction == "input" else self._output_handles
        for handle in mapping.values():
            handle.setParentItem(None)
            handle.deleteLater()
        mapping.clear()
        for port_id in ports:
            handle = CanvasConnectionHandle(self.node_id, direction, port_id, self)
            handle.drag_started.connect(self.connection_drag_started)
            handle.drag_moved.connect(self.connection_drag_moved)
            handle.drag_finished.connect(self.connection_drag_finished)
            mapping[port_id] = handle

    def _draw_port_labels(self, painter: QPainter) -> None:
        if not self._node.inputs and not self._node.outputs:
            return
        label_height = max(painter.fontMetrics().height(), 14)
        half_width = max((self._width - 28.0) * 0.5, 0.0)
        for port_id, handle in self._input_handles.items():
            pos = handle.pos()
            painter.drawText(
                QRectF(14.0, pos.y() - label_height / 2.0, half_width, label_height),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                port_id,
            )
        for port_id, handle in self._output_handles.items():
            pos = handle.pos()
            painter.drawText(
                QRectF(
                    self._width - 14.0 - half_width,
                    pos.y() - label_height / 2.0,
                    half_width,
                    label_height,
                ),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                port_id,
            )

    @property
    def node_data(self) -> NodeData:
        return self._node.clone()


class CanvasEdgeItem(QGraphicsPathItem, QObject):
    """Bezier wire. Dragging either end reconnects that endpoint."""

    clicked = Signal(str, Qt.MouseButton, QPointF)
    endpoint_drag_started = Signal(str, str, QPointF)
    endpoint_drag_moved = Signal(str, str, QPointF)
    endpoint_drag_finished = Signal(str, str, QPointF)

    def __init__(
        self,
        edge: EdgeData,
        source_item: CanvasNodeItem,
        target_item: CanvasNodeItem,
        parent=None,
    ) -> None:
        QGraphicsPathItem.__init__(self, parent)
        QObject.__init__(self)
        self._edge = edge.clone()
        self.edge_id = edge.id
        self._source = source_item
        self._target = target_item
        self._source_port = edge.source_port
        self._target_port = edge.target_port
        self._hovered = False
        self._detached = False
        self._dragging = False
        self._endpoint: str | None = None
        self._press_pos = QPointF()
        self._relation = "normal"
        self.setZValue(1.0)
        self.setFlag(self.GraphicsItemFlag.ItemIsSelectable, True)
        self.setAcceptHoverEvents(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        source_item.ports_changed.connect(self._handle_ports_changed)
        target_item.ports_changed.connect(self._handle_ports_changed)
        self.update_path()
        self._update_pen()

    def follows(
        self,
        source: CanvasNodeItem | None,
        target: CanvasNodeItem | None,
    ) -> bool:
        """Return whether this wire is still attached to these two node items."""

        return source is self._source and target is self._target

    def update_from_edge(self, edge: EdgeData) -> None:
        self._edge = edge.clone()
        self._source_port = edge.source_port
        self._target_port = edge.target_port
        self.update_path()
        self._update_pen()

    def update_path(self) -> None:
        """Rebuild the curve from the current port positions."""

        start = self._source.port_scene_position("output", self._source_port)
        end = self._target.port_scene_position("input", self._target_port)
        path = QPainterPath(start)
        bend = max(abs(end.x() - start.x()) * 0.5, 48.0)
        path.cubicTo(
            QPointF(start.x() + bend, start.y()),
            QPointF(end.x() - bend, end.y()),
            end,
        )
        self.setPath(path)

    def touches(self, node_ids: set[str]) -> bool:
        """Return whether either endpoint is in ``node_ids``."""

        return self._edge.source in node_ids or self._edge.target in node_ids

    def set_relation(self, relation: str) -> None:
        """Mark this wire as ``linked`` to the selection, ``dimmed``, or ``normal``.

        Linked wires are drawn in front. Dimmed wires stay visible but recede,
        so a dense graph still shows the wires of the selected node.
        """

        if relation not in {"normal", "linked", "dimmed"}:
            relation = "normal"
        if relation == self._relation:
            return
        self._relation = relation
        self._apply_z()
        self._update_pen()

    def detach(self) -> None:
        """Drop node subscriptions before the edge leaves the scene."""

        if self._detached:
            return
        self._detached = True
        for node in {self._source, self._target}:
            node.ports_changed.disconnect(self._handle_ports_changed)

    def hoverEnterEvent(self, event) -> None:  # type: ignore[override]
        self._hovered = True
        self._update_pen()
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event) -> None:  # type: ignore[override]
        self._hovered = False
        self._update_pen()
        super().hoverLeaveEvent(event)

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_pos = event.scenePos()
            self._dragging = False
            self._endpoint = self._nearest_endpoint(event.scenePos())
            if self._endpoint is not None:
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # type: ignore[override]
        if (
            self._endpoint is not None
            and event.buttons() & Qt.MouseButton.LeftButton
        ):
            if not self._dragging:
                if (event.scenePos() - self._press_pos).manhattanLength() < 4.0:
                    event.accept()
                    return
                self._dragging = True
                self.endpoint_drag_started.emit(
                    self.edge_id,
                    self._endpoint,
                    event.scenePos(),
                )
            self.endpoint_drag_moved.emit(
                self.edge_id,
                self._endpoint,
                event.scenePos(),
            )
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        endpoint = self._endpoint
        dragging = self._dragging
        self._dragging = False
        self._endpoint = None
        if endpoint is not None and event.button() == Qt.MouseButton.LeftButton:
            if dragging:
                self.endpoint_drag_finished.emit(
                    self.edge_id,
                    endpoint,
                    event.scenePos(),
                )
            else:
                self.setSelected(True)
                self.clicked.emit(self.edge_id, event.button(), event.scenePos())
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def itemChange(self, change, value):  # type: ignore[override]
        if change == self.GraphicsItemChange.ItemSelectedHasChanged:
            self._apply_z()
            self._update_pen()
        return super().itemChange(change, value)

    def paint(self, painter: QPainter, option, widget=None) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        if option.state & QStyle.StateFlag.State_Selected:
            option.state &= ~QStyle.StateFlag.State_Selected
        painter.setPen(self.pen())
        painter.drawPath(self.path())

    def shape(self) -> QPainterPath:  # type: ignore[override]
        stroker = QPainterPathStroker()
        stroker.setWidth(14.0)
        stroker.setCapStyle(Qt.PenCapStyle.RoundCap)
        stroker.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        return stroker.createStroke(self.path())

    def _nearest_endpoint(self, scene_pos: QPointF) -> str | None:
        start = self._source.port_scene_position("output", self._source_port)
        end = self._target.port_scene_position("input", self._target_port)
        if _distance(scene_pos, start) <= _ENDPOINT_RADIUS:
            return "source"
        if _distance(scene_pos, end) <= _ENDPOINT_RADIUS:
            return "target"
        return None

    def _handle_ports_changed(self, _node_id: str) -> None:
        if not self._detached:
            self.update_path()

    def _apply_z(self) -> None:
        if self.isSelected():
            self.setZValue(4.0)
        elif self._relation == "linked":
            self.setZValue(3.0)
        elif self._relation == "dimmed":
            self.setZValue(0.0)
        else:
            self.setZValue(1.0)

    def _update_pen(self) -> None:
        color = QColor("#94A3B8")
        width = 1.8
        if self.isSelected() or self._relation == "linked":
            color = QColor("#2563EB")
            width = 2.6
        elif self._relation == "dimmed":
            color = QColor("#CBD5E1")
            width = 1.2
        elif self._hovered:
            color = QColor("#64748B")
            width = 2.1
        if self._hovered and self._relation == "dimmed" and not self.isSelected():
            color = QColor("#94A3B8")
            width = 1.8
        pen = QPen(color, width)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        self.setPen(pen)
        self.update()

    @property
    def edge_data(self) -> EdgeData:
        return self._edge.clone()

    def anchored_end(self, endpoint: str) -> PortHit:
        """Return the port that stays put while ``endpoint`` is dragged away.

        ``endpoint`` is ``source`` or ``target``, naming the end under the pointer.
        """

        if endpoint == "source":
            position = self._target.port_scene_position("input", self._target_port)
            return PortHit(
                self._edge.target,
                "input",
                self._target_port or "",
                position.x(),
                position.y(),
            )
        position = self._source.port_scene_position("output", self._source_port)
        return PortHit(
            self._edge.source,
            "output",
            self._source_port or "",
            position.x(),
            position.y(),
        )


def _distance(left: QPointF, right: QPointF) -> float:
    delta = left - right
    return (delta.x() ** 2 + delta.y() ** 2) ** 0.5


def _run_state(item: CanvasNodeItem) -> str:
    return str(item._node.metadata.get("run_state") or "")


def _run_color(item: CanvasNodeItem) -> QColor | None:
    return {
        "running": QColor("#2563EB"),
        "ok": QColor("#16A34A"),
        "error": QColor("#DC2626"),
    }.get(_run_state(item))
