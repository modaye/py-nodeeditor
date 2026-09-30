from __future__ import annotations

from typing import Dict, Iterable, Mapping

from .models import CanvasState, EdgeData, NodeData

__all__ = [
    "CanvasCommand",
    "CommandManager",
    "AddNodeCommand",
    "RemoveNodeCommand",
    "RemoveNodesCommand",
    "MoveNodesCommand",
    "AddEdgeCommand",
    "RemoveEdgeCommand",
    "UpdateNodeCommand",
    "ConnectCommand",
    "DeleteSelectionCommand",
    "PasteNodesCommand",
]


class CanvasCommand:
    """Base class for a reversible mutation applied to a canvas state."""

    def __init__(self, label: str) -> None:
        self.label = label

    def redo(self, state: CanvasState) -> None:
        raise NotImplementedError

    def undo(self, state: CanvasState) -> None:
        raise NotImplementedError


class CommandManager:
    """Simple undo/redo stack for canvas commands."""

    def __init__(self, state: CanvasState) -> None:
        self._state = state
        self._undo_stack: list[CanvasCommand] = []
        self._redo_stack: list[CanvasCommand] = []

    @property
    def state(self) -> CanvasState:
        return self._state

    @property
    def can_undo(self) -> bool:
        return bool(self._undo_stack)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo_stack)

    def push(self, command: CanvasCommand) -> None:
        command.redo(self._state)
        self._undo_stack.append(command)
        self._redo_stack.clear()

    def undo(self) -> None:
        if not self._undo_stack:
            return
        command = self._undo_stack.pop()
        command.undo(self._state)
        self._redo_stack.append(command)

    def redo(self) -> None:
        if not self._redo_stack:
            return
        command = self._redo_stack.pop()
        command.redo(self._state)
        self._undo_stack.append(command)

    def clear(self) -> None:
        self._undo_stack.clear()
        self._redo_stack.clear()


class AddNodeCommand(CanvasCommand):
    def __init__(self, node: NodeData) -> None:
        super().__init__(f"Add node: {node.id}")
        self._node = node.clone()

    def redo(self, state: CanvasState) -> None:
        state.add_node(self._node)

    def undo(self, state: CanvasState) -> None:
        state.remove_node(self._node.id)


class RemoveNodeCommand(CanvasCommand):
    def __init__(self, node_id: str) -> None:
        super().__init__(f"Remove node: {node_id}")
        self._node_id = node_id
        self._removed: tuple[NodeData, list[EdgeData]] | None = None

    def redo(self, state: CanvasState) -> None:
        self._removed = state.remove_node(self._node_id)

    def undo(self, state: CanvasState) -> None:
        if self._removed is None:
            return
        node, edges = self._removed
        state.restore_node(node)
        for edge in edges:
            state.restore_edge(edge)


class RemoveNodesCommand(CanvasCommand):
    def __init__(self, node_ids: Iterable[str]) -> None:
        unique_ids = list(dict.fromkeys(node_ids))
        label = "Remove node" if len(unique_ids) == 1 else "Remove nodes"
        super().__init__(label)
        self._node_ids = unique_ids
        self._removed_nodes: list[NodeData] = []
        self._removed_edges: list[EdgeData] = []

    def redo(self, state: CanvasState) -> None:
        self._removed_nodes.clear()
        self._removed_edges.clear()
        for node_id in self._node_ids:
            removed = state.remove_node(node_id)
            if removed is None:
                continue
            node, edges = removed
            self._removed_nodes.append(node)
            self._removed_edges.extend(edges)

    def undo(self, state: CanvasState) -> None:
        if not self._removed_nodes:
            return
        for node in self._removed_nodes:
            state.restore_node(node)
        for edge in self._removed_edges:
            state.restore_edge(edge)
        state.selection.set_nodes([node.id for node in self._removed_nodes])


class MoveNodesCommand(CanvasCommand):
    def __init__(
        self,
        before: Mapping[str, tuple[float, float]],
        after: Mapping[str, tuple[float, float]],
    ) -> None:
        label = "Move node" if len(after) == 1 else "Move nodes"
        super().__init__(label)
        self._before = {
            node_id: (float(position[0]), float(position[1]))
            for node_id, position in before.items()
        }
        self._after = {
            node_id: (float(position[0]), float(position[1]))
            for node_id, position in after.items()
        }

    def redo(self, state: CanvasState) -> None:
        state.set_node_positions(self._after)

    def undo(self, state: CanvasState) -> None:
        state.set_node_positions(self._before)


