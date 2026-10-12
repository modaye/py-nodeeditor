"""Reject one port id without copying the built-in connection checks.

Run: ``uv run python examples/connection_rule.py``
"""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from py_editor import CanvasController, CanvasPolicy, CanvasView, ConnectResult, Port
from py_editor.policy import ConnectRequest


def reject_blocked(_state, request: ConnectRequest) -> ConnectResult | None:
    """Refuse a wire whose target port id is ``blocked``."""

    if request.target_port != "blocked":
        return None
    return ConnectResult(
        accepted=False,
        reason="cancelled",
        source=request.source,
        target=request.target,
        source_port=request.source_port,
        target_port=request.target_port,
    )


def build() -> tuple[CanvasController, CanvasView]:
    """A sink whose only input is rejected by ``reject_blocked``."""

    controller = CanvasController(policy=CanvasPolicy(extra_rules=(reject_blocked,)))
    source = controller.create_node(
        "Source",
        position=(80.0, 160.0),
        ports=[Port("out", "output")],
    )
    sink = controller.create_node(
        "Sink",
        position=(360.0, 160.0),
        ports=[Port("blocked", "input")],
    )
    controller.connect(source.id, sink.id)
    view = CanvasView()
    view.set_controller(controller)
    return controller, view


def main() -> None:
    """Open the canvas. The two nodes stay unconnected."""

    app = QApplication(sys.argv)
    _controller, view = build()
    view.setWindowTitle("Connection rule")
    view.resize(900, 600)
    view.show()
    view.fit_to_contents()
    raise SystemExit(app.exec())


if __name__ == "__main__":
    main()
