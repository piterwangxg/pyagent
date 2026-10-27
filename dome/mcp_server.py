# MCP server，工具提供方。两种跑法：不带参数走 stdio，被 mcp_agent.py 当子进程拉起来；带一个 http 参数就自己起一个 HTTP 服务
# 用官方 SDK 带的高层 server 类，普通函数加上 @mcp.tool() 就是工具，函数签名和 docstring 自动变成 schema
# 这个类在 SDK 1.x 里叫 FastMCP，2.x 改名 MCPServer、导入路径也换了，这里两种都认
import ast
import operator
import sys

try:
    from mcp.server.fastmcp import FastMCP as MCPServer
except ImportError:
    from mcp.server.mcpserver import MCPServer

mcp = MCPServer("demo-tools")

@mcp.tool()
def get_weather(city: str) -> str:
    """获取指定城市的天气"""
    return f"{city}今天晴，气温 27度，微风。"

_ALLOWED_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.USub: operator.neg
}

def _eval_node(node):
    """递归求值，只认数字和运算符，别的节点一律拒"""
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _ALLOWED_OPS:
        return _ALLOWED_OPS[type(node.op)](_eval_node(node.left), _eval_node(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _ALLOWED_OPS:
        return _ALLOWED_OPS[type(node.op)](_eval_node(node.operand))
    raise ValueError(f"不支持的表达式片段: {type(node).__name__}")

@mcp.tool()
def calculate(expression: str) -> str:
    """计算数学表达式"""
    try:
        return str(_eval_node(ast.parse(expression, mode="eval").body))
    except Exception as e:
        return f"计算错误: {e}"

if __name__ == "__main__":
    # 不带参数是 stdio，stdout 是协议通道，这里千万别 print，日志只能走 stderr
    # 带参数就是 HTTP：先 python mcp_server.py http 起服务，再 python mcp_agent.py http 连过去
    # stateless_http=True 让每个请求各自独立，对应 2026-07-28 那版无状态的方向
    # 这段是 SDK 2.x 的写法，host、port、stateless_http 在 1.x 里是构造函数参数，run() 只收 transport
    mode = sys.argv[1] if len(sys.argv) > 1 else "stdio"
    if mode == "http":
        mcp.run(transport="streamable-http", host="127.0.0.1", port=8000, stateless_http=True)
    else:
        mcp.run(transport="stdio")
