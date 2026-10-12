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
from PySide6.QtWidgets import QGraphicsItem, QGraphicsObject, QGraphicsPathItem, QStyle

from .models import EdgeData, NodeData
from .policy import PortHit

__all__ = [
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
        self._compact = False
        self._emphasis: dict[tuple[str, str], str] = {}
        self._hovered_port: tuple[str, str] | None = None
        self._port_press: tuple[str, str] | None = None
        self._port_dragging = False
        self._press_pos = QPointF()
        self._shape_cache: QPainterPath | None = None
        self._width = NODE_WIDTH
        self._height = node_height(max(len(node.port_ids("input")), len(node.port_ids("output"))))
        self.setFlag(self.GraphicsItemFlag.ItemSendsGeometryChanges, True)
        self._apply_flags()
        self.setZValue(10.0)
        self.setPos(node.position[0], node.position[1])
        self.setAcceptHoverEvents(True)
        self.setCursor(Qt.CursorShape.ArrowCursor)

    @property
    def card_size(self) -> tuple[float, float]:
        """Width and height of the node card, without the port hit margin."""

        return (self._width, self._height)

    def boundingRect(self) -> QRectF:  # type: ignore[override]
        margin = HANDLE_OFFSET + HANDLE_HIT_RADIUS
        return QRectF(-margin, -4.0, self._width + margin * 2, self._height + 8.0)

    def shape(self) -> QPainterPath:  # type: ignore[override]
        if self._shape_cache is None:
            path = QPainterPath()
            path.addRoundedRect(QRectF(0.0, 0.0, self._width, self._height), 8.0, 8.0)
            if not self._compact:
                for direction in ("input", "output"):
                    for port_id in self._node.port_ids(direction):
                        path.addEllipse(self.port_anchor(direction, port_id), 8.0, 8.0)
            self._shape_cache = path
        return self._shape_cache

    def set_compact(self, compact: bool) -> None:
        """Draw a plain block when the card is only a few pixels on screen."""

        if compact == self._compact:
            return
        self._compact = compact
        self._shape_cache = None
        self.update()

    def same_visual(self, node: NodeData) -> bool:
        """Return whether ``node`` would paint the same card already on screen."""

        current = self._node
        return (
            current.position == node.position
            and current.title == node.title
            and current.node_type == node.node_type
            and             current.ports == node.ports
            and current.draggable == node.draggable
            and current.selectable == node.selectable
            and current.connectable == node.connectable
            and current.deletable == node.deletable
            and current.metadata == node.metadata
        )

    def paint(self, painter: QPainter, option, widget=None) -> None:
        if option.state & QStyle.StateFlag.State_Selected:
            option.state &= ~QStyle.StateFlag.State_Selected
        rect = QRectF(0.0, 0.0, self._width, self._height)
        if self._compact:
            self._paint_compact(painter, rect)
            return
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        radius = 8.0
        border = QColor("#2563EB") if self.isSelected() else QColor("#CBD5E1")
        if not self.isSelected() and self._hovered:
            border = QColor("#94A3B8")
        painter.setPen(QPen(border, 1.6 if self.isSelected() else 1.2))
        painter.setBrush(QColor("#FFFFFF"))
        painter.drawRoundedRect(rect, radius, radius)

        clip = QPainterPath()
        clip.addRoundedRect(rect, radius, radius)
        painter.save()
        painter.setClipPath(clip)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#F8FAFC"))
        painter.drawRect(QRectF(0.0, 0.0, self._width, HEADER_HEIGHT))
        painter.restore()

        painter.setPen(QColor("#0F172A"))
        metrics = painter.fontMetrics()
        title_width = max(self._width - 28.0, 24.0)
        painter.drawText(
            QRectF(12.0, 0.0, title_width, HEADER_HEIGHT),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            metrics.elidedText(self._node.title, Qt.TextElideMode.ElideRight, int(title_width)),
        )
        painter.setPen(QColor("#64748B"))
        self._draw_port_labels(painter)

    def update_from_node(self, node: NodeData) -> None:
        """Apply model changes without rebuilding the item."""

        previous_ports = self._node.ports
        self._node = node.clone()
        self._apply_flags()
        height = node_height(max(len(node.port_ids("input")), len(node.port_ids("output"))))
        ports_changed = previous_ports != self._node.ports
        if height != self._height or ports_changed:
            self.prepareGeometryChange()
            self._height = height
            self._shape_cache = None
        blocked = self.blockSignals(True)
        self.setPos(node.position[0], node.position[1])
        self.blockSignals(blocked)
        self.update()
        if ports_changed:
            self.ports_changed.emit(self.node_id)

    def commit_position(self, x: float, y: float) -> None:
        """Store a position the item already shows, without moving it again."""

        self._node = self._node.with_position(x, y)

    def port_hits(self) -> list[PortHit]:
        """Scene positions of every port, used for magnetic snapping."""

        hits: list[PortHit] = []
        for direction in ("input", "output"):
            for port_id in self._node.port_ids(direction):
                pos = self.mapToScene(self.port_anchor(direction, port_id))
                hits.append(PortHit(self.node_id, direction, port_id, pos.x(), pos.y()))
        return hits

    def set_handle_emphasis(self, emphasis: dict[tuple[str, str], str]) -> None:
        """Mark ports as ``idle``, ``compatible``, ``active``, or ``invalid``."""

        if emphasis == self._emphasis:
            return
        self._emphasis = dict(emphasis)
        self.update()

    def port_scene_position(self, direction: str, port_id: str | None) -> QPointF:
        ports = self._node.port_ids(direction)
        if port_id is not None and port_id in ports:
            return self.mapToScene(self.port_anchor(direction, port_id))
        rect = QRectF(0.0, 0.0, self._width, self._height)
        return self.mapToScene(rect.center())

    def itemChange(self, change: QGraphicsItem.GraphicsItemChange, value: object) -> object:
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

    def hoverMoveEvent(self, event) -> None:  # type: ignore[override]
        port = self._port_at(event.pos())
        if port != self._hovered_port:
            self._hovered_port = port
            self.setCursor(
                Qt.CursorShape.CrossCursor if port is not None else Qt.CursorShape.ArrowCursor
            )
            self.update()
        super().hoverMoveEvent(event)

    def hoverLeaveEvent(self, event) -> None:  # type: ignore[override]
        self._hovered = False
        self._hovered_port = None
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.update()
        super().hoverLeaveEvent(event)

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        port = self._port_at(event.pos()) if event.button() == Qt.MouseButton.LeftButton else None
        if port is not None:
            self._port_press = port
            self._port_dragging = False
            self._press_pos = event.pos()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # type: ignore[override]
        if self._port_press is None or not (event.buttons() & Qt.MouseButton.LeftButton):
            super().mouseMoveEvent(event)
            return
        if not self._port_dragging:
            if (event.pos() - self._press_pos).manhattanLength() < 4.0:
                event.accept()
                return
            self._port_dragging = True
            direction, port_id = self._port_press
            self.connection_drag_started.emit(
                self.node_id,
                direction,
                port_id,
                self.mapToScene(self.port_anchor(direction, port_id)),
            )
        direction, port_id = self._port_press
        self.connection_drag_moved.emit(self.node_id, direction, port_id, event.scenePos())
        event.accept()

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        port = self._port_press
        dragging = self._port_dragging
        self._port_press = None
        self._port_dragging = False
        if port is not None and event.button() == Qt.MouseButton.LeftButton:
            if dragging:
                direction, port_id = port
                self.connection_drag_finished.emit(
                    self.node_id,
                    direction,
                    port_id,
                    event.scenePos(),
                )
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _paint_compact(self, painter: QPainter, rect: QRectF) -> None:
        # A 1px border disappears once the card is only a few screen pixels.
        if self.isSelected():
            fill = QColor("#DBEAFE")
            border = QColor("#2563EB")
        else:
            fill = QColor("#FFFFFF")
            border = QColor("#94A3B8")
        painter.setPen(QPen(border, 1))
        painter.setBrush(fill)
        painter.drawRect(rect)

    def port_anchor(self, direction: str, port_id: str) -> QPointF:
        """Local point of a port. Paint, hit testing, and wires all use this."""

        ports = self._node.port_ids(direction)
        count = len(ports)
        index = ports.index(port_id) if port_id in ports else 0
        x = -HANDLE_OFFSET if direction == "input" else self._width + HANDLE_OFFSET
        top = HEADER_HEIGHT + 8.0
        bottom = self._height - 12.0
        if count <= 1:
            y = (top + bottom) / 2.0
        else:
            y = top + (bottom - top) * index / (count - 1)
        return QPointF(x, y)

    def _apply_flags(self) -> None:
        self.setFlag(self.GraphicsItemFlag.ItemIsMovable, self._node.draggable)
        self.setFlag(self.GraphicsItemFlag.ItemIsSelectable, self._node.selectable)

    def _port_at(self, local: QPointF) -> tuple[str, str] | None:
        if self._compact:
            return None
        limit = HANDLE_HIT_RADIUS * HANDLE_HIT_RADIUS
        best: tuple[str, str] | None = None
        best_distance = limit
        for direction in ("input", "output"):
            for port_id in self._node.port_ids(direction):
                pos = self.port_anchor(direction, port_id)
                distance = (local.x() - pos.x()) ** 2 + (local.y() - pos.y()) ** 2
                if distance <= best_distance:
                    best = (direction, port_id)
                    best_distance = distance
        return best

    def _draw_port_labels(self, painter: QPainter) -> None:
        if not self._node.ports:
            return
        label_height = max(painter.fontMetrics().height(), 14)
        half_width = max((self._width - 28.0) * 0.5, 0.0)
        for port_id in self._node.port_ids("input"):
            pos = self.port_anchor("input", port_id)
            painter.drawText(
                QRectF(14.0, pos.y() - label_height / 2.0, half_width, label_height),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                port_id,
            )
        for port_id in self._node.port_ids("output"):
            pos = self.port_anchor("output", port_id)
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
        self._paint_ports(painter)

    def _paint_ports(self, painter: QPainter) -> None:
        for direction in ("input", "output"):
            for port_id in self._node.port_ids(direction):
                pos = self.port_anchor(direction, port_id)
                mode = self._emphasis.get((direction, port_id), "idle")
                color = QColor("#7C3AED" if direction == "input" else "#2563EB")
                radius = 5.0
                if mode == "active":
                    color = QColor("#1D4ED8")
                    radius = 6.5
                elif mode == "invalid":
                    color = QColor("#DC2626")
                    radius = 6.5
                elif mode == "compatible":
                    color = QColor("#93C5FD")
                elif self._hovered_port == (direction, port_id) or (
                    self._port_dragging and self._port_press == (direction, port_id)
                ):
                    color = color.lighter(120)
                painter.setBrush(color)
                painter.setPen(QPen(QColor("#FFFFFF"), 1.5))
                painter.drawEllipse(QRectF(pos.x() - radius, pos.y() - radius, radius * 2, radius * 2))

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
        self._compact = False
        self._shape_cache: QPainterPath | None = None
        self._endpoint: str | None = None
        self._press_pos = QPointF()
        self._relation = "normal"
        self._flow_offset: float | None = None
        self._at_nodes: tuple[float, float, float, float] | None = None
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
        ports_same = (
            edge.source == self._edge.source
            and edge.target == self._edge.target
            and edge.source_port == self._source_port
            and edge.target_port == self._target_port
        )
        if ports_same and self._at_nodes == self._node_place():
            if edge.metadata != self._edge.metadata or edge.edge_type != self._edge.edge_type:
                self._edge = edge.clone()
            return
        self._edge = edge.clone()
        self._source_port = edge.source_port
        self._target_port = edge.target_port
        self.update_path()
        self._update_pen()

    def set_compact(self, compact: bool) -> None:
        """Use a straight hairline when the graph is zoomed out."""

        if compact == self._compact:
            return
        self._compact = compact
        self.update_path()
        self._update_pen()

    def update_path(self) -> None:
        """Rebuild the curve from the current port positions."""

        start = self._source.port_scene_position("output", self._source_port)
        end = self._target.port_scene_position("input", self._target_port)
        path = QPainterPath(start)
        if self._compact:
            path.lineTo(end)
        else:
            bend = max(abs(end.x() - start.x()) * 0.5, 48.0)
            path.cubicTo(
                QPointF(start.x() + bend, start.y()),
                QPointF(end.x() - bend, end.y()),
                end,
            )
        self._shape_cache = None
        self.setPath(path)
        self._at_nodes = self._node_place()

    def _node_place(self) -> tuple[float, float, float, float]:
        """Node origins the current curve was built from.

        A refresh can skip rebuilding when these have not moved. Port changes
        go through ``update_path`` directly, so a matching origin still means
        the anchors are the ones already in the path.
        """

        source = self._source.pos()
        target = self._target.pos()
        return (source.x(), source.y(), target.x(), target.y())

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

    def itemChange(self, change: QGraphicsItem.GraphicsItemChange, value: object) -> object:
        if change == self.GraphicsItemChange.ItemSelectedHasChanged:
            self._apply_z()
            self._update_pen()
        return super().itemChange(change, value)

    def paint(self, painter: QPainter, option, widget=None) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, not self._compact)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        if option.state & QStyle.StateFlag.State_Selected:
            option.state &= ~QStyle.StateFlag.State_Selected
        painter.setPen(self.pen())
        painter.drawPath(self.path())

    def shape(self) -> QPainterPath:  # type: ignore[override]
        if self._shape_cache is None:
            stroker = QPainterPathStroker()
            stroker.setWidth(8.0 if self._compact else 14.0)
            stroker.setCapStyle(Qt.PenCapStyle.RoundCap)
            stroker.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            self._shape_cache = stroker.createStroke(self.path())
        return self._shape_cache

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
        pen = QPen(color, 1.0 if self._compact else width)
        pen.setCosmetic(self._compact)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        if self._flow_offset is not None and not self._compact:
            pen.setStyle(Qt.PenStyle.CustomDashLine)
            pen.setDashPattern([1.2, 7.0])
            pen.setDashOffset(self._flow_offset)
        self.setPen(pen)
        self.update()

    def set_flow_offset(self, offset: float | None) -> None:
        """Advance the direction dash. ``None`` restores a solid stroke."""

        if offset is None:
            if self._flow_offset is None:
                return
            self._flow_offset = None
            self._update_pen()
            return
        self._flow_offset = offset
        if self._compact:
            return
        pen = self.pen()
        if pen.style() != Qt.PenStyle.CustomDashLine:
            self._update_pen()
            return
        pen.setDashOffset(offset)
        self.setPen(pen)

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

