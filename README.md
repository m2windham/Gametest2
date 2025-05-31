# Blender MCP + APIRS System

A universal, LLM/AI-ready system for controlling Blender via a robust Model Context Protocol (MCP) socket server and an advanced Python APIRS server.

## Features
- **Universal Blender Addon**: Socket server in Blender, accepts JSON commands for any Blender API operation (model, scene, material, texture, lighting, camera, animation, mesh, geometry, collections, objects, constraints, modifiers, particles, physics, sculpting, grease pencil, etc.).
- **Extensible Command Dispatcher**: Easily add new command types (PolyHaven, Hyper3D, APIRS, etc).
- **Robust Error Handling**: Full logging, error reporting, and context management.
- **Async/Concurrent**: Handles multiple clients and long-running operations.
- **Python APIRS Server**: Bridges to Blender, exposes FastMCP tools/resources, and implements your advanced protocol/resource system.
- **LLM/AI-Ready**: Designed for programmatic/AI-driven workflows.
- **Full Blender API Coverage**: All `bpy.ops`, `bpy.data`, and context operations supported ([Blender Python API Reference](https://docs.blender.org/api/current/index.html)).

## Getting Started

### 1. Install the Blender Addon
- Open Blender > Edit > Preferences > Add-ons > Install.
- Select `blender_mcp_addon.py`.
- Enable the addon and start the MCP server from the sidebar panel.

### 2. Run the APIRS Server
- Install dependencies: `pip install -r requirements.txt` (or use `uv`/`pdm` for lockfile support).
- Run: `python apirs_server.py`

### 3. Send Commands
- Use any client (Python, LLM, etc.) to send JSON commands to the APIRS server, which relays to Blender.
- Example command:
  ```json
  {"type": "create_object", "params": {"shape": "cube", "name": "MyCube"}}
  ```

## Architecture
- `blender_mcp_addon.py`: Universal Blender MCP socket server addon.
- `apirs_server.py`: Python FastMCP/APIRS server, bridges to Blender, exposes tools/resources.

## Advanced Features
- PolyHaven, Hyper3D, and APIRS endpoints included.
- Arbitrary code execution (with safety).
- Full logging and error reporting.

## License
MIT or as specified by project owner. 