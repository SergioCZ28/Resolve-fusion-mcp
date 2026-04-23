"""
Fusion MCP Listener -- runs INSIDE DaVinci Resolve's Fusion page.

This script opens a TCP socket server on localhost:9876 and listens for
JSON commands from the MCP server. It has access to Fusion's API objects
(comp, fusion) and can create nodes, connect them, set parameters, etc.

Usage:
    - Place in: %APPDATA%/Blackmagic Design/DaVinci Resolve/Support/Fusion/Scripts/Comp/
    - In Resolve: Workspace > Scripts > Comp > fusion_listener
    - Or paste directly into the Fusion console (Py tab)

Architecture:
    MCP Server --TCP socket--> This script (inside Resolve)
    Commands:  {"type": "command_name", "params": {...}}
    Responses: {"status": "success|error", "result|message": ...}

Port: 9876 (same as Blender MCP convention)
"""

import hmac
import socket
import threading
import json
import io
import os
import sys
import tempfile
import traceback

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Bind to 127.0.0.1 explicitly, not "localhost". "localhost" resolves to both
# IPv4 and IPv6 on some systems, and dual-stack behaviour varies -- 127.0.0.1
# is unambiguous and stays on the loopback interface.
HOST = "127.0.0.1"
PORT = 9878

# Shared secret read from the environment. If set, every command must include
# a matching "token" field or it is rejected. This stops *any* other process
# on the local machine (malicious browser extension, rogue script, ...) from
# speaking to the listener, since only processes Sergio launches see the env.
# If unset, the listener still runs but logs a warning at startup.
AUTH_TOKEN = os.environ.get("FUSION_AUTH_TOKEN", "")

LOG_PATH = os.environ.get(
    "FUSION_LISTENER_LOG",
    os.path.join(tempfile.gettempdir(), "fusion_listener.log"),
)

# ---------------------------------------------------------------------------
# Fusion API helpers
# ---------------------------------------------------------------------------

def get_fusion_and_comp():
    """Get the current Fusion and Composition objects.

    Inside the Fusion console, 'fusion' (or 'fu') and 'comp' are pre-bound
    globals. We try those first, then fall back to discovering them.

    Returns:
        tuple: (fusion_obj, comp_obj) or raises RuntimeError
    """
    # Try pre-bound globals (available when run from Fusion console/scripts)
    fu = None
    co = None

    # 'fusion' and 'fu' are the same object, pre-bound in Fusion's Python env
    if "fusion" in dir(__builtins__) if hasattr(__builtins__, '__dict__') else False:
        fu = fusion  # noqa: F821
    elif "fu" in globals():
        fu = globals()["fu"]
    else:
        # Try the scriptapp approach (works if fusionscript is available)
        try:
            import DaVinciResolveScript as dvr_script
            resolve = dvr_script.scriptapp("Resolve")
            if resolve:
                fu = resolve.Fusion()
        except Exception:
            pass

    if fu is None:
        raise RuntimeError(
            "Cannot find Fusion object. Make sure this script is running "
            "inside DaVinci Resolve (Fusion console or Scripts menu)."
        )

    co = fu.CurrentComp
    if co is None:
        raise RuntimeError(
            "No current composition. Open or create a Fusion composition first."
        )

    return fu, co


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------
# Each handler receives (fusion, comp, **params) and returns a dict result.
# The socket server wraps this in {"status": "success", "result": <return>}
# or {"status": "error", "message": <traceback>} on exception.


def handle_ping(fusion, comp, **params):
    """Health check -- confirms the listener is alive."""
    return "pong"


def handle_get_comp_info(fusion, comp, **params):
    """Return information about the current Fusion composition.

    Returns:
        dict with comp name and list of all tools (nodes) with their types.
    """
    tool_list = comp.GetToolList(False)  # False = all tools, not just selected
    tools = []
    for idx, tool in tool_list.items():
        tools.append({
            "name": tool.Name,
            "type": tool.GetAttrs("TOOLS_RegID"),  # e.g. "Blur", "Merge"
            "id": tool.Name,  # Name is the unique identifier in Fusion
        })

    return {
        "comp_name": comp.GetAttrs("COMPS_Name"),
        "tool_count": len(tools),
        "tools": tools,
    }


