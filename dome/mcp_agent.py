# 前面我们了解了Function Calling 工具调用方式，这种方式适用于本地、少量、单应用
# 接下来我们介绍一种更强大的工具调用方式，MCP (Model Context Protocol)，适用于外部、共享、跨模型
# MCP是基于Function Calling 的，MCP server 提供的工具最后还是翻译成 tools schema 发给模型，模型还是吐 tool_calls，代码还是校验、执行、回灌，循环一点没变。
# MCP 是 Anthropic 2024-11 提的一个开放协议，就是客户端和服务端之间的几条 JSON-RPC 约定：协商版本、列工具（tools/list）、调工具（tools/call）
# JSON-RPC是一种无状态、与语言无关的远程过程调用（RPC）协议 —— 客户端发一个 JSON 对象 "调用某个方法"，服务端回一个 JSON 对象 "返回结果或错误"。
# 传输常见两种一种是本地stdio，一种是远程流式streamable HTTP
# 除了工具，MCP 还定义了 resources（只读数据，比如文件、表）和 prompts（预设提示词模板）。做 agent 主要用 tools，另外两个先不展开。
# 好处是工具和 agent 解耦了。同一个 server，Claude Desktop、Cursor、我们这个脚本都能接；换模型也不用重写工具。

# 版本这事得单独提一句，MCP 是按日期出修订版的：2024-11-05、2025-03-26、2025-06-18、2025-11-25，最新一个是 2026-07-28。
# 早几版大致是：2025-03-26 引入 streamable HTTP，把老 SSE 换下去；2025-06-18 去掉 JSON-RPC 批量、加了结构化输出和 elicitation；2025-11-25 把 Tasks、扩展机制先塞进来。
# 最新这次动得挺大，核心从有状态的会话协议改成了无状态的请求/响应：initialize/initialized 握手取消了，Mcp-Session-Id 也没了，每个请求自带协议版本、客户端身份和能力，所以随便落到哪台服务器实例都能处理。
# 服务端发起的请求（采样、elicitation）改成多轮往返 MRTR，列表结果带缓存提示，Tasks 挪进扩展，roots、sampling、logging 和老的 HTTP+SSE 一起进入废弃期。
# 这些改动落到我们这个示例上其实就一处：session.initialize() 是 2025-11-25 及以前的握手。
# SDK 2.x 里它还在，走的是 legacy 路径；想直接谈 2026-07-28，把它换成 session.discover()，或者用高层 Client（默认 mode='auto'，先探 server/discover，探不到再退回握手）。
# 这里留着 initialize，是因为 1.x 和 2.x 都认这一条；另外 2.x 把字段命名从驼峰改成了下划线，工具模型的 inputSchema 变成 input_schema，结果里的 isError 变成 is_error，下面都用 getattr 兜了一下。

# 这个示例拆成两个文件：
# mcp_server.py，工具提供方，十来行，用官方 SDK 的高层 server 类写，普通函数加个装饰器就是工具
# mcp_agent.py，也就是本文件，客户端加上那套熟悉的循环
# 建议先扫一眼 mcp_server.py，回来再看 client 这边。

# 再补一个 streamable HTTP 的跑法。stdio 适合本地单机，server 就是客户端拉的子进程；HTTP 是把 server 当服务跑起来，客户端只记一个 URL，多个客户端能连同一个 server，也能跨机器。
# 代码上要动的只有连接那几行：stdio_client(...) 换成 streamable_http_client(url)，session 和循环完全一样。
# 跑法是不带参数走 stdio；想走 HTTP，先 python mcp_server.py http 起服务，再开一个窗口 python mcp_agent.py http。

# client 要干的事按顺序是：拉起 server 子进程（或者连上已经跑起来的 HTTP 服务）→ 握手 → 列工具 → 把工具描述转成 OpenAI 的 schema → 进循环 → 模型要调工具时走 session.call_tool，不再直接调本地函数

import asyncio
import json
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
try:
    from mcp.client.streamable_http import streamable_http_client  # SDK 2.x
except ImportError:
    from mcp.client.streamable_http import streamablehttp_client as streamable_http_client  # SDK 1.x，名字没下划线，返回值还多一个拿 session id 的函数
from openai import AsyncOpenAI

# MCP 的客户端是异步的，模型这边也得用 AsyncOpenAI，不然一 await 一不 await，循环里写起来很别扭

api_key = os.getenv("DEEPSEEK_API_KEY")
if not api_key:
    raise RuntimeError("没读到 DEEPSEEK_API_KEY，先设置环境变量再跑")

client = AsyncOpenAI(
    api_key=api_key,
    base_url="https://api.deepseek.com"
)

# stdio 传输就是替我们起一个子进程。command 用 sys.executable，保证跟当前脚本是同一个解释器，免得 PATH 里找不到 python
# 接别人写好的 server 只改这两行，比如 npx 起一个文件系统 server：command="npx", args=["-y", "@modelcontextprotocol/server-filesystem", r"D:\360\pg"]
SERVER_PARAMS = StdioServerParameters(
    command=sys.executable,
    args=[os.path.join(os.path.dirname(os.path.abspath(__file__)), "mcp_server.py")]
)

