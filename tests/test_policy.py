from __future__ import annotations

from py_editor.controller import CanvasController
from py_editor.graphics import node_height
from py_editor.policy import PortHit, nearest_port, preferred_port


def _graph():
    controller = CanvasController()
    source = controller.create_node("Source", outputs=["out"])
    other = controller.create_node("Other", outputs=["out"])
    sink = controller.create_node("Sink", inputs=["in"])
    return controller, source, other, sink


def test_connect_picks_the_only_ports() -> None:
    controller, source, _other, sink = _graph()
    result = controller.connect(source.id, sink.id)
    assert result.accepted
    assert result.reason == "ok"
    assert result.source_port == "out"
    assert result.target_port == "in"
    assert len(controller.state.edges) == 1


def test_second_input_replaces_the_first_in_one_undo() -> None:
    controller, source, other, sink = _graph()
    controller.connect(source.id, sink.id)
    controller.connect(other.id, sink.id)
    edges = list(controller.state.edges.values())
    assert len(edges) == 1
    assert edges[0].source == other.id

    controller.undo()
    edges = list(controller.state.edges.values())
    assert len(edges) == 1
    assert edges[0].source == source.id


def test_output_can_fan_out() -> None:
    controller, source, _other, sink = _graph()
    extra = controller.create_node("Extra", inputs=["in"])
    assert controller.connect(source.id, sink.id).accepted
    assert controller.connect(source.id, extra.id).accepted
    assert len(controller.state.edges) == 2


def test_cycle_is_rejected_without_changing_the_graph() -> None:
    controller = CanvasController()
    first = controller.create_node("A", inputs=["in"], outputs=["out"])
    second = controller.create_node("B", inputs=["in"], outputs=["out"])
    third = controller.create_node("C", inputs=["in"], outputs=["out"])
    assert controller.connect(first.id, second.id).accepted
    assert controller.connect(second.id, third.id).accepted
    rejected = controller.connect(third.id, first.id)
    assert not rejected.accepted
    assert rejected.reason == "cycle"
    assert len(controller.state.edges) == 2


def test_duplicate_connection_does_not_push_undo() -> None:
    controller, source, _other, sink = _graph()
    controller.connect(source.id, sink.id)
    again = controller.connect(source.id, sink.id)
    assert not again.accepted
    assert again.reason == "duplicate"
    controller.undo()
    assert controller.state.edges == {}


def test_self_connection_is_rejected() -> None:
    controller = CanvasController()
    node = controller.create_node("Loop", inputs=["in"], outputs=["out"])
    result = controller.connect(node.id, node.id)
    assert result.reason == "self"


def test_ambiguous_ports_must_be_named() -> None:
    controller = CanvasController()
    source = controller.create_node("Source", outputs=["a", "b"])
    sink = controller.create_node("Sink", inputs=["in"])
    result = controller.connect(source.id, sink.id)
    assert result.reason == "missing-port"


def test_swap_direction_is_not_a_cycle() -> None:
    controller = CanvasController()
    left = controller.create_node("Left", inputs=["in"], outputs=["out"])
    right = controller.create_node("Right", inputs=["in"], outputs=["out"])
    created = controller.connect(left.id, right.id)
    assert created.edge_id is not None
    swapped = controller.connect(
        right.id,
        left.id,
        replace_edges=(created.edge_id,),
    )
    assert swapped.accepted
    edge = next(iter(controller.state.edges.values()))
    assert edge.source == right.id
    assert edge.target == left.id


def test_nearest_port_respects_radius_direction_and_exclusion() -> None:
    hits = [
        PortHit("a", "output", "out", 0.0, 0.0),
        PortHit("b", "input", "in", 10.0, 0.0),
        PortHit("c", "input", "in", 30.0, 0.0),
    ]
    hit = nearest_port(hits, 12.0, 0.0, radius=24, direction="input")
    assert hit is not None and hit.node_id == "b"
    output = nearest_port(hits, 12.0, 0.0, radius=24, direction="output")
    assert output is not None and output.node_id == "a"
    assert nearest_port(hits, 80.0, 0.0, radius=24, direction="input") is None
    assert (
        nearest_port(
            hits,
            10.0,
            0.0,
            radius=8,
            direction="input",
            exclude_nodes={"b"},
        )
        is None
    )


def test_preferred_port_uses_the_first_free_input() -> None:
    controller = CanvasController()
    source = controller.create_node("Source", outputs=["out"])
    sink = controller.create_node("Sink", inputs=["left", "right"])
    controller.connect(source.id, sink.id, target_port="left")
    port = preferred_port(
        controller.state,
        controller.state.nodes[sink.id],
        "input",
        controller.policy,
    )
    assert port == "right"


def test_node_height_grows_after_the_minimum() -> None:
    assert node_height(2) > node_height(1)
    assert node_height(6) > node_height(2)


def test_delete_selection_undo_restores_nodes_and_edges() -> None:
    controller, source, _other, sink = _graph()
    controller.connect(source.id, sink.id)
    controller.delete([source.id, sink.id])
    assert set(controller.state.nodes) == {_other.id}
    assert controller.state.edges == {}
    controller.undo()
    assert set(controller.state.nodes) == {source.id, sink.id, _other.id}
    assert len(controller.state.edges) == 1


def test_multi_input_keeps_every_wire() -> None:
    controller = CanvasController()
    first = controller.create_node("First", outputs=["out"])
    second = controller.create_node("Second", outputs=["out"])
    union = controller.create_node(
        "Union",
        inputs=["Input"],
        metadata={"multi_inputs": ["Input"]},
    )
    assert controller.connect(first.id, union.id).accepted
    assert controller.connect(second.id, union.id).accepted
    assert len(controller.state.edges) == 2
    again = controller.connect(first.id, union.id)
    assert again.reason == "duplicate"


def test_declared_port_kinds_must_match() -> None:
    controller = CanvasController()
    source = controller.create_node(
        "Picture",
        outputs=["out"],
        metadata={"port_types": {"outputs": {"out": "image"}}},
    )
    sink = controller.create_node(
        "Table",
        inputs=["in"],
        metadata={"port_types": {"inputs": {"in": "table"}}},
    )
    rejected = controller.connect(source.id, sink.id)
    assert rejected.reason == "type"
    plain = controller.create_node("Plain", outputs=["out"])
    assert controller.connect(plain.id, sink.id).accepted