def handle_add_tool(fusion, comp, **params):
    """Create a new tool (node) in the composition.

    Params:
        tool_id: str -- The tool type to create (e.g., "Blur", "Background", "Merge")
        x: int -- X position in the flow view (optional, default 0)
        y: int -- Y position in the flow view (optional, default 0)

    Returns:
        dict with the created tool's name and type.
    """
    tool_id = params.get("tool_id")
    if not tool_id:
        raise ValueError("tool_id is required (e.g., 'Blur', 'Background', 'Merge')")

    x = params.get("x", -32768)  # -32768 = auto-position (Fusion convention)
    y = params.get("y", -32768)

    comp.Lock()  # prevent UI updates during creation
    comp.StartUndo("MCP: Add " + tool_id)

    tool = comp.AddTool(tool_id, x, y)

    comp.EndUndo(True)
    comp.Unlock()

    if tool is None:
        raise RuntimeError(f"Failed to create tool '{tool_id}'. Is the tool ID correct?")

    return {
        "name": tool.Name,
        "type": tool.GetAttrs("TOOLS_RegID"),
    }


def handle_get_tool_info(fusion, comp, **params):
    """Get detailed information about a specific tool (node).

    Params:
        tool_name: str -- The name of the tool (e.g., "Background1", "Blur1")

    Returns:
        dict with tool name, type, and current input values.
    """
    tool_name = params.get("tool_name")
    if not tool_name:
        raise ValueError("tool_name is required")

    tool = comp.FindTool(tool_name)
    if tool is None:
        raise ValueError(f"Tool '{tool_name}' not found in composition")

    # Get all inputs and their current values
    input_list = tool.GetInputList()
    inputs = {}
    current_time = comp.CurrentTime
    for idx, inp in input_list.items():
        try:
            name = inp.GetAttrs("INPS_Name")
            val = inp[current_time]
            # Only include serializable values
            if isinstance(val, (int, float, str, bool, type(None))):
                inputs[name] = val
            elif isinstance(val, dict):
                inputs[name] = val
            else:
                inputs[name] = str(val)
        except Exception:
            pass  # skip inputs that can't be read

    return {
        "name": tool.Name,
        "type": tool.GetAttrs("TOOLS_RegID"),
        "inputs": inputs,
    }


def handle_connect_nodes(fusion, comp, **params):
    """Connect two nodes together.

    Params:
        from_tool: str -- Name of the source tool (output)
        to_tool: str -- Name of the destination tool (input)
        to_input: str -- Name of the input to connect to (e.g., "Background",
                         "Foreground", "Input"). Default: "Input"

    Returns:
        dict confirming the connection.
    """
    from_name = params.get("from_tool")
    to_name = params.get("to_tool")
    to_input = params.get("to_input", "Input")

    if not from_name or not to_name:
        raise ValueError("Both from_tool and to_tool are required")

    from_tool = comp.FindTool(from_name)
    to_tool = comp.FindTool(to_name)

    if from_tool is None:
        raise ValueError(f"Source tool '{from_name}' not found")
    if to_tool is None:
        raise ValueError(f"Destination tool '{to_name}' not found")

    comp.Lock()
    comp.StartUndo(f"MCP: Connect {from_name} -> {to_name}")

    # Connect: to_tool.InputName = from_tool.Output
    to_tool_input = getattr(to_tool, to_input, None)
    if to_tool_input is None:
        comp.EndUndo(False)
        comp.Unlock()
        raise ValueError(
            f"Input '{to_input}' not found on tool '{to_name}'. "
            f"Use get_tool_info to see available inputs."
        )

    from_output = from_tool.FindMainOutput(1)
    if from_output is None:
        comp.EndUndo(False)
        comp.Unlock()
        raise ValueError(f"Tool '{from_name}' has no main output")

    to_tool.ConnectInput(to_input, from_tool)

    comp.EndUndo(True)
    comp.Unlock()

    return {
        "connected": True,
        "from": from_name,
        "to": to_name,
        "input": to_input,
    }


