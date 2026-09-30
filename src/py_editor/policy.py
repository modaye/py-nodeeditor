"""Connection rules that do not depend on Qt.

The view decides where the pointer is. ``CanvasPolicy`` decides whether that
connection is allowed, which input edge it replaces, and which port is nearest.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .models import CanvasState, EdgeData, NodeData

__all__ = [
    "CanvasPolicy",
    "ConnectRequest",
    "ConnectResult",
    "PortHit",
    "introduces_cycle",
    "nearest_port",
    "preferred_port",
]


@dataclass(frozen=True, slots=True)
class PortHit:
    """A port located in scene coordinates."""

    node_id: str
    direction: str
    port_id: str
    x: float
    y: float


@dataclass(frozen=True, slots=True)
class ConnectRequest:
    """Endpoints for a connection that has not been validated yet."""

    source: str
    target: str
    source_port: str | None = None
    target_port: str | None = None


@dataclass(frozen=True, slots=True)
class ConnectResult:
    """Outcome of validating or applying a connection.

    ``reason`` is one of ``ok``, ``unknown-node``, ``missing-port``, ``self``,
    ``type``, ``cycle``, ``duplicate``, or ``cancelled``.
    """

    accepted: bool
    reason: str
    source: str | None = None
    target: str | None = None
    source_port: str | None = None
    target_port: str | None = None
    edge_id: str | None = None
    replaced_edge_ids: tuple[str, ...] = ()


@dataclass(slots=True)
class CanvasPolicy:
    """Rules used by :meth:`CanvasController.connect` and the drag gesture.

    ``snap_radius`` is the maximum distance, in scene pixels, at which a dragged
    wire attaches to a port. ``single_input`` replaces any edge already plugged
    into the target port, except a port listed in the target node's
    ``multi_inputs`` metadata. ``allow_cycles`` permits a connection that would
    loop back to its source. Ports with declared kinds refuse a wire when the
    kinds differ; a port with no kind still accepts any wire.
    """

    snap_radius: float = 24.0
    single_input: bool = True
    allow_cycles: bool = False

    def evaluate(
        self,
        state: CanvasState,
        request: ConnectRequest,
        *,
        ignore: Iterable[str] = (),
    ) -> ConnectResult:
        """Return whether ``request`` can be added to ``state``.

        Edge ids in ``ignore`` are treated as already removed. The view passes
        the edge being reconnected so a swap is not rejected as a cycle or a
        duplicate of itself.
        """

        source_node = state.nodes.get(request.source)
        target_node = state.nodes.get(request.target)
        if source_node is None or target_node is None:
            return ConnectResult(
                accepted=False,
                reason="unknown-node",
                source=request.source,
                target=request.target,
            )
        source_port = _resolve_port(source_node.outputs, request.source_port)
        target_port = _resolve_port(target_node.inputs, request.target_port)
        if source_port is None or target_port is None:
            return ConnectResult(
                accepted=False,
                reason="missing-port",
                source=request.source,
                target=request.target,
                source_port=source_port,
                target_port=target_port,
            )
        if request.source == request.target:
            return ConnectResult(
                accepted=False,
                reason="self",
                source=request.source,
                target=request.target,
                source_port=source_port,
                target_port=target_port,
            )
        source_kind = _port_kind(source_node, "outputs", source_port)
        target_kind = _port_kind(target_node, "inputs", target_port)
        if source_kind is not None and target_kind is not None and source_kind != target_kind:
            return ConnectResult(
                accepted=False,
                reason="type",
                source=request.source,
                target=request.target,
                source_port=source_port,
                target_port=target_port,
            )

        ignored = {edge_id for edge_id in ignore}
        replaced = _input_edge_ids(state, request.target, target_port)
        keeps_existing = not self.single_input or _accepts_many(target_node, target_port)
        if keeps_existing:
            replaced = []
        else:
            ignored.update(replaced)

        existing = _matching_edge(
            state,
            request.source,
            source_port,
            request.target,
            target_port,
        )
        if existing is not None:
            return ConnectResult(
                accepted=False,
                reason="duplicate",
                source=request.source,
                target=request.target,
                source_port=source_port,
                target_port=target_port,
            )
        if not self.allow_cycles and introduces_cycle(
            state,
            request.source,
            request.target,
            ignored,
        ):
            return ConnectResult(
                accepted=False,
                reason="cycle",
                source=request.source,
                target=request.target,
                source_port=source_port,
                target_port=target_port,
            )
        removed = tuple(dict.fromkeys([*replaced, *ignore]))
        return ConnectResult(
            accepted=True,
            reason="ok",
            source=request.source,
            target=request.target,
            source_port=source_port,
            target_port=target_port,
            replaced_edge_ids=removed,
        )


def nearest_port(
    hits: Iterable[PortHit],
    x: float,
    y: float,
    *,
    radius: float,
    direction: str | None = None,
    exclude_nodes: Iterable[str] = (),
) -> PortHit | None:
    """Return the closest port inside ``radius``, or ``None``."""

    excluded = set(exclude_nodes)
    limit = radius * radius
    best: PortHit | None = None
    best_distance = limit
    for hit in hits:
        if direction is not None and hit.direction != direction:
            continue
        if hit.node_id in excluded:
            continue
        distance = (hit.x - x) ** 2 + (hit.y - y) ** 2
        if distance > limit:
            continue
        if best is None or distance < best_distance:
            best = hit
            best_distance = distance
    return best


def preferred_port(
    state: CanvasState,
    node: NodeData,
    direction: str,
    policy: CanvasPolicy,
) -> str | None:
    """Pick a port when the pointer is over a node body rather than a handle.

    An input with ``single_input`` uses the first empty port, then the first
    port so the new wire replaces the old one. Outputs use the first port.
    """

    ports = node.outputs if direction == "output" else node.inputs
    if not ports:
        return None
    if direction == "input" and policy.single_input:
        for port in ports:
            if not _input_edge_ids(state, node.id, port):
                return port
        for port in ports:
            if _accepts_many(node, port):
                return port
    return ports[0]


def introduces_cycle(
    state: CanvasState,
    source: str,
    target: str,
    ignore: Iterable[str] = (),
) -> bool:
    """Return whether ``source -> target`` would loop back onto ``source``."""

    if source == target:
        return True
    ignored = set(ignore)
    seen: set[str] = set()
    stack = [target]
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        for edge in state.edges.values():
            if edge.id in ignored or edge.source != current:
                continue
            if edge.target == source:
                return True
            stack.append(edge.target)
    return False


def _resolve_port(ports: list[str], requested: str | None) -> str | None:
    cleaned = requested.strip() if isinstance(requested, str) else None
    if cleaned == "":
        cleaned = None
    if cleaned is not None:
        return cleaned if cleaned in ports else None
    if len(ports) == 1:
        return ports[0]
    return None


def _accepts_many(node: NodeData, port_id: str) -> bool:
    """A port listed in ``multi_inputs`` keeps every wire plugged into it."""

    listed = node.metadata.get("multi_inputs")
    if isinstance(listed, (list, tuple)):
        return port_id in {str(item) for item in listed}
    return False


def _port_kind(node: NodeData, side: str, port_id: str) -> str | None:
    """Return the declared kind of one port, or None when the node has none."""

    raw = node.metadata.get("port_types")
    if not isinstance(raw, dict):
        return None
    side_map = raw.get(side)
    if not isinstance(side_map, dict):
        return None
    kind = side_map.get(port_id)
    if kind is None or str(kind).strip() == "":
        return None
    return str(kind)


def _input_edge_ids(state: CanvasState, node_id: str, port_id: str) -> list[str]:
    return [
        edge.id
        for edge in state.edges.values()
        if edge.target == node_id and edge.target_port == port_id
    ]


def _matching_edge(
    state: CanvasState,
    source: str,
    source_port: str,
    target: str,
    target_port: str,
) -> EdgeData | None:
    for edge in state.edges.values():
        if (
            edge.source == source
            and edge.target == target
            and edge.source_port == source_port
            and edge.target_port == target_port
        ):
            return edge
    return None
