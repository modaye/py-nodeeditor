"""Repeatable canvas audit. Does not change editor code.

Run: uv run python tests/audit_canvas.py
Prints one JSON object. Offscreen, so the numbers are the software rasterizer.
"""

from __future__ import annotations

import ctypes
import gc
import json
import os
import platform
import sys
import time
from ctypes import wintypes

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import __version__ as pyside_version
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication, QGraphicsScene

from py_editor import CanvasController, CanvasView
from py_editor.graphics import NODE_WIDTH, CanvasEdgeItem, CanvasNodeItem
from py_editor.models import CanvasState, EdgeData, NodeData, Port
from py_editor.policy import introduces_cycle

WIDTH = 1280
HEIGHT = 720
COUNTS = (2000, 10000, 20000)


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


def _working_set() -> int:
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
    ok = psapi.GetProcessMemoryInfo(
        kernel.GetCurrentProcess(),
        ctypes.byref(counters),
        counters.cb,
    )
    if not ok:
        raise OSError(ctypes.get_last_error())
    return int(counters.WorkingSetSize)


def _mb(size: int) -> float:
    return round(size / (1024 * 1024), 1)


def _ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000.0


def _median(samples: list[float]) -> float:
    ordered = sorted(samples)
    return ordered[len(ordered) // 2]


def chain(count: int) -> CanvasState:
    """Grid of nodes. Each node links to the next, including row wraps."""

    state = CanvasState()
    columns = max(int(count**0.5), 1)
    for index in range(count):
        node_id = f"n{index}"
        state.nodes[node_id] = NodeData(
            id=node_id,
            title=f"Step {index}",
            position=((index % columns) * 240.0, (index // columns) * 140.0),
            ports=(
                [Port("out", "output")]
                if index == 0
                else [Port("in", "input")]
                if index == count - 1
                else [Port("in", "input"), Port("out", "output")]
            ),
        )
        if index == 0:
            continue
        state.edges[f"e{index}"] = EdgeData(
            f"e{index}",
            f"n{index - 1}",
            node_id,
            source_port="out",
            target_port="in",
        )
    return state


def _paint(view: CanvasView) -> None:
    view.viewport().repaint()
    QApplication.processEvents()


def _sample(action, repeats: int = 8) -> dict[str, float]:
    samples: list[float] = []
    for _ in range(repeats):
        started = time.perf_counter()
        action()
        samples.append(_ms(started))
    return {"median_ms": round(_median(samples), 2), "max_ms": round(max(samples), 2)}


def _install_build_timers(view: CanvasView) -> dict[str, float]:
    bucket = {"node_ms": 0.0, "edge_ms": 0.0, "add_ms": 0.0}
    create_node = view._create_node_item
    create_edge = view._create_edge_item
    add_item = view._scene.addItem

    def timed_node(node: NodeData) -> CanvasNodeItem:
        started = time.perf_counter()
        item = create_node(node)
        bucket["node_ms"] += _ms(started)
        return item

    def timed_edge(edge, source, target) -> CanvasEdgeItem:
        started = time.perf_counter()
        item = create_edge(edge, source, target)
        bucket["edge_ms"] += _ms(started)
        return item

    def timed_add(item) -> None:
        started = time.perf_counter()
        add_item(item)
        bucket["add_ms"] += _ms(started)

    view._create_node_item = timed_node  # type: ignore[method-assign]
    view._create_edge_item = timed_edge  # type: ignore[method-assign]
    view._scene.addItem = timed_add  # type: ignore[method-assign]
    return bucket


def _open(state: CanvasState) -> tuple[CanvasView, dict[str, float], float]:
    view = CanvasView()
    view.resize(WIDTH, HEIGHT)
    bucket = _install_build_timers(view)
    started = time.perf_counter()
    view.set_controller(CanvasController(state))
    view.show()
    _paint(view)
    mount_ms = _ms(started)
    return view, bucket, mount_ms


def _pan(view: CanvasView, dx: int) -> None:
    view.horizontalScrollBar().setValue(view.horizontalScrollBar().value() + dx)
    _paint(view)


def _drag_once(view: CanvasView, node_id: str, dx: float) -> tuple[float, int]:
    calls = {"n": 0}
    original = CanvasEdgeItem.update_path

    def counting(self: CanvasEdgeItem) -> None:
        calls["n"] += 1
        original(self)

    CanvasEdgeItem.update_path = counting  # type: ignore[method-assign]
    item = view._node_items[node_id]
    point = QPointF(view.mapFromScene(item.sceneBoundingRect().center()))
    try:
        view.mousePressEvent(_mouse(QMouseEvent.Type.MouseButtonPress, point, view))
        moved = QPointF(point.x() + dx, point.y() + 4)
        started = time.perf_counter()
        view.mouseMoveEvent(_mouse(QMouseEvent.Type.MouseMove, moved, view))
        _paint(view)
        elapsed = _ms(started)
        view.mouseReleaseEvent(_mouse(QMouseEvent.Type.MouseButtonRelease, moved, view))
        _paint(view)
    finally:
        CanvasEdgeItem.update_path = original  # type: ignore[method-assign]
    return elapsed, calls["n"]


def _mouse(kind: QMouseEvent.Type, point: QPointF, view: CanvasView) -> QMouseEvent:
    button = Qt.MouseButton.LeftButton
    return QMouseEvent(
        kind,
        point,
        view.mapToGlobal(point.toPoint()),
        button,
        button,
        Qt.KeyboardModifier.NoModifier,
    )


def _checks(view: CanvasView, count: int) -> list[dict[str, object]]:
    """Correctness probes against the live view. Each one restores what it changes."""

    checks: list[dict[str, object]] = []

    def record(name: str, ok: bool, detail: str) -> None:
        checks.append({"name": name, "ok": ok, "detail": detail})

    view.fit_to_contents()
    _paint(view)
    record(
        "fit enters compact and builds a cache",
        view._compact and view._overview is not None and not view._overview.isNull(),
        f"compact={view._compact} cache={view._overview is not None} "
        f"scale={view.transform().m11():.4f} "
        f"cache_px={None if view._overview is None else (view._overview.width(), view._overview.height())}",
    )
    cold = view._overview
    _paint(view)
    record(
        "second overview paint reuses the pixmap",
        view._overview is cold,
        "same object" if view._overview is cold else "rebuilt",
    )

    node = view._node_items["n10"]
    before = _colors_on_node(view, node)
    view.controller.state.update_node("n10", title="Changed")
    view.sync_node("n10")
    invalidated = view._overview is None
    _paint(view)
    after = _colors_on_node(view, node)
    record(
        "title change clears the overview cache",
        invalidated,
        f"cleared={invalidated}",
    )
    record(
        "title change is visible in the overview picture",
        any(color != before_color for color, before_color in zip(after, before)),
        f"before={before[:4]} after={after[:4]}",
    )
    view.controller.state.update_node("n10", metadata={})
    view.sync_node("n10")
    _paint(view)

    degree = len(view._edges_by_node.get("n10", ()))
    _elapsed, updated = _drag_once(view, "n10", 24)
    record(
        "dragging a node updates only its wires",
        updated == degree and degree > 0,
        f"update_path={updated} degree={degree} edges={len(view._edge_items)}",
    )

    port = (
        node.port_anchor("output", "out")
        if "out" in node._node.port_ids("output")
        else node.port_anchor("input", "in")
    )
    record(
        "compact cards do not start a wire from a port",
        view._compact and node._port_at(port) is None,
        f"compact={view._compact}",
    )

    center = view.mapFromScene(node.sceneBoundingRect().center())
    hit = view.itemAt(center)
    hit_node = hit if isinstance(hit, CanvasNodeItem) else None
    record(
        "overview hit-testing still finds the node",
        hit_node is not None and hit_node.node_id == node.node_id,
        f"hit={type(hit).__name__ if hit is not None else None}",
    )

    matched = view.focus_titled("Step 50")
    record(
        "title search uses substring, so Step 50 also matches Step 500",
        "n50" in matched and len(matched) > 1,
        f"matches={len(matched)}",
    )
    view.controller.clear_selection()
    view.refresh()
    return checks


def _colors_on_node(view: CanvasView, node: CanvasNodeItem) -> list[tuple[int, int, int]]:
    if view._overview is None:
        return []
    image = view._overview.toImage()
    rect = node.sceneBoundingRect()
    scale = view._overview_scale
    origin = view._overview_rect
    colors: list[tuple[int, int, int]] = []
    for sx, sy in (
        (rect.center().x(), rect.center().y()),
        (rect.left() + 2, rect.top() + 2),
        (rect.right() - 2, rect.center().y()),
    ):
        px = int((sx - origin.left()) * scale)
        py = int((sy - origin.top()) * scale)
        px = min(max(px, 0), image.width() - 1)
        py = min(max(py, 0), image.height() - 1)
        color = image.pixelColor(px, py)
        colors.append((color.red(), color.green(), color.blue()))
    return colors


def _cycle(state: CanvasState, count: int) -> dict[str, float | bool]:
    """Time a walk of the whole chain, then a walk that starts at the tail."""

    started = time.perf_counter()
    full = introduces_cycle(state, f"n{count - 1}", "n0")
    full_ms = _ms(started)
    started = time.perf_counter()
    tail = introduces_cycle(state, "n0", f"n{count - 1}")
    return {
        "full_chain_ms": round(full_ms, 1),
        "full_chain_reports_cycle": full,
        "tail_only_ms": round(_ms(started), 1),
        "tail_reports_cycle": tail,
    }


def _index_delta(count: int) -> dict[str, float]:
    state = chain(count)
    started = time.perf_counter()
    items = [CanvasNodeItem(node) for node in state.nodes.values()]
    create_ms = _ms(started)
    scene = QGraphicsScene()
    scene.setItemIndexMethod(QGraphicsScene.ItemIndexMethod.NoIndex)
    started = time.perf_counter()
    for item in items:
        scene.addItem(item)
    noindex_ms = _ms(started)
    for item in items:
        scene.removeItem(item)
    scene.setItemIndexMethod(QGraphicsScene.ItemIndexMethod.BspTreeIndex)
    started = time.perf_counter()
    for item in items:
        scene.addItem(item)
    bsp_ms = _ms(started)
    scene.clear()
    del items
    gc.collect()
    return {
        "create_nodes_ms": round(create_ms, 1),
        "add_noindex_ms": round(noindex_ms, 1),
        "add_bsp_ms": round(bsp_ms, 1),
    }


def _fanout(leaves: int) -> dict[str, float | int]:
    state = CanvasState()
    state.nodes["hub"] = NodeData("hub", "Hub", position=(0.0, 0.0), ports=[Port("out", "output")])
    for index in range(leaves):
        node_id = f"leaf{index}"
        state.nodes[node_id] = NodeData(
            node_id,
            f"Leaf {index}",
            position=(280.0, index * 28.0),
            ports=[Port("in", "input")],
        )
        state.edges[f"h{index}"] = EdgeData(
            f"h{index}", "hub", node_id, source_port="out", target_port="in"
        )
    view, _bucket, mount_ms = _open(state)
    view.centerOn(view._node_items["hub"])
    _paint(view)
    elapsed, updated = _drag_once(view, "hub", 30)
    view.close()
    QApplication.processEvents()
    return {
        "leaves": leaves,
        "mount_ms": round(mount_ms, 1),
        "drag_ms": round(elapsed, 1),
        "wires_updated": updated,
    }


def _reload(count: int, rounds: int) -> dict[str, object]:
    gc.collect()
    before = _working_set()
    samples: list[float] = []
    view = CanvasView()
    view.resize(WIDTH, HEIGHT)
    view.show()
    for _ in range(rounds):
        started = time.perf_counter()
        view.set_controller(CanvasController(chain(count)))
        _paint(view)
        samples.append(_ms(started))
        gc.collect()
    loaded = _working_set()
    view.set_controller(CanvasController(CanvasState()))
    _paint(view)
    gc.collect()
    released = _working_set()
    left = len(view._node_items) + len(view._edge_items)
    view.close()
    QApplication.processEvents()
    gc.collect()
    return {
        "rounds": rounds,
        "before_mb": _mb(before),
        "after_loads_mb": _mb(loaded),
        "after_empty_mb": _mb(released),
        "growth_across_loads_mb": round(_mb(loaded) - _mb(before), 1),
        "retained_after_empty_mb": round(_mb(released) - _mb(before), 1),
        "load_ms": [round(sample, 1) for sample in samples],
        "items_left": left,
    }


def audit_count(count: int) -> dict[str, object]:
    gc.collect()
    before = _working_set()
    started = time.perf_counter()
    state = chain(count)
    model_ms = _ms(started)
    ports = sum(len(node.inputs) + len(node.outputs) for node in state.nodes.values())
    view, bucket, mount_ms = _open(state)
    after_mount = _working_set()
    view.centerOn(view._node_items["n0"])
    _paint(view)
    pan = _sample(lambda: _pan(view, 80), 8)
    started = time.perf_counter()
    view.fit_to_contents()
    _paint(view)
    fit_ms = _ms(started)
    fit_scale = abs(view.transform().m11())
    cache_bytes = 0
    if view._overview is not None:
        cache_bytes = view._overview.width() * view._overview.height() * 4
    overview = _sample(lambda: _pan(view, 6), 8)
    view.centerOn(view._node_items["n0"])
    view.resetTransform()
    view._apply_detail()
    _paint(view)
    hits = _sample(lambda: view.itemAt(view.viewport().rect().center()), 12)
    drag_samples: list[float] = []
    for _ in range(4):
        elapsed, _updated = _drag_once(view, "n20", 8)
        drag_samples.append(elapsed)
    started = time.perf_counter()
    view.zoom_by(1 / 1.12)
    _paint(view)
    zoom_ms = _ms(started)
    started = time.perf_counter()
    found = view.focus_titled(f"Step {count // 2}")
    search_ms = _ms(started)
    started = time.perf_counter()
    for index in range(40):
        node_id = f"n{index + 100}"
        view.controller.state.update_node(node_id, title=f"Step {index}")
        view.sync_node(node_id)
    status_ms = _ms(started)
    path_calls = {"n": 0}
    original = CanvasEdgeItem.update_path

    def counting(self: CanvasEdgeItem) -> None:
        path_calls["n"] += 1
        original(self)

    CanvasEdgeItem.update_path = counting  # type: ignore[method-assign]
    try:
        started = time.perf_counter()
        view.refresh()
        refresh_ms = _ms(started)
    finally:
        CanvasEdgeItem.update_path = original  # type: ignore[method-assign]
    checks = _checks(view, count)
    after = _working_set()
    view.close()
    QApplication.processEvents()
    gc.collect()
    return {
        "nodes": count,
        "edges": count - 1,
        "ports": ports,
        "items": count + count - 1,
        "model_ms": round(model_ms, 1),
        "node_items_ms": round(bucket["node_ms"], 1),
        "edge_items_ms": round(bucket["edge_ms"], 1),
        "scene_add_ms": round(bucket["add_ms"], 1),
        "mount_and_first_paint_ms": round(mount_ms, 1),
        "fit_ms": round(fit_ms, 1),
        "fit_scale": round(fit_scale, 4),
        "compact_node_px": round(fit_scale * NODE_WIDTH, 2),
        "cache_mb": round(cache_bytes / (1024 * 1024), 2),
        "pan_1x": pan,
        "pan_overview": overview,
        "zoom_one_step_ms": round(zoom_ms, 1),
        "hit": hits,
        "drag": {"median_ms": round(_median(drag_samples), 2), "max_ms": round(max(drag_samples), 2)},
        "search_ms": round(search_ms, 1),
        "search_matches": len(found),
        "status_40_ms": round(status_ms, 1),
        "noop_refresh_ms": round(refresh_ms, 1),
        "noop_refresh_rewrote_edges": path_calls["n"],
        "cycle": _cycle(state, count),
        "memory": {
            "before_mb": _mb(before),
            "after_mount_mb": _mb(after_mount),
            "after_suite_mb": _mb(after),
        },
        "checks": checks,
    }


def main() -> None:
    app = QApplication.instance() or QApplication([])
    gc.collect()
    report: dict[str, object] = {
        "environment": {
            "python": platform.python_version(),
            "pyside": pyside_version,
            "platform": platform.platform(),
            "processor": platform.processor(),
            "qpa": os.environ.get("QT_QPA_PLATFORM", ""),
            "viewport": [WIDTH, HEIGHT],
            "note": "Offscreen software raster. Not a measurement of the desktop compositor.",
        },
        "sizes": [audit_count(count) for count in COUNTS],
        "index": {str(count): _index_delta(count) for count in (2000, 10000)},
        "fanout": _fanout(1000),
        "reload": {
            "2000": _reload(2000, 3),
            "10000": _reload(10000, 2),
        },
    }
    app.processEvents()
    json.dump(report, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
