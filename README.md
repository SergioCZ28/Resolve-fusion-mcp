# DaVinci Resolve Fusion MCP

**The only DaVinci Resolve MCP server that works on the free version.**

All existing Resolve MCP projects require DaVinci Resolve Studio (paid). This one uses a socket bridge pattern -- a lightweight listener runs inside Fusion's console, and the MCP server communicates with it over TCP. No Studio license needed.

## How It Works

```
Claude Code --stdio--> MCP Server (Python) --TCP socket--> Listener (inside Fusion console)
```

A small Python script runs inside Resolve's Fusion console and listens for commands on a TCP socket. The MCP server (launched by Claude Code) sends JSON commands to the listener, which executes Fusion API calls and returns results. Same architecture as [Blender MCP](https://github.com/ahujasid/blender-mcp).

## Available Tools

### Core

| Tool | Description |
|------|-------------|
| `get_fusion_comp_info` | Get composition state -- all nodes with names and types |
| `add_fusion_tool` | Create a new node (Background, Blur, Merge, Text+, etc.) |
| `get_fusion_tool_info` | Inspect a node's parameters and current values |
| `connect_fusion_nodes` | Connect two nodes together (output to input) |
| `set_fusion_parameter` | Set any parameter on a node (blur size, color, position, etc.) |
| `execute_fusion_code` | Run arbitrary Python code inside Fusion (escape hatch) |

### Convenience

| Tool | Description |
|------|-------------|
| `add_fusion_text` | Create a Text+ node with content, size, and color in one call |
| `animate_fusion_parameter` | Add keyframes to animate a parameter over time |
| `add_fusion_mask` | Create a mask (Ellipse/Rectangle/Polygon), optionally connect as EffectMask |

## Quick Start

### 1. Install the MCP server

```bash
pip install fastmcp
```

Clone this repo (or download `src/server.py` and `src/fusion_listener.py`):

```bash
git clone https://github.com/SergioCZ28/Resolve-fusion-mcp.git
```

### 2. Add to Claude Code

Add this to your Claude Code `settings.json` (or `.claude/settings.json`) under `mcpServers`:

```json
{
  "davinci-resolve": {
    "command": "python",
    "args": ["C:/path/to/Resolve-fusion-mcp/src/server.py"]
  }
}
```

Replace the path with wherever you cloned the repo. That's the only config needed -- no environment variables, no DLL paths.

### 3. Start the listener in Resolve

1. Open DaVinci Resolve and go to the **Fusion page**
2. Open the console at the bottom of the screen
3. Click the **Py3** tab
4. Paste this one-liner and press Enter:

```python
exec(open(r"C:/path/to/Resolve-fusion-mcp/src/fusion_listener.py").read())
```

You'll see `[MCP Listener] Server started in background thread`. The listener is now running and Claude can control Fusion.

> **Note:** You need to start the listener once per Resolve session. If you restart Resolve, paste it again.

## Usage Examples

Once connected, talk to Claude naturally:

- "What nodes are in my current Fusion comp?"
- "Add a Blur node and connect it after the MediaIn"
- "Set the blur size to 5.0"
- "Create a composite: colored background with text merged on top"
- "Show me the parameters on Background1"
- "Add orange text that says 'Hello World' at size 0.2"
- "Animate the blur from 0 to 10 over 30 frames"
- "Add an ellipse mask to the text node"

## Why Socket Bridge?

DaVinci Resolve has two scripting modes:

| Mode | How it works | Requires Studio? |
|------|-------------|-----------------|
| **External scripting** (`DaVinciResolveScript`) | Connect from outside via `fusionscript.dll` | Yes |
| **Console scripting** | Run Python inside Fusion's built-in console | No |

Every other Resolve MCP uses the external scripting API, which only works with the paid Studio version. This project runs a listener inside the Fusion console (free version access) and bridges to it over TCP.

The trade-off: you need to paste a one-liner into the Fusion console each session. But you get full Fusion control without paying for Studio.

## Comparison

| Project | Stars | Approach | Free Version? |
|---------|-------|----------|--------------|
| [samuelgursky/davinci-resolve-mcp](https://github.com/samuelgursky/davinci-resolve-mcp) | ~756 | Direct API (DaVinciResolveScript) | No -- Studio only |
| [apvlv/davinci-resolve-mcp](https://github.com/apvlv/davinci-resolve-mcp) | ~55 | Direct API | No -- Studio only |
| [Tooflex/davinci-resolve-mcp](https://github.com/Tooflex/davinci-resolve-mcp) | small | Direct API | No -- Studio only |
| **This project** | -- | Socket bridge (like Blender MCP) | **Yes** |

## Architecture

```
+------------------+         +------------------+         +------------------+
|   Claude Code    |  stdio  |    MCP Server    |   TCP   | Fusion Listener  |
|                  | ------> |   (server.py)    | ------> | (inside Resolve) |
|  Natural language|         |  Translates MCP  |  :9878  | Has full Fusion  |
|  tool calls      | <------ |  tools to JSON   | <------ | API access       |
|                  |         |  socket commands  |         | (comp, tools)    |
+------------------+         +------------------+         +------------------+
```

**MCP Server** (`src/server.py`): Runs as a subprocess of Claude Code. Exposes tools via MCP protocol (stdio transport). Each tool call opens a TCP connection to the listener, sends a JSON command, and returns the result.

**Fusion Listener** (`src/fusion_listener.py`): Runs inside Resolve's Fusion console in a background thread. Listens on `localhost:9878` for JSON commands. Has access to `comp`, `fusion`, and all Fusion scripting objects.

**Command format:**
```json
{"type": "add_tool", "params": {"tool_id": "Blur", "x": 0, "y": 0}}
```

**Response format:**
```json
{"status": "success", "result": {"name": "Blur1", "type": "Blur"}}
```

## Requirements

- DaVinci Resolve 18+ (free version works)
- Python 3.11+
- `fastmcp` package (`pip install fastmcp`)
- Claude Code (or any MCP client)

## License

MIT
