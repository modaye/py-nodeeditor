from py_editor.layout import arrange_nodes


def test_horizontal_layout_flows_left_to_right() -> None:
    sizes = {"a": (100.0, 40.0), "b": (100.0, 40.0), "c": (120.0, 40.0)}
    positions = arrange_nodes(
        sizes,
        [("a", "b"), ("b", "c")],
        direction="horizontal",
    )
    assert positions["a"][0] < positions["b"][0] < positions["c"][0]


def test_vertical_layout_flows_top_to_bottom() -> None:
    sizes = {"a": (100.0, 40.0), "b": (100.0, 50.0), "c": (100.0, 40.0)}
    positions = arrange_nodes(
        sizes,
        [("a", "b"), ("b", "c")],
        direction="vertical",
    )
    assert positions["a"][1] < positions["b"][1] < positions["c"][1]


def test_branch_shares_a_column_when_horizontal() -> None:
    sizes = {"a": (80.0, 40.0), "b": (80.0, 40.0), "c": (80.0, 40.0)}
    positions = arrange_nodes(
        sizes,
        [("a", "b"), ("a", "c")],
        direction="horizontal",
    )
    assert positions["b"][0] == positions["c"][0]
    assert positions["b"][1] != positions["c"][1]
    assert positions["a"][0] < positions["b"][0]


def test_cycle_stays_bounded() -> None:
    sizes = {"a": (10.0, 10.0), "b": (10.0, 10.0)}
    positions = arrange_nodes(sizes, [("a", "b"), ("b", "a")])
    assert set(positions) == {"a", "b"}
    assert abs(positions["a"][0] - positions["b"][0]) < 500
