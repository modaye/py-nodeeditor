# 公开 API

`py_editor` 只负责节点、连线和撤销。参数、运行结果、图标和拖放类型都由宿主放在自己的数据里，库不解释这些内容。

包名是 `py_editor`。

## 数据

```python
schema_version = 1

class Port:          # id, side, kind=None, multi=False
class NodeData:      # id, title, node_type, position, ports, 四个布尔标志, metadata
class EdgeData:      # id, source, target, source_port, target_port, edge_type, metadata
class Viewport:      # x, y, zoom
class CanvasState:   # nodes, edges, selection, viewport
class CanvasSelection:
```

`NodeData.to_dict` / `CanvasState.to_dict` 写出 `schema_version`。`from_dict` 在版本不是 `1`、缺少视口、或端口不是对象列表时抛出 `ValueError`。

`metadata` 的值必须能被 `json` 序列化，这一点由宿主保证。库只做浅拷贝，不解释内容。

四个标志默认真，并且总是写入字典。

## 编辑

`CanvasController(state=None, policy=None)`。

| 方法 | 行为 |
| --- | --- |
| `create_node(...)` | 建节点。同一侧的重复端口 id 拒绝；输入和输出可以同名。进撤销栈 |
| `update_node(node_id, **fields)` | 替换给出的字段，含 `ports`、`metadata`、标志。进撤销栈 |
| `remove_node` / `move_nodes` / `delete` | `delete` 跳过 `deletable=False` 的对象。一次撤销 |
| `connect(source, target, source_port, target_port, ...)` | 唯一加边方式。失败返回 `ConnectResult`，不压栈 |
| `undo` / `redo` / `clear_history` | 通知订阅者 |
| `set_selection` / `selected_nodes` / `selected_edges` | 不进撤销，不触发 `subscribe` |
| `copy_selection` / `cut_selection` / `paste` | 内存剪贴板。覆盖 `rewrite_pasted_nodes` 可改副本 |
| `insert_between(node, edge_id)` | 节点必须是一个输入、一个输出，且 kind 与被拆的边兼容 |
| `subscribe` / `unsubscribe` | `callback: Callable[[], None]` |

没有 `create_edge` 和 `add_edge`。

`state` 可读。不要通过改 `state.nodes[id].metadata` 更新画面；那样不会通知，也不再是 API。

## 连接

`CanvasPolicy(snap_radius=24, single_input=True, allow_cycles=False, extra_rules=())`。

`single_input=True` 时，`Port.multi=False` 的输入口保留一条线。`Port.multi=True` 的输入口不受这条限制。`kind` 两端都非空且不同则 `reason="type"`。其余原因：`ok`、`unknown-node`、`missing-port`、`self`、`cycle`、`duplicate`、`cancelled`、`locked`。

`extra_rules` 的元素是 `(CanvasState, ConnectRequest) -> ConnectResult | None`。内置规则通过后才调用。返回 `None` 表示不发表意见。规则函数不许改状态。

不可连的节点（`connectable=False`）在手势开始时就被拒绝，到不了 `evaluate`。

## 外观与注册

```python
registry = CanvasRegistry()
registry.register_node_factory("demo", lambda node, view: DemoNode(node))
registry.register_edge_factory("demo", lambda edge, source, target, view: DemoEdge(edge, source, target))
view = CanvasView(registry=registry)
```

未传入时，视图使用自己的空 `CanvasRegistry`，只有默认卡片和默认边。

`DemoNode` 继承 `CanvasNodeItem`。只改颜色就重写 `paint`。改圆点位置必须重写 `port_anchor`。边的端点调用这个方法，所以两者不会各算各的。

预览样式是 `ConnectionPreviewStyleOptions`（颜色、线宽、虚线）。

## 视图

```python
CanvasView(
    registry=None,
    drop_mime=None,
    connection_caption=None,
    grid=True,
    edge_animation=False,
)
```

`drop_mime=None` 时不接受拖放。宿主传入自己的 MIME 字符串。放下后：

```text
tool_dropped(tool_id: str, x: float, y: float, edge_id: str)
```

`connection_caption(origin, snapped, valid, reason) -> str` 缺省时只含标题和端口 id。

信号：`selection_changed`、`graph_changed`、`connection_finished`、`node_double_clicked`、`connection_hint`、`node_menu`、`edge_menu(menu, edge_id)`。

视口：`viewport_state() -> Viewport`，`apply_viewport(viewport)`。平移结束、缩放、适应画面后写回 `state.viewport`。`set_controller` 应用状态里的视口。

`arrange(direction, include=None)`。`include` 缺省表示全部节点。

`fit_to_contents`、`focus_on_selection`、`focus_nodes`、`zoom_by`、`undo`、`redo`、`refresh`、`sync_node` 可用。`focus_on_selection` 框住选中的节点和选中的边。`Ctrl+Shift+0` 调用它，`Ctrl+0` 仍是适应全部节点。`sync_node` 只是按模型重画一个节点，调用前模型必须已经通过 `update_node` 改过。

`set_snap_to_grid(False)` 是默认。打开后，松开拖动时节点坐标对齐背景网格的 24 像素。预览跟着指针走。撤销回到按下时的坐标。

坐标转换用 Qt 的 `mapToScene` / `mapFromScene`。

## 宿主接入

1. 用 `ports=[Port("in", "input", kind="table"), Port("out", "output", kind="table")]` 描述插座。`kind` 是宿主的记号，库只做相等比较。
2. 参数、运行结果、图标都放在 `metadata`。要画出来就注册自己的 `CanvasNodeItem`。
3. 调色板拖放：`drop_mime` 用宿主自己的字符串，在 `tool_dropped` 里 `create_node`。
4. 更多连接限制：往 `extra_rules` 加函数。
5. 图变了要跑业务：`subscribe`。视图会自己跟上控制器。宿主自己的 `refresh()` 仍然可以调用，必须是空操作安全的。

## 兼容性

包版本 `0.1.0` 是 Alpha。图文件只接受 `schema_version == 1`，没有从其他形状升级的路径。

在升到 `1.0` 之前：

- `py_editor.__all__` 里的名字，以及本文列出的方法和参数，保持可调用。新参数必须有默认值。
- 今天能 `from_dict` 的版本 1 文档，以后的 `0.x` 也必须能读。读入时忽略不认识的键；必填字段缺了或类型错了仍抛 `ValueError`。
- 要删、改名或改变必填字段，先把 `schema_version` 加一，并在本文写明旧版本不再读取。在那之前不写迁移代码。
- 以 `_` 开头的方法、`CanvasState.add_edge`，以及没有写进本文的行为，不算稳定接口。

`1.0` 之后按语义化版本：补方法和可选字段算次版本，断掉上述合同算主版本。
