"""Save a graph to JSON and open that document again.

Run: ``uv run python examples/save_restore.py``
"""

from __future__ import annotations

import json
import sys

from PySide6.QtWidgets import QApplication

from py_editor import CanvasController, CanvasState, CanvasView, Port, Viewport


def build() -> CanvasController:
    """One wire, with the camera stored on the document."""

    controller = CanvasController()
    source = controller.create_node(
        "Source",
        node_id="source",
        position=(80.0, 160.0),
        ports=[Port("out", "output", kind="table")],
    )
    sink = controller.create_node(
        "Sink",
        node_id="sink",
        position=(360.0, 160.0),
        ports=[Port("in", "input", kind="table")],
    )
    controller.connect(source.id, sink.id, source_port="out", target_port="in")
    controller.state.viewport = Viewport(220.0, 180.0, 1.0)
    return controller


def dump(controller: CanvasController) -> str:
    """Serialize the controller's document."""

    return json.dumps(controller.state.to_dict())


def load(text: str) -> CanvasController:
    """Build a controller from :func:`dump` output. Version 1 only."""

    return CanvasController(CanvasState.from_dict(json.loads(text)))


def main() -> None:
    """Save the sample, load it, and show the restored camera."""

    app = QApplication(sys.argv)
    controller = load(dump(build()))
    view = CanvasView()
    view.setWindowTitle("Restored graph")
    view.resize(900, 600)
    view.set_controller(controller)
    view.show()
    raise SystemExit(app.exec())


if __name__ == "__main__":
    main()
