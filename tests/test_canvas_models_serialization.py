from __future__ import annotations

import pytest

from py_editor.models import CanvasState, EdgeData, NodeData


def test_node_serialization_roundtrip() -> None:
    node = NodeData(
        id="node-1",
        title="Example",
        node_type="demo",
        position=(12.5, 34.0),
        inputs=["in"],
        outputs=["out"],
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
    assert restored.selection.nodes == state.selection.nodes
    assert restored.selection.edges == state.selection.edges


def test_canvas_state_from_dict_validates_edges() -> None:
    payload = {
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


def test_node_from_dict_validates_inputs_outputs() -> None:
    payload = {
        "id": "bad",
        "title": "Bad",
        "inputs": "not-list",
    }
    with pytest.raises(ValueError):
        NodeData.from_dict(payload)


def test_node_from_dict_legacy_canvas_metadata() -> None:
    payload = {
        "id": "legacy",
        "title": "Legacy",
        "node_type": "legacy-demo",
    }
    node = NodeData.from_dict(payload)
    assert node.node_type == "legacy-demo"


def test_edge_from_dict_legacy_canvas_metadata() -> None:
    payload = {
        "id": "legacy-edge",
        "source": "a",
        "target": "b",
        "edge_type": "legacy-edge",
    }
    edge = EdgeData.from_dict(payload)
    assert edge.edge_type == "legacy-edge"
