"""A node that only changes paint. Ports stay on ``port_anchor``.

Run: ``uv run python examples/custom_node.py``
"""

from __future__ import annotations

import sys

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QApplication, QStyle

from py_editor import CanvasController, CanvasNodeItem, CanvasRegistry, CanvasView, Port


class AccentNode(CanvasNodeItem):
    """Default card plus a violet bar under the title."""

    def paint(self, painter: QPainter, option, widget=None) -> None:
        super().paint(painter, option, widget)
        if option.state & QStyle.StateFlag.State_Selected:
            option.state &= ~QStyle.StateFlag.State_Selected
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#7C3AED"))
        painter.drawRect(QRectF(0.0, 28.0, self.card_size[0], 3.0))


def build() -> tuple[CanvasController, CanvasView]:
    """Register ``AccentNode`` and connect two of them."""

    registry = CanvasRegistry()
    registry.register_node_factory("accent", lambda node, _view: AccentNode(node))
    controller = CanvasController()
    source = controller.create_node(
        "Source",
        node_type="accent",
        position=(80.0, 160.0),
        ports=[Port("out", "output")],
    )
    sink = controller.create_node(
        "Sink",
        node_type="accent",
        position=(360.0, 160.0),
        ports=[Port("in", "input")],
    )
    controller.connect(source.id, sink.id)
    view = CanvasView(registry=registry)
    view.set_controller(controller)
    return controller, view


def main() -> None:
    """Open the accent-node window."""

    app = QApplication(sys.argv)
    _controller, view = build()
    view.setWindowTitle("Custom node")
    view.resize(900, 600)
    view.show()
    view.fit_to_contents()
    raise SystemExit(app.exec())


if __name__ == "__main__":
    main()
