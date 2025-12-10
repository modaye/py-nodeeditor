# Py-Editor

Py-Editor 是一个基于 PySide6 的可重用画布框架，用于构建图形化节点编辑器。它提供了创建可视化节点图所需的核心组件，可以轻松扩展以支持各种应用场景，如可视化编程、流程设计、数据处理等。

## 核心特性

- **可视化节点编辑**：提供直观的图形界面，支持创建、连接和操作节点
- **可扩展架构**：通过注册机制自定义节点和连线的外观与行为
- **Undo/Redo 支持**：内置命令系统，支持完整的撤销/重做功能
- **序列化能力**：支持将画布状态保存为字典格式，便于存储和恢复
- **剪贴板操作**：支持复制、剪切和粘贴节点
- **自定义渲染**：允许完全控制节点和连线的视觉呈现

## 安装

```bash
pip install py-editor
```

或者如果你在这个仓库中开发，可以直接运行示例：

```bash
uv run examples/demo_canvas.py
uv run examples/calculator_canvas.py
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

# 连接节点
controller.create_edge(
    source=node_a.id,
    target=node_b.id,
    edge_type="demo",
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

## 示例程序

项目包含了两个示例程序来演示不同的使用场景：

1. **基础画布演示** ([examples/demo_canvas.py](examples/demo_canvas.py)):
   展示了如何使用自定义渲染创建一个简单的节点编辑器

2. **计算器演示** ([examples/calculator_canvas.py](examples/calculator_canvas.py):
   一个完整的计算器应用，展示了更复杂的节点交互和计算引擎集成

运行示例：
```bash
uv run examples/demo_canvas.py
uv run examples/calculator_canvas.py
```

## 核心概念

- `NodeData`: 节点的序列化描述（ID、标题、类型、端口、元数据）
- `EdgeData`: 连线的序列化描述（类型、端点、端口、元数据）
- `CanvasState`: 内存中的图形结构，包含节点、连线和当前选择
- `CanvasController`: 高级接口，负责执行可撤销的操作
- `CanvasRegistry`: 将节点/连线类型映射到工厂函数的注册表
- `CanvasView`: 基于 Qt 的交互式视图组件

## 扩展功能

画布可以通过以下方式扩展：

- 实现新的命令类（继承 `CanvasCommand`）以支持自定义撤销/重做行为
- 通过 `CanvasRegistry.set_connection_preview_factory` 注册连接预览工厂函数
- 构建监听视图/控制器信号的插件，例如添加跟踪节点集合的分组覆盖层

## 许可证

本项目采用 MIT 许可证。详细信息请参见 [LICENSE](LICENSE) 文件。