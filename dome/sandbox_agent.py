# 这里介绍一个重要的组件-沙箱
# 沙箱是为了将一些危险操作隔离
#
# 前面的示例里，工具都是我们自己写的，参数再离谱，最坏也就是算错一个数。真实 agent 常常要“执行代码”“跑命令”，输入来自模型，
# 而模型的话不能当可信输入看：它会写错路径、写出死循环，也可能被网页、文档里夹的注入指令带着走。
# 这类操作有个共同点：不可逆，而且权限和 agent 本身一样大。发消息、下单、删文件都算，重跑一次不等于没发生。
# 校验参数格式挡得住“调错”，挡不住“这段代码要删你的盘”，所以边界得落在代码里：给什么权限、能跑多久、能碰哪些路径，都由外面决定。
# 这就是沙箱。它不提高模型答对的概率，只保证模型犯的错不波及外面；对错还是靠返回的那点信号，退出码、stdout、异常栈。
#
# 一个沙箱大致管四件事：
# 权限——能读哪些目录、能不能联网、能起哪些命令，默认全不给，按需开。
# 资源——跑多久、占多少内存、输出多长，死循环和“打印一百万行”都得能掐掉。
# 生命周期——一次调用一个干净目录，跑完就删，上一轮的残留不影响这一轮。
# 结果——只把受限的、可判定的一段文本带回来，带不回来的不算结果。
#
# 隔离强度是分层的，按“要防谁”来选：
# 进程级（本示例）——subprocess 起独立进程，限目录、限时间、限输出。防的是写错和手滑，不是铁了心的攻击者，懂行的人绕得过去。
# 容器级——Docker 之类，只读挂载、断网、非 root，能挡大部分越权，代价是要有镜像和运行时。
# 微虚拟机——Firecracker、gVisor，一个任务一台“机器”，隔离最硬，启动也是百毫秒级，云上的代码解释器基本这条路。
# 托管服务——E2B 这类把上面封成 API，最省事，但数据要出门。
#
# 本示例选进程级，因为零依赖、跑得起，该有的几个开关还能都演一遍：
# 静态先扫源码（import 白名单，拦 eval、open 这类名字），运行层再套一层（空目录、超时、最小环境变量、输出截断）。
# 静态那层只挡低级错误，黑名单永远有漏，真正的边界在子进程和容器那层，别把它当成安全承诺。
#
# 还有一个问题：为什么不用 exec() 就地跑？
# 同进程的代码共享内存、文件和网络，权限就是 agent 自己的权限；跑飞了也没法收场，一个 while True 能把 agent 自己卡死。
# 进程边界是最小可用的隔离边界，有了它，超时才有东西可杀，权限才有地方收窄。eval 那条线也是同一个道理，永远别拿它执行模型给的字符串。

import ast
import json
import os
import shutil
import subprocess
import sys
import tempfile

from openai import OpenAI

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SANDBOX_ROOT = os.path.join(BASE_DIR, "sandbox_runs")

# 允许 import 的只有纯计算模块，不碰系统、不碰网络、不碰进程
ALLOWED_MODULES = {"math", "statistics", "decimal", "fractions", "itertools", "collections", "re", "json"}

# 这些名字在沙箱里没有正当用途，见到就拒。open 被禁掉是有意的：写文件能力整个先不给，
# 想保留读写就得靠容器做只读挂载和配额，靠字符串黑名单是看不住的
BANNED_NAMES = {"eval", "exec", "compile", "open", "__import__", "input",
                "globals", "locals", "vars", "getattr", "setattr", "delattr",
                "breakpoint", "exit", "quit"}

TIMEOUT = 3        # 秒，示例值。真项目按任务类型给，别一个数打天下
MAX_OUTPUT = 2000  # 字符，stdout 和 stderr 各截各的


def check_source(code: str) -> str:
    """静态检查。放行返回空串，否则返回拒绝原因。

    走 AST 是为了分清“注释里提了一句 import os”和“真的 import 了 os”，字符串匹配做不到这一点。
    反过来，它也只能看源码长什么样：字符串拼出来的调用它看不见，所以 __import__ 这类名字要一并禁掉。
    """
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        # 语法错也走拒绝，错误原文回给模型，它下一轮自己改
        return f"语法错误: {e}"

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] not in ALLOWED_MODULES:
                    return f"不允许 import {alias.name}"
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                return "不允许相对导入"
            if (node.module or "").split(".")[0] not in ALLOWED_MODULES:
                return f"不允许 import {node.module}"
        elif isinstance(node, ast.Name) and node.id in BANNED_NAMES:
            return f"不允许使用 {node.id}"
        elif isinstance(node, ast.Attribute) and node.attr.startswith("__") and node.attr.endswith("__"):
            # 双下划线一路连着类和函数，是绕过名字检查最常用的口子
            return f"不允许访问 {node.attr}"
    return ""


def _cut(text: str) -> str:
    """输出也要限长。打印一百万行同样是攻击，缓冲区撑爆之前就得截断"""
    text = text.strip()
    if not text:
        return "(空)"
    if len(text) <= MAX_OUTPUT:
        return text
    return text[:MAX_OUTPUT] + f"\n……（超过 {MAX_OUTPUT} 字，后面截掉了）"


