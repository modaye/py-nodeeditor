from __future__ import annotations

import time

from py_editor.controller import CanvasController
from py_editor.graphics import node_height
from py_editor.models import CanvasState, EdgeData, NodeData, Port
from py_editor.policy import PortHit, introduces_cycle, nearest_port, preferred_port


def _graph():
    controller = CanvasController()
    source = controller.create_node("Source", ports=[Port("out", "output")])
    other = controller.create_node("Other", ports=[Port("out", "output")])
    sink = controller.create_node("Sink", ports=[Port("in", "input")])
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
    extra = controller.create_node("Extra", ports=[Port("in", "input")])
    assert controller.connect(source.id, sink.id).accepted
    assert controller.connect(source.id, extra.id).accepted
    assert len(controller.state.edges) == 2


def test_cycle_is_rejected_without_changing_the_graph() -> None:
    controller = CanvasController()
    first = controller.create_node("A", ports=[Port("in", "input"), Port("out", "output")])
    second = controller.create_node("B", ports=[Port("in", "input"), Port("out", "output")])
    third = controller.create_node("C", ports=[Port("in", "input"), Port("out", "output")])
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
    node = controller.create_node("Loop", ports=[Port("in", "input"), Port("out", "output")])
    result = controller.connect(node.id, node.id)
    assert result.reason == "self"


def test_ambiguous_ports_must_be_named() -> None:
    controller = CanvasController()
    source = controller.create_node("Source", ports=[Port("a", "output"), Port("b", "output")])
    sink = controller.create_node("Sink", ports=[Port("in", "input")])
    result = controller.connect(source.id, sink.id)
    assert result.reason == "missing-port"


def test_long_chain_cycle_check_follows_edges_instead_of_rescanning() -> None:
    state = CanvasState()
    count = 4000
    for index in range(count):
        state.nodes[f"n{index}"] = NodeData(
            f"n{index}",
            f"Step {index}",
            ports=[]
            if index == 0
            else [Port("in", "input")]
            if index == count - 1
            else [Port("in", "input"), Port("out", "output")],
        )
        if index:
            state.edges[f"e{index}"] = EdgeData(
                f"e{index}",
                f"n{index - 1}",
                f"n{index}",
                source_port="out",
                target_port="in",
            )
    started = time.perf_counter()
    assert introduces_cycle(state, f"n{count - 1}", "n0") is True
    assert introduces_cycle(state, "n0", f"n{count - 1}") is False
    assert time.perf_counter() - started < 0.05


def test_swap_direction_is_not_a_cycle() -> None:
    controller = CanvasController()
    left = controller.create_node("Left", ports=[Port("in", "input"), Port("out", "output")])
    right = controller.create_node("Right", ports=[Port("in", "input"), Port("out", "output")])
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
    source = controller.create_node("Source", ports=[Port("out", "output")])
    sink = controller.create_node(
        "Sink",
        ports=[Port("left", "input"), Port("right", "input")],
    )
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
    first = controller.create_node("First", ports=[Port("out", "output")])
    second = controller.create_node("Second", ports=[Port("out", "output")])
    union = controller.create_node(
        "Union",
        ports=[Port("Input", "input", multi=True)],
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
        ports=[Port("out", "output", kind="image")],
    )
    sink = controller.create_node(
        "Table",
        ports=[Port("in", "input", kind="table")],
    )
    rejected = controller.connect(source.id, sink.id)
    assert rejected.reason == "type"
    plain = controller.create_node("Plain", ports=[Port("out", "output")])
    assert controller.connect(plain.id, sink.id).accepted
