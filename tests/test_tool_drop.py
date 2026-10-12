"""A tool dragged from a palette lands on the canvas through the view events."""

from PySide6.QtCore import QMimeData, QPoint, Qt, QtMsgType, qInstallMessageHandler
from PySide6.QtGui import QDragEnterEvent, QDragLeaveEvent, QDragMoveEvent, QDropEvent
from PySide6.QtWidgets import QApplication

from py_editor import CanvasView

_MIME = "application/x-demo-tool"


def test_dragging_a_tool_onto_the_view_reports_its_id() -> None:
    app = QApplication.instance() or QApplication([])
    view = CanvasView(drop_mime=_MIME)
    seen: list[str] = []
    view.tool_dropped.connect(lambda tool_id, _x, _y, _edge: seen.append(tool_id))
    mime = QMimeData()
    mime.setData(_MIME, b"read_csv")
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
    warnings = _warnings_from(lambda: app.notify(view.viewport(), QDragLeaveEvent()))
    assert warnings == []


def test_a_tool_drag_can_leave_without_a_qt_warning() -> None:
    app = QApplication.instance() or QApplication([])
    view = CanvasView(drop_mime=_MIME)
    mime = QMimeData()
    mime.setData(_MIME, b"read_csv")
    arguments = (
        QPoint(30, 40),
        Qt.DropAction.CopyAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )

    def leave() -> None:
        assert app.notify(view.viewport(), QDragEnterEvent(*arguments))
        assert app.notify(view.viewport(), QDragLeaveEvent())

    assert _warnings_from(leave) == []


def _warnings_from(action) -> list[str]:
    found: list[str] = []

    def handler(mode, _context, message: str) -> None:
        if mode == QtMsgType.QtWarningMsg:
            found.append(message)

    previous = qInstallMessageHandler(handler)
    try:
        action()
    finally:
        qInstallMessageHandler(previous)
    return found
