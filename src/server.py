"""
DaVinci Resolve Fusion MCP Server

Bridge between Claude (via MCP/stdio) and the Fusion listener (via TCP socket).
Each MCP tool sends a JSON command to the listener running inside Resolve,
receives the response, and returns it to Claude.

Architecture:
    Claude Code --stdio--> This server --TCP socket--> fusion_listener.py (inside Resolve)

Run with: python src/server.py
"""

import json
import socket
import os
from dataclasses import dataclass, field
from fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

FUSION_HOST = os.getenv("FUSION_HOST", "127.0.0.1")
FUSION_PORT = int(os.getenv("FUSION_PORT", "9878"))
FUSION_AUTH_TOKEN = os.environ.get("FUSION_AUTH_TOKEN", "")
SOCKET_TIMEOUT = 30.0  # seconds


# ---------------------------------------------------------------------------
# Socket connection to Fusion listener
# ---------------------------------------------------------------------------

@dataclass
class FusionConnection:
    """Manages the TCP socket connection to the Fusion listener.

    Singleton pattern: one connection shared across all tool calls.
    Auto-reconnects if the connection drops.
    """
    host: str = "127.0.0.1"
    port: int = 9878
    sock: socket.socket = field(default=None, repr=False)

    def connect(self) -> bool:
        """Establish TCP connection to the Fusion listener.

        Returns:
            True if connection successful.

        Raises:
            ConnectionError if the listener is not running.
        """
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.connect((self.host, self.port))
            return True
        except ConnectionRefusedError:
            raise ConnectionError(
                f"Cannot connect to Fusion listener at {self.host}:{self.port}. "
                "Make sure DaVinci Resolve is open, you're on the Fusion page, "
                "and the listener script is running "
                "(Workspace > Scripts > Comp > fusion_listener)."
            )

    def disconnect(self):
        """Close the socket connection."""
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass
            self.sock = None

    def send_command(self, command_type: str, params: dict = None) -> dict:
        """Send a command to the Fusion listener and return the result.

        Args:
            command_type: The command name (e.g., "ping", "add_tool")
            params: Optional dict of parameters for the command

        Returns:
            The "result" field from the response dict.

        Raises:
            ConnectionError: If the listener is not reachable.
            RuntimeError: If the command returned an error.
        """
        command = {
            "type": command_type,
            "params": params or {},
        }
        if FUSION_AUTH_TOKEN:
            command["token"] = FUSION_AUTH_TOKEN

        # Send the command as JSON
        message = json.dumps(command).encode("utf-8")
        self.sock.sendall(message)

        # Receive the response (chunked, same pattern as Blender MCP)
        self.sock.settimeout(SOCKET_TIMEOUT)
        response_data = self._receive_full_response()
        response = json.loads(response_data.decode("utf-8"))

        # Check for errors from the listener
        if response.get("status") == "error":
            error_msg = response.get("message", "Unknown error from Fusion")
            raise RuntimeError(error_msg)

        return response.get("result", {})

    def _receive_full_response(self) -> bytes:
        """Read chunks from the socket until we have a complete JSON message.

        Same pattern as Blender MCP: keep reading and trying to parse JSON.
        When json.loads() succeeds, the message is complete.
        """
        chunks = []
        while True:
            chunk = self.sock.recv(8192)
            if not chunk:
                raise ConnectionError("Fusion listener disconnected")
            chunks.append(chunk)

            # Try to parse -- if it works, we have a complete message
            data = b"".join(chunks)
            try:
                json.loads(data.decode("utf-8"))
                return data
            except json.JSONDecodeError:
                continue  # incomplete, keep reading


# ---------------------------------------------------------------------------
# Singleton connection with health check
# ---------------------------------------------------------------------------

_connection: FusionConnection = None


def get_connection() -> FusionConnection:
    """Get or create the singleton connection to the Fusion listener.

    Reuses the existing connection if alive (verified by ping).
    Reconnects if the connection is dead.
    """
    global _connection

    if _connection is not None:
        try:
            # Health check: send a ping
            _connection.send_command("ping")
            return _connection
        except Exception:
            # Connection is dead, reconnect
            _connection.disconnect()
            _connection = None

    _connection = FusionConnection(host=FUSION_HOST, port=FUSION_PORT)
    _connection.connect()
    return _connection


# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------

mcp = FastMCP("davinci-resolve")


@mcp.tool()
def get_fusion_comp_info() -> str:
    """Get information about the current Fusion composition.

    Returns the composition name and a list of all nodes (tools) with
    their names and types. Use this to understand what's currently in
    the Fusion node graph before making changes.

    Returns:
        JSON with comp_name, tool_count, and list of tools.
    """
    conn = get_connection()
    result = conn.send_command("get_comp_info")
    return json.dumps(result, indent=2)


@mcp.tool()
def add_fusion_tool(tool_id: str, x: int = -32768, y: int = -32768) -> str:
    """Create a new node (tool) in the Fusion composition.

    Args:
        tool_id: The type of node to create. Common types:
                 Background, Merge, Blur, Transform, ColorCorrector,
                 MediaIn, MediaOut, Text+, Resize, BrightnessContrast,
                 Loader, Saver, ChannelBooleans, MatteControl
        x: X position in flow view (default: auto-position)
        y: Y position in flow view (default: auto-position)

    Returns:
        JSON with the created tool's name and type.
    """
    conn = get_connection()
    result = conn.send_command("add_tool", {
        "tool_id": tool_id,
        "x": x,
        "y": y,
    })
    return json.dumps(result, indent=2)


