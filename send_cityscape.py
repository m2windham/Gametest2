import socket
import json
import time
import random

HOST = 'localhost'
PORT = 9876  # Match your Blender MCP port

# Utility to send a command and wait for response
def send_command(sock, command):
    msg = json.dumps(command).encode('utf-8')
    sock.sendall(msg)
    resp = sock.recv(65536)
    try:
        return json.loads(resp.decode('utf-8'))
    except Exception as e:
        print('Error decoding response:', e)
        return None

def main():
    # Connect to MCP server
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect((HOST, PORT))
    print('Connected to MCP server.')

    # 1. Clear the scene
    print('Clearing scene...')
    resp = send_command(sock, {"type": "clear_scene", "params": {}})
    print('Response:', resp)
    time.sleep(0.5)

    # 2. Create ground plane
    print('Adding ground plane...')
    resp = send_command(sock, {
        "type": "create_object",
        "params": {
            "shape": "plane",
            "name": "Ground",
            "kwargs": {"size": 50, "location": [0, 0, 0]}
        }
    })
    print('Response:', resp)
    time.sleep(0.3)

    # 3. Create a grid of cubes (city buildings)
    grid_size = 10
    spacing = 4
    min_height = 2
    max_height = 12
    for x in range(-grid_size//2, grid_size//2):
        for y in range(-grid_size//2, grid_size//2):
            height = random.uniform(min_height, max_height)
            name = f"Building_{x}_{y}"
            resp = send_command(sock, {
                "type": "create_object",
                "params": {
                    "shape": "cube",
                    "name": name,
                    "kwargs": {
                        "size": 1,
                        "location": [x * spacing, y * spacing, height / 2],
                        "scale": [1, 1, height]
                    }
                }
            })
            print(f'Created {name}:', resp)
            time.sleep(0.15)

    # 4. Add a sun light
    print('Adding sun light...')
    resp = send_command(sock, {
        "type": "create_object",
        "params": {
            "shape": "light",
            "name": "Sun",
            "kwargs": {"type": "SUN", "location": [0, 0, 40]}
        }
    })
    print('Response:', resp)
    time.sleep(0.3)

    # 5. Add a camera
    print('Adding camera...')
    resp = send_command(sock, {
        "type": "create_object",
        "params": {
            "shape": "camera",
            "name": "CityCamera",
            "kwargs": {"location": [0, -grid_size * spacing, max_height * 2], "rotation_euler": [1.2, 0, 0]}
        }
    })
    print('Response:', resp)
    time.sleep(0.3)

    print('Cityscape generation complete!')
    sock.close()

if __name__ == "__main__":
    main() 