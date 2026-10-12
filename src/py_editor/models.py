from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Tuple

SCHEMA_VERSION = 1

__all__ = [
    "SCHEMA_VERSION",
    "Port",
    "Viewport",
    "NodeData",
    "EdgeData",
    "CanvasSelection",
    "CanvasState",
    "SelectionPayload",
    "ensure_unique_id",
]


@dataclass(frozen=True, slots=True)
class Port:
    """One side of a node. ``multi`` only changes how many wires an input keeps."""

    id: str
    side: str
    kind: str | None = None
    multi: bool = False

    def __post_init__(self) -> None:
        if self.side not in ("input", "output"):
            raise ValueError("Port side must be 'input' or 'output'")
        if not self.id:
            raise ValueError("Port id is required")

    def to_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "id": self.id,
            "side": self.side,
            "multi": self.multi,
        }
        if self.kind is not None:
            payload["kind"] = self.kind
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "Port":
        if not isinstance(payload, Mapping):
            raise ValueError("Port must be a mapping")
        if "id" not in payload or "side" not in payload:
            raise ValueError("Port requires 'id' and 'side'")
        kind = payload.get("kind")
        if kind is not None and not isinstance(kind, str):
            raise ValueError("Port 'kind' must be a string")
        multi = payload.get("multi", False)
        if not isinstance(multi, bool):
            raise ValueError("Port 'multi' must be a boolean")
        return cls(id=str(payload["id"]), side=str(payload["side"]), kind=kind, multi=multi)


@dataclass(frozen=True, slots=True)
class Viewport:
    """Scene point at the center of the view, and the current zoom."""

    x: float
    y: float
    zoom: float

    def to_dict(self) -> Dict[str, float]:
        return {"x": float(self.x), "y": float(self.y), "zoom": float(self.zoom)}

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "Viewport":
        if not isinstance(payload, Mapping):
            raise ValueError("Viewport must be a mapping")
        try:
            zoom = float(payload["zoom"])
            return cls(x=float(payload["x"]), y=float(payload["y"]), zoom=zoom)
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Viewport requires numeric x, y, and zoom") from exc


@dataclass(slots=True)
class NodeData:
    id: str
    title: str
    node_type: str = "default"
    position: Tuple[float, float] = (0.0, 0.0)
    ports: List[Port] = field(default_factory=list)
    draggable: bool = True
    selectable: bool = True
    connectable: bool = True
    deletable: bool = True
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        seen: set[tuple[str, str]] = set()
        for port in self.ports:
            key = (port.side, port.id)
            if key in seen:
                raise ValueError(f"Duplicate {port.side} port id: {port.id}")
            seen.add(key)

    def port_ids(self, side: str) -> list[str]:
        return [port.id for port in self.ports if port.side == side]

    def clone(self, *, with_id: str | None = None) -> "NodeData":
        node_id = with_id if with_id is not None else self.id
        return NodeData(
            id=node_id,
            title=self.title,
            node_type=self.node_type,
            position=(self.position[0], self.position[1]),
            ports=list(self.ports),
            draggable=self.draggable,
            selectable=self.selectable,
            connectable=self.connectable,
            deletable=self.deletable,
            metadata=dict(self.metadata),
        )

    def shifted(
        self,
        dx: float,
        dy: float,
        *,
        new_id: str | None = None,
    ) -> "NodeData":
        return self.clone(with_id=new_id).with_position(
            self.position[0] + dx,
            self.position[1] + dy,
        )

    def with_position(self, x: float, y: float) -> "NodeData":
        return replace(self, position=(x, y))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "id": self.id,
            "title": self.title,
            "node_type": self.node_type,
            "position": [float(self.position[0]), float(self.position[1])],
            "ports": [port.to_dict() for port in self.ports],
            "draggable": self.draggable,
            "selectable": self.selectable,
            "connectable": self.connectable,
            "deletable": self.deletable,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "NodeData":
        if payload.get("schema_version") != SCHEMA_VERSION:
            raise ValueError("Node schema_version must be 1")
        if "id" not in payload:
            raise ValueError("Node data is missing required field 'id'")
        identifier = str(payload["id"])
        title = str(payload.get("title", ""))
        node_type = str(payload.get("node_type", "default"))
        position_raw = payload.get("position", (0.0, 0.0))
        if not isinstance(position_raw, (list, tuple)) or len(position_raw) != 2:
            raise ValueError("Node 'position' must be a sequence of two values")
        try:
            position = (float(position_raw[0]), float(position_raw[1]))
        except (TypeError, ValueError) as exc:
            raise ValueError("Node 'position' values must be numeric") from exc
        ports_raw = payload.get("ports")
        if not isinstance(ports_raw, list):
            raise ValueError("Node 'ports' must be a list of port objects")
        ports = [Port.from_dict(item) for item in ports_raw]
        metadata_raw = payload.get("metadata", {})
        metadata: Dict[str, Any]
        if metadata_raw is None:
            metadata = {}
        elif isinstance(metadata_raw, Mapping):
            metadata = dict(metadata_raw)
        else:
            raise ValueError("Node 'metadata' must be a mapping if provided")
        return cls(
            id=identifier,
            title=title,
            node_type=node_type,
            position=position,
            ports=ports,
            draggable=_required_flag(payload, "draggable"),
            selectable=_required_flag(payload, "selectable"),
            connectable=_required_flag(payload, "connectable"),
            deletable=_required_flag(payload, "deletable"),
            metadata=metadata,
        )


