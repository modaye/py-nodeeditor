from __future__ import annotations

from PySide6.QtCore import QObject, QPoint, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QPainter,
    QPainterPath,
    QPainterPathStroker,
    QPen,
)
from PySide6.QtWidgets import QGraphicsObject, QGraphicsPathItem, QStyle

from .models import EdgeData, NodeData

__all__ = [
    "CanvasNodeItem",
    "CanvasEdgeItem",
    "CanvasConnectionHandle",
]


_NODE_WIDTH = 160.0
_NODE_HEIGHT = 72.0


class CanvasConnectionHandle(QGraphicsObject):
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
        self._press_pos = QPointF()
        self.setAcceptHoverEvents(True)
        self.setAcceptedMouseButtons(Qt.MouseButton.LeftButton)
        self.setZValue(20.0)

    def boundingRect(self) -> QRectF:  # type: ignore[override]
        radius = 6.0
        return QRectF(-radius, -radius, radius * 2, radius * 2)

    def paint(self, painter: QPainter, option, widget=None) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        base_color = QColor("#38BDF8")
        if self.direction == "input":
            base_color = QColor("#A855F7")
        if self._dragging:
            color = base_color.lighter(140)
        elif self._hovered:
            color = base_color.lighter(120)
        else:
            color = base_color
        painter.setBrush(color)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(self.boundingRect())

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
    position_changed = Signal(str, float, float)
    selection_changed = Signal(str, bool)
    double_clicked = Signal(str)
    connection_drag_started = Signal(str, str, str, QPointF)
    connection_drag_moved = Signal(str, str, str, QPointF)
    connection_drag_finished = Signal(str, str, str, QPointF)
    ports_changed = Signal(str)
    clicked = Signal(str, Qt.MouseButton, QPointF)
    context_menu_requested = Signal(str, QPoint, QPointF)
    hover_entered = Signal(str, QPointF)
    hover_left = Signal(str)
    hover_moved = Signal(str, QPointF)
    drag_started = Signal(str, QPointF)
    drag_moved = Signal(str, QPointF)
    drag_finished = Signal(str, QPointF)

    def __init__(self, node: NodeData, parent=None) -> None:
        super().__init__(parent)
        self._node = node.clone()
        self.node_id = node.id
        self._hovered = False
        self.setFlag(self.GraphicsItemFlag.ItemIsMovable, True)
        self.setFlag(self.GraphicsItemFlag.ItemIsSelectable, True)
        self.setFlag(self.GraphicsItemFlag.ItemSendsGeometryChanges, True)
        self.setZValue(10.0)
        self.setPos(node.position[0], node.position[1])
        self.setAcceptHoverEvents(True)
        self._input_handles: dict[str, CanvasConnectionHandle] = {}
        self._output_handles: dict[str, CanvasConnectionHandle] = {}
        self._press_pos = QPointF()
        self._dragging = False
        self._rebuild_handles_for_direction("input", self._node.inputs)
        self._rebuild_handles_for_direction("output", self._node.outputs)
        self._position_handles()

    def boundingRect(self) -> QRectF:  # type: ignore[override]
        return QRectF(0.0, 0.0, _NODE_WIDTH, _NODE_HEIGHT)

    def paint(self, painter: QPainter, option, widget=None) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = self.boundingRect()
        radius = 8.0
        background = QColor("#1F2937")
        border = QColor("#475569")
        if self.isSelected():
            background = QColor("#334155")
            border = QColor("#60A5FA")
        painter.setBrush(background)
        painter.setPen(QPen(border, 1.5))
        painter.drawRoundedRect(rect, radius, radius)

        header_rect = QRectF(rect.x(), rect.y(), rect.width(), 24.0)
        painter.setBrush(QColor("#334155"))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(header_rect, radius, radius)

        painter.setPen(QColor("#E2E8F0"))
        title_rect = header_rect.adjusted(8.0, 4.0, -8.0, -4.0)
        painter.drawText(
            title_rect,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            self._node.title,
        )

    def update_from_node(self, node: NodeData) -> None:
        previous_inputs = list(self._node.inputs)
        previous_outputs = list(self._node.outputs)
        self._node = node.clone()
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

    def itemChange(self, change, value):  # type: ignore[override]
        if change == self.GraphicsItemChange.ItemPositionHasChanged:
            if not self.signalsBlocked():
                point: QPointF = value
                self.position_changed.emit(
                    self.node_id,
                    point.x(),
                    point.y(),
                )
        elif change == self.GraphicsItemChange.ItemSelectedHasChanged:
            if not self.signalsBlocked():
                self.selection_changed.emit(self.node_id, bool(value))
        return super().itemChange(change, value)

    def mouseDoubleClickEvent(self, event) -> None:  # type: ignore[override]
        self.double_clicked.emit(self.node_id)
        event.accept()

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_pos = event.scenePos()
            self._dragging = False
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # type: ignore[override]
        is_left = bool(event.buttons() & Qt.MouseButton.LeftButton)
        if is_left and not self._dragging:
            if (event.scenePos() - self._press_pos).manhattanLength() >= 4.0:
                self._dragging = True
                self.drag_started.emit(self.node_id, self.scenePos())
        super().mouseMoveEvent(event)
        if is_left and self._dragging:
            self.drag_moved.emit(self.node_id, self.scenePos())

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        was_dragging = self._dragging
        self._dragging = False
        super().mouseReleaseEvent(event)
        if was_dragging:
            self.drag_finished.emit(self.node_id, self.scenePos())
        elif event.button() == Qt.MouseButton.LeftButton:
            if (event.scenePos() - self._press_pos).manhattanLength() <= 4.0:
                self.clicked.emit(
                    self.node_id,
                    event.button(),
                    event.scenePos(),
                )

    def contextMenuEvent(self, event) -> None:  # type: ignore[override]
        self.context_menu_requested.emit(
            self.node_id,
            event.screenPos(),
            event.scenePos(),
        )
        event.accept()

    def hoverEnterEvent(self, event) -> None:  # type: ignore[override]
        self._hovered = True
        self.update()
        self.hover_entered.emit(self.node_id, event.scenePos())
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event) -> None:  # type: ignore[override]
        self._hovered = False
        self.update()
        self.hover_left.emit(self.node_id)
        super().hoverLeaveEvent(event)

    def hoverMoveEvent(self, event) -> None:  # type: ignore[override]
        self.hover_moved.emit(self.node_id, event.scenePos())
        super().hoverMoveEvent(event)

    def _position_handles(self) -> None:
        height = self.boundingRect().height()
        width = self.boundingRect().width()
        self._position_port_group(self._input_handles, -12.0, height)
        self._position_port_group(
            self._output_handles,
            width + 12.0,
            height,
        )

    def _position_port_group(
        self,
        handles: dict[str, CanvasConnectionHandle],
        x: float,
        height: float,
    ) -> None:
        count = len(handles)
        if count == 0:
            return
        step = height / (count + 1)
        for index, handle in enumerate(handles.values(), start=1):
            handle.setPos(x, step * index)

    def _rebuild_handles_for_direction(
        self,
        direction: str,
        ports: list[str],
    ) -> None:
        mapping = (
            self._input_handles
            if direction == "input"
            else self._output_handles
        )
        for handle in mapping.values():
            handle.deleteLater()
        mapping.clear()
        for port_id in ports:
            handle = CanvasConnectionHandle(
                self.node_id,
                direction,
                port_id,
                self,
            )
            handle.drag_started.connect(self._relay_connection_drag_started)
            handle.drag_moved.connect(self._relay_connection_drag_moved)
            handle.drag_finished.connect(self._relay_connection_drag_finished)
            mapping[port_id] = handle

    def _relay_connection_drag_started(
        self,
        node_id: str,
        direction: str,
        port_id: str,
        anchor: QPointF,
    ) -> None:
        self.connection_drag_started.emit(node_id, direction, port_id, anchor)

    def _relay_connection_drag_moved(
        self,
        node_id: str,
        direction: str,
        port_id: str,
        position: QPointF,
    ) -> None:
        self.connection_drag_moved.emit(node_id, direction, port_id, position)

    def _relay_connection_drag_finished(
        self,
        node_id: str,
        direction: str,
        port_id: str,
        position: QPointF,
    ) -> None:
        self.connection_drag_finished.emit(
            node_id,
            direction,
            port_id,
            position,
        )

    def port_scene_position(
        self,
        direction: str,
        port_id: str | None,
    ) -> QPointF:
        handle = None
        if direction == "input" and port_id is not None:
            handle = self._input_handles.get(port_id)
        elif direction == "output" and port_id is not None:
            handle = self._output_handles.get(port_id)
        if handle is not None:
            return handle.scenePos()
        rect = self.boundingRect()
        center = rect.center()
        return self.scenePos() + QPointF(center.x(), center.y())

    @property
    def node_data(self) -> NodeData:
        return self._node.clone()

    def _draw_port_labels(self, painter: QPainter) -> None:
        if not self._node.inputs and not self._node.outputs:
            return
        rect = self.boundingRect()
        label_height = painter.fontMetrics().height()
        if label_height <= 0:
            label_height = 16.0
        inner_margin = 10.0
        available_width = max(rect.width() - (inner_margin * 2), 0.0)
        half_width = available_width * 0.5
        for port_id in self._node.inputs:
            handle = self._input_handles.get(port_id)
            if handle is None:
                continue
            pos = handle.pos()
            text_rect = QRectF(
                rect.left() + inner_margin,
                pos.y() - (label_height / 2.0),
                half_width,
                label_height,
            )
            painter.drawText(
                text_rect,
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                port_id,
            )
        for port_id in self._node.outputs:
            handle = self._output_handles.get(port_id)
            if handle is None:
                continue
            pos = handle.pos()
            text_rect = QRectF(
                rect.right() - inner_margin - half_width,
                pos.y() - (label_height / 2.0),
                half_width,
                label_height,
            )
            painter.drawText(
                text_rect,
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                port_id,
            )


