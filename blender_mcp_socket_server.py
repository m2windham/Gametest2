bl_info = {
    "name": "Universal Blender MCP Socket Server",
    "author": "Your Name / AI Assistant",
    "version": (1, 0, 0),
    "blender": (3, 0, 0),
    "location": "View3D > Sidebar > MCP Server",
    "description": "Universal socket server for LLM/AI-driven Blender control (full API coverage, async, logging, code exec, chunked upload)",
    "category": "System",
}

import bpy
import socket
import threading
import json
import traceback
import os
import time
import bmesh
import select

PORT = 9877
LOG_PATH = os.path.join(os.path.dirname(__file__), "mcp_blender.log")
CHUNKED_UPLOADS = {}
JOB_QUEUE = []
JOB_STATUS = {}
MAX_CHUNK_SIZE = 2 * 1024 * 1024  # 2MB
ALLOWED_IPS = {"127.0.0.1", "localhost"}

# --- Load blendertool.json ---
BLENDERTOOL_PATH = os.path.join(os.path.dirname(__file__), "blendertool.json")
with open(BLENDERTOOL_PATH, "r", encoding="utf-8") as f:
    BLENDER_TOOL_SPEC = json.load(f)

# --- Logging ---
def log(msg, level="INFO"):
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    log_msg = f"{timestamp} [{level}] {msg}\n"
    with open(LOG_PATH, "a") as f:
        f.write(log_msg)
    print(log_msg, end="")  # Output to Blender terminal

# --- Tool Dispatchers ---
def handle_list_command(params):
    return {"status": "ok", "result": BLENDER_TOOL_SPEC["commands"]}

def handle_use_command(params):
    tool_name = params.get("tool_name")
    tool_params = params.get("params", {})
    tool_def = next((cmd for cmd in BLENDER_TOOL_SPEC["commands"] if cmd["tool_name"] == tool_name), None)
    if not tool_def:
        return {"status": "error", "message": f"Tool '{tool_name}' not found in blendertool.json."}
    if tool_name in COMMANDS:
        return COMMANDS[tool_name](tool_params)
    return {"status": "error", "message": f"Tool '{tool_name}' is defined in blendertool.json but not implemented in the addon."}

# --- Command Implementations (keep only those needed for USE_COMMAND) ---
def cmd_create_object(params):
    shape = params.get("shape", "cube")
    name = params.get("name", shape)
    location = params.get("location", [0, 0, 0])
    mesh = None
    obj = None
    if shape == "cube":
        mesh = bpy.data.meshes.new(name + "_mesh")
        bm = bmesh.new()
        bmesh.ops.create_cube(bm, size=2.0)
        bm.to_mesh(mesh)
        bm.free()
    elif shape == "uv_sphere":
        mesh = bpy.data.meshes.new(name + "_mesh")
        bm = bmesh.new()
        bmesh.ops.create_uvsphere(bm, u_segments=32, v_segments=16, radius=1.0)
        bm.to_mesh(mesh)
        bm.free()
    elif shape == "cylinder":
        mesh = bpy.data.meshes.new(name + "_mesh")
        bm = bmesh.new()
        bmesh.ops.create_cone(bm, segments=32, radius1=1.0, radius2=1.0, depth=2.0)
        bm.to_mesh(mesh)
        bm.free()
    elif shape == "cone":
        mesh = bpy.data.meshes.new(name + "_mesh")
        bm = bmesh.new()
        bmesh.ops.create_cone(bm, segments=32, radius1=1.0, radius2=0.0, depth=2.0)
        bm.to_mesh(mesh)
        bm.free()
    else:
        return {"status": "error", "message": f"Unsupported shape: {shape}"}
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    obj.location = location
    log(f"Created object {name} of type {shape}")
    return {"status": "ok", "result": f"Object {name} created", "object_name": name}

def cmd_delete_object(params):
    name = params.get("name")
    obj = bpy.data.objects.get(name)
    if not obj:
        return {"status": "error", "message": f"Object {name} not found"}
    bpy.data.objects.remove(obj, do_unlink=True)
    log(f"Deleted object {name}")
    return {"status": "ok", "result": f"Object {name} deleted"}

def cmd_list_objects(params):
    objs = [obj.name for obj in bpy.context.scene.objects]
    return {"status": "ok", "result": objs}

