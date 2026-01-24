import importlib
import sys

print(sys.version)

mods = ["mcp", "mcp.client.stdio", "mcp.client.sse"]
for m in mods:
    try:
        importlib.import_module(m)
        print(m, "OK")
    except Exception as e:
        print(m, "FAIL", e)

try:
    from mcp import ClientSession, StdioServerParameters  # noqa: F401
    from mcp.client.stdio import stdio_client  # noqa: F401
    print("ClientSession/StdioServerParameters/stdio_client OK")
except Exception as e:
    print("Top-level or stdio_client import FAIL", e)

try:
    from mcp.client.sse import sse_client  # noqa: F401
    print("sse_client OK")
except Exception as e:
    print("sse_client FAIL", e)
