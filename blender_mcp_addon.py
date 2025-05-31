bl_info = {
    "name": "Universal Blender MCP Socket Server",
    "author": "Your Name / AI Assistant",
    "version": (2, 0, 0),
    "blender": (3, 0, 0),
    "location": "View3D > Sidebar > MCP",
    "description": "Universal socket server for LLM/AI-driven Blender control (full API coverage, best practices, async, logging, EasyBPY, geometry-script)",
    "category": "System",
}

import bpy
import socket
import threading
import json
import traceback
import sys
import types
import select
import base64
import time
import logging
import os
import uuid

DEFAULT_PORT = 9876  # Added to fix NameError and provide a default port for the server

server_running = False  # Added to fix NameError for UI panel and server logic

# --- Optional: Import EasyBPY and geometry-script if available ---
try:
    import easybpy
except ImportError:
    easybpy = None

try:
    import geometry_script
except ImportError:
    geometry_script = None

# --- Logging Setup ---
LOG_PATH = os.path.join(os.path.expanduser("~"), "mcp_blender.log")
logging.basicConfig(filename=LOG_PATH, level=logging.DEBUG, format="%(asctime)s %(levelname)s %(message)s")
def log(msg):
    print(msg)
    logging.debug(msg)

# --- Protocol Version ---
MCP_PROTOCOL_VERSION = "2.0"

# --- Job Queue with Timeout and Status ---
JOB_QUEUE = []  # List of (job_id, func, args, kwargs, enqueued_time, timeout, status)
JOB_STATUS = {}  # job_id -> {'status': str, 'result': any, 'error': str, 'start': float, 'end': float}
DEFAULT_JOB_TIMEOUT = 30  # seconds

def process_job_queue():
    now = time.time()
    if JOB_QUEUE:
        job_id, func, args, kwargs, enqueued, timeout, status = JOB_QUEUE[0]
        job_info = JOB_STATUS.get(job_id, {})
        if status == 'queued':
            JOB_QUEUE[0] = (job_id, func, args, kwargs, enqueued, timeout, 'running')
            JOB_STATUS[job_id] = {'status': 'running', 'start': now, 'result': None, 'error': None}
            try:
                result = func(*args, **kwargs)
                JOB_STATUS[job_id].update({'status': 'done', 'result': result, 'end': time.time()})
            except Exception as e:
                JOB_STATUS[job_id].update({'status': 'error', 'error': str(e), 'end': time.time()})
            JOB_QUEUE.pop(0)
        elif status == 'running':
            # Should not happen, but skip if stuck
            if now - enqueued > timeout:
                JOB_STATUS[job_id].update({'status': 'timeout', 'error': 'Job timed out', 'end': time.time()})
                JOB_QUEUE.pop(0)
    return 0.1

def queue_job(func, *args, timeout=DEFAULT_JOB_TIMEOUT, **kwargs):
    job_id = str(uuid.uuid4())
    JOB_QUEUE.append((job_id, func, args, kwargs, time.time(), timeout, 'queued'))
    JOB_STATUS[job_id] = {'status': 'queued', 'start': time.time(), 'result': None, 'error': None}
    return job_id

def get_job_status(job_id):
    return JOB_STATUS.get(job_id, {'status': 'unknown'})

bpy.app.timers.register(process_job_queue, persistent=True)

# --- Configurable Security/Performance Options ---
MAX_MESSAGE_SIZE = 2 * 1024 * 1024  # 2MB
ALLOWED_IPS = {"127.0.0.1", "localhost"}  # Set to None to allow all
MAX_CONNECTIONS = 10

# --- Chunked Upload State ---
UPLOADS = {}  # upload_id -> {'chunks': [], 'start_time': float}
CHUNK_TIMEOUT = 60  # seconds to keep incomplete uploads
COMMAND_TIMEOUT = 30  # seconds per command

def cleanup_uploads():
    now = time.time()
    to_delete = [uid for uid, v in UPLOADS.items() if now - v['start_time'] > CHUNK_TIMEOUT]
    for uid in to_delete:
        del UPLOADS[uid]
        print(f"[MCP] Cleaned up expired upload: {uid}")