def cmd_assign_material(params):
    obj_name = params.get("object")
    mat_name = params.get("material")
    obj = bpy.data.objects.get(obj_name)
    mat = bpy.data.materials.get(mat_name)
    if not obj or not mat:
        return {"status": "error", "message": "Object or material not found"}
    if obj.type != 'MESH':
        return {"status": "error", "message": "Object is not a mesh"}
    if obj.data.materials:
        obj.data.materials[0] = mat
    else:
        obj.data.materials.append(mat)
    log(f"Assigned material {mat_name} to {obj_name}")
    return {"status": "ok", "result": f"Material {mat_name} assigned to {obj_name}"}

def cmd_create_material(params):
    name = params.get("name", "Material")
    base_color = params.get("base_color", [0.8, 0.8, 0.8, 1])
    metallic = params.get("metallic", 0.0)
    roughness = params.get("roughness", 0.5)
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get('Principled BSDF')
    if bsdf:
        bsdf.inputs['Base Color'].default_value = base_color
        bsdf.inputs['Metallic'].default_value = metallic
        bsdf.inputs['Roughness'].default_value = roughness
    log(f"Created material {name}")
    return {"status": "ok", "result": f"Material {name} created", "material_name": name}

def cmd_add_modifier(params):
    obj_name = params.get("object")
    mod_type = params.get("type")
    mod_name = params.get("name", mod_type)
    settings = params.get("settings", {})
    obj = bpy.data.objects.get(obj_name)
    if not obj:
        return {"status": "error", "message": f"Object {obj_name} not found"}
    mod = obj.modifiers.new(mod_name, mod_type)
    for k, v in settings.items():
        setattr(mod, k, v)
    log(f"Added modifier {mod_type} to {obj_name}")
    return {"status": "ok", "result": f"Modifier {mod_type} added to {obj_name}"}

def cmd_create_camera(params):
    name = params.get("name", "Camera")
    location = params.get("location", [0, -5, 2])
    rotation = params.get("rotation", [1.5708, 0, 0])
    cam_data = bpy.data.cameras.new(name)
    cam_obj = bpy.data.objects.new(name, cam_data)
    bpy.context.scene.collection.objects.link(cam_obj)
    cam_obj.location = location
    cam_obj.rotation_euler = rotation
    log(f"Created camera {name}")
    return {"status": "ok", "result": f"Camera {name} created", "object_name": name}

def cmd_create_light(params):
    light_type = params.get("type", "POINT")
    name = params.get("name", light_type)
    location = params.get("location", [0, 0, 5])
    energy = params.get("energy", 1000.0)
    light_data = bpy.data.lights.new(name, type=light_type)
    light_data.energy = energy
    light_obj = bpy.data.objects.new(name, light_data)
    bpy.context.scene.collection.objects.link(light_obj)
    light_obj.location = location
    log(f"Created light {name} of type {light_type}")
    return {"status": "ok", "result": f"Light {name} created", "object_name": name}

def cmd_create_collection(params):
    name = params.get("name", "Collection")
    if name in bpy.data.collections:
        return {"status": "error", "message": f"Collection {name} already exists"}
    col = bpy.data.collections.new(name)
    bpy.context.scene.collection.children.link(col)
    log(f"Created collection {name}")
    return {"status": "ok", "result": f"Collection {name} created"}

def cmd_move_to_collection(params):
    obj = bpy.data.objects.get(params.get("object"))
    col = bpy.data.collections.get(params.get("collection"))
    if not obj or not col:
        return {"status": "error", "message": "Object or collection not found"}
    for c in obj.users_collection:
        c.objects.unlink(obj)
    col.objects.link(obj)
    log(f"Moved {obj.name} to collection {col.name}")
    return {"status": "ok", "result": f"Moved {obj.name} to {col.name}"}

def cmd_transform_object(params):
    obj = bpy.data.objects.get(params.get("name"))
    if not obj:
        return {"status": "error", "message": "Object not found"}
    if "location" in params:
        obj.location = params["location"]
    if "rotation" in params:
        obj.rotation_euler = params["rotation"]
    if "scale" in params:
        obj.scale = params["scale"]
    log(f"Transformed object {obj.name}")
    return {"status": "ok", "result": f"Transformed {obj.name}"}

def cmd_save_blend(params):
    filepath = params.get("filepath", "untitled.blend")
    bpy.ops.wm.save_as_mainfile(filepath=filepath)
    log(f"Saved blend file to {filepath}")
    return {"status": "ok", "result": f"Saved to {filepath}"}

def cmd_load_blend(params):
    filepath = params.get("filepath")
    if not filepath:
        return {"status": "error", "message": "No filepath provided"}
    bpy.ops.wm.open_mainfile(filepath=filepath)
    log(f"Loaded blend file {filepath}")
    return {"status": "ok", "result": f"Loaded {filepath}"}

