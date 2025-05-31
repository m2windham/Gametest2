import sys
import socket
import json
import os
import time
import logging
from dataclasses import dataclass
from typing import Optional, Dict, Any, List, Union
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import (
    ErrorData,
    GetPromptResult,
    Prompt,
    PromptArgument,
    PromptMessage,
    TextContent,
    Tool,
    INVALID_PARAMS,
    INTERNAL_ERROR,
)
from pydantic import BaseModel, Field
from mcp.shared.exceptions import McpError

# --- Load blendertool.json ---
BLENDERTOOL_PATH = os.path.join(os.path.dirname(__file__), "blendertool.json")
with open(BLENDERTOOL_PATH, "r", encoding="utf-8") as f:
    BLENDER_TOOL_SPEC = json.load(f)

# Configure logging
LOG_DIR = os.path.dirname(os.path.abspath(__file__))
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(LOG_DIR, "blender_mcp_relay.log")),
        logging.StreamHandler(sys.stderr)
    ]
)
logger = logging.getLogger("BlenderMCPRelay")

@dataclass
class BlenderConnection:
    host: str = "127.0.0.1"
    port: int = 9877
    sock: Optional[socket.socket] = None
    timeout: float = 60.0
    max_reconnect_attempts: int = 3
    base_reconnect_delay: float = 1.0

    def connect(self) -> bool:
        """Establish a connection to the Blender addon."""
        if self.sock:
            try:
                self.sock.sendall(b'')  # Test if socket is still valid
                return True
            except:
                self.disconnect()

        for attempt in range(self.max_reconnect_attempts):
            try:
                self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self.sock.settimeout(self.timeout)
                self.sock.connect((self.host, self.port))
                logger.info(f"Connected to Blender at {self.host}:{self.port}")
                return True
            except Exception as e:
                delay = self.base_reconnect_delay * (2 ** attempt)
                logger.error(f"Failed to connect (attempt {attempt + 1}/{self.max_reconnect_attempts}): {str(e)}")
                if attempt < self.max_reconnect_attempts - 1:
                    logger.info(f"Retrying in {delay} seconds...")
                    time.sleep(delay)
                else:
                    logger.error("Max reconnect attempts reached")
                    self.disconnect()
                    return False
        return False

    def disconnect(self):
        """Close the connection to Blender."""
        if self.sock:
            try:
                self.sock.close()
            except Exception as e:
                logger.error(f"Error disconnecting from Blender: {str(e)}")
            finally:
                self.sock = None

    def receive_full_response(self) -> bytes:
        """Receive a complete JSON response from Blender."""
        chunks = []
        start_time = time.time()
        while True:
            try:
                chunk = self.sock.recv(8192)
                if not chunk:
                    if not chunks:
                        raise Exception("Connection closed before receiving data")
                    break
                chunks.append(chunk)
                try:
                    data = b''.join(chunks)
                    json.loads(data.decode('utf-8'))
                    logger.info(f"Received complete response ({len(data)} bytes)")
                    return data
                except json.JSONDecodeError:
                    continue
            except socket.timeout:
                elapsed = time.time() - start_time
                logger.warning(f"Socket timeout after {elapsed:.2f} seconds")
                if chunks:
                    data = b''.join(chunks)
                    try:
                        json.loads(data.decode('utf-8'))
                        logger.info(f"Recovered partial response ({len(data)} bytes)")
                        return data
                    except json.JSONDecodeError:
                        raise Exception("Incomplete JSON response received")
                raise Exception("No data received within timeout")
            except Exception as e:
                logger.error(f"Error during receive: {str(e)}")
                raise

    def send_command(self, command: Dict[str, Any]) -> Dict[str, Any]:
        """Send a command to Blender and return the response."""
        if not self.sock and not self.connect():
            raise ConnectionError("Not connected to Blender")
        try:
            logger.info(f"Sending command: {command}")
            self.sock.sendall(json.dumps(command).encode('utf-8'))
            response_data = self.receive_full_response()
            response = json.loads(response_data.decode('utf-8'))
            if response.get("status") == "error":
                logger.error(f"Blender error: {response.get('message')}")
                raise Exception(response.get("message", "Unknown error"))
            return response
        except Exception as e:
            logger.error(f"Error communicating with Blender: {str(e)}", exc_info=True)
            self.disconnect()
            raise Exception(f"Connection to Blender lost: {str(e)}")

# --- Main Server ---
async def serve() -> None:
    server = Server("blender-mcp")
    blender_connection = BlenderConnection()

    @server.list_tools()
    async def list_tools() -> List[Tool]:
        return [
            Tool(
                name="LIST_COMMAND",
                description="List all available Blender tools and their metadata as defined in blendertool.json.",
                inputSchema={"type": "object"},
            ),
            Tool(
                name="USE_COMMAND",
                description="Invoke a Blender tool by name with parameters. The tool_name must match an entry in blendertool.json.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "tool_name": {"type": "string", "description": "Name of the tool to invoke (see blendertool.json)."},
                        "params": {"type": "object", "description": "Parameters for the tool."}
                    },
                    "required": ["tool_name"]
                },
            ),
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> List[TextContent]:
        try:
            if name == "LIST_COMMAND":
                return [TextContent(type="text", text=json.dumps(BLENDER_TOOL_SPEC["commands"], indent=2))]
            elif name == "USE_COMMAND":
                tool_name = arguments.get("tool_name")
                params = arguments.get("params", {})
                # Only look up real tools in blendertool.json
                tool_def = next((cmd for cmd in BLENDER_TOOL_SPEC["commands"] if cmd["tool_name"] == tool_name), None)
                if not tool_def:
                    raise McpError(ErrorData(code=INVALID_PARAMS, message=f"Tool '{tool_name}' not found in blendertool.json."))
                response = blender_connection.send_command({
                    "type": tool_name,
                    "params": params
                })
                if response.get("status") == "error":
                    raise McpError(ErrorData(code=INTERNAL_ERROR, message=response.get("message", "Unknown error")))
                return [TextContent(type="text", text=json.dumps(response, indent=2))]
            else:
                raise McpError(ErrorData(code=INVALID_PARAMS, message=f"Unknown tool: {name}"))
        except McpError as e:
            raise e
        except Exception as e:
            raise McpError(ErrorData(
                code=INTERNAL_ERROR,
                message=f"Failed to execute Blender command '{name}': {str(e)}"
            ))

    options = server.create_initialization_options()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, options, raise_exceptions=True)

if __name__ == "__main__":
    import asyncio
    asyncio.run(serve()) 