import socket
import json
import time

class BlenderMCPAgent:
    def __init__(self, host='localhost', port=9876, delay=0.75):
        self.host = host
        self.port = port
        self.delay = delay

    def send_command(self, command):
        """Send a single JSON command to the MCP server and return the response."""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.connect((self.host, self.port))
            sock.sendall(json.dumps(command).encode('utf-8'))
            resp = sock.recv(65536)
            return json.loads(resp.decode('utf-8'))

    def send_commands(self, commands):
        """Send a list of JSON commands to the MCP server, one by one, with delay."""
        results = []
        for cmd in commands:
            result = self.send_command(cmd)
            print('Sent:', cmd)
            print('Response:', result)
            results.append(result)
            time.sleep(self.delay)
        return results

# Example usage (uncomment to use directly):
# agent = BlenderMCPAgent()
# commands = [
#     {"type": "clear_scene", "params": {}},
#     {"type": "create_object", "params": {"shape": "cube", "name": "TestCube", "kwargs": {"location": [0,0,0]}}},
# ]
# agent.send_commands(commands) 