def cmd_describe(params):
    meta = {}
    for k, v in COMMANDS.items():
        doc = v.__doc__ or ""
        args = []
        if "Args:" in doc:
            args_part = doc.split("Args:", 1)[1]
            for line in args_part.split("\n"):
                line = line.strip()
                if not line:
                    continue
                if line.startswith("- "):
                    line = line[2:]
                if "(" in line and ")" in line:
                    arg = line.split("(")[0].strip()
                    args.append(arg)
        meta[k] = {"doc": doc, "args": args}
    return {"status": "ok", "result": meta}

def cmd_test_connection(params):
    return {"status": "ok", "result": "Server is running and responding"}

def cmd_create_bmesh_cube(params):
    import bmesh
    size = params.get('size', 2.0)
    location = params.get('location', [0, 0, 0])
    mesh = bpy.data.meshes.new("BMeshCube")
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=size)
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new("BMeshCube", mesh)
    bpy.context.scene.collection.objects.link(obj)
    obj.location = location
    log(f"Created BMesh cube at {location} with size {size}")
    return {"status": "ok", "result": f"BMesh cube created", "object_name": obj.name}

def cmd_draw_text(params):
    text = params.get('text', 'Hello Blender')
    location = params.get('location', [0, 0, 0])
    log(f"Requested to draw text '{text}' at {location}")
    return {"status": "ok", "result": f"Text '{text}' draw request received (not implemented)"}

# --- Command Map ---
COMMANDS = {
    "create_object": cmd_create_object,
    "delete_object": cmd_delete_object,
    "list_objects": cmd_list_objects,
    "assign_material": cmd_assign_material,
    "create_material": cmd_create_material,
    "add_modifier": cmd_add_modifier,
    "create_camera": cmd_create_camera,
    "create_light": cmd_create_light,
    "create_collection": cmd_create_collection,
    "move_to_collection": cmd_move_to_collection,
    "transform_object": cmd_transform_object,
    "save_blend": cmd_save_blend,
    "load_blend": cmd_load_blend,
    "describe": cmd_describe,
    "test_connection": cmd_test_connection,
    "create_bmesh_cube": cmd_create_bmesh_cube,
    "draw_text": cmd_draw_text,
}

# --- Command Dispatcher ---
def handle_command(cmd):
    try:
        t = cmd.get("type")
        p = cmd.get("params", {})
        if t == "LIST_COMMAND":
            return handle_list_command(p)
        if t == "USE_COMMAND":
            return handle_use_command(p)
        if t == "exec_python":
            code = p.get("code", "")
            try:
                exec(code, {"bpy": bpy})
                return {"status": "ok", "result": "Code executed"}
            except Exception as e:
                return {"status": "error", "message": str(e), "traceback": traceback.format_exc()}
        if t == "chunked_upload_init":
            upload_id = str(time.time())
            CHUNKED_UPLOADS[upload_id] = {"chunks": [], "start": time.time()}
            return {"status": "ok", "upload_id": upload_id}
        if t == "chunked_upload_chunk":
            upload_id = p.get("upload_id")
            chunk = p.get("chunk")
            if upload_id not in CHUNKED_UPLOADS:
                return {"status": "error", "message": "Invalid upload_id"}
            CHUNKED_UPLOADS[upload_id]["chunks"].append(chunk)
            return {"status": "ok", "received": len(chunk)}
        if t == "chunked_upload_finalize":
            upload_id = p.get("upload_id")
            if upload_id not in CHUNKED_UPLOADS:
                return {"status": "error", "message": "Invalid upload_id"}
            data = b"".join([bytes.fromhex(c) for c in CHUNKED_UPLOADS[upload_id]["chunks"]])
            del CHUNKED_UPLOADS[upload_id]
            return {"status": "ok", "size": len(data)}
        return {"status": "error", "message": f"Unknown command: {t}"}
    except Exception as e:
        return {"status": "error", "message": str(e), "traceback": traceback.format_exc()}

