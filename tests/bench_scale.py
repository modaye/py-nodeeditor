"""Time a large canvas. Run: uv run python tests/bench_scale.py 10000"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication

from py_editor import CanvasController, CanvasView
from tests.perf_graphs import grid_chain as chain


def _paint(view: CanvasView) -> None:
    view.viewport().repaint()
    QApplication.processEvents()


def _ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000.0


def bench(count: int) -> None:
    app = QApplication.instance() or QApplication([])
    started = time.perf_counter()
    state = chain(count)
    build_ms = _ms(started)

    view = CanvasView()
    view.resize(1280, 720)
    started = time.perf_counter()
    view.set_controller(CanvasController(state))
    view.show()
    _paint(view)
    mount_ms = _ms(started)

    view.centerOn(view._node_items["n0"])
    _paint(view)
    pans: list[float] = []
    for step in range(24):
        started = time.perf_counter()
        view.horizontalScrollBar().setValue(view.horizontalScrollBar().value() + 80)
        view.verticalScrollBar().setValue(view.verticalScrollBar().value() + (12 if step % 2 else 0))
        _paint(view)
        pans.append(_ms(started))

    started = time.perf_counter()
    view.fit_to_contents()
    _paint(view)
    fit_ms = _ms(started)
    overview_scale = abs(view.transform().m11())
    overview: list[float] = []
    for _ in range(8):
        started = time.perf_counter()
        view.horizontalScrollBar().setValue(view.horizontalScrollBar().value() + 4)
        _paint(view)
        overview.append(_ms(started))

    view.centerOn(view._node_items["n0"])
    view.resetTransform()
    view._apply_detail()
    _paint(view)
    hits: list[float] = []
    for x in range(40, 1200, 40):
        started = time.perf_counter()
        view.itemAt(QPoint(x, 360))
        hits.append(_ms(started))

    node = view._node_items["n0"]
    point = view.mapFromScene(node.sceneBoundingRect().center())
    press = QMouseEvent(
        QMouseEvent.Type.MouseButtonPress,
        point,
        view.mapToGlobal(point),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    view.mousePressEvent(press)
    drags: list[float] = []
    for step in range(16):
        moved = point + QPoint(step * 6, step * 2)
        event = QMouseEvent(
            QMouseEvent.Type.MouseMove,
            moved,
            view.mapToGlobal(moved),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        started = time.perf_counter()
        view.mouseMoveEvent(event)
        _paint(view)
        drags.append(_ms(started))

    def summary(samples: list[float]) -> str:
        ordered = sorted(samples)
        mid = ordered[len(ordered) // 2]
        return f"median {mid:.1f} ms  max {ordered[-1]:.1f} ms"

    items = len(view.scene().items())
    print(
        f"nodes={count} scene_items={items} "
        f"overview_scale={overview_scale:.3f} compact_now={view._compact}"
    )
    print(f"  build model     {build_ms:.0f} ms")
    print(f"  mount + first   {mount_ms:.0f} ms")
    print(f"  pan at 1x       {summary(pans)}")
    print(f"  fit all         {fit_ms:.0f} ms")
    print(f"  pan overview    {summary(overview)}")
    print(f"  itemAt          {summary(hits)}")
    print(f"  drag node       {summary(drags)}")
    view.close()
    app.processEvents()


if __name__ == "__main__":
    counts = [int(arg) for arg in sys.argv[1:]] or [2000, 10000]
    for count in counts:
        bench(count)
