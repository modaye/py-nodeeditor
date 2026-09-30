"""A tool dragged from a palette lands on the canvas through the view events."""

from PySide6.QtCore import QMimeData, QPoint, Qt
from PySide6.QtGui import QDragEnterEvent, QDragMoveEvent, QDropEvent
from PySide6.QtWidgets import QApplication

from py_editor import TOOL_DROP_MIME, CanvasView


def test_dragging_a_tool_onto_the_view_reports_its_id() -> None:
    app = QApplication.instance() or QApplication([])
    view = CanvasView()
    seen: list[str] = []
    view.tool_dropped.connect(lambda tool_id, _x, _y: seen.append(tool_id))
    mime = QMimeData()
    mime.setData(TOOL_DROP_MIME, b"read_csv")
    arguments = (
        QPoint(30, 40),
        Qt.DropAction.CopyAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    entered = QDragEnterEvent(*arguments)
    moved = QDragMoveEvent(*arguments)
    dropped = QDropEvent(*arguments)
    assert app.notify(view.viewport(), entered)
    assert entered.isAccepted()
    assert app.notify(view.viewport(), moved)
    assert app.notify(view.viewport(), dropped)
    assert seen == ["read_csv"]
