# CLAUDE.md -- DaVinci Resolve Fusion MCP

## What This Project Is

An MCP server that lets Claude control DaVinci Resolve's Fusion page -- creating nodes,
connecting them, setting parameters, querying composition state. Part of Sergio's MCP
learning project (Level 3: bridge pattern to external application).

## Architecture: Socket Bridge (like Blender MCP)

```
Claude Code --stdio--> MCP Server (Python) --TCP socket--> Listener script (inside Fusion console)
```

**Why a socket bridge?** The free version of DaVinci Resolve does NOT support external
scripting (`DaVinciResolveScript` requires Studio). So we can't connect from outside.
Instead, we run a listener script inside Fusion's console, which HAS access to comp/tools.
The MCP server sends JSON commands over a TCP socket, the listener executes them and
sends results back.

Same architecture as Blender MCP (github.com/ahujasid/blender-mcp).

### User Workflow
1. Open DaVinci Resolve, go to Fusion page
2. Open the console (bottom of Fusion page)
3. Paste and run the listener script (one time per session)
4. Claude can now control Fusion through the MCP server

## Components

### 1. Fusion Listener (`src/fusion_listener.py`)
- Runs INSIDE Resolve's Fusion console (pasted by user)
- Opens TCP socket on localhost:port
- Receives JSON commands, executes Fusion API calls, returns results
- Has access to `comp`, `fusion`, all Fusion objects
- Command format: `{"type": "command_name", "params": {...}}`
- Response format: `{"status": "success|error", "result": {...}}`

### 2. MCP Server (`src/server.py`)
- Runs OUTSIDE Resolve (launched by Claude Code via stdio)
- Exposes tools to Claude via MCP protocol
- Each tool sends a JSON command over TCP socket to the listener
- Receives JSON response and returns to Claude

## Fusion API Quick Reference

### Object Hierarchy (available inside Fusion console)
```
fusion                             # Application object (pre-bound as 'fu')
comp = fu.CurrentComp              # Current composition

comp.AddTool("Blur", x, y)        # Create node
merge.Background = bg.Output      # Connect nodes
bg.TopLeftRed = 0.5                # Set parameter
comp.GetToolList()                 # Get all nodes
comp.FindTool("Background1")      # Find by name
comp.Lock() / comp.Unlock()       # Batch edits (prevent UI updates)
comp.StartUndo("name")            # Undo group
```

### Common Tool IDs
`Background`, `Merge`, `Blur`, `Transform`, `ColorCorrector`, `Loader`, `Saver`,
`MediaIn`, `MediaOut`, `TextPlus`, `Resize`, `BrightnessContrast`, `ChannelBooleans`,
`MatteControl`, `EllipseMask`, `RectangleMask`, `PolylineMask`, `BSpline`

## MCP Tools (9 total)

### Core tools
| Tool | Listener command | Description |
|------|-----------------|-------------|
| `get_fusion_comp_info` | `get_comp_info` | List all nodes in composition |
| `add_fusion_tool` | `add_tool` | Create any node by tool ID |
| `get_fusion_tool_info` | `get_tool_info` | Inspect node parameters |
| `connect_fusion_nodes` | `connect_nodes` | Wire nodes together |
| `set_fusion_parameter` | `set_parameter` | Set a parameter value |
| `execute_fusion_code` | `execute_code` | Run arbitrary Python in Fusion |

### Convenience tools (Tier 1)
| Tool | Listener command | Description |
|------|-----------------|-------------|
| `add_fusion_text` | `add_text` | Create Text+ with content, size, color |
| `animate_fusion_parameter` | `animate_parameter` | Set keyframes on a parameter |
| `add_fusion_mask` | `add_mask` | Create mask, optionally connect as EffectMask |

## Project Structure

```
davinci-resolve-mcp/
  CLAUDE.md              # This file
  README.md              # GitHub readme (install, usage)
  src/
    server.py            # MCP server (exposes tools to Claude)
    fusion_listener.py   # Listener script (runs inside Fusion console)
  tests/
    test_socket.py       # Test: send command to listener, check response
```

## Key Paths (Sergio's Machine)

| What | Path |
|------|------|
| Resolve install | `C:\Program Files\Blackmagic Design\DaVinci Resolve\` |
| Scripting docs | `C:\ProgramData\Blackmagic Design\DaVinci Resolve\Support\Developer\Scripting\` |
| fusionscript.dll | `C:\Program Files\Blackmagic Design\DaVinci Resolve\fusionscript.dll` |
| User scripts | `%APPDATA%\Roaming\Blackmagic Design\DaVinci Resolve\Support\Fusion\Scripts\` |

## Dev Commands

```bash
conda activate mcp_server

# Run MCP server (for Claude Code)
python src/server.py

# Test socket connection (listener must be running in Fusion)
python tests/test_socket.py
```

## Conventions

- snake_case for functions/variables, PascalCase for classes
- Type hints on function signatures
- TCP socket on localhost, default port 9878
- JSON commands: `{"type": "cmd", "params": {...}}`
- JSON responses: `{"status": "success|error", "result|message": ...}`
