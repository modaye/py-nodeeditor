"""Connection rules that do not depend on Qt.

The view decides where the pointer is. ``CanvasPolicy`` decides whether that
connection is allowed, which input edge it replaces, and which port is nearest.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass

from .models import CanvasState, EdgeData, NodeData, Port

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
    ``type``, ``cycle``, ``duplicate``, ``locked``, or ``cancelled``.
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
    into the target port, unless that port has ``multi``. ``allow_cycles``
    permits a connection that would loop back to its source. Ports with declared
    kinds refuse a wire when the kinds differ; a port with no kind still accepts
    any wire. ``extra_rules`` run after the built-in checks and can reject.
    """

    snap_radius: float = 24.0
    single_input: bool = True
    allow_cycles: bool = False
    extra_rules: tuple[Callable[[CanvasState, ConnectRequest], ConnectResult | None], ...] = ()

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
        if not source_node.connectable or not target_node.connectable:
            return ConnectResult(
                accepted=False,
                reason="locked",
                source=request.source,
                target=request.target,
            )
        source_port = _resolve_port(
            [port for port in source_node.ports if port.side == "output"],
            request.source_port,
        )
        target_port = _resolve_port(
            [port for port in target_node.ports if port.side == "input"],
            request.target_port,
        )
        if source_port is None or target_port is None:
            return ConnectResult(
                accepted=False,
                reason="missing-port",
                source=request.source,
                target=request.target,
                source_port=None if source_port is None else source_port.id,
                target_port=None if target_port is None else target_port.id,
            )
        if request.source == request.target:
            return ConnectResult(
                accepted=False,
                reason="self",
                source=request.source,
                target=request.target,
                source_port=source_port.id,
                target_port=target_port.id,
            )
        if (
            source_port.kind is not None
            and target_port.kind is not None
            and source_port.kind != target_port.kind
        ):
            return ConnectResult(
                accepted=False,
                reason="type",
                source=request.source,
                target=request.target,
                source_port=source_port.id,
                target_port=target_port.id,
            )

        ignored = {edge_id for edge_id in ignore}
        replaced = _input_edge_ids(state, request.target, target_port.id)
        keeps_existing = not self.single_input or target_port.multi
        if keeps_existing:
            replaced = []
        else:
            ignored.update(replaced)

        existing = _matching_edge(
            state,
            request.source,
            source_port.id,
            request.target,
            target_port.id,
        )
        if existing is not None:
            return ConnectResult(
                accepted=False,
                reason="duplicate",
                source=request.source,
                target=request.target,
                source_port=source_port.id,
                target_port=target_port.id,
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
                source_port=source_port.id,
                target_port=target_port.id,
            )
        resolved = ConnectRequest(
            source=request.source,
            target=request.target,
            source_port=source_port.id,
            target_port=target_port.id,
        )
        for rule in self.extra_rules:
            verdict = rule(state, resolved)
            if verdict is not None and not verdict.accepted:
                return verdict
        removed = tuple(dict.fromkeys([*replaced, *ignore]))
        return ConnectResult(
            accepted=True,
            reason="ok",
            source=request.source,
            target=request.target,
            source_port=source_port.id,
            target_port=target_port.id,
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

    ports = [port for port in node.ports if port.side == direction]
    if not ports:
        return None
    if direction == "input" and policy.single_input:
        for port in ports:
            if not _input_edge_ids(state, node.id, port.id):
                return port.id
        for port in ports:
            if port.multi:
                return port.id
    return ports[0].id


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
    outgoing: dict[str, list[str]] = {}
    for edge in state.edges.values():
        if edge.id in ignored:
            continue
        outgoing.setdefault(edge.source, []).append(edge.target)
    seen: set[str] = set()
    stack = [target]
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        for nxt in outgoing.get(current, ()):
            if nxt == source:
                return True
            stack.append(nxt)
    return False


def _resolve_port(ports: list[Port], requested: str | None) -> Port | None:
    cleaned = requested.strip() if isinstance(requested, str) else None
    if cleaned == "":
        cleaned = None
    if cleaned is not None:
        for port in ports:
            if port.id == cleaned:
                return port
        return None
    if len(ports) == 1:
        return ports[0]
    return None


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
