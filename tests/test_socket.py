"""
Quick test: verify the Fusion listener is running and responding.

Run this AFTER starting the listener in Resolve:
    python tests/test_socket.py

Expected output:
    Connected to localhost:9876
    Sent: ping
    Response: pong
    Listener is working!
"""

import socket
import json
import sys

HOST = "localhost"
PORT = 9878


def send_command(sock, command_type, params=None):
    """Send a JSON command and receive the response."""
    command = {"type": command_type, "params": params or {}}
    sock.sendall(json.dumps(command).encode("utf-8"))

    # Read response
    chunks = []
    sock.settimeout(5.0)
    while True:
        chunk = sock.recv(8192)
        if not chunk:
            break
        chunks.append(chunk)
        data = b"".join(chunks)
        try:
            return json.loads(data.decode("utf-8"))
        except json.JSONDecodeError:
            continue

    return None


def main():
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect((HOST, PORT))
        print(f"Connected to {HOST}:{PORT}")
    except ConnectionRefusedError:
        print(f"ERROR: Cannot connect to {HOST}:{PORT}")
        print("Make sure:")
        print("  1. DaVinci Resolve is open")
        print("  2. You're on the Fusion page")
        print("  3. The listener is running (Workspace > Scripts > Comp > fusion_listener)")
        sys.exit(1)

    # Test 1: Ping
    print("Sent: ping")
    response = send_command(sock, "ping")
    if response and response.get("status") == "success":
        print(f"Response: {response['result']}")
        print("Listener is working!")
    else:
        print(f"Unexpected response: {response}")
        sys.exit(1)

    # Test 2: Get comp info
    print("\nSent: get_comp_info")
    response = send_command(sock, "get_comp_info")
    if response and response.get("status") == "success":
        result = response["result"]
        print(f"Comp: {result.get('comp_name', '?')}")
        print(f"Tools: {result.get('tool_count', 0)}")
        for tool in result.get("tools", []):
            print(f"  - {tool['name']} ({tool['type']})")
    else:
        print(f"Error: {response}")

    sock.close()
    print("\nAll tests passed!")


if __name__ == "__main__":
    main()