def handle_set_parameter(fusion, comp, **params):
    """Set a parameter on a tool.

    Params:
        tool_name: str -- Name of the tool (e.g., "Blur1")
        parameter: str -- Parameter name (e.g., "XBlurSize", "TopLeftRed")
        value: any -- The value to set (number, string, bool)

    Returns:
        dict confirming the parameter was set.
    """
    tool_name = params.get("tool_name")
    parameter = params.get("parameter")
    value = params.get("value")

    if not tool_name or not parameter:
        raise ValueError("tool_name and parameter are required")

    tool = comp.FindTool(tool_name)
    if tool is None:
        raise ValueError(f"Tool '{tool_name}' not found")

    comp.Lock()
    comp.StartUndo(f"MCP: Set {tool_name}.{parameter}")

    tool.SetInput(parameter, value)

    comp.EndUndo(True)
    comp.Unlock()

    return {
        "tool": tool_name,
        "parameter": parameter,
        "value": value,
    }


def handle_add_text(fusion, comp, **params):
    """Create a Text+ node with content, size, and color in one call.

    Params:
        text: str -- The text content to display
        size: float -- Font size (default 0.1, Fusion's normalized range)
        color_r: float -- Red channel 0-1 (default 1.0)
        color_g: float -- Green channel 0-1 (default 1.0)
        color_b: float -- Blue channel 0-1 (default 1.0)
        x: int -- X position in flow view (default auto)
        y: int -- Y position in flow view (default auto)

    Returns:
        dict with the created tool's name and settings applied.
    """
    text = params.get("text", "Hello")
    size = params.get("size", 0.1)
    color_r = params.get("color_r", 1.0)
    color_g = params.get("color_g", 1.0)
    color_b = params.get("color_b", 1.0)
    x = params.get("x", -32768)
    y = params.get("y", -32768)

    comp.Lock()
    comp.StartUndo("MCP: Add Text+")

    tool = comp.AddTool("TextPlus", x, y)
    if tool is None:
        comp.EndUndo(False)
        comp.Unlock()
        raise RuntimeError("Failed to create TextPlus node")

    tool.SetInput("StyledText", text)
    tool.SetInput("Size", size)
    tool.SetInput("Red1", color_r)
    tool.SetInput("Green1", color_g)
    tool.SetInput("Blue1", color_b)

    comp.EndUndo(True)
    comp.Unlock()

    return {
        "name": tool.Name,
        "type": "TextPlus",
        "text": text,
        "size": size,
        "color": [color_r, color_g, color_b],
    }


def handle_animate_parameter(fusion, comp, **params):
    """Set keyframes on a tool parameter at specified frames.

    Params:
        tool_name: str -- Name of the tool (e.g., "Blur1")
        parameter: str -- Parameter name (e.g., "XBlurSize")
        keyframes: list -- List of dicts with "frame" (int) and "value" (number)
                          e.g., [{"frame": 0, "value": 0}, {"frame": 30, "value": 5.0}]

    Returns:
        dict confirming keyframes were set.
    """
    tool_name = params.get("tool_name")
    parameter = params.get("parameter")
    keyframes = params.get("keyframes", [])

    if not tool_name or not parameter:
        raise ValueError("tool_name and parameter are required")
    if not keyframes:
        raise ValueError("keyframes list is required (e.g., [{\"frame\": 0, \"value\": 0}])")

    tool = comp.FindTool(tool_name)
    if tool is None:
        raise ValueError(f"Tool '{tool_name}' not found")

    comp.Lock()
    comp.StartUndo(f"MCP: Animate {tool_name}.{parameter}")

    # BezierSpline creates a smooth animation curve
    inp = getattr(tool, parameter, None)
    if inp is None:
        comp.EndUndo(False)
        comp.Unlock()
        raise ValueError(f"Parameter '{parameter}' not found on tool '{tool_name}'")

    # Animate: set values at specific frames (Fusion auto-creates keyframes)
    for kf in keyframes:
        frame = kf.get("frame", 0)
        value = kf.get("value", 0)
        tool.SetInput(parameter, value, frame)

    comp.EndUndo(True)
    comp.Unlock()

    return {
        "tool": tool_name,
        "parameter": parameter,
        "keyframes_set": len(keyframes),
        "frames": [kf.get("frame") for kf in keyframes],
    }


