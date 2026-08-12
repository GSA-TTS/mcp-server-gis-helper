from . import get_drawn_geometry, open_map, validate_geometry


def register_tools(mcp):
    """Register all tools with the MCP server."""
    open_map.register(mcp)
    get_drawn_geometry.register(mcp)
    validate_geometry.register(mcp)
