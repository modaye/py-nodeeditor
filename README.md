# Py-Editor

Py-Editor 是一个基于 PySide6 的可重用画布框架，用于构建图形化节点编辑器。它提供了创建可视化节点图所需的核心组件，可以轻松扩展以支持各种应用场景，如可视化编程、流程设计、数据处理等。

## 核心特性

- **可视化节点编辑**：平移、缩放、框选、吸附连线，并在选中节点时突出相关连线
- **可扩展架构**：通过注册机制自定义节点和连线的外观与行为
- **Undo/Redo 支持**：内置命令系统，支持完整的撤销/重做功能
- **序列化能力**：支持将画布状态保存为字典格式，便于存储和恢复
- **剪贴板操作**：支持复制、剪切和粘贴节点
- **自定义渲染**：允许完全控制节点和连线的视觉呈现

## 安装

包名是 `py-editor`，需要 Python 3.13 或更高版本。在这个仓库里：

```bash
uv sync
uv run python -m py_editor
```

## 快速开始

以下是一个简单的示例，展示如何创建一个基本的画布窗口：

```python
from PySide6.QtWidgets import QApplication
from py_editor import CanvasController, CanvasRegistry, CanvasView

app = QApplication([])
registry = CanvasRegistry()
controller = CanvasController()
view = CanvasView(registry=registry)
view.set_controller(controller)

# 创建节点
node_a = controller.create_node(
    "Input",
    position=(80.0, 120.0),
    node_type="demo",
    outputs=["value"],
)
node_b = controller.create_node(
    "Output",
    position=(320.0, 120.0),
    node_type="demo",
    inputs=["value"],
)

# 连接节点。connect 会检查输入口占用和成环，并记入撤销栈。
controller.connect(
    node_a.id,
    node_b.id,
    source_port="value",
    target_port="value",
)

view.refresh()
view.show()
app.exec()
```

## 自定义节点样式

你可以通过注册工厂函数来自定义节点和连线的外观：

```python
from PySide6.QtGui import QColor, QPen
from PySide6.QtCore import Qt
from py_editor import CanvasRegistry, CanvasNodeItem, CanvasEdgeItem

class CustomNodeItem(CanvasNodeItem):
    def paint(self, painter, option, widget=None):
        # 自定义节点绘制逻辑
        painter.setBrush(QColor("#FF6B6B"))
        painter.setPen(QPen(QColor("#4ECDC4"), 2))
        # 绘制节点形状...

class CustomEdgeItem(CanvasEdgeItem):
    def _update_pen(self):
        # 自定义连线样式
        pen = self.pen()
        pen.setColor(QColor("#4ECDC4"))
        pen.setWidth(2)
        self.setPen(pen)

# 注册自定义工厂函数
registry = CanvasRegistry()
registry.register_node_factory("custom", lambda node, view: CustomNodeItem(node))
registry.register_edge_factory(
    "custom",
    lambda edge, source_item, target_item, view: CustomEdgeItem(
        edge, source_item, target_item
    ),
)
```

## 数据序列化

画布状态可以轻松地序列化为字典格式，方便保存和加载：

```python
import json
from py_editor import CanvasState

# 保存画布状态
payload = controller.state.to_dict()
with open("layout.json", "w", encoding="utf-8") as fh:
    json.dump(payload, fh, indent=2)

# 加载画布状态
with open("layout.json", "r", encoding="utf-8") as fh:
    restored_state = CanvasState.from_dict(json.load(fh))
controller = CanvasController(restored_state)
```

## 操作

在空白处按住左键拖动可以平移画布。按住 Shift 再拖动是框选。滚轮缩放。从端口拖出连线，松手时吸附到兼容端口；拖动已有连线的端点可以改接。拖线时会标出两端的节点、端口和列名，接不上时写明原因。

点中一个节点后，和它相连的线变成蓝色并画在上层，其余的线变淡。按 Esc 取消选择后，线条恢复。右键节点可以复制、删除或断开连线；右键空白处可以排列或适应画面。

- `Ctrl+Z` / `Ctrl+Y`：撤销 / 重做
- `Ctrl+C` / `Ctrl+X` / `Ctrl+V` / `Ctrl+D`：复制 / 剪切 / 粘贴 / 复制一份
- `Delete`：删除选中的节点或连线
- `Ctrl+A`：全选
- `Ctrl+0`：适应画面
- `Ctrl++` / `Ctrl+-`：放大 / 缩小
- `Ctrl+L`：从左到右排列
- `Ctrl+Shift+L`：从上到下排列

`CanvasPolicy` 默认每个输入口只保留一条线。节点元数据 `multi_inputs` 里列出的端口可以接入多条线。不允许连成环。两端都声明了端口类型且类型不同时，连线会被拒绝。`controller.connect(...)` 使用这些规则并记入撤销栈。`create_edge` 只写入数据，不检查规则。

## 核心概念

- `NodeData`: 节点的序列化描述（ID、标题、类型、端口、元数据）
- `EdgeData`: 连线的序列化描述（类型、端点、端口、元数据）
- `CanvasState`: 内存中的图形结构，包含节点、连线和当前选择
- `CanvasController`: 撤销栈上的图编辑接口。`connect` 走连线规则，`create_edge` 只写入数据
- `CanvasPolicy`: 吸附半径、输入口是否单连接、是否允许成环
- `CanvasRegistry`: 将节点/连线类型映射到工厂函数的注册表
- `CanvasView`: 基于 Qt 的交互式视图组件

## 扩展功能

画布可以通过以下方式扩展：

- 实现新的命令类（继承 `py_editor.commands.CanvasCommand`）以支持自定义撤销/重做行为
- 通过 `CanvasRegistry.set_connection_preview_factory` 注册连接预览工厂函数
- 构建监听视图/控制器信号的插件，例如添加跟踪节点集合的分组覆盖层

## 许可证

本项目采用 MIT 许可证。详细信息请参见 [LICENSE](LICENSE) 文件。