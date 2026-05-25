# memex — Codebase Knowledge Graph VS Code Extension

Visualise your codebase as an interactive temporal knowledge graph in VS Code.

## Features

- **Interactive Graph**: Visualise codebase elements (Modules, Symbols, Decisions, Problems) and their connections in real-time.
- **D3 force-directed Layout**: Easily pan, zoom, drag, and interact with graph nodes.
- **Active Editor Highlighting**: The currently open file in VS Code is highlighted on the graph.
- **Double Click Navigation**: Double click any Module or Symbol node to open the corresponding file instantly.
- **Details Sidebar**: Click any node to view metadata, including status, scope, linked commit, and AI-synthesised summaries.
- **Server Controller**: Start and stop the local `memex` server with one click via status bar commands.
- **Dynamic Port Discovery & Alignment**: Automatically aligns HTTP/SSE clients to the local active repository server's port. Uses settings to allow customized server orchestration.
- **Debounced Watcher Notifications**: Coalesces rapid consecutive file system changes (e.g. from git branch stashes/checkouts) into a single visualizer refresh trigger.

## Extension Settings

This extension contributes the following settings:

* `memex.serverPort`: The port number on which the local `memex` server runs (default: `7463`).

## Installation

Ensure you have `memex` CLI installed:
```bash
uv tool install memex-mcp
```

## Setup & Running

1. Open your workspace folder in VS Code.
2. The extension activates automatically and checks if the local `memex` server is running.
3. Click the `memex` item in the Status Bar to open the graph, or run `memex: Open Graph Panel` in the Command Palette.
4. If offline, click **Start local server** or run `memex: Start local server`.

