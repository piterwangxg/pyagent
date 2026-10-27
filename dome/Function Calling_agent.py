# 最小agent 
# agent 基于对话，单次对话中完成一个任务基于循环，一次循环中就是一次模型决策，当没有工具调用时结束
# 以下是最简示例
"""
while True:
    reply = llm(msg)
    if not reply.tool_calls:
        break
    msg += run_tool(reply.tool_calls)
"""

# 接下引申出工具调用
# 首先要明白现在大模型为什么可以调用工具，大模型不是一个预测模型吗？它如何知道调用工具呢？
# 首先工具是什么呢？是一个函数？一个接口？ 其实都差不多，大致流程都是输入参数，返回结果。
# 所以大模型调用工具就是根据工具说明（schema）来生成参数，交由工具执行器来执行（run_tool），最后把结果返回给大模型，大模型带着工具结果再推理。
# 生成的参数是什么格式呢？是json格式的字符串。
# 大模型如何生成参数呢？是在训练时，通过大量"上下文里有工具清单 + 用户需要外部信息 → 输出这段 JSON"的样本，训练模型学会生成参数。
# 又因为大模型是预测模型，所以他生成的参数不能直接信任，需要对生成结果根据你的工具的schema进行验证，验证json能否解析，字段是否对齐等。如果验证通过，就交给工具执行器执行，否则就返回给大模型重新生成。
# 那么一次工具调用走下来就是：模型生成参数 → 校验 → 执行 → 结果回灌 → 模型再推理。模型这一侧只负责生成，后面全是代码干的。
# 但真实的活很少一步就干完，这套动作得放到循环里反复跑。为什么需要循环？不是工具会失败要重试，而是任务本身就是多步的，下一步做什么靠模型看着结果自己定。
# 工具失败也能被循环吃掉，错误信息回灌给模型，它下一轮自己改参数、换方法。重试是模型的决定，不是执行器拿着同一份参数盲试三次。
# 循环什么时候停呢？正常的出口只有一个，模型不再请求工具调用，吐出最终答案。次数上限和用户中断只是兜底，防止跑飞。要注意"工具执行成功"也不算终止条件，它只是中间状态，执行器只知道自己这一步成了，不知道整个活干完没有。
# 一次 reply 可能带多个 tool_calls，并行执行，算同一轮，不消耗循环次数。
# 那么以上就是 Function Calling 的完整流程。回头看最上面那个最小示例，其实就是这套流程循环起来的样子，工具调用是里面的动作，循环是外面的壳。

# 接下来做一个最小的工具调用示例
import ast
import json
import operator
import os
from openai import OpenAI
# 定义工具
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

def calculate(expression: str) -> str:
    """计算数学表达式"""
    try:
        return str(_eval_node(ast.parse(expression, mode="eval").body))
    except Exception as e:
        return f"计算错误: {e}"

# 工具注册表，方便 Agent 根据名字找到对应的函数
TOOL_REGISTRY = {
    "get_weather": get_weather,
    "calculate": calculate
}

# 工具的 Schema 定义（告诉 LLM 它有什么工具可以用）
# function call schema
TOOLS_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "获取指定城市的天气",
            "parameters": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "calculate",
            "description": "计算数学表达式",
            "parameters": {"type": "object", "properties": {"expression": {"type": "string"}}, "required": ["expression"]}
        }
    }
]

api_key = os.getenv("DEEPSEEK_API_KEY")
if not api_key:
    raise RuntimeError("没读到 DEEPSEEK_API_KEY，先设置环境变量再跑")

client = OpenAI(
    api_key=api_key,
    base_url="https://api.deepseek.com"
)

def run_agent(user_prompt: str):
    # 创建对话消息历史
    messages = [
        {"role": "system", "content": "你是一个有用的助手，需要时请调用工具完成任务。"},
        {"role": "user", "content": user_prompt}
    ]
    # 设置最大循环次数
    max_iterations = 5  
    # 进入 Agent Loop
    for i in range(max_iterations):
        print(f"\n--- Agent Loop 第 {i+1} 轮 ---")
        
        response = client.chat.completions.create(
            model="deepseek-flash",  
            messages=messages,
            tools=TOOLS_SCHEMA,
            tool_choice="auto"
        )
        
        assistant_message = response.choices[0].message
        # 将 LLM 的回复加入历史
        messages.append(assistant_message)
        
        # 如果 LLM 没有调用工具，结束循环
        if not assistant_message.tool_calls:
            print(f"最终回答: {assistant_message.content}")
            return assistant_message.content
            
        # 如果调用了工具 
        for tool_call in assistant_message.tool_calls:
            func_name = tool_call.function.name

            # 第一道校验：参数能不能解析成 json
            # 解析失败也得回一条 tool 消息，tool_calls 和 tool 消息要配对 
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
            
            print(f"执行工具: {func_name}({func_args})")
            
            # 【行动】执行本地函数，模型给的参数可能对不上签名，这里兜一下
            if func_name not in TOOL_REGISTRY:
                observation = f"未知工具: {func_name}"
            else:
                try:
                    observation = str(TOOL_REGISTRY[func_name](**func_args))
                except Exception as e:
                    observation = f"工具执行失败: {e}"
                
            print(f"执行结果: {observation}")
            
            # 【观察】将工具结果追加到对话历史中
            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": observation
            })
            
    # 到上限还没收工，把最后一轮模型说的话带上 
    return f"Agent 达到最大循环次数，任务中断。最后一次回复: {assistant_message.content}"

if __name__ == "__main__":
    # 故意给一个无法计算的表达式，测试 LLM 的自我纠错
    run_agent("帮我查一下北京的天气，然后算一下 10 除以 0 等于多少？")