"""MCP server (stdio) with one tool. The agent starts it as a subprocess."""

from mcp.server.mcpserver import MCPServer

server = MCPServer("k3d-gpu-tools")


@server.tool()
def multiply(a: int, b: int) -> int:
    """Multiply two integers."""
    return a * b


if __name__ == "__main__":
    server.run("stdio")
