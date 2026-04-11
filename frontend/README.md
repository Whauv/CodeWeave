# Frontend

The browser UI for CodeWeave lives here.

- `index.html` defines the zero-build application shell
- `graph.js` coordinates the frontend modules
- `graph_renderer.js` renders tree and force graphs
- `graph_state.js` stores shared UI state
- `graph_ui_controller.js` handles theme, layout, exports, and toolbar actions
- `graph_effects_controller.js` manages blast, search, and hover visual state
- `history_controller.js` manages evolution mode
- `node_interactions.js` wires node events and tooltips
- `panel.js` renders the right-side detail panel and chat
- `scan_controller.js` handles scan submission and scan history
- `browser_store.js` persists browser-side history and cached graph data