# --- Utility: Ensure Blender is in Object Mode ---
def ensure_object_mode():
    try:
        if bpy.context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
    except Exception as e:
        print(f"[MCP] Could not set Object Mode: {e}")

# --- Tool Registry for Tool-based Protocol ---
TOOL_REGISTRY = {}
PROMPT_REGISTRY = []  # For future prompt/template support

def tool(name=None, description=None, args=None):
    """Decorator to register a function as a tool with metadata."""
    def decorator(func):
        tool_name = name or func.__name__
        TOOL_REGISTRY[tool_name] = {
            "func": func,
            "description": description or (func.__doc__ or ""),
            "args": args or [],
        }
        return func
    return decorator

# --- Tool Handlers (with docstrings for LLMs) ---
@tool(name="create_object", description="Create a new object in the scene.", args=[{"name": "name", "type": "str", "desc": "Object name"}])
def handle_create_object(params):
    """Create a new object in the scene."""
    name = params.get("name", "Cube")
    mesh = bpy.data.meshes.new(name)
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    return {"status": "ok", "result": f"Object {name} created"}

@tool(name="delete_object", description="Delete an object from the scene.", args=[{"name": "name", "type": "str", "desc": "Object name"}])
def handle_delete_object(params):
    """Delete an object from the scene."""
    name = params.get("name")
    obj = bpy.data.objects.get(name)
    if obj:
        bpy.data.objects.remove(obj, do_unlink=True)
        return {"status": "ok", "result": f"Object {name} deleted"}
    else:
        return {"status": "error", "message": f"Object {name} not found"}

@tool(name="create_material", description="Create a new material.", args=[{"name": "name", "type": "str", "desc": "Material name"}, {"name": "color", "type": "list", "desc": "RGBA color"}])
def handle_create_material(params):
    """Create a new material."""
    name = params.get("name", "Material")
    color = params.get("color", [1,1,1,1])
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    if bsdf:
        bsdf.inputs["Base Color"].default_value = color
    return {"status": "ok", "result": f"Material {name} created"}

@tool(name="assign_material", description="Assign a material to an object.", args=[{"name": "object", "type": "str"}, {"name": "material", "type": "str"}])
def handle_assign_material(params):
    """Assign a material to an object."""
    obj_name = params.get("object")
    mat_name = params.get("material")
    obj = bpy.data.objects.get(obj_name)
    mat = bpy.data.materials.get(mat_name)
    if obj and mat:
        if obj.data.materials:
            obj.data.materials[0] = mat
        else:
            obj.data.materials.append(mat)
        return {"status": "ok", "result": f"Material {mat_name} assigned to {obj_name}"}
    else:
        return {"status": "error", "message": "Object or material not found"}

@tool(name="run_code", description="Execute arbitrary Blender Python code.", args=[{"name": "code", "type": "str"}])
def handle_run_code(params):
    """Execute arbitrary Blender Python code."""
    code = params.get("code", "")
    local_ns = {"bpy": bpy, "easybpy": easybpy, "geometry_script": geometry_script}
    try:
        exec(code, local_ns)
        return {"status": "ok", "result": "Code executed"}
    except Exception as e:
        return {"status": "error", "message": str(e), "traceback": traceback.format_exc()}

# --- Tool-based Protocol Endpoints ---
def handle_list_tools(params):
    """List all available tools with metadata."""
    tools = []
    for name, meta in TOOL_REGISTRY.items():
        tools.append({
            "name": name,
            "description": meta["description"],
            "args": meta["args"],
        })
    return {"status": "ok", "tools": tools}

def handle_call_tool(params):
    """Call a tool by name with arguments."""
    tool_name = params.get("tool_name")
    arguments = params.get("arguments", {})
    meta = TOOL_REGISTRY.get(tool_name)
    if not meta:
        return {"status": "error", "message": f"Tool {tool_name} not found"}
    try:
        return meta["func"](arguments)
    except Exception as e:
        return {"status": "error", "message": str(e), "traceback": traceback.format_exc()}