def run_python(code: str) -> str:
    """工具本体：先查，再进子进程跑，最后把可判定的结果带回来。

    返回的这段文本就是观察，模型下一轮只能看到它。所以退出码、stdout、stderr 三样都得给，
    少了 stderr，模型只知道失败了不知道错在哪，循环就退化成瞎试。
    """
    reason = check_source(code)
    if reason:
        # 拦下来也要回一条结果，不然模型对不上这次调用，请求直接报错
        return f"沙箱拒绝执行: {reason}"

    os.makedirs(SANDBOX_ROOT, exist_ok=True)
    # 一次调用一个空目录。同一次任务里也彼此隔离，上一步写坏的东西不会污染这一步
    workdir = tempfile.mkdtemp(prefix="run_", dir=SANDBOX_ROOT)
    try:
        # -I 隔离模式：不读 PYTHON* 环境变量、不加用户 site-packages、cwd 不进 sys.path
        # -X utf8 让子进程的输入输出按 utf-8 走，Windows 上默认是 gbk，中文会乱码
        proc = subprocess.run(
            [sys.executable, "-I", "-X", "utf8", "-c", code],
            cwd=workdir,
            # 环境变量也是通道，只给最小的一套；TEMP 指到运行目录，子进程写的临时文件也落在这
            env={"SYSTEMROOT": os.environ.get("SYSTEMROOT", ""), "TEMP": workdir, "TMP": workdir},
            capture_output=True,
            timeout=TIMEOUT,
        )
        out = proc.stdout.decode("utf-8", "replace")
        err = proc.stderr.decode("utf-8", "replace")
        return f"退出码: {proc.returncode}\nstdout:\n{_cut(out)}\nstderr:\n{_cut(err)}"
    except subprocess.TimeoutExpired:
        # 超时是真杀进程，不是不理会让它继续跑。没有这一步，一个 while True 就能把 agent 卡死
        return f"沙箱超时: 超过 {TIMEOUT} 秒，进程已终止"
    except Exception as e:
        return f"沙箱异常: {e}"
    finally:
        # 生命周期收尾。目录里本来就不该有东西，删掉是为了不留下任何跨轮的残留
        shutil.rmtree(workdir, ignore_errors=True)


TOOLS_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "run_python",
            "description": "在沙箱里执行一段 Python 代码，返回退出码和输出。不能读写文件、不能联网",
            "parameters": {
                "type": "object",
                "properties": {"code": {"type": "string", "description": "完整的 Python 源码"}},
                "required": ["code"],
            },
        },
    }
]

TOOL_REGISTRY = {"run_python": run_python}

api_key = os.getenv("DEEPSEEK_API_KEY")
if not api_key:
    raise RuntimeError("没读到 DEEPSEEK_API_KEY，先设置环境变量再跑")

client = OpenAI(api_key=api_key, base_url="https://api.deepseek.com")


def run_agent(user_prompt: str):
    messages = [
        {
            "role": "system",
            # 系统提示里把工具的边界说清楚，模型少写不必要的越界代码，但别指望它自觉，边界在代码里
            "content": "你是一个会写代码解决问题的助手。需要计算就写 Python 交给 run_python 执行，不要凭记忆给数字。"
                       "工具报错时看 stderr 改代码再跑，别重复提交同一段。",
        },
        {"role": "user", "content": user_prompt},
    ]
    max_iterations = 6

    for i in range(max_iterations):
        print(f"\n--- Agent Loop 第 {i+1} 轮 ---")

        response = client.chat.completions.create(
            model="deepseek-flash",
            messages=messages,
            tools=TOOLS_SCHEMA,
            tool_choice="auto",
        )

        assistant_message = response.choices[0].message
        # 带 tool_calls 的这条必须先回填，后面 tool 消息才能按 id 对上
        messages.append(assistant_message)

        if not assistant_message.tool_calls:
            print(f"最终回答: {assistant_message.content}")
            return assistant_message.content

        for tool_call in assistant_message.tool_calls:
            func_name = tool_call.function.name

            try:
                func_args = json.loads(tool_call.function.arguments or "{}")
            except json.JSONDecodeError as e:
                observation = f"参数不是合法的 JSON: {e}"
                print(f"参数校验失败: {observation}")
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": observation,
                })
                continue

            print(f"执行工具: {func_name}({str(func_args)[:60]}...)")

            fn = TOOL_REGISTRY.get(func_name)
            if fn is None:
                observation = f"未知工具: {func_name}"
            else:
                try:
                    observation = str(fn(**func_args))
                except TypeError as e:
                    observation = f"工具参数不匹配: {e}"
                except Exception as e:
                    observation = f"工具执行失败: {e}"

            # 只把摘要打进终端，完整输出走工具结果，跟上下文里看到的一致
            print(f"执行结果: {observation[:200]}")

            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": observation,
            })

    return "Agent 达到最大循环次数，任务中断。"


if __name__ == "__main__":
    # 先绕开模型，直接拿三段代码试边界：正常、静态拒绝、超时。这段是确定性的，跑一次就知道沙箱在不在
    print("【1】正常代码")
    print(run_python("print(sum(x * x for x in range(10)))"))
    print("\n【2】越权代码")
    print(run_python("import os\nprint(os.listdir('.'))"))
    print("\n【3】死循环")
    print(run_python("while True:\n    pass"))

    # 再让 agent 用这个沙箱干活。要算的东西自己写不出准确值，逼它走代码这条路
    print("\n========== 下面交给 agent ==========")
    run_agent(
        "帮我算三件事：1 到 500 之间有多少个素数；2**200 的十进制一共有多少位；"
        "再用蒙特卡洛撒 10 万个点估一下 π。都要给出代码算出来的结果。"
    )
