import socket
import json
import logging
from mcp.server.fastmcp import FastMCP, Context
from typing import Dict, Any

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("APIRS")

BLENDER_HOST = "localhost"
BLENDER_PORT = 9876  # Match the port in your Blender addon

class BlenderConnection:
    def __init__(self, host=BLENDER_HOST, port=BLENDER_PORT):
        self.host = host
        self.port = port
        self.sock = None

    def connect(self):
        if self.sock:
            return True
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.connect((self.host, self.port))
            logger.info(f"Connected to Blender at {self.host}:{self.port}")
            return True
        except Exception as e:
            logger.error(f"Failed to connect to Blender: {e}")
            self.sock = None
            return False

    def disconnect(self):
        if self.sock:
            try:
                self.sock.close()
            except Exception as e:
                logger.error(f"Error disconnecting: {e}")
            self.sock = None

    def send_command(self, command_type: str, params: Dict[str, Any] = None) -> Dict[str, Any]:
        if not self.sock and not self.connect():
            raise ConnectionError("Not connected to Blender")
        command = {"type": command_type, "params": params or {}}
        try:
            self.sock.sendall(json.dumps(command).encode("utf-8"))
            resp = self.sock.recv(65536)
            return json.loads(resp.decode("utf-8"))
        except Exception as e:
            logger.error(f"Error sending command: {e}")
            self.disconnect()
            raise

blender = BlenderConnection()

mcp = FastMCP(
    "APIRS",
    description="Advanced Protocol/Resource System server bridging to Blender via MCP."
)

@mcp.tool()
def create_object(ctx: Context, shape: str, name: str = None) -> str:
    """Create a primitive object in Blender (cube, sphere, cylinder, cone, etc)."""
    result = blender.send_command("create_object", {"shape": shape, "name": name})
    return json.dumps(result)

@mcp.tool()
def set_material(ctx: Context, object: str, material: str) -> str:
    """Assign a material to an object."""
    result = blender.send_command("set_material", {"object": object, "material": material})
    return json.dumps(result)

@mcp.tool()
def add_light(ctx: Context, light_type: str = "POINT", name: str = "Light") -> str:
    """Add a light to the scene."""
    result = blender.send_command("add_light", {"light_type": light_type, "name": name})
    return json.dumps(result)

@mcp.tool()
def set_hdri(ctx: Context, filepath: str) -> str:
    """Set an HDRI environment texture."""
    result = blender.send_command("set_hdri", {"filepath": filepath})
    return json.dumps(result)

@mcp.tool()
def run_code(ctx: Context, code: str) -> str:
    """Run arbitrary Python code in Blender (use with caution)."""
    result = blender.send_command("run_code", {"code": code})
    return json.dumps(result)

@mcp.tool()
def get_scene_info(ctx: Context) -> str:
    """Get information about the current Blender scene."""
    result = blender.send_command("get_scene_info")
    return json.dumps(result)

@mcp.tool()
def get_object_info(ctx: Context, name: str) -> str:
    """Get information about a specific object."""
    result = blender.send_command("get_object_info", {"name": name})
    return json.dumps(result)

# Add more tools for full API coverage as needed...

@mcp.tool()
def apirs_custom(ctx: Context, command_type: str, params: Dict[str, Any] = None) -> str:
    """Send a custom APIRS command to Blender."""
    result = blender.send_command(command_type, params)
    return json.dumps(result)

if __name__ == "__main__":
    mcp.run() 