def handle_list_prompts(params):
    """List available prompts/templates (stub)."""
    return {"status": "ok", "prompts": PROMPT_REGISTRY}

# --- Universal Dispatcher ---
def universal_dispatch(command, client_id=None):
    try:
        cmd_type = command.get("type")
        params = command.get("params", {})
        log(f"[MCP] Command from {client_id}: {cmd_type} {params}")
        # Protocol version check
        if cmd_type == "protocol_version":
            return {"status": "ok", "result": MCP_PROTOCOL_VERSION}
        # Introspection/help
        if cmd_type == "help":
            return {"status": "ok", "result": list(COMMAND_DISPATCHER.keys())}
        # Main dispatcher
        if cmd_type in COMMAND_DISPATCHER:
            return COMMAND_DISPATCHER[cmd_type](params)
        else:
            return {"status": "error", "message": f"Unknown command type: {cmd_type}"}
    except Exception as e:
        tb = traceback.format_exc()
        log(f"[MCP] Error: {e}\n{tb}")
        return {"status": "error", "message": str(e), "traceback": tb}

# --- Command Handlers ---
def handle_easybpy(params):
    if not easybpy:
        return {"status": "error", "message": "EasyBPY not installed"}
    easybpy.cube()
    return {"status": "ok", "result": "Cube created with EasyBPY"}

def handle_geometry_script(params):
    if not geometry_script:
        return {"status": "error", "message": "geometry-script not installed"}
    script = params.get("script")
    if not script:
        return {"status": "error", "message": "No script provided"}
    geometry_script.run(script)
    return {"status": "ok", "result": "geometry-script executed"}

# --- Tool: Clear Scene Safely ---
@tool(name="clear_scene", description="Safely delete all objects in the scene and purge orphans.", args=[])
def handle_clear_scene(params):
    """Safely delete all objects in the scene and purge orphans."""
    log("[MCP] Clearing scene...")
    try:
        # Deselect all, then select all
        bpy.ops.object.select_all(action='DESELECT')
        for obj in list(bpy.data.objects):
            try:
                obj.select_set(True)
            except Exception as e:
                log(f"[MCP] Error selecting object {obj.name}: {e}")
        bpy.ops.object.delete()
        log("[MCP] All objects deleted.")
        # Purge orphans with delay
        for i in range(3):
            try:
                bpy.ops.outliner.orphans_purge(do_local_ids=True, do_linked_ids=True, do_recursive=True)
                log(f"[MCP] Orphans purged (pass {i+1})")
            except Exception as e:
                log(f"[MCP] Orphan purge error: {e}")
        return {"status": "ok", "result": "Scene cleared"}
    except Exception as e:
        log(f"[MCP] Scene clear error: {e}")
        return {"status": "error", "message": str(e)}

# --- Tool: Query Job Status ---
@tool(name="get_job_status", description="Get the status of a queued job.", args=[{"name": "job_id", "type": "str"}])
def handle_get_job_status(params):
    job_id = params.get("job_id")
    return get_job_status(job_id)

# --- Tool: Run Natural Language Command (Stub) ---
@tool(name="run_nl_command", description="Run a natural language command using an LLM (stub).", args=[{"name": "command", "type": "str"}])
def handle_run_nl_command(params):
    """Stub for future LLM integration: run a natural language command."""
    cmd = params.get("command", "")
    # In future: call LLM, generate code, execute
    log(f"[MCP] Received NL command: {cmd}")
    return {"status": "error", "message": "Natural language command execution not implemented yet."}

COMMAND_DISPATCHER = {
    "create_object": handle_create_object,
    "delete_object": handle_delete_object,
    "create_material": handle_create_material,
    "assign_material": handle_assign_material,
    "easybpy": handle_easybpy,
    "geometry_script": handle_geometry_script,
    "run_code": handle_run_code,
    "list_tools": handle_list_tools,
    "call_tool": handle_call_tool,
    "list_prompts": handle_list_prompts,
    "clear_scene": handle_clear_scene,
    "get_job_status": handle_get_job_status,
    "run_nl_command": handle_run_nl_command,
    # ...add more handlers for full API coverage...
}

