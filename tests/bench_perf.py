"""Phase-separated canvas timings. Not part of pytest.

Run from the repository:

    uv run python tests/bench_perf.py
    uv run python tests/bench_perf.py --quick

Writes ``docs/editor/perf-baseline.json``. Offscreen Qt, so paint times are
the software rasterizer, not a monitor's refresh rate. ``grid_chain`` matches
``tests/bench_scale.py``.

Each case keeps model build, JSON load, scene mount, first paint, and
interaction in separate fields. A number is one sample unless ``samples``
is set; interaction fields then carry median and max.
"""

from __future__ import annotations

import argparse
import gc
import json
import os
import platform
import sys
import time
import tracemalloc
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from PySide6 import __version__ as pyside_version
from PySide6.QtCore import QPoint, QPointF, Qt, qVersion
from PySide6.QtGui import QImage, QMouseEvent, QPainter
from PySide6.QtWidgets import QApplication, QStyleOptionGraphicsItem

from py_editor import CanvasController, CanvasView
from py_editor.graphics import CanvasEdgeItem, CanvasNodeItem
from py_editor.models import CanvasState, Port
from tests.perf_graphs import crossing, fan, grid_chain, sparse_grid

_DEFAULT_OUT = _ROOT / "docs" / "editor" / "perf-baseline.json"
_FRAME_16 = 16.7
_FRAME_33 = 33.3


def _ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000.0


def _round(value: float) -> float:
    return round(value, 2)