# --- Socket Server ---
class MCPSocketServer:
    def __init__(self):
        self.server = None
        self.thread = None
        self.running = False

    def start(self):
        if self.running:
            log("Server already running.", "WARNING")
            return
        self.running = True
        self.thread = threading.Thread(target=self._run_server)
        self.thread.daemon = True
        self.thread.start()
        log("MCP Socket Server started on 0.0.0.0:9877", "INFO")

    def stop(self):
        if not self.running:
            log("Server not running.", "WARNING")
            return
        self.running = False
        if self.server:
            self.server.close()
        if self.thread:
            self.thread.join()
        log("MCP Socket Server stopped.", "INFO")

    def _run_server(self):
        try:
            log("Creating server socket...", "INFO")
            self.server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            log("Setting socket options...", "INFO")
            self.server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            log(f"Attempting to bind to 0.0.0.0:{PORT}", "INFO")
            try:
                self.server.bind(("0.0.0.0", PORT))
                log("Successfully bound to port", "INFO")
            except Exception as e:
                log(f"Failed to bind: {str(e)}", "ERROR")
                raise
            log("Starting to listen...", "INFO")
            self.server.listen(5)
            log(f"Server successfully listening on 0.0.0.0:{PORT}", "INFO")
            log("Setting socket to non-blocking mode...", "INFO")
            self.server.setblocking(False)
            log("Server is ready to accept connections", "INFO")
            while self.running:
                try:
                    readable, _, _ = select.select([self.server], [], [], 0.1)
                    if self.server in readable:
                        conn, addr = self.server.accept()
                        conn.setblocking(True)
                        log(f"New connection from {addr[0]}:{addr[1]}", "INFO")
                        if ALLOWED_IPS and addr[0] not in ALLOWED_IPS:
                            log(f"Connection from {addr[0]} denied - not in allowed IPs", "WARNING")
                            conn.close()
                            continue
                        client_thread = threading.Thread(
                            target=self._handle_client,
                            args=(conn, addr)
                        )
                        client_thread.daemon = True
                        client_thread.start()
                        log(f"Started client thread for {addr[0]}:{addr[1]}", "INFO")
                except Exception as e:
                    if self.running:
                        log(f"Error in main server loop: {str(e)}", "ERROR")
                        log(f"Error details: {traceback.format_exc()}", "ERROR")
        except Exception as e:
            if self.running:
                log(f"Error starting server: {str(e)}", "ERROR")
                log(f"Error details: {traceback.format_exc()}", "ERROR")
    def _handle_client(self, conn, addr):
        try:
            buffer = b""
            while True:
                chunk = conn.recv(65536)
                if not chunk:
                    break
                buffer += chunk
                while True:
                    try:
                        decoded = buffer.decode(errors='ignore')
                        obj, idx = json.JSONDecoder().raw_decode(decoded)
                        buffer = decoded[idx:].lstrip().encode()
                    except Exception:
                        break
                    try:
                        cmd = obj
                        log(f"Received from {addr[0]}:{addr[1]}: {cmd}", "INFO")
                        resp = handle_command(cmd)
                        log(f"Sending to {addr[0]}:{addr[1]}: {resp}", "INFO")
                        conn.sendall(json.dumps(resp).encode())
                    except Exception as e:
                        error_resp = {
                            "error": {
                                "code": -32000,
                                "message": str(e)
                            }
                        }
                        log(f"Error handling command from {addr[0]}:{addr[1]}: {str(e)}", "ERROR")
                        conn.sendall(json.dumps(error_resp).encode())
        finally:
            conn.close()
            log(f"Connection closed for {addr[0]}:{addr[1]}", "INFO")

# --- UI Panel ---
class MCP_PT_Panel(bpy.types.Panel):
    bl_label = "MCP Server"
    bl_idname = "MCP_PT_Panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "MCP Server"

    def draw(self, context):
        layout = self.layout
        if not hasattr(bpy.types.Scene, "mcp_server"):
            bpy.types.Scene.mcp_server = MCPSocketServer()
        server = bpy.types.Scene.mcp_server
        if server.running:
            layout.operator("mcp.stop_server", text="Stop Server")
        else:
            layout.operator("mcp.start_server", text="Start Server")

class MCP_OT_StartServer(bpy.types.Operator):
    bl_idname = "mcp.start_server"
    bl_label = "Start MCP Server"
    def execute(self, context):
        bpy.types.Scene.mcp_server.start()
        return {"FINISHED"}

class MCP_OT_StopServer(bpy.types.Operator):
    bl_idname = "mcp.stop_server"
    bl_label = "Stop MCP Server"
    def execute(self, context):
        bpy.types.Scene.mcp_server.stop()
        return {"FINISHED"}

classes = (MCP_PT_Panel, MCP_OT_StartServer, MCP_OT_StopServer)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    if not hasattr(bpy.types.Scene, "mcp_server"):
        bpy.types.Scene.mcp_server = MCPSocketServer()
    log("MCP server addon registered")

def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    if hasattr(bpy.types.Scene, "mcp_server"):
        bpy.types.Scene.mcp_server.stop()
    log("MCP server addon unregistered")

if __name__ == "__main__":
    register() 