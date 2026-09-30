"""Open a small canvas for trying the editor."""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from .controller import CanvasController
from .view import CanvasView


def main() -> None:
    """Show a few connected nodes using the default editing policy."""

    app = QApplication(sys.argv)
    controller = CanvasController()
    source = controller.create_node(
        "Source",
        position=(80.0, 180.0),
        outputs=["out"],
    )
    filt = controller.create_node(
        "Filter",
        position=(360.0, 80.0),
        inputs=["in"],
        outputs=["out"],
    )
    join = controller.create_node(
        "Join",
        position=(360.0, 280.0),
        inputs=["left", "right"],
        outputs=["out"],
    )
    sink = controller.create_node(
        "Output",
        position=(680.0, 180.0),
        inputs=["in"],
    )
    controller.connect(source.id, filt.id)
    controller.connect(source.id, join.id, target_port="left")
    controller.connect(filt.id, sink.id)

    view = CanvasView()
    view.setWindowTitle("Py Node Editor")
    view.resize(1020, 680)
    view.set_controller(controller)
    view.show()
    view.fit_to_contents()
    raise SystemExit(app.exec())


if __name__ == "__main__":
    main()