# --- Socket Server (Main Thread Only) ---
class MCPServerThread(threading.Thread):
    def __init__(self, port=DEFAULT_PORT):
        super().__init__(daemon=True)
        self.port = port
        self.sock = None
        self._stop_event = threading.Event()

    def run(self):
        global server_running
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("localhost", self.port))
        self.sock.listen(5)
        server_running = True
        log(f"[MCP] Listening on localhost:{self.port}")
        try:
            while not self._stop_event.is_set():
                self.sock.settimeout(1.0)
                try:
                    conn, addr = self.sock.accept()
                except socket.timeout:
                    continue
                threading.Thread(target=self.handle_client, args=(conn, addr), daemon=True).start()
        finally:
            self.sock.close()
            server_running = False
            log("[MCP] Server stopped.")

    def handle_client(self, conn, addr):
        with conn:
            try:
                data = b""
                while True:
                    chunk = conn.recv(4096)
                    if not chunk:
                        break
                    data += chunk
                    if len(chunk) < 4096:
                        break
                msg = data.decode('utf-8').strip()
                log(f"[MCP] Received from {addr}: {msg[:120]}")
                try:
                    command = json.loads(msg)
                except Exception as e:
                    error = {"status": "error", "message": f"Invalid JSON: {e}", "protocol_version": MCP_PROTOCOL_VERSION}
                    conn.sendall(json.dumps(error).encode('utf-8'))
                    return
                # Queue job for main thread execution
                def respond():
                    response = universal_dispatch(command, client_id=addr)
                    response["protocol_version"] = MCP_PROTOCOL_VERSION
                    try:
                        conn.sendall(json.dumps(response).encode('utf-8'))
                    except Exception as e:
                        log(f"[MCP] Error sending response: {e}")
                queue_job(respond)
            except Exception as e:
                tb = traceback.format_exc()
                error = {"status": "error", "message": str(e), "traceback": tb, "protocol_version": MCP_PROTOCOL_VERSION}
                try:
                    conn.sendall(json.dumps(error).encode('utf-8'))
                except:
                    pass

    def stop(self):
        global server_running
        self._stop_event.set()
        server_running = False

# --- Blender UI Panel, Registration, etc. ---
# (Same as before, but add a button to show log file location and protocol version)

# --- Dev/LLM Support: fake-bpy-module ---
# Add to requirements-dev.txt: fake-bpy-module-<your-blender-version>
# This enables code completion and static analysis in IDEs and for LLMs.

