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

COMMANDS = {
    # Add more commands here as you expand
}

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