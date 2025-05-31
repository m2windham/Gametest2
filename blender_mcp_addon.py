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
# Utility: Get mesh primitive operator from shape name
MESH_PRIMITIVE_OPS = {
    # All Blender mesh primitives as of 3.x
    "cube": bpy.ops.mesh.primitive_cube_add,
    "plane": bpy.ops.mesh.primitive_plane_add,
    "circle": bpy.ops.mesh.primitive_circle_add,
    "uv_sphere": bpy.ops.mesh.primitive_uv_sphere_add,
    "ico_sphere": bpy.ops.mesh.primitive_ico_sphere_add,
    "cylinder": bpy.ops.mesh.primitive_cylinder_add,
    "cone": bpy.ops.mesh.primitive_cone_add,
    "torus": bpy.ops.mesh.primitive_torus_add,
    "grid": bpy.ops.mesh.primitive_grid_add,
    "monkey": bpy.ops.mesh.primitive_monkey_add,
    # Aliases for user convenience
    "sphere": bpy.ops.mesh.primitive_uv_sphere_add,  # alias
    "icosphere": bpy.ops.mesh.primitive_ico_sphere_add,  # alias
}
# Keep this in sync with Blender's bpy.ops.mesh primitives!
MESH_PRIMITIVE_NAMES = set(MESH_PRIMITIVE_OPS.keys())

def get_mesh_primitive_op(shape):
    return MESH_PRIMITIVE_OPS.get(shape)