# --- Improved Socket Server Thread ---
class MCPServerThread(threading.Thread):
    def __init__(self, port=DEFAULT_PORT):
        super().__init__(daemon=True)
        self.port = port
        self.sock = None
        self._stop_event = threading.Event()
        self.active_connections = 0

    def run(self):
        global server_running
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("localhost", self.port))
        self.sock.listen(MAX_CONNECTIONS)
        server_running = True
        print(f"[MCP] Listening on localhost:{self.port}")
        try:
            while not self._stop_event.is_set():
                self.sock.settimeout(1.0)
                try:
                    conn, addr = self.sock.accept()
                except socket.timeout:
                    continue
                if ALLOWED_IPS and addr[0] not in ALLOWED_IPS:
                    print(f"[MCP] Connection from {addr[0]} denied (not in allowlist)")
                    conn.close()
                    continue
                if self.active_connections >= MAX_CONNECTIONS:
                    print(f"[MCP] Max connections reached, rejecting {addr}")
                    conn.close()
                    continue
                self.active_connections += 1
                print(f"[MCP] Client connected: {addr}")
                threading.Thread(target=self.handle_client, args=(conn, addr), daemon=True).start()
        finally:
            self.sock.close()
            server_running = False
            print("[MCP] Server stopped.")

    def handle_client(self, conn, addr):
        try:
            conn.settimeout(10.0)
            buffer = b""
            while True:
                ready = select.select([conn], [], [], 1.0)[0]
                if not ready:
                    if self._stop_event.is_set():
                        break
                    continue
                chunk = conn.recv(4096)
                if not chunk:
                    break
                buffer += chunk
                if len(buffer) > MAX_MESSAGE_SIZE:
                    error = {"status": "error", "message": "Message too large"}
                    conn.sendall(json.dumps(error).encode('utf-8'))
                    print(f"[MCP] Error: Message from {addr} exceeded max size")
                    return
                # Try to decode as JSON (handle multiple messages per connection)
                while True:
                    try:
                        msg = buffer.decode('utf-8')
                        obj, idx = self._json_partial_parse(msg)
                        if obj is None:
                            break  # Need more data
                        command = obj
                        buffer = buffer[idx:]
                        print(f"[MCP] Received from {addr}: {json.dumps(command)[:120]}")
                        ensure_object_mode()
                        response = universal_dispatch(command)
                        print(f"[MCP] Response to {addr}: {json.dumps(response)[:120]}")
                        conn.sendall(json.dumps(response).encode('utf-8'))
                    except json.JSONDecodeError:
                        break  # Wait for more data
                    except Exception as e:
                        tb = traceback.format_exc()
                        error = {"status": "error", "message": str(e), "traceback": tb}
                        print(f"[MCP] Error handling command from {addr}: {e}\n{tb}")
                        try:
                            conn.sendall(json.dumps(error).encode('utf-8'))
                        except:
                            pass
                        break
        except Exception as e:
            tb = traceback.format_exc()
            print(f"[MCP] Client handler error ({addr}): {e}\n{tb}")
        finally:
            self.active_connections = max(0, self.active_connections - 1)
            print(f"[MCP] Client disconnected: {addr}")
            try:
                conn.close()
            except:
                pass

    def _json_partial_parse(self, s):
        """
        Attempts to parse a JSON object from the start of string s.
        Returns (obj, idx) where obj is the parsed object or None, and idx is the end index.
        """
        decoder = json.JSONDecoder()
        s = s.lstrip()
        if not s:
            return None, 0
        try:
            obj, idx = decoder.raw_decode(s)
            return obj, idx
        except json.JSONDecodeError:
            return None, 0

    def stop(self):
        global server_running
        self._stop_event.set()
        server_running = False

# --- Blender UI Panel ---
class MCP_PT_panel(bpy.types.Panel):
    bl_label = "MCP Socket Server"
    bl_idname = "MCP_PT_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'MCP'

    def draw(self, context):
        layout = self.layout
        if server_running:
            layout.label(text=f"Server running on port {DEFAULT_PORT}", icon='CHECKMARK')
            layout.operator("mcp.stop_server", text="Stop Server", icon='CANCEL')
        else:
            layout.label(text="Server stopped", icon='CANCEL')
            layout.operator("mcp.start_server", text="Start Server", icon='PLAY')

class MCP_OT_start_server(bpy.types.Operator):
    bl_idname = "mcp.start_server"
    bl_label = "Start MCP Server"

    def execute(self, context):
        global server_thread, server_running
        if not server_running:
            server_thread = MCPServerThread()
            server_thread.start()
        return {'FINISHED'}

class MCP_OT_stop_server(bpy.types.Operator):
    bl_idname = "mcp.stop_server"
    bl_label = "Stop MCP Server"

    def execute(self, context):
        global server_thread, server_running
        if server_running and server_thread:
            server_thread.stop()
            server_thread = None
        return {'FINISHED'}

# --- Registration ---
classes = [
    MCP_PT_panel,
    MCP_OT_start_server,
    MCP_OT_stop_server,
]

def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    print("[MCP] Addon registered.")

def unregister():
    global server_thread, server_running
    if server_running and server_thread:
        server_thread.stop()
        server_thread = None
    for cls in classes:
        bpy.utils.unregister_class(cls)
    print("[MCP] Addon unregistered.")

if __name__ == "__main__":
    register() 