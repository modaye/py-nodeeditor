"""Fixed graphs for the performance bench. No Qt.

Layouts stay deterministic so two runs describe the same picture:

- ``grid_chain``: square grid, 240 by 140, each node wired to the next.
  This is the picture ``tests/bench_scale.py`` has always used.
- ``sparse_grid``: four times that spacing, a wire only every eighth node.
- ``fan``: one hub and a vertical stack of leaves, every leaf wired to the hub.
- ``crossing``: two columns. Each left node wires to three right nodes,
  so the wires cross.
"""

from __future__ import annotations

from py_editor.models import CanvasState, EdgeData, NodeData, Port

_PAIR = (Port("in", "input"), Port("out", "output"))


def grid_chain(count: int) -> CanvasState:
    """Square grid. Node ``n{i}`` links to ``n{i+1}``. Spacing is 240 by 140."""

    state = CanvasState()
    columns = max(int(count**0.5), 1)
    for index in range(count):
        _put(
            state,
            index,
            (index % columns) * 240.0,
            (index // columns) * 140.0,
            count,
        )
        if index:
            _link(state, index - 1, index)
    return state


def sparse_grid(count: int) -> CanvasState:
    """Same count as the chain, spread out, with about one eighth of the wires."""

    state = CanvasState()
    columns = max(int(count**0.5), 1)
    for index in range(count):
        _put(
            state,
            index,
            (index % columns) * 960.0,
            (index // columns) * 560.0,
            count,
        )
        if index and index % 8 == 0:
            _link(state, index - 8, index)
    return state


def fan(count: int) -> CanvasState:
    """One hub at the origin and ``count - 1`` leaves in a column."""

    state = CanvasState()
    state.nodes["hub"] = NodeData(
        "hub",
        "Hub",
        position=(0.0, 0.0),
        ports=[Port("out", "output")],
    )
    for index in range(count - 1):
        node_id = f"leaf{index}"
        state.nodes[node_id] = NodeData(
            node_id,
            f"Leaf {index}",
            position=(280.0, index * 28.0),
            ports=[Port("in", "input")],
        )
        state.edges[f"e{index}"] = EdgeData(
            f"e{index}",
            "hub",
            node_id,
            source_port="out",
            target_port="in",
        )
    return state


def crossing(count: int) -> CanvasState:
    """Two columns. Each left node connects to three spread-out right nodes."""

    state = CanvasState()
    left = max(count // 2, 1)
    right = max(count - left, 1)
    for index in range(left):
        state.nodes[f"L{index}"] = NodeData(
            f"L{index}",
            f"Left {index}",
            position=(0.0, index * 36.0),
            ports=[Port("out", "output")],
        )
    for index in range(right):
        state.nodes[f"R{index}"] = NodeData(
            f"R{index}",
            f"Right {index}",
            position=(520.0, index * 36.0),
            ports=[Port("in", "input")],
        )
    offsets = (0, max(right // 3, 1), max((2 * right) // 3, 1))
    seen: set[tuple[int, int]] = set()
    for index in range(left):
        for offset in offsets:
            target = (index + offset) % right
            key = (index, target)
            if key in seen:
                continue
            seen.add(key)
            edge_id = f"c{index}-{target}"
            state.edges[edge_id] = EdgeData(
                edge_id,
                f"L{index}",
                f"R{target}",
                source_port="out",
                target_port="in",
            )
    return state


def _put(state: CanvasState, index: int, x: float, y: float, count: int) -> None:
    if index == 0:
        ports = [Port("out", "output")]
    elif index == count - 1:
        ports = [Port("in", "input")]
    else:
        ports = list(_PAIR)
    state.nodes[f"n{index}"] = NodeData(
        f"n{index}",
        f"Step {index}",
        position=(x, y),
        ports=ports,
    )


def _link(state: CanvasState, source: int, target: int) -> None:
    state.edges[f"e{target}"] = EdgeData(
        f"e{target}",
        f"n{source}",
        f"n{target}",
        source_port="out",
        target_port="in",
    )
