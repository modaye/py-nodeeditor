"""Layered layout for directed node graphs.

The result is a set of positions. Horizontal layout grows to the right, the
way a data pipeline is usually read. Vertical layout grows downward.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Iterable, Literal, Mapping

__all__ = ["arrange_nodes"]

Direction = Literal["horizontal", "vertical"]


def arrange_nodes(
    sizes: Mapping[str, tuple[float, float]],
    edges: Iterable[tuple[str, str]],
    *,
    direction: Direction = "horizontal",
    gap: float = 80.0,
) -> dict[str, tuple[float, float]]:
    """Place ``sizes`` so edges mostly point along ``direction``.

    ``sizes`` maps a node id to ``(width, height)``. Nodes keep a stable order
    inside a layer when nothing else suggests a better one. Cycles are bounded
    so a loop cannot push a node arbitrarily far.
    """

    if direction not in ("horizontal", "vertical"):
        raise ValueError("direction must be 'horizontal' or 'vertical'")
    if not sizes:
        return {}
    links = _unique_edges(sizes, edges)
    layers = _ordered_layers(list(sizes), links)
    return _place(layers, sizes, direction, gap)


def _unique_edges(
    sizes: Mapping[str, tuple[float, float]],
    edges: Iterable[tuple[str, str]],
) -> list[tuple[str, str]]:
    links: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for source, target in edges:
        if source not in sizes or target not in sizes or source == target:
            continue
        link = (source, target)
        if link in seen:
            continue
        seen.add(link)
        links.append(link)
    return links


def _ordered_layers(
    order: list[str],
    edges: list[tuple[str, str]],
) -> list[list[str]]:
    rank = {node_id: 0 for node_id in order}
    limit = len(order)
    for _ in range(limit):
        changed = False
        for source, target in edges:
            proposed = rank[source] + 1
            if proposed < limit and rank[target] < proposed:
                rank[target] = proposed
                changed = True
        if not changed:
            break
    incoming: dict[str, list[str]] = defaultdict(list)
    outgoing: dict[str, list[str]] = defaultdict(list)
    for source, target in edges:
        outgoing[source].append(target)
        incoming[target].append(source)
    max_rank = max(rank.values(), default=0)
    layers = [
        [node_id for node_id in order if rank[node_id] == level]
        for level in range(max_rank + 1)
    ]
    layers = [layer for layer in layers if layer]
    index: dict[str, int] = {}

    def reindex() -> None:
        index.clear()
        for layer in layers:
            for position, node_id in enumerate(layer):
                index[node_id] = position

    reindex()
    for _ in range(3):
        for layer_index in range(1, len(layers)):
            layers[layer_index].sort(
                key=lambda node_id: _barycenter(incoming[node_id], index)
            )
        reindex()
        for layer_index in range(len(layers) - 2, -1, -1):
            layers[layer_index].sort(
                key=lambda node_id: _barycenter(outgoing[node_id], index)
            )
        reindex()
    return layers


def _barycenter(neighbors: list[str], index: Mapping[str, int]) -> float:
    values = [index[node_id] for node_id in neighbors if node_id in index]
    if not values:
        return 0.0
    return sum(values) / len(values)


def _place(
    layers: list[list[str]],
    sizes: Mapping[str, tuple[float, float]],
    direction: Direction,
    gap: float,
) -> dict[str, tuple[float, float]]:
    positions: dict[str, tuple[float, float]] = {}
    cursor = 0.0
    cross_gap = gap * 0.75
    for layer in layers:
        if direction == "horizontal":
            offsets = _centered([sizes[node_id][1] for node_id in layer], cross_gap)
            depth = max(sizes[node_id][0] for node_id in layer)
            for node_id, offset in zip(layer, offsets):
                positions[node_id] = (cursor, offset)
            cursor += depth + gap
        else:
            offsets = _centered([sizes[node_id][0] for node_id in layer], cross_gap)
            depth = max(sizes[node_id][1] for node_id in layer)
            for node_id, offset in zip(layer, offsets):
                positions[node_id] = (offset, cursor)
            cursor += depth + gap
    return positions


def _centered(lengths: list[float], gap: float) -> list[float]:
    total = sum(lengths) + gap * max(len(lengths) - 1, 0)
    cursor = -total / 2.0
    offsets: list[float] = []
    for length in lengths:
        offsets.append(cursor)
        cursor += length + gap
    return offsets
