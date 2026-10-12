"""Two connected nodes on a canvas.

Run: ``uv run python examples/basic_editor.py``

``view.set_snap_to_grid(True)`` aligns a node to the 24px background grid
when the drag is released. It is off unless the host turns it on.
"""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from py_editor import CanvasController, CanvasView, Port


def build() -> tuple[CanvasController, CanvasView]:
    """Return a controller and a view that already shows one wire."""

    controller = CanvasController()
    source = controller.create_node(
        "Source",
        position=(80.0, 160.0),
        ports=[Port("out", "output")],
    )
    sink = controller.create_node(
        "Sink",
        position=(360.0, 160.0),
        ports=[Port("in", "input")],
    )
    controller.connect(source.id, sink.id)
    view = CanvasView()
    view.set_controller(controller)
    return controller, view


def main() -> None:
    """Open the window and frame both nodes."""

    app = QApplication(sys.argv)
    _controller, view = build()
    view.setWindowTitle("Basic editor")
    view.resize(900, 600)
    view.show()
    view.fit_to_contents()
    raise SystemExit(app.exec())


if __name__ == "__main__":
    main()
