bl_info = {
    "name": "Universal Blender MCP Socket Server",
    "author": "Your Name / AI Assistant",
    "version": (1, 0, 0),
    "blender": (3, 0, 0),
    "location": "",
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

PORT = 9876
LOG_PATH = os.path.join(os.path.dirname(__file__), "mcp_blender.log")
CHUNKED_UPLOADS = {}
JOB_QUEUE = []
JOB_STATUS = {}
MAX_CHUNK_SIZE = 2 * 1024 * 1024  # 2MB
ALLOWED_IPS = {"127.0.0.1", "localhost"}

# --- Logging ---
def log(msg):
    with open(LOG_PATH, "a") as f:
        f.write(f"{time.ctime()} {msg}\n")

# --- Command Implementations ---
def cmd_create_object(params):
    """Create a primitive object in Blender (cube, sphere, cylinder, cone, etc). Args: shape (str), name (str, optional), location (list, optional)"""
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
    """Delete an object by name. Args: name (str)"""
    name = params.get("name")
    obj = bpy.data.objects.get(name)
    if not obj:
        return {"status": "error", "message": f"Object {name} not found"}
    bpy.data.objects.remove(obj, do_unlink=True)
    log(f"Deleted object {name}")
    return {"status": "ok", "result": f"Object {name} deleted"}

def cmd_list_objects(params):
    """List all objects in the current scene. Args: none"""
    objs = [obj.name for obj in bpy.context.scene.objects]
    return {"status": "ok", "result": objs}

def cmd_assign_material(params):
    """Assign a material to an object. Args: object (str), material (str)"""
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

def cmd_get_scene_info(params):
    """Get basic scene info (objects, frame, etc). Args: none"""
    info = {
        "objects": [obj.name for obj in bpy.context.scene.objects],
        "frame": bpy.context.scene.frame_current,
        "frame_start": bpy.context.scene.frame_start,
        "frame_end": bpy.context.scene.frame_end,
    }
    return {"status": "ok", "result": info}

def cmd_create_material(params):
    """Create a new material. Args: name (str), base_color (list, optional), metallic (float, optional), roughness (float, optional)"""
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
    """Add a modifier to an object. Args: object (str), type (str), name (str, optional), settings (dict, optional)"""
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

def cmd_set_parent(params):
    """Set parent of an object. Args: child (str), parent (str)"""
    child = bpy.data.objects.get(params.get("child"))
    parent = bpy.data.objects.get(params.get("parent"))
    if not child or not parent:
        return {"status": "error", "message": "Child or parent not found"}
    child.parent = parent
    log(f"Set parent of {child.name} to {parent.name}")
    return {"status": "ok", "result": f"Parent of {child.name} set to {parent.name}"}

def cmd_create_camera(params):
    """Create a camera. Args: name (str, optional), location (list, optional), rotation (list, optional)"""
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
    """Create a light. Args: type (str), name (str, optional), location (list, optional), energy (float, optional)"""
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
    """Create a new collection. Args: name (str)"""
    name = params.get("name", "Collection")
    if name in bpy.data.collections:
        return {"status": "error", "message": f"Collection {name} already exists"}
    col = bpy.data.collections.new(name)
    bpy.context.scene.collection.children.link(col)
    log(f"Created collection {name}")
    return {"status": "ok", "result": f"Collection {name} created"}

def cmd_move_to_collection(params):
    """Move an object to a collection. Args: object (str), collection (str)"""
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
    """Transform an object. Args: name (str), location (list, optional), rotation (list, optional), scale (list, optional)"""
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
    """Save the current .blend file. Args: filepath (str)"""
    filepath = params.get("filepath", "untitled.blend")
    bpy.ops.wm.save_as_mainfile(filepath=filepath)
    log(f"Saved blend file to {filepath}")
    return {"status": "ok", "result": f"Saved to {filepath}"}

def cmd_load_blend(params):
    """Load a .blend file. Args: filepath (str)"""
    filepath = params.get("filepath")
    if not filepath:
        return {"status": "error", "message": "No filepath provided"}
    bpy.ops.wm.open_mainfile(filepath=filepath)
    log(f"Loaded blend file {filepath}")
    return {"status": "ok", "result": f"Loaded {filepath}"}

def cmd_describe(params):
    """Return full metadata for all commands (name, docstring, args) for LLM/AI introspection. Args: none"""
    meta = {}
    for k, v in COMMANDS.items():
        doc = v.__doc__ or ""
        # Try to extract argument info from docstring
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

# --- Command Metadata for LLM/AI Discoverability ---
COMMANDS = {
    "create_object": cmd_create_object,
    "delete_object": cmd_delete_object,
    "list_objects": cmd_list_objects,
    "assign_material": cmd_assign_material,
    "get_scene_info": cmd_get_scene_info,
    "create_material": cmd_create_material,
    "add_modifier": cmd_add_modifier,
    "set_parent": cmd_set_parent,
    "create_camera": cmd_create_camera,
    "create_light": cmd_create_light,
    "create_collection": cmd_create_collection,
    "move_to_collection": cmd_move_to_collection,
    "transform_object": cmd_transform_object,
    "save_blend": cmd_save_blend,
    "load_blend": cmd_load_blend,
    "describe": cmd_describe,
}

# --- Command Dispatcher ---
def handle_command(cmd):
    try:
        t = cmd.get("type")
        p = cmd.get("params", {})
        if t == "help":
            return {"status": "ok", "result": list(COMMANDS.keys())}
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
            # TODO: handle the uploaded data (e.g., save to file, import asset)
            return {"status": "ok", "size": len(data)}
        if t in COMMANDS:
            return COMMANDS[t](p)
        return {"status": "error", "message": f"Unknown command: {t}"}
    except Exception as e:
        return {"status": "error", "message": str(e), "traceback": traceback.format_exc()}

# --- Socket Server Thread ---
def server_thread():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("localhost", PORT))
    s.listen(5)
    log(f"Listening on port {PORT}")
    while True:
        conn, addr = s.accept()
        if ALLOWED_IPS and addr[0] not in ALLOWED_IPS:
            log(f"Connection from {addr[0]} denied")
            conn.close()
            continue
        data = b""
        while True:
            chunk = conn.recv(65536)
            if not chunk:
                break
            data += chunk
            if len(chunk) < 65536:
                break
        try:
            cmd = json.loads(data.decode())
            log(f"Received: {cmd}")
            resp = handle_command(cmd)
        except Exception as e:
            resp = {"status": "error", "message": str(e)}
        conn.sendall(json.dumps(resp).encode())
        conn.close()

# --- Blender Addon Registration ---
def register():
    threading.Thread(target=server_thread, daemon=True).start()
    log("MCP server started")

def unregister():
    log("MCP server stopped") 