# HTTP 模式下 server 不在本进程里，得自己先起：python mcp_server.py http
# 8000 和 /mcp 是 SDK 的默认值，改端口的话两边得对上
HTTP_URL = "http://127.0.0.1:8000/mcp"

def to_openai_schema(mcp_tool) -> dict:
    """MCP 的工具描述和 OpenAI 的 function schema 字段一一对应，转一层就能喂给模型"""
    # 1.x 里字段叫 inputSchema，2.x 改成 input_schema，两种都认
    input_schema = getattr(mcp_tool, "input_schema", None) or getattr(mcp_tool, "inputSchema", None)
    return {
        "type": "function",
        "function": {
            "name": mcp_tool.name,
            "description": mcp_tool.description or "",
            # inputSchema 本身就是 JSON Schema，直接拿来当 parameters
            "parameters": input_schema or {"type": "object", "properties": {}}
        }
    }

async def run_agent(user_prompt: str, transport: str = "stdio"):
    # 两种传输除了连上之前那一下，后面全一样，所以就在这挑一下上下文管理器
    # stdio 是客户端顺手把 server 拉起来；HTTP 是 server 已经跑在 8000 端口上，这边只是连过去
    if transport == "http":
        conn = streamable_http_client(HTTP_URL)
    else:
        conn = stdio_client(SERVER_PARAMS)

    # 连接的生命周期就是最外面这两层 with，退出时子进程或 HTTP 连接跟着关掉
    async with conn as streams:
        # stdio 给二元组，1.x 的 streamablehttp_client 给三元组，前两个都是读写流
        read, write = streams[0], streams[1]
        async with ClientSession(read, write) as session:
            # 探测版本。initialize() 是 ≤2025-11-25 的握手路径，2026-07-28 之后这段没了
            # 想走新协议就把这行换成 await session.discover()，后面的收发不用改
            await session.initialize()

            # 工具清单由 server 报上来，不再手写。server 那边加一个函数，这边一行不用改
            mcp_tools = (await session.list_tools()).tools
            print(f"从 MCP server 拉到 {len(mcp_tools)} 个工具: {[t.name for t in mcp_tools]}")
            tools_schema = [to_openai_schema(t) for t in mcp_tools]

            messages = [
                {"role": "system", "content": "你是一个有用的助手，需要时请调用工具完成任务。"},
                {"role": "user", "content": user_prompt}
            ]
            max_iterations = 5

            for i in range(max_iterations):
                print(f"\n--- Agent Loop 第 {i+1} 轮 ---")

                response = await client.chat.completions.create(
                    model="deepseek-flash",
                    messages=messages,
                    tools=tools_schema,
                    tool_choice="auto"
                )

                assistant_message = response.choices[0].message
                messages.append(assistant_message)

                if not assistant_message.tool_calls:
                    print(f"最终回答: {assistant_message.content}")
                    return assistant_message.content

                for tool_call in assistant_message.tool_calls:
                    func_name = tool_call.function.name

                    # JSON 解析这层客户端还得自己做，参数格式错是模型的问题，server 管不到
                    try:
                        func_args = json.loads(tool_call.function.arguments or "{}")
                    except json.JSONDecodeError as e:
                        observation = f"参数不是合法的 JSON: {e}"
                        print(f"参数校验失败: {observation}")
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": observation
                        })
                        continue

                    print(f"调用 MCP 工具: {func_name}({func_args})")

                    # 真正执行的是 server 里的函数，这边只发一个 tools/call
                    # 参数类型这类校验落在 server 的 schema 上，不合格也是回一条错误结果，不会把连接搞崩
                    try:
                        result = await session.call_tool(func_name, func_args)
                        text = "\n".join(c.text for c in result.content if getattr(c, "text", None))
                        # isError（1.x）和 is_error（2.x）是同一样东西
                        is_error = getattr(result, "is_error", None)
                        if is_error is None:
                            is_error = getattr(result, "isError", False)
                        observation = f"工具执行失败: {text}" if is_error else text
                    except Exception as e:
                        # 协议层的错，比如工具名对不上，SDK 会直接抛
                        observation = f"工具调用失败: {e}"

                    print(f"执行结果: {observation}")

                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": observation
                    })

            return f"Agent 达到最大循环次数，任务中断。最后一次回复: {assistant_message.content}"

if __name__ == "__main__":
    # 不带参数走 stdio；python mcp_agent.py http 则去连本机 8000 端口的 server，记得先把服务起起来
    transport = sys.argv[1] if len(sys.argv) > 1 else "stdio"
    print(f"传输方式: {transport}")
    asyncio.run(run_agent("帮我查一下北京的天气，然后算一下 10 除以 0 等于多少？", transport))