class AddEdgeCommand(CanvasCommand):
    def __init__(self, edge: EdgeData) -> None:
        super().__init__(f"Add edge: {edge.id}")
        self._edge = edge.clone()

    def redo(self, state: CanvasState) -> None:
        state.add_edge(self._edge)

    def undo(self, state: CanvasState) -> None:
        state.remove_edge(self._edge.id)


class RemoveEdgeCommand(CanvasCommand):
    def __init__(self, edge_id: str) -> None:
        super().__init__(f"Remove edge: {edge_id}")
        self._edge_id = edge_id
        self._removed: EdgeData | None = None

    def redo(self, state: CanvasState) -> None:
        self._removed = state.remove_edge(self._edge_id)

    def undo(self, state: CanvasState) -> None:
        if self._removed is None:
            return
        state.restore_edge(self._removed)


class UpdateNodeCommand(CanvasCommand):
    def __init__(self, node_id: str, **updates: object) -> None:
        super().__init__(f"Update node: {node_id}")
        self._node_id = node_id
        self._updates: Dict[str, object] = dict(updates)
        self._before: NodeData | None = None
        self._after: NodeData | None = None

    def redo(self, state: CanvasState) -> None:
        if not state.has_node(self._node_id):
            raise KeyError(f"Unknown node: {self._node_id}")
        self._before = state.nodes[self._node_id].clone()
        self._after = state.update_node(self._node_id, **self._updates)

    def undo(self, state: CanvasState) -> None:
        if self._before is None:
            return
        state.restore_node(self._before)
        if self._after is not None:
            state.selection.nodes.discard(self._after.id)
        state.selection.nodes.add(self._before.id)


class ConnectCommand(CanvasCommand):
    """Add one edge and drop the edges it replaces, as a single undo step."""

    def __init__(self, edge: EdgeData, replace_ids: Iterable[str]) -> None:
        super().__init__(f"Connect {edge.source} -> {edge.target}")
        self._edge = edge.clone()
        self._replace_ids = list(dict.fromkeys(replace_ids))
        self._removed: list[EdgeData] = []

    def redo(self, state: CanvasState) -> None:
        self._removed = []
        for edge_id in self._replace_ids:
            removed = state.remove_edge(edge_id)
            if removed is not None:
                self._removed.append(removed)
        state.add_edge(self._edge)

    def undo(self, state: CanvasState) -> None:
        state.remove_edge(self._edge.id)
        for edge in self._removed:
            state.restore_edge(edge)


class DeleteSelectionCommand(CanvasCommand):
    """Remove nodes and edges, including wires attached to those nodes."""

    def __init__(
        self,
        node_ids: Iterable[str],
        edge_ids: Iterable[str],
    ) -> None:
        super().__init__("Delete selection")
        self._node_ids = list(dict.fromkeys(node_ids))
        self._edge_ids = list(dict.fromkeys(edge_ids))
        self._nodes: list[NodeData] = []
        self._edges: list[EdgeData] = []

    def redo(self, state: CanvasState) -> None:
        self._nodes = []
        self._edges = []
        for edge_id in self._edge_ids:
            removed = state.remove_edge(edge_id)
            if removed is not None:
                self._edges.append(removed)
        for node_id in self._node_ids:
            removed = state.remove_node(node_id)
            if removed is None:
                continue
            node, edges = removed
            self._nodes.append(node)
            self._edges.extend(edges)

    def undo(self, state: CanvasState) -> None:
        for node in self._nodes:
            state.restore_node(node)
        for edge in self._edges:
            state.restore_edge(edge)


class PasteNodesCommand(CanvasCommand):
    def __init__(
        self,
        nodes: Iterable[NodeData],
        edges: Iterable[EdgeData],
    ) -> None:
        super().__init__("Paste nodes")
        self._nodes = [node.clone() for node in nodes]
        self._edges = [edge.clone() for edge in edges]

    def redo(self, state: CanvasState) -> None:
        for node in self._nodes:
            state.add_node(node)
        for edge in self._edges:
            state.add_edge(edge)
        state.selection.set_nodes([node.id for node in self._nodes])
        state.selection.set_edges([edge.id for edge in self._edges])

    def undo(self, state: CanvasState) -> None:
        for edge in self._edges:
            state.remove_edge(edge.id)
        for node in self._nodes:
            state.remove_node(node.id)