@tool(name="create_object", description="Create a new object in the scene. Supports all Blender primitive types.", args=[
    {"name": "name", "type": "str", "desc": "Object name"},
    {"name": "shape", "type": "str", "desc": "Primitive type (cube, sphere, cylinder, cone, torus, plane, circle, grid, monkey, bezier_curve, nurbs_curve, path, nurbs_surface, metaball, text, lattice, empty, light, camera, speaker, armature, grease_pencil, etc.)"},
    {"name": "kwargs", "type": "dict", "desc": "Additional operator arguments (optional)"}
])
def handle_create_object(params):
    """Create a new object in the scene. Uses data API for mesh primitives for headless/background safety."""
    name = params.get("name", "Object")
    shape = params.get("shape", None)
    kwargs = params.get("kwargs", {})
    obj = None
    if shape:
        shape = shape.lower()
        if shape in MESH_PRIMITIVE_NAMES:
            # Use operator for all mesh primitives (robust, future-proof)
            op = get_mesh_primitive_op(shape)
            if op:
                try:
                    op(**kwargs)
                    obj = bpy.context.active_object
                except Exception as e:
                    log(f"[MCP] Error creating mesh primitive {shape}: {e}")
                    return {"status": "error", "message": f"Error creating mesh primitive {shape}: {e}"}
            else:
                log(f"[MCP] Mesh primitive operator not found for: {shape}")
                return {"status": "error", "message": f"Mesh primitive operator not found for: {shape}"}
            if obj:
                # Set location/scale after creation for consistency
                if "location" in kwargs:
                    obj.location = kwargs["location"]
                if "scale" in kwargs:
                    obj.scale = kwargs["scale"]
                bpy.context.scene.collection.objects.link(obj)
                obj.name = name
        else:
            # Non-mesh primitives: use operator as before
            primitive_ops = {
                # Curve
                "bezier_curve": bpy.ops.curve.primitive_bezier_curve_add,
                "bezier_circle": bpy.ops.curve.primitive_bezier_circle_add,
                "nurbs_curve": bpy.ops.curve.primitive_nurbs_curve_add,
                "nurbs_circle": bpy.ops.curve.primitive_nurbs_circle_add,
                "path": bpy.ops.curve.primitive_nurbs_path_add,
                # Surface
                "nurbs_surface": bpy.ops.surface.primitive_nurbs_surface_surface_add,
                "nurbs_surface_circle": bpy.ops.surface.primitive_nurbs_surface_circle_add,
                "nurbs_surface_curve": bpy.ops.surface.primitive_nurbs_surface_curve_add,
                "nurbs_surface_cylinder": bpy.ops.surface.primitive_nurbs_surface_cylinder_add,
                "nurbs_surface_sphere": bpy.ops.surface.primitive_nurbs_surface_sphere_add,
                "nurbs_surface_torus": bpy.ops.surface.primitive_nurbs_surface_torus_add,
                # Metaball
                "metaball": bpy.ops.object.metaball_add,
                # Text
                "text": bpy.ops.object.text_add,
                # Lattice
                "lattice": bpy.ops.object.add,  # type='LATTICE'
                # Empty
                "empty": bpy.ops.object.empty_add,
                # Light
                "light": bpy.ops.object.light_add,
                # Camera
                "camera": bpy.ops.object.camera_add,
                # Speaker
                "speaker": bpy.ops.object.speaker_add,
                # Armature
                "armature": bpy.ops.object.armature_add,
                # Grease Pencil
                "grease_pencil": bpy.ops.object.gpencil_add,
            }
            op = primitive_ops.get(shape)
            try:
                if op:
                    if shape == "lattice":
                        op(type='LATTICE', **kwargs)
                    elif shape == "light":
                        op_type = kwargs.get('type', 'POINT')
                        op(location=kwargs.get('location', [0,0,0]), type=op_type)
                        obj = bpy.context.active_object
                        if obj and 'rotation_euler' in kwargs:
                            obj.rotation_euler = kwargs['rotation_euler']
                    elif shape == "camera":
                        op(location=kwargs.get('location', [0,0,0]))
                        obj = bpy.context.active_object
                        if obj and 'rotation_euler' in kwargs:
                            obj.rotation_euler = kwargs['rotation_euler']
                    elif shape == "empty":
                        op(type=kwargs.get('type', 'PLAIN_AXES'), **kwargs)
                        obj = bpy.context.active_object
                    else:
                        op(**kwargs)
                    obj = bpy.context.active_object
                    if obj:
                        obj.name = name
                else:
                    log(f"[MCP] Unsupported shape requested: {shape}")
                    return {"status": "error", "message": f"Unsupported shape: {shape}"}
            except Exception as e:
                log(f"[MCP] Error creating {shape}: {e}")
                return {"status": "error", "message": f"Error creating {shape}: {e}"}
    else:
        log("[MCP] No shape specified for create_object")
        mesh = bpy.data.meshes.new(name)
        obj = bpy.data.objects.new(name, mesh)
        bpy.context.scene.collection.objects.link(obj)
    if obj:
        return {"status": "ok", "result": f"Object {obj.name} created", "object_name": obj.name, "object_type": obj.type}
    else:
        return {"status": "error", "message": "Object creation failed"}

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
    log("[MCP] Clearing scene (robust method)...")
    try:
        # Remove all objects directly
        for obj in list(bpy.data.objects):
            try:
                obj_name = obj.name  # Store name before removal
                bpy.data.objects.remove(obj, do_unlink=True)
                log(f"[MCP] Removed object: {obj_name}")
            except Exception as e:
                log(f"[MCP] Error removing object: {e}")
        log("[MCP] All objects removed from bpy.data.objects.")
        # Purge orphans with delay
        for i in range(3):
            try:
                bpy.ops.outliner.orphans_purge(do_local_ids=True, do_linked_ids=True, do_recursive=True)
                log(f"[MCP] Orphans purged (pass {i+1})")
            except Exception as e:
                log(f"[MCP] Orphan purge error: {e}")
        return {"status": "ok", "result": "Scene cleared (robust)"}
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

