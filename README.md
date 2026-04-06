# DaVinci Resolve Fusion MCP

An MCP (Model Context Protocol) server that lets Claude control DaVinci Resolve's Fusion page. Create nodes, connect them, set parameters, and query your composition -- all through natural language.

## Requirements

- DaVinci Resolve (Free or Studio) installed and running
- Python 3.6+ (tested with 3.11)
- `mcp` Python package

## Setup

### 1. Enable External Scripting in Resolve

Open DaVinci Resolve, go to **Preferences > General > External scripting using** and set it to **Local**.

### 2. Install Dependencies

```bash
conda activate mcp_server
pip install mcp
```

### 3. Set Environment Variables

**Windows:**
```cmd
set RESOLVE_SCRIPT_API=C:\ProgramData\Blackmagic Design\DaVinci Resolve\Support\Developer\Scripting
set RESOLVE_SCRIPT_LIB=C:\Program Files\Blackmagic Design\DaVinci Resolve\fusionscript.dll
set PYTHONPATH=%PYTHONPATH%;%RESOLVE_SCRIPT_API%\Modules\
```

### 4. Add to Claude Code

Add to your Claude Code `settings.json` under `mcpServers`:

```json
{
  "davinci-resolve": {
    "command": "python",
    "args": ["path/to/src/server.py"],
    "env": {
      "RESOLVE_SCRIPT_API": "C:\\ProgramData\\Blackmagic Design\\DaVinci Resolve\\Support\\Developer\\Scripting",
      "RESOLVE_SCRIPT_LIB": "C:\\Program Files\\Blackmagic Design\\DaVinci Resolve\\fusionscript.dll",
      "PYTHONPATH": "C:\\ProgramData\\Blackmagic Design\\DaVinci Resolve\\Support\\Developer\\Scripting\\Modules\\"
    }
  }
}
```

## Available Tools

| Tool | Description |
|------|-------------|
| `get_comp_info` | Get current Fusion composition state (nodes, connections) |
| `add_tool` | Create a new node in the composition |
| `connect_nodes` | Connect two nodes together |
| `set_parameter` | Set a parameter on a node |
| `execute_fusion_code` | Run arbitrary Python code inside the Fusion context |

## Usage Examples

Once connected, ask Claude things like:
- "What nodes are in my current Fusion comp?"
- "Add a Blur node after the MediaIn"
- "Set the blur size to 5.0"
- "Create a simple composite: Background + Text merged together"

## Architecture

```
Claude Code --stdio--> MCP Server --DaVinciResolveScript--> DaVinci Resolve
```

The MCP server connects to a running Resolve instance via the official scripting API (`fusionscript.dll`). No plugins or addons need to be installed inside Resolve.

## License

MIT