class CanvasEdgeItem(QGraphicsPathItem, QObject):
    clicked = Signal(str, Qt.MouseButton, QPointF)
    double_clicked = Signal(str, QPointF)
    context_menu_requested = Signal(str, QPoint, QPointF)
    hover_entered = Signal(str)
    hover_left = Signal(str)

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
        self.setZValue(1.0)
        self.setFlag(self.GraphicsItemFlag.ItemIsSelectable, True)
        self.setAcceptHoverEvents(True)
        self._press_pos = QPointF()
        source_item.position_changed.connect(self._handle_node_moved)
        target_item.position_changed.connect(self._handle_node_moved)
        source_item.ports_changed.connect(self._handle_ports_changed)
        target_item.ports_changed.connect(self._handle_ports_changed)
        self.update_path()
        self._update_pen()

    def update_from_edge(self, edge: EdgeData) -> None:
        self._edge = edge.clone()
        self._source_port = edge.source_port
        self._target_port = edge.target_port
        self.update_path()
        self._update_pen()

    def update_path(self) -> None:
        start = self._source.port_scene_position("output", self._source_port)
        end = self._target.port_scene_position("input", self._target_port)
        path = QPainterPath(start)
        delta_x = (end.x() - start.x()) * 0.5
        control_1 = QPointF(start.x() + delta_x, start.y())
        control_2 = QPointF(end.x() - delta_x, end.y())
        path.cubicTo(control_1, control_2, end)
        self.setPath(path)
        self._update_pen()

    def hoverEnterEvent(self, event) -> None:  # type: ignore[override]
        self._hovered = True
        self._update_pen()
        self.hover_entered.emit(self.edge_id)
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event) -> None:  # type: ignore[override]
        self._hovered = False
        self._update_pen()
        self.hover_left.emit(self.edge_id)
        super().hoverLeaveEvent(event)

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_pos = event.scenePos()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        super().mouseReleaseEvent(event)
        if event.button() == Qt.MouseButton.LeftButton:
            if (event.scenePos() - self._press_pos).manhattanLength() <= 4.0:
                self.clicked.emit(
                    self.edge_id,
                    event.button(),
                    event.scenePos(),
                )

    def mouseDoubleClickEvent(self, event) -> None:  # type: ignore[override]
        self.double_clicked.emit(self.edge_id, event.scenePos())
        event.accept()

    def contextMenuEvent(self, event) -> None:  # type: ignore[override]
        self.context_menu_requested.emit(
            self.edge_id,
            event.screenPos(),
            event.scenePos(),
        )
        event.accept()

    def itemChange(self, change, value):  # type: ignore[override]
        if change == self.GraphicsItemChange.ItemSelectedHasChanged:
            self._update_pen()
        return super().itemChange(change, value)

    def _handle_node_moved(self, *_args) -> None:
        self.update_path()

    def _handle_ports_changed(self, _node_id: str) -> None:
        self.update_path()

    def _update_pen(self) -> None:
        color = QColor("#CBD5F5")
        width = 1.6
        if self.isSelected():
            color = QColor("#60A5FA")
            width = 2.4
        elif self._hovered:
            color = QColor("#A5B4FC")
            width = 1.9
        pen = QPen(color, width)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        self.setPen(pen)
        self.update()

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

    @property
    def edge_data(self) -> EdgeData:
        return self._edge.clone()
