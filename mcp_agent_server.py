from flask import Flask, request, jsonify
from blender_mcp_agent import BlenderMCPAgent

app = Flask(__name__)
agent = BlenderMCPAgent()

@app.route("/mcp", methods=["POST"])
def mcp():
    commands = request.json
    if not isinstance(commands, list):
        return jsonify({"error": "Request body must be a JSON list of commands."}), 400
    results = agent.send_commands(commands)
    return jsonify(results)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001) 