def handle_add_mask(fusion, comp, **params):
    """Create a mask node and optionally connect it as an EffectMask.

    Params:
        mask_type: str -- "Ellipse", "Rectangle", or "Polygon" (default "Ellipse")
        connect_to: str -- (optional) Name of tool to connect this mask to as EffectMask
        x: int -- X position in flow view (default auto)
        y: int -- Y position in flow view (default auto)

    Returns:
        dict with created mask info and connection status.
    """
    mask_type = params.get("mask_type", "Ellipse")
    connect_to = params.get("connect_to")
    x = params.get("x", -32768)
    y = params.get("y", -32768)

    # Map friendly names to Fusion tool IDs
    mask_map = {
        "Ellipse": "EllipseMask",
        "Rectangle": "RectangleMask",
        "Polygon": "PolylineMask",
    }

    tool_id = mask_map.get(mask_type)
    if tool_id is None:
        raise ValueError(
            f"Unknown mask_type '{mask_type}'. Use: Ellipse, Rectangle, or Polygon"
        )

    comp.Lock()
    comp.StartUndo(f"MCP: Add {mask_type} Mask")

    mask = comp.AddTool(tool_id, x, y)
    if mask is None:
        comp.EndUndo(False)
        comp.Unlock()
        raise RuntimeError(f"Failed to create {tool_id} node")

    connected = False
    if connect_to:
        target = comp.FindTool(connect_to)
        if target is None:
            comp.EndUndo(True)
            comp.Unlock()
            return {
                "name": mask.Name,
                "type": tool_id,
                "connected": False,
                "warning": f"Target tool '{connect_to}' not found -- mask created but not connected",
            }

        target.ConnectInput("EffectMask", mask)
        connected = True

    comp.EndUndo(True)
    comp.Unlock()

    result = {
        "name": mask.Name,
        "type": tool_id,
        "mask_type": mask_type,
        "connected": connected,
    }
    if connected:
        result["connected_to"] = connect_to
    return result


def handle_execute_code(fusion, comp, **params):
    """Execute arbitrary Python code inside the Fusion environment.

    This is the escape hatch -- Claude can send any valid Python code that
    will run with access to fusion, comp, and all Fusion API objects.

    Params:
        code: str -- Python code to execute

    Returns:
        dict with captured stdout output.
    """
    code = params.get("code")
    if not code:
        raise ValueError("code is required")

    # Build namespace with Fusion objects
    namespace = {
        "fusion": fusion,
        "fu": fusion,
        "comp": comp,
    }

    # Capture print output
    capture = io.StringIO()
    old_stdout = sys.stdout
    sys.stdout = capture

    try:
        exec(code, namespace)
    finally:
        sys.stdout = old_stdout

    output = capture.getvalue()
    return {
        "executed": True,
        "output": output if output else "(no output)",
    }


# ---------------------------------------------------------------------------
# Command dispatch table
# ---------------------------------------------------------------------------

HANDLERS = {
    "ping": handle_ping,
    "get_comp_info": handle_get_comp_info,
    "add_tool": handle_add_tool,
    "get_tool_info": handle_get_tool_info,
    "connect_nodes": handle_connect_nodes,
    "set_parameter": handle_set_parameter,
    "add_text": handle_add_text,
    "animate_parameter": handle_animate_parameter,
    "add_mask": handle_add_mask,
    "execute_code": handle_execute_code,
}


# ---------------------------------------------------------------------------
# TCP Socket Server
# ---------------------------------------------------------------------------

