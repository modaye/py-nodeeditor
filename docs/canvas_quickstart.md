# Canvas Quickstart

This guide introduces the reusable canvas framework shipped with the `rp` project. It is designed to be published as a standalone package, so feel free to reference or bundle this document when distributing the canvas module.

## Installation

```bash
pip install py_editor  # replace with the final package name when published
```

If you work inside this repository, activate the uv-managed virtual environment and run the demos directly:

```powershell
uv run examples/demo_canvas.py
uv run examples/calculator_canvas.py
```

## Core Concepts

- `NodeData`: Serializable description of a node (id, title, `node_type`, ports, metadata).
- `EdgeData`: Serializable description of a connection (`edge_type`, endpoints, ports, metadata).
- `CanvasState`: In-memory graph containing nodes, edges, and the current selection. Provides dictionary-style access plus helpers for cloning and serialization.
- `CanvasController`: High-level façade that issues undoable commands (`add_node`, `create_edge`, `update_node`, clipboard operations, etc.).
- `CanvasRegistry`: Maps `node_type` / `edge_type` identifiers to factory functions that produce custom Qt graphics items.
- `CanvasView`: Interactive Qt view that renders the state, listens to controller events, and exposes signals for UI integrations.

The framework relies on PySide6 for rendering. All visual components are Qt `QGraphicsItem` subclasses.

## First Canvas Window

The snippet below wires together the state, controller, view, and registry to show a minimal window:

```python
from PySide6.QtWidgets import QApplication
from py_editor import CanvasController, CanvasRegistry, CanvasView

app = QApplication([])
registry = CanvasRegistry()
controller = CanvasController()
view = CanvasView(registry=registry)
view.set_controller(controller)

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

`canvas_registry` from `py_editor` is a shared, ready-to-use registry if you do not need isolation between canvases.

## Custom Rendering

Register factories to override node or edge visuals:

```python
from py_editor import CanvasRegistry, CanvasNodeItem, CanvasEdgeItem

class DemoNode(CanvasNodeItem):
    def paint(self, painter, option, widget=None):
        super().paint(painter, option, widget)
        # Add custom decorations here

class DemoEdge(CanvasEdgeItem):
    def _update_pen(self):
        super()._update_pen()
        pen = self.pen()
        pen.setColor(Qt.red)
        self.setPen(pen)

registry = CanvasRegistry()
registry.register_node_factory("demo", lambda node, view: DemoNode(node))
registry.register_edge_factory(
    "demo",
    lambda edge, source_item, target_item, view: DemoEdge(
        edge,
        source_item,
        target_item,
    ),
)
```

`CanvasRegistry` falls back to metadata for legacy payloads, but new integrations should set `node_type` and `edge_type` explicitly when creating nodes or edges.

## Serialization

The models serialize to plain dictionaries, making it easy to store projects as JSON:

```python
import json
from py_editor import CanvasState

payload = controller.state.to_dict()
with open("layout.json", "w", encoding="utf-8") as fh:
    json.dump(payload, fh, indent=2)

with open("layout.json", "r", encoding="utf-8") as fh:
    restored_state = CanvasState.from_dict(json.load(fh))
controller = CanvasController(restored_state)
```

The `CanvasState.from_dict` constructor validates that edges only reference known node ids and upgrades legacy metadata fields into the new type properties.

## Undo/Redo and Clipboard

All mutations executed through `CanvasController` are undoable. The controller exposes `undo`, `redo`, `copy_selection`, `cut_selection`, and `paste` helpers, so your UI can wire them to menu actions or shortcuts.

```python
view.undo_requested.connect(controller.undo)
view.redo_requested.connect(controller.redo)
```

(Emit these signals from your own widgets; the view keeps the scene focused on selection updates and node dragging.)

## Extending the Canvas

The canvas can be extended without modifying core code:

- Implement new commands by subclassing `CanvasCommand` if you need custom undo/redo behavior.
- Register connection preview factories via `CanvasRegistry.set_connection_preview_factory` to customize drag indicators.
- Build plugins that listen to view/controller signals—for example, to add grouping overlays that track collections of nodes.

Because every visual item retains a clone of its `NodeData`/`EdgeData`, integrations can inspect `item.node_data` or `item.edge_data` without mutating state directly.

## Testing

Example test coverage is provided in `tests/test_canvas_models_serialization.py` and `tests/test_canvas_registry.py`. When packaging the canvas separately, keep these tests (or equivalents) to ensure serialization and factory resolution work as expected.

Run the suite with:

```powershell
uv run pytest tests/test_canvas_models_serialization.py tests/test_canvas_registry.py
```

## Further Reading

- `examples/demo_canvas.py` – Minimal demo wiring custom rendering into the shared registry.
- `examples/calculator_canvas.py` – End-to-end example featuring node/edge scheduling logic, metadata editing, and custom factories.

With these building blocks you can embed the canvas into your own PySide6 applications and ship it as an independent, scriptable graph editor.