def _stats(samples: list[float]) -> dict[str, float | int]:
    ordered = sorted(samples)
    return {
        "n": len(ordered),
        "median_ms": _round(ordered[len(ordered) // 2]),
        "max_ms": _round(ordered[-1]),
        "over_16ms": sum(sample > _FRAME_16 for sample in ordered),
        "over_33ms": sum(sample > _FRAME_33 for sample in ordered),
    }


def _working_set() -> int | None:
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    class _MemoryCounters(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    counters = _MemoryCounters()
    counters.cb = ctypes.sizeof(counters)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    psapi.GetProcessMemoryInfo.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_MemoryCounters),
        wintypes.DWORD,
    ]
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
    if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
        return None
    return int(counters.WorkingSetSize)


def _mb(size: int | None) -> float | None:
    if size is None:
        return None
    return round(size / (1024 * 1024), 1)


def _paint(view: CanvasView) -> None:
    view.viewport().repaint()
    QApplication.processEvents()


def _environment(app: QApplication) -> dict[str, object]:
    screen = app.primaryScreen()
    geometry = None if screen is None else [screen.geometry().width(), screen.geometry().height()]
    return {
        "os": platform.platform(),
        "machine": platform.machine(),
        "processor": os.environ.get("PROCESSOR_IDENTIFIER", platform.processor()),
        "python": platform.python_version(),
        "pyside6": pyside_version,
        "qt": qVersion(),
        "qpa": os.environ.get("QT_QPA_PLATFORM", ""),
        "screen_px": geometry,
        "method": (
            "Offscreen. Model, JSON, mount, and first viewport.repaint() are "
            "separate clocks. Interaction samples include one repaint unless "
            "a field says move_ms. Median of the listed samples. "
            "Working set is the Windows process after that case, not a delta "
            "that subtracts Qt's cache."
        ),
    }


def _model_cost(build) -> tuple[CanvasState, dict[str, object]]:
    tracemalloc.start()
    started = time.perf_counter()
    state = build()
    model_ms = _ms(started)
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    started = time.perf_counter()
    payload = state.to_dict()
    save_ms = _ms(started)
    text = json.dumps(payload)
    started = time.perf_counter()
    restored = CanvasState.from_dict(json.loads(text))
    load_ms = _ms(started)
    return state, {
        "nodes": len(state.nodes),
        "edges": len(state.edges),
        "model_ms": _round(model_ms),
        "python_peak_mb": round(peak / (1024 * 1024), 2),
        "save_ms": _round(save_ms),
        "json_bytes": len(text),
        "load_ms": _round(load_ms),
        "load_nodes": len(restored.nodes),
    }


def _mount(state: CanvasState, width: int, height: int) -> tuple[CanvasView, dict[str, object]]:
    view = CanvasView()
    view.resize(width, height)
    started = time.perf_counter()
    view.set_controller(CanvasController(state))
    mount_ms = _ms(started)
    view.show()
    started = time.perf_counter()
    _paint(view)
    first_paint_ms = _ms(started)
    index = view.scene().itemIndexMethod()
    return view, {
        "viewport": [width, height],
        "mount_ms": _round(mount_ms),
        "first_paint_ms": _round(first_paint_ms),
        "scene_items": len(view.scene().items()),
        "index": index.name if hasattr(index, "name") else str(index),
        "working_set_mb": _mb(_working_set()),
    }


def _pointer(view: CanvasView, point: QPointF) -> QPointF:
    mapped = view.mapFromScene(point)
    return QPointF(mapped.x(), mapped.y())


def _mouse(kind: QMouseEvent.Type, point: QPointF, view: CanvasView) -> QMouseEvent:
    button = Qt.MouseButton.LeftButton
    # A release reports the button that went up, and no button still held.
    # Leaving LeftButton in the held set keeps the scene's mouse grab, so the
    # next scroll is delivered as a drag and the card jumps.
    held = Qt.MouseButton.NoButton if kind == QMouseEvent.Type.MouseButtonRelease else button
    local = QPoint(int(point.x()), int(point.y()))
    return QMouseEvent(
        kind,
        point,
        view.mapToGlobal(local),
        button,
        held,
        Qt.KeyboardModifier.NoModifier,
    )


def _pan(view: CanvasView, steps: int, dx: int) -> dict[str, float | int]:
    samples: list[float] = []
    for step in range(steps):
        started = time.perf_counter()
        bar = view.horizontalScrollBar()
        bar.setValue(bar.value() + dx)
        if step % 2:
            vertical = view.verticalScrollBar()
            vertical.setValue(vertical.value() + 12)
        _paint(view)
        samples.append(_ms(started))
    return _stats(samples)


def _zoom(view: CanvasView) -> dict[str, float | int]:
    samples: list[float] = []
    for _ in range(4):
        started = time.perf_counter()
        view.zoom_by(1 / 1.12)
        _paint(view)
        samples.append(_ms(started))
    return _stats(samples)


def _drag(view: CanvasView, node_id: str, dx: float) -> dict[str, float | int]:
    calls = {"n": 0}
    original = CanvasEdgeItem.update_path

    def counting(self: CanvasEdgeItem) -> None:
        calls["n"] += 1
        original(self)

    item = view._node_items[node_id]
    view.centerOn(item)
    _paint(view)
    point = _pointer(view, item.sceneBoundingRect().center())
    CanvasEdgeItem.update_path = counting  # type: ignore[method-assign]
    try:
        view.mousePressEvent(_mouse(QMouseEvent.Type.MouseButtonPress, point, view))
        moved = QPointF(point.x() + dx, point.y() + 6)
        started = time.perf_counter()
        view.mouseMoveEvent(_mouse(QMouseEvent.Type.MouseMove, moved, view))
        move_ms = _ms(started)
        moved_paths = calls["n"]
        started = time.perf_counter()
        _paint(view)
        paint_ms = _ms(started)
        painted_paths = calls["n"] - moved_paths
        view.mouseReleaseEvent(_mouse(QMouseEvent.Type.MouseButtonRelease, moved, view))
        _paint(view)
    finally:
        CanvasEdgeItem.update_path = original  # type: ignore[method-assign]
    degree = len(view._edges_by_node.get(node_id, ()))
    return {
        "move_ms": _round(move_ms),
        "paint_ms": _round(paint_ms),
        "update_path_on_move": moved_paths,
        "update_path_on_paint": painted_paths,
        "incident_edges": degree,
    }


def _drag_many(view: CanvasView, node_ids: list[str]) -> dict[str, float | int]:
    view._controller.set_selection(nodes=node_ids, edges=[])
    started = time.perf_counter()
    view._apply_selection(node_ids, [])
    select_ms = _ms(started)
    drag = _drag(view, node_ids[0], 24)
    drag["select_scan_ms"] = _round(select_ms)
    drag["moved_nodes"] = len(node_ids)
    return drag


def _batch(view: CanvasView, count: int) -> dict[str, float]:
    controller = view.controller
    assert controller is not None
    created: list[str] = []
    started = time.perf_counter()
    for index in range(count):
        node = controller.create_node(
            f"Extra {index}",
            position=(index * 30.0, -240.0),
            ports=[Port("in", "input"), Port("out", "output")],
        )
        created.append(node.id)
    command_ms = _ms(started)
    started = time.perf_counter()
    QApplication.processEvents()
    _paint(view)
    flush_ms = _ms(started)
    started = time.perf_counter()
    controller.delete(created)
    delete_command_ms = _ms(started)
    started = time.perf_counter()
    QApplication.processEvents()
    _paint(view)
    delete_flush_ms = _ms(started)
    return {
        "create_command_ms": _round(command_ms),
        "create_flush_ms": _round(flush_ms),
        "delete_command_ms": _round(delete_command_ms),
        "delete_flush_ms": _round(delete_flush_ms),
        "count": count,
    }


def _connect(view: CanvasView) -> dict[str, float | str]:
    controller = view.controller
    assert controller is not None
    made = controller.create_node("BenchSink", position=(-200.0, -200.0), ports=[Port("in", "input")])
    QApplication.processEvents()
    source = next(iter(controller.state.nodes))
    started = time.perf_counter()
    result = controller.connect(source, made.id)
    command_ms = _ms(started)
    started = time.perf_counter()
    QApplication.processEvents()
    _paint(view)
    flush_ms = _ms(started)
    if result.edge_id:
        controller.delete([], [result.edge_id])
        QApplication.processEvents()
    controller.delete([made.id])
    QApplication.processEvents()
    return {
        "accepted": result.accepted,
        "command_ms": _round(command_ms),
        "flush_ms": _round(flush_ms),
    }


def _fit(view: CanvasView) -> dict[str, float | bool]:
    started = time.perf_counter()
    view.fit_to_contents()
    fit_ms = _ms(started)
    started = time.perf_counter()
    _paint(view)
    paint_ms = _ms(started)
    started = time.perf_counter()
    _paint(view)
    second_ms = _ms(started)
    overview = view._overview
    return {
        "fit_ms": _round(fit_ms),
        "paint_ms": _round(paint_ms),
        "second_paint_ms": _round(second_ms),
        "overview_px": None if overview is None else [overview.width(), overview.height()],
        "compact": view._compact,
        "zoom": _round(abs(view.transform().m11())),
    }


def _noop_refresh(view: CanvasView) -> dict[str, float]:
    """Time the pieces of a refresh that does not change the graph."""

    def once(action) -> float:
        started = time.perf_counter()
        action()
        return _round(_ms(started))

    parts = {
        "nodes_scene_rect_ms": once(view._nodes_scene_rect),
        "sync_nodes_ms": once(lambda: view._sync_nodes(view.controller.state.nodes)),
        "sync_edges_ms": once(lambda: view._sync_edges(view.controller.state.edges)),
    }
    started = time.perf_counter()
    view.refresh()
    _paint(view)
    parts["refresh_and_paint_ms"] = _round(_ms(started))
    return parts


def _item_cost(view: CanvasView) -> dict[str, float]:
    item = next(iter(view._node_items.values()))
    item.shape()
    started = time.perf_counter()
    for _ in range(2000):
        item.boundingRect()
    bounds_us = _ms(started) / 2000 * 1000
    started = time.perf_counter()
    for _ in range(2000):
        item.shape()
    shape_us = _ms(started) / 2000 * 1000
    item._shape_cache = None
    started = time.perf_counter()
    item.shape()
    shape_cold_us = _ms(started) * 1000
    image = QImage(200, 120, QImage.Format.Format_ARGB32)
    painter = QPainter(image)
    option = QStyleOptionGraphicsItem()
    started = time.perf_counter()
    for _ in range(40):
        item.paint(painter, option, None)
    painter.end()
    paint_us = _ms(started) / 40 * 1000
    return {
        "bounding_rect_us": _round(bounds_us),
        "shape_cached_us": _round(shape_us),
        "shape_cold_us": _round(shape_cold_us),
        "paint_one_card_us": _round(paint_us),
    }


def _hits(view: CanvasView) -> dict[str, float | int]:
    samples: list[float] = []
    for x in range(40, 800, 40):
        started = time.perf_counter()
        view.itemAt(QPoint(x, view.height() // 2))
        samples.append(_ms(started))
    return _stats(samples)


def _close(view: CanvasView) -> None:
    view.close()
    view.deleteLater()
    QApplication.processEvents()
    gc.collect()


def _case(name: str, build, *, width: int, height: int, interact: str) -> dict[str, object]:
    state, model = _model_cost(build)
    view, mounted = _mount(state, width, height)
    row: dict[str, object] = {"layout": name, **model, **mounted}
    anchor = "n0" if "n0" in view._node_items else next(iter(view._node_items))
    view.centerOn(view._node_items[anchor])
    _paint(view)
    row["pan_1x"] = _pan(view, 8, 80)
    if interact == "full":
        row["zoom"] = _zoom(view)
        view.resetTransform()
        view._apply_detail()
        _paint(view)
        row["drag_one"] = _drag(view, anchor, 18)
        if name.startswith("fan"):
            leaf = next(node_id for node_id in view._node_items if node_id != "hub")
            row["drag_hub"] = _drag(view, "hub", 12)
            row["drag_leaf"] = _drag(view, leaf, 12)
        ids = list(view._node_items)[:8]
        if len(ids) >= 2:
            row["drag_many"] = _drag_many(view, ids)
        row["item_at"] = _hits(view)
        row["batch_50"] = _batch(view, 50)
        row["connect"] = _connect(view)
        row["noop_refresh"] = _noop_refresh(view)
        row["fit"] = _fit(view)
        row["pan_fitted"] = _pan(view, 4, 4)
    elif interact == "scale":
        row["drag_one"] = _drag(view, anchor, 18)
        row["fit"] = _fit(view)
        row["pan_fitted"] = _pan(view, 4, 4)
    elif interact == "paint":
        row["item_cost"] = _item_cost(view)
        row["drag_one"] = _drag(view, anchor, 18)
    _close(view)
    return row


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--quick", action="store_true", help="100 and 1000 only")
    parser.add_argument("--out", type=Path, default=_DEFAULT_OUT)
    args = parser.parse_args()
    app = QApplication.instance() or QApplication([])
    sizes = [100, 1000] if args.quick else [100, 500, 1000, 2000, 10000]
    rows: list[dict[str, object]] = []
    for count in sizes:
        kind = "full" if count in (1000, 2000) else "scale"
        if count == 100:
            kind = "paint"
        rows.append(_case(f"chain-{count}", lambda count=count: grid_chain(count), width=1280, height=720, interact=kind))
        print(f"chain-{count} mount={rows[-1]['mount_ms']} paint={rows[-1]['first_paint_ms']}", flush=True)
    if not args.quick:
        for name, build in (
            ("sparse-1000", lambda: sparse_grid(1000)),
            ("fan-1000", lambda: fan(1000)),
            ("crossing-1000", lambda: crossing(1000)),
        ):
            rows.append(_case(name, build, width=1280, height=720, interact="full"))
            print(f"{name} mount={rows[-1]['mount_ms']} paint={rows[-1]['first_paint_ms']}", flush=True)
        rows.append(_case("chain-1000-small", lambda: grid_chain(1000), width=320, height=240, interact="paint"))
        rows.append(_case("chain-1000-large", lambda: grid_chain(1000), width=1920, height=1080, interact="paint"))
    document = {"environment": _environment(app), "cases": rows}
    args.out.write_text(json.dumps(document, indent=2), encoding="utf-8")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