def execute_command(command):
    """Parse a command dict and dispatch to the appropriate handler.

    Args:
        command: dict with "type" (str) and optional "params" (dict)

    Returns:
        dict with "status" ("success" or "error") and "result" or "message"
    """
    cmd_type = command.get("type")
    params = command.get("params", {})

    if AUTH_TOKEN:
        supplied = command.get("token", "")
        if not isinstance(supplied, str) or not hmac.compare_digest(supplied, AUTH_TOKEN):
            return {"status": "error", "message": "authentication failed"}

    if cmd_type not in HANDLERS:
        return {
            "status": "error",
            "message": f"Unknown command: '{cmd_type}'. "
                       f"Available: {list(HANDLERS.keys())}",
        }

    try:
        fusion, comp = get_fusion_and_comp()
        result = HANDLERS[cmd_type](fusion, comp, **params)
        return {"status": "success", "result": result}
    except Exception as e:
        return {
            "status": "error",
            "message": f"{type(e).__name__}: {str(e)}",
            "traceback": traceback.format_exc(),
        }


def log(msg):
    """Write a message to the log file (print() crashes from threads in Fusion)."""
    with open(LOG_PATH, "a") as f:
        f.write(msg + "\n")


def handle_client(client_socket):
    """Handle a single client connection (the MCP server).

    Reads JSON messages, executes commands, sends responses.
    Stays connected until the client disconnects.
    """
    client_socket.settimeout(None)  # blocking mode
    buffer = b""

    log("Client connected")

    try:
        while True:
            data = client_socket.recv(8192)
            if not data:
                break  # client disconnected

            buffer += data

            # Try to parse complete JSON messages
            try:
                command = json.loads(buffer.decode("utf-8"))
                buffer = b""  # reset on successful parse

                log(f"Command: {command.get('type', '?')}")

                response = execute_command(command)
                response_bytes = json.dumps(response).encode("utf-8")
                client_socket.sendall(response_bytes)

            except json.JSONDecodeError:
                pass  # incomplete message, keep reading

    except Exception as e:
        log(f"Client error: {type(e).__name__}: {e}")
    finally:
        client_socket.close()
        log("Client disconnected")


def start_server():
    """Start the TCP socket server. Runs forever, accepting one client at a time."""
    try:
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((HOST, PORT))
        server.listen(1)

        # Clear log and write startup message
        with open(LOG_PATH, "w") as f:
            f.write(f"Listening on {HOST}:{PORT}\n")
            if not AUTH_TOKEN:
                f.write(
                    "WARNING: FUSION_AUTH_TOKEN not set -- listener accepts any local "
                    "connection without auth. Set FUSION_AUTH_TOKEN before launching "
                    "Resolve to lock it down.\n"
                )

        try:
            while True:
                client, addr = server.accept()
                log(f"Client connected from {addr}")
                handle_client(client)
        except KeyboardInterrupt:
            pass
        finally:
            server.close()
    except Exception as e:
        with open(LOG_PATH, "w") as f:
            f.write(f"STARTUP ERROR: {type(e).__name__}: {e}\n")
            f.write(traceback.format_exc())


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
#
# HOW TO START THE LISTENER:
#
# In the Fusion console (Py3 tab), paste this one-liner (replace PATH with the
# absolute path to this file on your machine):
#
#   exec(open(r"PATH/TO/fusion_listener.py").read())
#
# The listener will start in a background thread and the console stays usable.
# You'll see "Listening on 127.0.0.1:9878" in the log file when ready.

def safe_start_server():
    """Wrapper that catches any crash and writes it to a log file."""
    try:
        start_server()
    except Exception as e:
        with open(LOG_PATH, "w") as f:
            f.write("THREAD CRASH:\n")
            f.write(traceback.format_exc())

def launch():
    """Start the listener in a background thread."""
    server_thread = threading.Thread(target=safe_start_server, daemon=False)
    server_thread.start()
    print("[MCP Listener] Server started in background thread")
    print("[MCP Listener] Fusion remains interactive -- you can keep working")

# Auto-launch when the file is exec'd or run
launch()
