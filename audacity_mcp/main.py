import atexit

from mcp.server.fastmcp import FastMCP

from audacity_mcp.audacity_client import AudacityClient
from audacity_mcp.tool_registry import register_all_tools

mcp = FastMCP("AudacityMCPMax")
client = AudacityClient()
# atexit calls its callbacks synchronously, so registering the async close()
# only ever created a coroutine that was discarded — the pipes were never
# closed at shutdown (issue #3).
atexit.register(client.close_sync)

register_all_tools(mcp)


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
