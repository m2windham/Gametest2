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

def check_response(resp, context):
    if not resp or resp.get('status') != 'ok':
        print(f'Error during {context}:', resp)
        print('Stopping script due to error.')
        exit(1)

def main():
    # Connect to MCP server
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect((HOST, PORT))
    print('Connected to MCP server.')

    # 1. Clear the scene
    print('Clearing scene...')
    resp = send_command(sock, {"type": "clear_scene", "params": {}})
    check_response(resp, 'clear_scene')
    print('Response:', resp)
    time.sleep(1)

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
    check_response(resp, 'ground plane')
    print('Response:', resp)
    time.sleep(1)

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
            check_response(resp, name)
            print(f'Created {name}:', resp)
            time.sleep(1)

    # 3b. Add roads (horizontal and vertical planes)
    print('Adding roads...')
    road_width = 1.2
    road_length = grid_size * spacing
    for i in range(-grid_size//2, grid_size//2+1):
        # Horizontal roads (X axis)
        resp = send_command(sock, {
            "type": "create_object",
            "params": {
                "shape": "plane",
                "name": f"Road_H_{i}",
                "kwargs": {
                    "size": 1,
                    "location": [0, i * spacing, 0.01],
                    "scale": [road_length/2, road_width, 1]
                }
            }
        })
        check_response(resp, f'Road_H_{i}')
        print(f'Created Road_H_{i}:', resp)
        time.sleep(1)
        # Vertical roads (Y axis)
        resp = send_command(sock, {
            "type": "create_object",
            "params": {
                "shape": "plane",
                "name": f"Road_V_{i}",
                "kwargs": {
                    "size": 1,
                    "location": [i * spacing, 0, 0.01],
                    "scale": [road_width, road_length/2, 1]
                }
            }
        })
        check_response(resp, f'Road_V_{i}')
        print(f'Created Road_V_{i}:', resp)
        time.sleep(1)

    # 3c. Add cars (small cubes or cylinders on roads)
    print('Adding cars...')
    num_cars = 20
    for i in range(num_cars):
        road_x = random.randint(-grid_size//2, grid_size//2-1)
        road_y = random.randint(-grid_size//2, grid_size//2-1)
        is_horizontal = random.choice([True, False])
        if is_horizontal:
            x = random.uniform(-road_length/2, road_length/2)
            y = road_y * spacing + random.uniform(-road_width, road_width)
        else:
            x = road_x * spacing + random.uniform(-road_width, road_width)
            y = random.uniform(-road_length/2, road_length/2)
        z = 0.25
        car_name = f"Car_{i}"
        resp = send_command(sock, {
            "type": "create_object",
            "params": {
                "shape": "cube",
                "name": car_name,
                "kwargs": {
                    "size": 1,
                    "location": [x, y, z],
                    "scale": [0.7, 1.2, 0.5]
                }
            }
        })
        check_response(resp, car_name)
        print(f'Created {car_name}:', resp)
        time.sleep(1)

    # 3d. Add props (trees, cones, etc.)
    print('Adding props...')
    num_trees = 15
    for i in range(num_trees):
        x = random.uniform(-road_length/2, road_length/2)
        y = random.uniform(-road_length/2, road_length/2)
        z = 0.5
        tree_name = f"Tree_{i}"
        resp = send_command(sock, {
            "type": "create_object",
            "params": {
                "shape": "uv_sphere",
                "name": tree_name,
                "kwargs": {
                    "location": [x, y, z+1.2],
                    "scale": [0.7, 0.7, 1.2]
                }
            }
        })
        check_response(resp, tree_name)
        print(f'Created {tree_name}:', resp)
        time.sleep(1)
        # Add trunk (cylinder)
        trunk_name = f"Trunk_{i}"
        resp = send_command(sock, {
            "type": "create_object",
            "params": {
                "shape": "cylinder",
                "name": trunk_name,
                "kwargs": {
                    "location": [x, y, z],
                    "scale": [0.2, 0.2, 1.2]
                }
            }
        })
        check_response(resp, trunk_name)
        print(f'Created {trunk_name}:', resp)
        time.sleep(1)
    num_cones = 10
    for i in range(num_cones):
        x = random.uniform(-road_length/2, road_length/2)
        y = random.uniform(-road_length/2, road_length/2)
        z = 0.2
        cone_name = f"Cone_{i}"
        resp = send_command(sock, {
            "type": "create_object",
            "params": {
                "shape": "cone",
                "name": cone_name,
                "kwargs": {
                    "location": [x, y, z],
                    "scale": [0.3, 0.3, 0.5]
                }
            }
        })
        check_response(resp, cone_name)
        print(f'Created {cone_name}:', resp)
        time.sleep(1)

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