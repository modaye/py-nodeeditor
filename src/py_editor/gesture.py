"""Preview curve shown while the user drags a connection."""

from __future__ import annotations

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QFont, QPainterPath, QPen
from PySide6.QtWidgets import QGraphicsPathItem, QGraphicsScene, QGraphicsSimpleTextItem

from .policy import ConnectRequest, PortHit

__all__ = ["ConnectionGesture", "connection_path"]


def connection_path(start: QPointF, end: QPointF) -> QPainterPath:
    """Cubic curve that leaves ``start`` to the right and enters ``end`` from the left."""

    path = QPainterPath(start)
    bend = max(abs(end.x() - start.x()) * 0.5, 48.0)
    path.cubicTo(
        QPointF(start.x() + bend, start.y()),
        QPointF(end.x() - bend, end.y()),
        end,
    )
    return path


class ConnectionGesture:
    """Own the temporary wire used by port drags and edge reconnects."""

    def __init__(self, scene: QGraphicsScene) -> None:
        self._scene = scene
        self._path: QGraphicsPathItem | None = None
        self._caption: QGraphicsSimpleTextItem | None = None
        self._origin: PortHit | None = None
        self._snapped: PortHit | None = None
        self._replace_ids: tuple[str, ...] = ()

    @property
    def active(self) -> bool:
        return self._origin is not None

    @property
    def origin(self) -> PortHit | None:
        return self._origin

    @property
    def snapped(self) -> PortHit | None:
        return self._snapped

    @property
    def replace_ids(self) -> tuple[str, ...]:
        return self._replace_ids

    def begin(
        self,
        origin: PortHit,
        replace_edge_ids: tuple[str, ...] = (),
    ) -> None:
        """Start a wire at ``origin``. ``replace_edge_ids`` are removed on success."""

        self.cancel()
        self._origin = origin
        self._replace_ids = replace_edge_ids
        path = QGraphicsPathItem()
        path.setZValue(30.0)
        path.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self._scene.addItem(path)
        self._path = path
        caption = QGraphicsSimpleTextItem()
        caption.setZValue(31.0)
        caption.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        caption.setBrush(QColor("#0F172A"))
        font = QFont()
        font.setPointSize(9)
        caption.setFont(font)
        self._scene.addItem(caption)
        self._caption = caption

    def update(
        self,
        start: QPointF,
        end: QPointF,
        snapped: PortHit | None,
        *,
        color: str,
        dashed: bool,
        width: float = 2.0,
        caption: str = "",
    ) -> None:
        """Redraw the preview. ``start`` is the fixed port and ``end`` is the pointer or snap point."""

        self._snapped = snapped
        if self._path is None or self._origin is None:
            return
        if self._origin.direction == "output":
            curve = connection_path(start, end)
        else:
            curve = connection_path(end, start)
        self._path.setPath(curve)
        pen = QPen(QColor(color), width)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        pen.setStyle(Qt.PenStyle.DashLine if dashed else Qt.PenStyle.SolidLine)
        self._path.setPen(pen)
        if self._caption is not None:
            self._caption.setText(caption)
            midpoint = curve.pointAtPercent(0.55)
            self._caption.setPos(midpoint.x() + 8.0, midpoint.y() - 18.0)

    def request_for(self, snapped: PortHit | None) -> ConnectRequest | None:
        """Build a connection request from the fixed port and ``snapped``."""

        origin = self._origin
        if origin is None or snapped is None:
            return None
        if origin.direction == "output":
            return ConnectRequest(
                source=origin.node_id,
                target=snapped.node_id,
                source_port=origin.port_id,
                target_port=snapped.port_id,
            )
        return ConnectRequest(
            source=snapped.node_id,
            target=origin.node_id,
            source_port=snapped.port_id,
            target_port=origin.port_id,
        )

    def cancel(self) -> None:
        """Remove the preview without changing the graph."""

        if self._path is not None:
            self._scene.removeItem(self._path)
            self._path = None
        if self._caption is not None:
            self._scene.removeItem(self._caption)
            self._caption = None
        self._origin = None
        self._snapped = None
        self._replace_ids = ()
