"""Graph edits that do not need a window."""

from __future__ import annotations

from py_editor.controller import CanvasController
from py_editor.models import CanvasState, NodeData, Port


def _pair() -> CanvasController:
    controller = CanvasController()
    controller.create_node("A", node_id="a", position=(0.0, 0.0), ports=[Port("out", "output")])
    controller.create_node("B", node_id="b", position=(240.0, 0.0), ports=[Port("in", "input")])
    controller.connect("a", "b", source_port="out", target_port="in")
    return controller


def test_rejected_connect_leaves_the_graph_and_the_undo_stack() -> None:
    controller = _pair()
    before = controller.state.to_dict(include_selection=False)
    steps = len(controller.commands._undo_stack)
    rejected = controller.connect("a", "a", source_port="out", target_port="out")
    assert not rejected.accepted
    assert controller.state.to_dict(include_selection=False) == before
    assert len(controller.commands._undo_stack) == steps


def test_extra_undo_after_the_stack_is_empty_is_a_no_op() -> None:
    controller = _pair()
    while controller.commands.can_undo:
        controller.undo()
    assert controller.state.nodes == {}
    controller.undo()
    controller.redo()
    assert set(controller.state.nodes) == {"a"}


def test_delete_node_removes_its_edges_and_undo_restores_both() -> None:
    controller = _pair()
    controller.delete(["a"])
    assert "a" not in controller.state.nodes
    assert controller.state.edges == {}
    controller.undo()
    assert "a" in controller.state.nodes
    assert len(controller.state.edges) == 1


def test_remove_edges_is_one_undo_step() -> None:
    controller = _pair()
    controller.create_node("C", node_id="c", position=(240.0, 160.0), ports=[Port("in", "input")])
    second = controller.connect("a", "c", source_port="out", target_port="in")
    assert second.accepted
    edge_ids = list(controller.state.edges)
    controller.remove_edges(edge_ids)
    assert controller.state.edges == {}
    controller.undo()
    assert set(controller.state.edges) == set(edge_ids)


def test_cut_edge_can_be_pasted_back_onto_the_same_nodes() -> None:
    controller = _pair()
    edge_id = next(iter(controller.state.edges))
    controller.set_selection(edges=[edge_id])
    assert controller.cut_selection() is not None
    assert controller.state.edges == {}
    pasted = controller.paste()
    assert pasted is not None
    assert len(pasted.edges) == 1
    restored = controller.state.edges[pasted.edges[0].id]
    assert restored.source == "a"
    assert restored.target == "b"
    assert restored.source_port == "out"
    assert restored.target_port == "in"
    controller.undo()
    assert controller.state.edges == {}
    controller.undo()
    assert edge_id in controller.state.edges


def test_replacing_an_edge_changes_its_target_and_type_in_one_undo() -> None:
    controller = _pair()
    controller.create_node("C", node_id="c", position=(240.0, 160.0), ports=[Port("in", "input")])
    old = next(iter(controller.state.edges))
    result = controller.connect(
        "a",
        "c",
        source_port="out",
        target_port="in",
        edge_type="dashed",
        replace_edges=[old],
    )
    assert result.accepted
    assert old not in controller.state.edges
    edge = controller.state.edges[result.edge_id]
    assert (edge.source, edge.target, edge.edge_type) == ("a", "c", "dashed")
    controller.undo()
    restored = controller.state.edges[old]
    assert (restored.source, restored.target, restored.edge_type) == ("a", "b", "default")


def test_insert_between_rejects_a_different_kind_and_undoes_a_match() -> None:
    controller = CanvasController()
    controller.create_node("A", node_id="a", ports=[Port("out", "output", kind="table")])
    controller.create_node("B", node_id="b", ports=[Port("in", "input", kind="table")])
    assert controller.connect("a", "b").accepted
    edge_id = next(iter(controller.state.edges))
    blocked = NodeData(
        "mid",
        "Mid",
        ports=[Port("in", "input", kind="image"), Port("out", "output", kind="table")],
    )
    assert controller.insert_between(blocked, edge_id) is None
    assert set(controller.state.nodes) == {"a", "b"}
    inserted = controller.insert_between(
        NodeData(
            "mid",
            "Mid",
            ports=[Port("in", "input", kind="table"), Port("out", "output", kind="table")],
        ),
        edge_id,
    )
    assert inserted is not None
    assert edge_id not in controller.state.edges
    controller.undo()
    assert edge_id in controller.state.edges
    assert set(controller.state.nodes) == {"a", "b"}


def test_unknown_node_update_raises_and_leaves_the_graph() -> None:
    controller = _pair()
    before = controller.state.to_dict(include_selection=False)
    try:
        controller.update_node("missing", title="Nope")
    except KeyError as exc:
        assert "missing" in str(exc)
    else:
        raise AssertionError("expected KeyError")
    assert controller.state.to_dict(include_selection=False) == before


def test_many_edits_roundtrip_without_losing_nodes_or_edges() -> None:
    controller = CanvasController()
    ids: list[str] = []
    for index in range(20):
        node = controller.create_node(
            f"N{index}",
            position=(float(index * 20), 0.0),
            ports=[Port("in", "input"), Port("out", "output")],
        )
        ids.append(node.id)
    for index in range(1, 20):
        assert controller.connect(ids[index - 1], ids[index]).accepted
    for round_index in range(80):
        controller.move_nodes({ids[0]: (float(round_index), 2.0)})
        controller.undo()
        extra = controller.create_node("tmp", ports=[Port("in", "input")])
        controller.delete([extra.id])
        controller.undo()
        controller.undo()
        controller.set_selection(nodes=[ids[0]], edges=[])
        controller.copy_selection()
        assert controller.paste() is not None
        controller.undo()
        restored = CanvasState.from_dict(controller.state.to_dict())
        assert set(restored.nodes) == set(controller.state.nodes)
        assert set(restored.edges) == set(controller.state.edges)
    assert controller.state.nodes[ids[0]].position == (0.0, 0.0)
    assert len(controller.state.edges) == 19


def test_move_then_roundtrip_keeps_positions_and_ports() -> None:
    controller = _pair()
    controller.move_nodes({"a": (12.0, 8.0), "b": (300.0, 40.0)})
    restored = CanvasState.from_dict(controller.state.to_dict())
    assert restored.nodes["a"].position == (12.0, 8.0)
    assert restored.nodes["b"].position == (300.0, 40.0)
    edge = next(iter(restored.edges.values()))
    assert (edge.source, edge.source_port, edge.target, edge.target_port) == (
        "a",
        "out",
        "b",
        "in",
    )


def test_listener_runs_after_edits_but_not_after_selection() -> None:
    controller = CanvasController()
    seen = {"n": 0}
    controller.subscribe(lambda: seen.__setitem__("n", seen["n"] + 1))
    controller.create_node("A", ports=[Port("out", "output")])
    controller.set_selection(nodes=controller.state.node_ids())
    assert seen["n"] == 1
    controller.undo()
    assert seen["n"] == 2
    controller.unsubscribe(controller._listeners[0])
    controller.redo()
    assert seen["n"] == 2
