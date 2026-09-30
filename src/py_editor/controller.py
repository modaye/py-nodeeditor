from __future__ import annotations

from typing import Any, Iterable, Mapping

from .commands import (
    AddEdgeCommand,
    AddNodeCommand,
    CommandManager,
    ConnectCommand,
    DeleteSelectionCommand,
    MoveNodesCommand,
    RemoveEdgeCommand,
    RemoveNodeCommand,
    RemoveNodesCommand,
    PasteNodesCommand,
    UpdateNodeCommand,
)
from .models import (
    CanvasState,
    EdgeData,
    NodeData,
    SelectionPayload,
    ensure_unique_id,
)
from .policy import CanvasPolicy, ConnectRequest, ConnectResult

__all__ = ["CanvasController"]


class CanvasController:
    """Apply undoable graph edits.

    ``create_node`` and ``create_edge`` write the model directly. ``connect``
    is the editing operation: it enforces :class:`CanvasPolicy`, replaces a
    busy input, and records one undo step.
    """

    def __init__(
        self,
        state: CanvasState | None = None,
        policy: CanvasPolicy | None = None,
    ) -> None:
        self._state = state or CanvasState()
        self._commands = CommandManager(self._state)
        self._clipboard: SelectionPayload | None = None
        self._policy = policy or CanvasPolicy()

    @property
    def state(self) -> CanvasState:
        return self._state

    @property
    def commands(self) -> CommandManager:
        return self._commands

    @property
    def policy(self) -> CanvasPolicy:
        """Connection rules shared with the canvas view."""

        return self._policy

    def undo(self) -> None:
        self._commands.undo()

    def redo(self) -> None:
        self._commands.redo()

    def clear_history(self) -> None:
        self._commands.clear()

    def add_node(self, node: NodeData) -> NodeData:
        self._commands.push(AddNodeCommand(node))
        return self._state.nodes[node.id].clone()

    def create_node(
        self,
        title: str,
        *,
        node_id: str | None = None,
        node_type: str = "default",
        position: tuple[float, float] = (0.0, 0.0),
        inputs: Iterable[str] | None = None,
        outputs: Iterable[str] | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> NodeData:
        base_id = node_id or title.strip().lower().replace(" ", "_")
        if not base_id:
            base_id = "node"
        unique_id = ensure_unique_id(self._state.node_ids(), base_id)
        normalized_type = "default"
        if isinstance(node_type, str) and node_type.strip():
            normalized_type = node_type.strip()
        node = NodeData(
            id=unique_id,
            title=title,
            node_type=normalized_type,
            position=(float(position[0]), float(position[1])),
            inputs=list(inputs or []),
            outputs=list(outputs or []),
            metadata=dict(metadata or {}),
        )
        return self.add_node(node)

    def remove_node(self, node_id: str) -> None:
        if not self._state.has_node(node_id):
            return
        self._commands.push(RemoveNodeCommand(node_id))

    def remove_nodes(self, node_ids: Iterable[str]) -> None:
        filtered = [
            node_id for node_id in node_ids if self._state.has_node(node_id)
        ]
        if not filtered:
            return
        if len(filtered) == 1:
            self.remove_node(filtered[0])
            return
        self._commands.push(RemoveNodesCommand(filtered))

    def move_nodes(
        self,
        updates: Mapping[str, tuple[float, float]],
    ) -> None:
        before: dict[str, tuple[float, float]] = {}
        after: dict[str, tuple[float, float]] = {}
        for node_id, position in updates.items():
            if not self._state.has_node(node_id):
                continue
            before[node_id] = self._state.nodes[node_id].position
            after[node_id] = (float(position[0]), float(position[1]))
        if not before:
            return
        self._commands.push(MoveNodesCommand(before, after))

    def add_edge(self, edge: EdgeData) -> EdgeData:
        self._commands.push(AddEdgeCommand(edge))
        return self._state.edges[edge.id].clone()

    def create_edge(
        self,
        source: str,
        target: str,
        *,
        edge_id: str | None = None,
        edge_type: str = "default",
        source_port: str | None = None,
        target_port: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> EdgeData:
        if (
            not self._state.has_node(source)
            or not self._state.has_node(target)
        ):
            raise KeyError("Edge references unknown nodes")
        base_id = edge_id or f"{source}->{target}"
        unique_id = ensure_unique_id(self._state.edge_ids(), base_id)
        normalized_type = "default"
        if isinstance(edge_type, str) and edge_type.strip():
            normalized_type = edge_type.strip()
        edge = EdgeData(
            id=unique_id,
            source=source,
            target=target,
            edge_type=normalized_type,
            source_port=source_port,
            target_port=target_port,
            metadata=dict(metadata or {}),
        )
        return self.add_edge(edge)

    def connect(
        self,
        source: str,
        target: str,
        *,
        source_port: str | None = None,
        target_port: str | None = None,
        edge_type: str = "default",
        edge_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        replace_edges: Iterable[str] = (),
    ) -> ConnectResult:
        """Validate and add a connection.

        A single output port is chosen when ``source_port`` is omitted, and a
        single input port when ``target_port`` is omitted. ``replace_edges``
        marks wires already detached by a reconnect gesture.
        """

        request = ConnectRequest(
            source=source,
            target=target,
            source_port=source_port,
            target_port=target_port,
        )
        decision = self._policy.evaluate(
            self._state,
            request,
            ignore=replace_edges,
        )
        if not decision.accepted:
            return decision
        assert decision.source_port is not None
        assert decision.target_port is not None
        base_id = edge_id or f"{decision.source}->{decision.target}"
        unique_id = ensure_unique_id(self._state.edge_ids(), base_id)
        normalized_type = "default"
        if isinstance(edge_type, str) and edge_type.strip():
            normalized_type = edge_type.strip()
        edge = EdgeData(
            id=unique_id,
            source=decision.source or source,
            target=decision.target or target,
            edge_type=normalized_type,
            source_port=decision.source_port,
            target_port=decision.target_port,
            metadata=dict(metadata or {}),
        )
        self._commands.push(ConnectCommand(edge, decision.replaced_edge_ids))
        return ConnectResult(
            accepted=True,
            reason="ok",
            source=edge.source,
            target=edge.target,
            source_port=edge.source_port,
            target_port=edge.target_port,
            edge_id=edge.id,
            replaced_edge_ids=decision.replaced_edge_ids,
        )

    def delete(
        self,
        node_ids: Iterable[str] = (),
        edge_ids: Iterable[str] = (),
    ) -> None:
        """Remove nodes and edges in one undo step."""

        nodes = [node_id for node_id in node_ids if self._state.has_node(node_id)]
        edges = [edge_id for edge_id in edge_ids if self._state.has_edge(edge_id)]
        if not nodes and not edges:
            return
        self._commands.push(DeleteSelectionCommand(nodes, edges))

    def remove_edge(self, edge_id: str) -> None:
        if not self._state.has_edge(edge_id):
            return
        self._commands.push(RemoveEdgeCommand(edge_id))

    def remove_edges(self, edge_ids: Iterable[str]) -> None:
        for edge_id in list(edge_ids):
            self.remove_edge(edge_id)

    def update_node(self, node_id: str, **updates: Any) -> None:
        if not self._state.has_node(node_id):
            raise KeyError(f"Unknown node: {node_id}")
        self._commands.push(UpdateNodeCommand(node_id, **updates))

    def set_selection(
        self,
        *,
        nodes: Iterable[str] | None = None,
        edges: Iterable[str] | None = None,
        append: bool = False,
    ) -> None:
        self._state.select(nodes=nodes, edges=edges, append=append)

    def clear_selection(self) -> None:
        self._state.selection.clear()

    def select_all_nodes(self) -> None:
        self._state.select(nodes=self._state.node_ids(), append=False)

    def selected_nodes(self) -> set[str]:
        return set(self._state.selection.nodes)

    def selected_edges(self) -> set[str]:
        return set(self._state.selection.edges)

    def copy_selection(self) -> SelectionPayload | None:
        payload = self._state.selection_payload(self._state.selection.nodes)
        if payload.is_empty():
            return None
        self._clipboard = SelectionPayload(
            [node.clone() for node in payload.nodes],
            [edge.clone() for edge in payload.edges],
        )
        return self._clipboard

    def cut_selection(self) -> SelectionPayload | None:
        node_ids = list(self._state.selection.nodes)
        edge_ids = list(self._state.selection.edges)
        payload = self.copy_selection()
        self.delete(node_ids, edge_ids)
        return payload

    def paste(
        self,
        offset: tuple[float, float] = (32.0, 32.0),
    ) -> SelectionPayload | None:
        if self._clipboard is None or self._clipboard.is_empty():
            return None
        payload = self._generate_paste_payload(self._clipboard, offset)
        if payload.is_empty():
            return None
        self._commands.push(PasteNodesCommand(payload.nodes, payload.edges))
        return payload

    def has_clipboard(self) -> bool:
        return self._clipboard is not None and not self._clipboard.is_empty()

    def _generate_paste_payload(
        self,
        payload: SelectionPayload,
        offset: tuple[float, float],
    ) -> SelectionPayload:
        dx, dy = offset
        existing_node_ids = set(self._state.node_ids())
        existing_edge_ids = set(self._state.edge_ids())
        mapping: dict[str, str] = {}
        new_nodes: list[NodeData] = []
        for node in payload.nodes:
            base = node.id or "node"
            unique = ensure_unique_id(existing_node_ids, base)
            existing_node_ids.add(unique)
            mapping[node.id] = unique
            new_nodes.append(node.shifted(dx, dy, new_id=unique))
        new_edges: list[EdgeData] = []
        for edge in payload.edges:
            if edge.source not in mapping or edge.target not in mapping:
                continue
            base = edge.id or f"{edge.source}->{edge.target}"
            unique = ensure_unique_id(existing_edge_ids, base)
            existing_edge_ids.add(unique)
            new_edges.append(
                EdgeData(
                    id=unique,
                    source=mapping[edge.source],
                    target=mapping[edge.target],
                    edge_type=edge.edge_type,
                    source_port=edge.source_port,
                    target_port=edge.target_port,
                    metadata=dict(edge.metadata),
                )
            )
        return SelectionPayload(new_nodes, new_edges)