@mcp.tool()
def get_fusion_tool_info(tool_name: str) -> str:
    """Get detailed information about a specific node (tool).

    Returns the tool's type and all its current parameter values.
    Use this to inspect a node before modifying it.

    Args:
        tool_name: The name of the tool (e.g., "Background1", "Blur1").
                   Get names from get_fusion_comp_info().

    Returns:
        JSON with tool name, type, and current input values.
    """
    conn = get_connection()
    result = conn.send_command("get_tool_info", {"tool_name": tool_name})
    return json.dumps(result, indent=2)


@mcp.tool()
def connect_fusion_nodes(
    from_tool: str,
    to_tool: str,
    to_input: str = "Input",
) -> str:
    """Connect two nodes together in the Fusion flow.

    Connects the main output of from_tool to a specific input of to_tool.

    Args:
        from_tool: Name of the source node (its output will be connected)
        to_tool: Name of the destination node (its input will receive)
        to_input: Which input on the destination to connect to.
                  Common inputs: "Input" (most tools), "Background" (Merge),
                  "Foreground" (Merge), "EffectMask"

    Returns:
        JSON confirming the connection.
    """
    conn = get_connection()
    result = conn.send_command("connect_nodes", {
        "from_tool": from_tool,
        "to_tool": to_tool,
        "to_input": to_input,
    })
    return json.dumps(result, indent=2)


@mcp.tool()
def set_fusion_parameter(tool_name: str, parameter: str, value: float) -> str:
    """Set a parameter value on a Fusion node.

    Args:
        tool_name: Name of the tool (e.g., "Blur1", "Background1")
        parameter: Parameter name (e.g., "XBlurSize" for blur amount,
                   "TopLeftRed" for background color red channel).
                   Use get_fusion_tool_info() to see available parameters.
        value: The value to set (number)

    Returns:
        JSON confirming the parameter was set.
    """
    conn = get_connection()
    result = conn.send_command("set_parameter", {
        "tool_name": tool_name,
        "parameter": parameter,
        "value": value,
    })
    return json.dumps(result, indent=2)


@mcp.tool()
def add_fusion_text(
    text: str = "Hello",
    size: float = 0.1,
    color_r: float = 1.0,
    color_g: float = 1.0,
    color_b: float = 1.0,
    x: int = -32768,
    y: int = -32768,
) -> str:
    """Create a Text+ node with content, size, and color in one call.

    Convenience tool that creates a TextPlus node and configures it
    in a single step (instead of add_tool + multiple set_parameter calls).

    Args:
        text: The text content to display (default: "Hello")
        size: Font size in Fusion's normalized range (default: 0.1).
              0.05 = small, 0.1 = medium, 0.2 = large, 0.5 = very large
        color_r: Red channel 0.0-1.0 (default: 1.0 white)
        color_g: Green channel 0.0-1.0 (default: 1.0 white)
        color_b: Blue channel 0.0-1.0 (default: 1.0 white)
        x: X position in flow view (default: auto-position)
        y: Y position in flow view (default: auto-position)

    Returns:
        JSON with the created node's name and applied settings.
    """
    conn = get_connection()
    result = conn.send_command("add_text", {
        "text": text,
        "size": size,
        "color_r": color_r,
        "color_g": color_g,
        "color_b": color_b,
        "x": x,
        "y": y,
    })
    return json.dumps(result, indent=2)


@mcp.tool()
def animate_fusion_parameter(
    tool_name: str,
    parameter: str,
    keyframes: list[dict],
) -> str:
    """Add keyframes to animate a parameter over time.

    Sets values at specific frames, creating a smooth animation curve.
    Fusion automatically interpolates between keyframes.

    Args:
        tool_name: Name of the tool to animate (e.g., "Blur1")
        parameter: Parameter name to animate (e.g., "XBlurSize", "Size").
                   Use get_fusion_tool_info() to see available parameters.
        keyframes: List of keyframe dicts, each with "frame" (int) and
                   "value" (number). Example:
                   [{"frame": 0, "value": 0}, {"frame": 30, "value": 5.0}]

    Returns:
        JSON confirming how many keyframes were set.
    """
    conn = get_connection()
    result = conn.send_command("animate_parameter", {
        "tool_name": tool_name,
        "parameter": parameter,
        "keyframes": keyframes,
    })
    return json.dumps(result, indent=2)


@mcp.tool()
def add_fusion_mask(
    mask_type: str = "Ellipse",
    connect_to: str = "",
    x: int = -32768,
    y: int = -32768,
) -> str:
    """Create a mask node and optionally connect it as an EffectMask.

    Masks are used to limit the effect of a tool to a specific region.
    When connect_to is specified, the mask is automatically wired into
    that tool's EffectMask input.

    Args:
        mask_type: Shape of the mask. Options: "Ellipse", "Rectangle",
                   "Polygon" (default: "Ellipse")
        connect_to: Name of a tool to connect this mask to as its
                    EffectMask (optional -- leave empty to create unconnected)
        x: X position in flow view (default: auto-position)
        y: Y position in flow view (default: auto-position)

    Returns:
        JSON with mask info and whether it was connected.
    """
    conn = get_connection()
    params = {
        "mask_type": mask_type,
        "x": x,
        "y": y,
    }
    if connect_to:
        params["connect_to"] = connect_to
    result = conn.send_command("add_mask", params)
    return json.dumps(result, indent=2)


@mcp.tool()
def execute_fusion_code(code: str) -> str:
    """Execute arbitrary Python code inside the Fusion environment.

    The code runs with access to 'fusion' (or 'fu') and 'comp' objects,
    giving full access to the Fusion scripting API. Use this for operations
    not covered by other tools.

    Any print() output will be captured and returned.

    Args:
        code: Python code to execute. Has access to: fusion, fu, comp.

    Returns:
        JSON with execution status and captured output.
    """
    conn = get_connection()
    result = conn.send_command("execute_code", {"code": code})
    return json.dumps(result, indent=2)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run()
