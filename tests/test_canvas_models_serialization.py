from __future__ import annotations

import pytest

from py_editor.models import SCHEMA_VERSION, CanvasState, EdgeData, NodeData, Port, Viewport


def test_node_serialization_roundtrip() -> None:
    node = NodeData(
        id="node-1",
        title="Example",
        node_type="demo",
        position=(12.5, 34.0),
        ports=[Port("in", "input"), Port("out", "output")],
        metadata={"extra": True},
    )
    payload = node.to_dict()
    restored = NodeData.from_dict(payload)
    assert restored == node
    payload["metadata"]["changed"] = True
    assert "changed" not in node.metadata


def test_edge_serialization_roundtrip() -> None:
    edge = EdgeData(
        id="edge-1",
        source="node-1",
        target="node-2",
        edge_type="custom",
        source_port="out",
        target_port="in",
        metadata={"weight": 2},
    )
    payload = edge.to_dict()
    restored = EdgeData.from_dict(payload)
    assert restored == edge
    payload["metadata"]["mutated"] = True
    assert "mutated" not in edge.metadata


def test_canvas_state_serialization_roundtrip() -> None:
    state = CanvasState()
    node_a = NodeData(
        id="a",
        title="A",
        node_type="alpha",
        position=(0.0, 0.0),
    )
    node_b = NodeData(
        id="b",
        title="B",
        node_type="beta",
        position=(10.0, 0.0),
    )
    edge = EdgeData(
        id="edge",
        source="a",
        target="b",
        edge_type="link",
    )
    state.nodes[node_a.id] = node_a
    state.nodes[node_b.id] = node_b
    state.edges[edge.id] = edge
    state.selection.set_nodes(["a"])
    state.selection.set_edges(["edge"])

    payload = state.to_dict()
    restored = CanvasState.from_dict(payload)

    assert restored.nodes == state.nodes
    assert restored.edges == state.edges
    assert restored.viewport == state.viewport
    assert payload["schema_version"] == SCHEMA_VERSION
    assert restored.selection.nodes == state.selection.nodes
    assert restored.selection.edges == state.selection.edges


def test_canvas_state_from_dict_validates_edges() -> None:
    payload = {
        "schema_version": SCHEMA_VERSION,
        "viewport": {"x": 0.0, "y": 0.0, "zoom": 1.0},
        "nodes": [NodeData(id="a", title="A").to_dict()],
        "edges": [
            {
                "id": "edge",
                "source": "a",
                "target": "missing",
            }
        ],
    }
    with pytest.raises(ValueError):
        CanvasState.from_dict(payload)


def test_string_ports_and_old_documents_are_rejected() -> None:
    with pytest.raises(ValueError):
        NodeData.from_dict({"id": "old", "inputs": ["in"], "outputs": ["out"]})
    with pytest.raises(ValueError):
        NodeData.from_dict(
            {
                "schema_version": SCHEMA_VERSION,
                "id": "bad",
                "ports": ["in"],
                "draggable": True,
                "selectable": True,
                "connectable": True,
                "deletable": True,
            }
        )
    with pytest.raises(ValueError):
        CanvasState.from_dict({"schema_version": SCHEMA_VERSION, "nodes": []})
    with pytest.raises(ValueError):
        NodeData("dup", "Dup", ports=[Port("in", "input"), Port("in", "input")])
    both_sides = NodeData("join", "Join", ports=[Port("left", "input"), Port("left", "output")])
    assert [port.id for port in both_sides.ports] == ["left", "left"]


def test_viewport_roundtrip() -> None:
    state = CanvasState(viewport=Viewport(12.0, -4.0, 0.5))
    restored = CanvasState.from_dict(state.to_dict())
    assert restored.viewport == Viewport(12.0, -4.0, 0.5)


def test_edge_from_dict_legacy_canvas_metadata() -> None:
    payload = {
        "id": "legacy-edge",
        "source": "a",
        "target": "b",
        "edge_type": "legacy-edge",
    }
    edge = EdgeData.from_dict(payload)
    assert edge.edge_type == "legacy-edge"
