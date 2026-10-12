"""The performance graphs keep the counts the bench report describes."""

from tests.perf_graphs import crossing, fan, grid_chain, sparse_grid


def test_grid_chain_is_one_wire_short_of_the_node_count() -> None:
    state = grid_chain(100)
    assert len(state.nodes) == 100
    assert len(state.edges) == 99
    assert state.nodes["n0"].position == (0.0, 0.0)
    assert state.nodes["n1"].position == (240.0, 0.0)


def test_sparse_grid_keeps_every_eighth_wire() -> None:
    state = sparse_grid(80)
    assert len(state.nodes) == 80
    assert len(state.edges) == 9
    assert state.edges["e8"].source == "n0"


def test_fan_wires_every_leaf_to_the_hub() -> None:
    state = fan(50)
    assert len(state.nodes) == 50
    assert len(state.edges) == 49
    assert all(edge.source == "hub" for edge in state.edges.values())


def test_crossing_uses_three_targets_when_they_differ() -> None:
    state = crossing(40)
    assert len(state.nodes) == 40
    assert len(state.edges) == 20 * 3
    assert state.nodes["L0"].position == (0.0, 0.0)
    assert state.nodes["R0"].position == (520.0, 0.0)
