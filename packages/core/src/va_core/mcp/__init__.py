"""Connector framework: MCP servers with read and draft tools only (ADR-0005, ADR-0018).

Install with the `mcp` extra (`va-core[mcp]`). A connector builds a `ToolRegistry` for its system,
registers read and draft tools, and serves it with `build_connector_server` + `connector_app`.
The tenant reaches the server only in a signed run envelope (`envelope.py`), never in arguments.
"""