@tool(name="sculpt_object", description="Apply sculpting operation to an object.", args=[{"name": "object", "type": "str", "desc": "Object name"}, {"name": "brush", "type": "str", "desc": "Brush type"}, {"name": "stroke", "type": "dict", "desc": "Stroke parameters"}])
def handle_sculpt_object(params):
    """Apply a sculpting operation to an object using a specified brush and stroke parameters."""
    obj_name = params.get("object")
    brush = params.get("brush", "DRAW")
    stroke = params.get("stroke", {})
    try:
        obj = bpy.data.objects.get(obj_name)
        if not obj:
            return {"status": "error", "message": f"Object {obj_name} not found"}
        bpy.context.view_layer.objects.active = obj
        bpy.ops.object.mode_set(mode='SCULPT')
        bpy.context.tool_settings.sculpt.brush = getattr(bpy.data.brushes, brush, bpy.data.brushes[0].name)
        bpy.ops.sculpt.brush_stroke(stroke=stroke)
        bpy.ops.object.mode_set(mode='OBJECT')
        return {"status": "ok", "result": f"Sculpted {obj_name} with {brush}"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@tool(name="edit_mesh", description="Apply mesh modeling operation.", args=[{"name": "object", "type": "str"}, {"name": "operation", "type": "str"}, {"name": "kwargs", "type": "dict"}])
def handle_edit_mesh(params):
    """Apply a mesh modeling operation (extrude, subdivide, bevel, etc.) to an object."""
    obj_name = params.get("object")
    operation = params.get("operation")
    kwargs = params.get("kwargs", {})
    try:
        obj = bpy.data.objects.get(obj_name)
        if not obj:
            return {"status": "error", "message": f"Object {obj_name} not found"}
        bpy.context.view_layer.objects.active = obj
        bpy.ops.object.mode_set(mode='EDIT')
        op = getattr(bpy.ops.mesh, operation, None)
        if not op:
            return {"status": "error", "message": f"Unsupported mesh operation: {operation}"}
        op(**kwargs)
        bpy.ops.object.mode_set(mode='OBJECT')
        return {"status": "ok", "result": f"Applied {operation} to {obj_name}"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@tool(name="add_modifier", description="Add a modifier to an object.", args=[{"name": "object", "type": "str"}, {"name": "modifier", "type": "str"}, {"name": "kwargs", "type": "dict"}])
def handle_add_modifier(params):
    """Add a modifier to an object."""
    obj_name = params.get("object")
    modifier = params.get("modifier")
    kwargs = params.get("kwargs", {})
    try:
        obj = bpy.data.objects.get(obj_name)
        if not obj:
            return {"status": "error", "message": f"Object {obj_name} not found"}
        mod = obj.modifiers.new(name=modifier, type=modifier.upper())
        for k, v in kwargs.items():
            setattr(mod, k, v)
        return {"status": "ok", "result": f"Added {modifier} modifier to {obj_name}"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@tool(name="apply_modifier", description="Apply a modifier on an object.", args=[{"name": "object", "type": "str"}, {"name": "modifier", "type": "str"}])
def handle_apply_modifier(params):
    """Apply a modifier on an object."""
    obj_name = params.get("object")
    modifier = params.get("modifier")
    try:
        obj = bpy.data.objects.get(obj_name)
        if not obj:
            return {"status": "error", "message": f"Object {obj_name} not found"}
        bpy.context.view_layer.objects.active = obj
        bpy.ops.object.modifier_apply(modifier=modifier)
        return {"status": "ok", "result": f"Applied {modifier} modifier on {obj_name}"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@tool(name="add_keyframe", description="Insert a keyframe for an object's property.", args=[{"name": "object", "type": "str"}, {"name": "data_path", "type": "str"}, {"name": "frame", "type": "int"}])
def handle_add_keyframe(params):
    """Insert a keyframe for an object's property at a given frame."""
    obj_name = params.get("object")
    data_path = params.get("data_path")
    frame = params.get("frame")
    try:
        obj = bpy.data.objects.get(obj_name)
        if not obj:
            return {"status": "error", "message": f"Object {obj_name} not found"}
        obj.keyframe_insert(data_path=data_path, frame=frame)
        return {"status": "ok", "result": f"Keyframe inserted for {obj_name} at frame {frame}"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@tool(name="set_animation", description="Set animation data for an object.", args=[{"name": "object", "type": "str"}, {"name": "action", "type": "str"}])
def handle_set_animation(params):
    """Set animation action for an object."""
    obj_name = params.get("object")
    action = params.get("action")
    try:
        obj = bpy.data.objects.get(obj_name)
        if not obj:
            return {"status": "error", "message": f"Object {obj_name} not found"}
        act = bpy.data.actions.get(action)
        if not act:
            return {"status": "error", "message": f"Action {action} not found"}
        obj.animation_data_create()
        obj.animation_data.action = act
        return {"status": "ok", "result": f"Set animation {action} for {obj_name}"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@tool(name="play_animation", description="Play the animation timeline.", args=[])
def handle_play_animation(params):
    """Play the animation timeline."""
    try:
        bpy.ops.screen.animation_play()
        return {"status": "ok", "result": "Animation playing"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@tool(name="set_uv", description="Set UV mapping for an object.", args=[{"name": "object", "type": "str"}])
def handle_set_uv(params):
    """Unwrap UVs for an object."""
    obj_name = params.get("object")
    try:
        obj = bpy.data.objects.get(obj_name)
        if not obj:
            return {"status": "error", "message": f"Object {obj_name} not found"}
        bpy.context.view_layer.objects.active = obj
        bpy.ops.object.mode_set(mode='EDIT')
        bpy.ops.uv.unwrap()
        bpy.ops.object.mode_set(mode='OBJECT')
        return {"status": "ok", "result": f"UV unwrapped for {obj_name}"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@tool(name="bake_texture", description="Bake texture for an object.", args=[{"name": "object", "type": "str"}, {"name": "bake_type", "type": "str"}])
def handle_bake_texture(params):
    """Bake a texture for an object."""
    obj_name = params.get("object")
    bake_type = params.get("bake_type", "COMBINED")
    try:
        obj = bpy.data.objects.get(obj_name)
        if not obj:
            return {"status": "error", "message": f"Object {obj_name} not found"}
        bpy.context.view_layer.objects.active = obj
        bpy.ops.object.bake(type=bake_type)
        return {"status": "ok", "result": f"Baked {bake_type} texture for {obj_name}"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@tool(name="add_constraint", description="Add a constraint to an object.", args=[{"name": "object", "type": "str"}, {"name": "constraint", "type": "str"}, {"name": "kwargs", "type": "dict"}])
def handle_add_constraint(params):
    """Add a constraint to an object."""
    obj_name = params.get("object")
    constraint = params.get("constraint")
    kwargs = params.get("kwargs", {})
    try:
        obj = bpy.data.objects.get(obj_name)
        if not obj:
            return {"status": "error", "message": f"Object {obj_name} not found"}
        con = obj.constraints.new(type=constraint.upper())
        for k, v in kwargs.items():
            setattr(con, k, v)
        return {"status": "ok", "result": f"Added {constraint} constraint to {obj_name}"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@tool(name="add_particle_system", description="Add a particle system to an object.", args=[{"name": "object", "type": "str"}, {"name": "settings", "type": "str"}])
def handle_add_particle_system(params):
    """Add a particle system to an object."""
    obj_name = params.get("object")
    settings = params.get("settings")
    try:
        obj = bpy.data.objects.get(obj_name)
        if not obj:
            return {"status": "error", "message": f"Object {obj_name} not found"}
        psys = obj.modifiers.new(name="ParticleSystem", type='PARTICLE_SYSTEM')
        psys.particle_system.settings = bpy.data.particles.get(settings)
        return {"status": "ok", "result": f"Added particle system to {obj_name}"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@tool(name="add_physics", description="Add a physics property to an object.", args=[{"name": "object", "type": "str"}, {"name": "physics_type", "type": "str"}])
def handle_add_physics(params):
    """Add a physics property (rigid body, soft body, cloth, etc.) to an object."""
    obj_name = params.get("object")
    physics_type = params.get("physics_type")
    try:
        obj = bpy.data.objects.get(obj_name)
        if not obj:
            return {"status": "error", "message": f"Object {obj_name} not found"}
        if physics_type == 'RIGID_BODY':
            bpy.ops.rigidbody.object_add()
        elif physics_type == 'SOFT_BODY':
            bpy.ops.object.modifier_add(type='SOFT_BODY')
        elif physics_type == 'CLOTH':
            bpy.ops.object.modifier_add(type='CLOTH')
        elif physics_type == 'FLUID':
            bpy.ops.object.modifier_add(type='FLUID')
        else:
            return {"status": "error", "message": f"Unsupported physics type: {physics_type}"}
        return {"status": "ok", "result": f"Added {physics_type} physics to {obj_name}"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

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
    "sculpt_object": handle_sculpt_object,
    "edit_mesh": handle_edit_mesh,
    "add_modifier": handle_add_modifier,
    "apply_modifier": handle_apply_modifier,
    "add_keyframe": handle_add_keyframe,
    "set_animation": handle_set_animation,
    "play_animation": handle_play_animation,
    "set_uv": handle_set_uv,
    "bake_texture": handle_bake_texture,
    "add_constraint": handle_add_constraint,
    "add_particle_system": handle_add_particle_system,
    "add_physics": handle_add_physics,
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