@dataclass(slots=True)
class EdgeData:
    id: str
    source: str
    target: str
    edge_type: str = "default"
    source_port: str | None = None
    target_port: str | None = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def clone(self, *, with_id: str | None = None) -> "EdgeData":
        edge_id = with_id if with_id is not None else self.id
        return EdgeData(
            id=edge_id,
            source=self.source,
            target=self.target,
            edge_type=self.edge_type,
            source_port=self.source_port,
            target_port=self.target_port,
            metadata=dict(self.metadata),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "source": self.source,
            "target": self.target,
            "edge_type": self.edge_type,
            "source_port": self.source_port,
            "target_port": self.target_port,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "EdgeData":
        missing = [
            field
            for field in ("id", "source", "target")
            if field not in payload
        ]
        if missing:
            raise ValueError(
                f"Edge data missing required field(s): {', '.join(missing)}"
            )
        identifier = str(payload["id"])
        source = str(payload["source"])
        target = str(payload["target"])
        edge_type = str(payload.get("edge_type", "default"))
        source_port_raw = payload.get("source_port")
        target_port_raw = payload.get("target_port")
        source_port = None if source_port_raw is None else str(source_port_raw)
        target_port = None if target_port_raw is None else str(target_port_raw)
        metadata_raw = payload.get("metadata", {})
        metadata: Dict[str, Any]
        if metadata_raw is None:
            metadata = {}
        elif isinstance(metadata_raw, Mapping):
            metadata = dict(metadata_raw)
        else:
            raise ValueError("Edge 'metadata' must be a mapping if provided")
        return cls(
            id=identifier,
            source=source,
            target=target,
            edge_type=edge_type,
            source_port=source_port,
            target_port=target_port,
            metadata=metadata,
        )


@dataclass(slots=True)
class SelectionPayload:
    nodes: list[NodeData] = field(default_factory=list)
    edges: list[EdgeData] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not self.nodes and not self.edges

    def shifted(self, dx: float, dy: float) -> "SelectionPayload":
        shifted_nodes = [node.shifted(dx, dy) for node in self.nodes]
        cloned_edges = [edge.clone() for edge in self.edges]
        return SelectionPayload(shifted_nodes, cloned_edges)


@dataclass(slots=True)
class CanvasSelection:
    nodes: set[str] = field(default_factory=set)
    edges: set[str] = field(default_factory=set)

    def clear(self) -> None:
        self.nodes.clear()
        self.edges.clear()

    def snapshot(self) -> "CanvasSelection":
        return CanvasSelection(set(self.nodes), set(self.edges))

    def set_nodes(self, node_ids: Iterable[str]) -> None:
        self.nodes = {node_id for node_id in node_ids}

    def set_edges(self, edge_ids: Iterable[str]) -> None:
        self.edges = {edge_id for edge_id in edge_ids}


def _required_flag(payload: Mapping[str, Any], name: str) -> bool:
    value = payload.get(name)
    if not isinstance(value, bool):
        raise ValueError(f"Node '{name}' must be a boolean")
    return value


@dataclass
class CanvasState:
    nodes: MutableMapping[str, NodeData] = field(default_factory=dict)
    edges: MutableMapping[str, EdgeData] = field(default_factory=dict)
    selection: CanvasSelection = field(default_factory=CanvasSelection)
    viewport: Viewport = field(default_factory=lambda: Viewport(0.0, 0.0, 1.0))

    def has_node(self, node_id: str) -> bool:
        return node_id in self.nodes

    def has_edge(self, edge_id: str) -> bool:
        return edge_id in self.edges

    def node_ids(self) -> List[str]:
        return list(self.nodes.keys())

    def edge_ids(self) -> List[str]:
        return list(self.edges.keys())

    def add_node(self, node: NodeData) -> None:
        self.nodes[node.id] = node.clone()
        self.selection.set_nodes([node.id])

    def update_node(self, node_id: str, **updates: Any) -> NodeData:
        if node_id not in self.nodes:
            raise KeyError(f"Unknown node: {node_id}")
        node = self.nodes[node_id]
        payload = node
        if "title" in updates:
            payload = replace(payload, title=str(updates["title"]))
        if "position" in updates:
            x, y = updates["position"]
            payload = payload.with_position(float(x), float(y))
        if "node_type" in updates:
            raw_type = updates["node_type"]
            normalized_type = "default"
            if isinstance(raw_type, str) and raw_type.strip():
                normalized_type = raw_type.strip()
            payload = replace(payload, node_type=normalized_type)
        if "ports" in updates:
            payload = replace(payload, ports=list(updates["ports"]))
        for flag in ("draggable", "selectable", "connectable", "deletable"):
            if flag in updates:
                payload = replace(payload, **{flag: bool(updates[flag])})
        if "metadata" in updates:
            payload = replace(payload, metadata=dict(updates["metadata"]))
        self.nodes[node_id] = payload
        return payload.clone()

    def set_node_positions(
        self,
        mapping: Mapping[str, Tuple[float, float]],
    ) -> None:
        for node_id, position in mapping.items():
            if node_id not in self.nodes:
                continue
            x, y = position
            self.nodes[node_id] = self.nodes[node_id].with_position(
                float(x),
                float(y),
            )

    def remove_node(
        self,
        node_id: str,
    ) -> tuple[NodeData, list[EdgeData]] | None:
        if node_id not in self.nodes:
            return None
        node = self.nodes.pop(node_id)
        removed_edges: list[EdgeData] = []
        for edge_id, edge in list(self.edges.items()):
            if edge.source == node_id or edge.target == node_id:
                removed_edges.append(self.edges.pop(edge_id))
        self.selection.nodes.discard(node_id)
        self.selection.edges.difference_update(
            {edge.id for edge in removed_edges}
        )
        return node.clone(), [edge.clone() for edge in removed_edges]

    def add_edge(self, edge: EdgeData) -> None:
        if edge.source not in self.nodes or edge.target not in self.nodes:
            raise KeyError("Edge references unknown nodes")
        self.edges[edge.id] = edge.clone()
        self.selection.set_edges([edge.id])

    def remove_edge(self, edge_id: str) -> EdgeData | None:
        edge = self.edges.pop(edge_id, None)
        if edge is None:
            return None
        self.selection.edges.discard(edge_id)
        return edge.clone()

    def restore_node(self, node: NodeData) -> None:
        self.nodes[node.id] = node.clone()

    def restore_edge(self, edge: EdgeData) -> None:
        if edge.source not in self.nodes or edge.target not in self.nodes:
            raise KeyError("Edge references unknown nodes")
        self.edges[edge.id] = edge.clone()

    def edges_for_node(self, node_id: str) -> List[EdgeData]:
        results: List[EdgeData] = []
        for edge in self.edges.values():
            if edge.source == node_id or edge.target == node_id:
                results.append(edge.clone())
        return results

    def selection_payload(self, node_ids: Iterable[str]) -> SelectionPayload:
        selected = {node_id for node_id in node_ids if node_id in self.nodes}
        nodes = [self.nodes[node_id].clone() for node_id in selected]
        edges = [
            edge.clone()
            for edge in self.edges.values()
            if edge.source in selected and edge.target in selected
        ]
        return SelectionPayload(nodes, edges)

    def select(
        self,
        *,
        nodes: Iterable[str] | None = None,
        edges: Iterable[str] | None = None,
        append: bool = False,
    ) -> None:
        if not append:
            self.selection.clear()
        if nodes is not None:
            for node_id in nodes:
                if node_id in self.nodes:
                    self.selection.nodes.add(node_id)
        if edges is not None:
            for edge_id in edges:
                if edge_id in self.edges:
                    self.selection.edges.add(edge_id)

    def clear(self) -> None:
        self.nodes.clear()
        self.edges.clear()
        self.selection.clear()

    def to_dict(self, *, include_selection: bool = True) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "viewport": self.viewport.to_dict(),
            "nodes": [node.to_dict() for node in self.nodes.values()],
            "edges": [edge.to_dict() for edge in self.edges.values()],
        }
        if include_selection:
            payload["selection"] = {
                "nodes": list(self.selection.nodes),
                "edges": list(self.selection.edges),
            }
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "CanvasState":
        if payload.get("schema_version") != SCHEMA_VERSION:
            raise ValueError("Canvas schema_version must be 1")
        if "viewport" not in payload:
            raise ValueError("CanvasState requires viewport")
        state = cls(viewport=Viewport.from_dict(payload["viewport"]))
        nodes_raw = payload.get("nodes", [])
        if nodes_raw is None:
            nodes_raw = []
        if not isinstance(nodes_raw, Iterable):
            raise ValueError("CanvasState 'nodes' must be an iterable")
        for node_payload in nodes_raw:
            if not isinstance(node_payload, Mapping):
                raise ValueError("Each node entry must be a mapping")
            node = NodeData.from_dict(node_payload)
            state.nodes[node.id] = node
        edges_raw = payload.get("edges", [])
        if edges_raw is None:
            edges_raw = []
        if not isinstance(edges_raw, Iterable):
            raise ValueError("CanvasState 'edges' must be an iterable")
        for edge_payload in edges_raw:
            if not isinstance(edge_payload, Mapping):
                raise ValueError("Each edge entry must be a mapping")
            edge = EdgeData.from_dict(edge_payload)
            if (
                edge.source not in state.nodes
                or edge.target not in state.nodes
            ):
                raise ValueError(
                    f"Edge references unknown node(s): '{edge.source}' -> "
                    f"'{edge.target}'"
                )
            state.edges[edge.id] = edge
        selection_raw = payload.get("selection")
        if isinstance(selection_raw, Mapping):
            node_ids = selection_raw.get("nodes", [])
            edge_ids = selection_raw.get("edges", [])
            if isinstance(node_ids, Iterable) and not isinstance(
                node_ids,
                (str, bytes),
            ):
                state.selection.set_nodes(
                    str(node_id)
                    for node_id in node_ids
                    if str(node_id) in state.nodes
                )
            if isinstance(edge_ids, Iterable) and not isinstance(
                edge_ids,
                (str, bytes),
            ):
                state.selection.set_edges(
                    str(edge_id)
                    for edge_id in edge_ids
                    if str(edge_id) in state.edges
                )
        return state


def ensure_unique_id(existing: Iterable[str], base: str) -> str:
    candidate = base
    suffix = 2
    while candidate in existing:
        candidate = f"{base}_{suffix}"
        suffix += 1
    return candidate
