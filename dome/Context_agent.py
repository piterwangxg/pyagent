# 这里就是关于agent上下文管理的部分
# 首先要清楚agent上下文是什么？
# 通俗的讲就是单次调用大模型时真正发出去的东西。api 是无状态的，每次调用都是一次全新的请求，模型只认这一次请求里给它的内容。
# 那这一次请求里都装了什么？
# 首先是用户当前的输入，这是需求的来源，没有它模型不知道要干什么。
# 然后是我们写的系统提示词，包含角色、目标、边界、输出格式、安全规则等。这部分不是模型生成的，是我们希望它扮演什么、不许做什么，相当于我们自己加的约束。
# 当前环境也常写在这儿，比如时间、工作目录，不然模型不知道自己在哪跑、现在几点。
# 再往后是工具。可用工具和参数 schema 是我们注册进去的，模型本身不知道你有哪些函数，只能照着这份清单生成参数，所以只要还想让它调工具，清单就得每轮都在。
# 工具执行完的结果要回灌，这是工具执行器给的，不是模型自己想出来的。回灌要成对：带 tool_calls 的那条 assistant 消息得先留着，结果再按 tool_call_id 和它配对，只放结果模型对不上是哪次调用。
# 再就是历史。大模型只是一个预测模型，我们通过 api 调用，它不会记得上一次说了什么，所以上一轮的对话记录、工具调用记录、它自己的输出，都得由我们带上去。
# 历史里有一点要单独说：失败的尝试别原样堆着，模型下一轮容易照着再试一遍，得把失败原因收成一句结论再放进去。验证结果进不了上下文，就等于没验证。
# 除了对话，还得有外部资料。模型不知道你的私有数据，也不知道今天发生了什么，编出来的东西你还验不了，所以要靠联网搜索、RAG 召回片段、数据库记录、用户提供的文档，把这些查回来再放进去。
# 这些资料和工具结果一样，都是从外面拿来的，不可信。system 里得把指令和数据的边界划出来，否则文档里来一句“忽略上面的指令”，行为就被扭走了。
# 任务一复杂，模型自己也会往上下文里写东西：把任务拆成几步，记下已完成到哪儿、下一步做什么。这部分是模型产出的，我们只是给它留个位置。
# 子任务最好单独开一份上下文，只把结论带回主线，子过程里的噪音别让它进来。

# 又因为上下文窗口是有限的，前面列的这些东西会持续增长。
# 每跑一轮至少多一条 assistant 消息，调了工具还要再挂几条结果，历史只增不减；工具结果的体量不由我们定，一次返回好几万字很常见；外部资料塞多少全看检索给多少；模型自己写的计划和笔记也会越写越长。
# 所以上下文必须管。
# 那从哪儿下手呢？
# 不能一刀切，要看是不是不能碰的、是不是只能压不能改意思的、是不是可以丢的。
# 先说不能碰的。
# 用户当前这条输入基本原样留着，它是需求的唯一来源，里面往往还夹着约束，比如不要用哪个库、必须是什么格式，摘掉一句模型就做偏了。
# system 里的角色、边界、安全规则也归这档，本来就没几个字，省下来意义不大，删了模型的行为就飘了。环境信息差不多，旧的那份可以换掉，当前这份得在。
# 还有最近这一轮的东西，带 tool_calls 的 assistant 消息和对应的 tool 结果，模型还没消费完，删了它就不知道上一步发生了什么。而且这一对拆开请求直接报错，能不能动不由我们决定。
# 再说只能压不能改意思的。
# 历史首当其冲，可以摘要可以合并，但用户要什么、已经确认了什么、否掉了什么，这几样不能丢，压出来的得是有结构的结论，别随手摘两句。
# 工具结果也一样，大结果能截，但要按内容截不是按位置截。返回五百行日志，截前二十行很可能正好把报错截掉。稳一点的做法是把原文落盘，上下文里只留摘要加一句原文在哪个文件，需要细节再读回来。
# 外部资料靠压文本不划算，更好的办法是在检索那一步就少给几个高质量片段，而不是先塞十个再压成一个。
# system 和工具 schema 也能收，措辞可以短，但参数定义不能改，改了调用就对不上；用不上的工具干脆别挂，挂多了模型容易选错。
# 最后是可以丢的。
# 结论已经落到别处的工具结果，原文可以扔；被新计划取代的旧计划可以扔；重复的检索片段、失败的尝试原文也能扔，但扔之前得把结论固化，否则模型下一轮还会再试一遍。
# 判断落在一条线上，看这条信息能不能自己重建。能重新查、能重新算的，丢了代价小；有副作用的操作，比如发消息、下单、写文件，重跑等于又执行一遍，结果必须留在上下文里。
# 还有一类是用户没明说的偏好，比如他嫌啰嗦、只要结论，这种丢了就真没了，只能压不能丢。

# 那以上大致是上下文管理的内容
# 下面是一个简单的示例，展示如何管理agent上下文
# 我们以一个建筑助手为例，工具就三个：查规范、算荷载、读回落盘的原文
# 三个工具刚好把三档演一遍：system 不碰，规范原文落盘是压，旧轮次压成摘要、摘要只留最近几条是丢
# 首先定义系统提示词
system_prompt = """你是一个建筑结构助手，帮用户做初步方案和规范核对。

角色和目标：给结论、给依据、给条文编号，不替用户做最终设计决定。

边界：涉及结构安全的数字，必须来自工具返回的规范条文，查不到就直说查不到，不许凭经验编。
材料单价、地区限值这类会变的数据，只认工具结果，不认记忆。

输出格式：先给结论，再列依据，控制在几段以内，不要写成长篇报告。

工具说明：规范原文很长，超过上限会落盘，消息里只留开头结尾和路径，需要细节时调 read_blob 按行读回来。
"""

import json
import os

from openai import OpenAI

# ---------- 工具部分，跟前面 Function Calling 那个示例是一回事 ----------

# 模拟一个规范库，真项目里这里是向量检索
_CODE_DB = {
    "活荷载": """《建筑结构荷载规范》GB 50009-2012

第 5.1.1 条  住宅、宿舍、旅馆、办公楼、医院病房、托儿所、幼儿园的楼面均布活荷载标准值不应小于 2.0kN/m²；教室、实验室、阅览室、会议室不应小于 2.5kN/m²；礼堂、剧场、影院、看台不应小于 3.0kN/m²。

第 5.1.2 条  设计楼面梁、墙、柱及基础时，楼面活荷载标准值的折减系数应按下列规定采用：设计楼面梁时，第 5.1.1 条规定的楼面活荷载标准值大于 2.5kN/m² 的取 0.9，其他情况取 1.0。

第 5.2.1 条  屋面均布活荷载，不上人屋面不应小于 0.5kN/m²，上人屋面不应小于 2.0kN/m²，屋顶花园应按实际情况采用且不小于 3.0kN/m²。

第 5.3.1 条  施工检修荷载应按实际情况采用，一般取 1.0kN，屋面板、挑檐、雨篷等构件验算时取 1.5kN，集中在一个最不利位置。

第 5.4.1 条  栏杆水平荷载：住宅、宿舍、办公楼、学校等取 1.0kN/m；公共场所取 1.0kN/m 且不小于 1.5kN。

第 5.5.1 条  隔墙荷载应按实际情况采用，轻质隔墙可按 1.0kN/m² 取值，位置不确定时应按最不利布置计算。

第 6.1.1 条  永久荷载标准值应按结构自重计算，材料自重可按本规范附录 A 采用，对自重变异性较大的材料应按实际情况确定。

第 6.2.1 条  预应力荷载标准值应按张拉控制力计算，扣除各项预应力损失。

第 7.1.1 条  风荷载标准值应按下式计算：wk = βz·μs·μz·w0，其中基本风压 w0 应按当地 50 年一遇的数值采用。

第 7.2.1 条  风压高度变化系数应按地面粗糙度类别确定，A 类指近海海面和海岛、海岸、湖岸及沙漠地区，B 类指田野、乡村、丛林、丘陵以及房屋比较稀疏的乡镇。

第 8.1.1 条  雪荷载标准值应按下式计算：sk = μr·s0，屋面积雪分布系数应按屋面和屋面形式确定。

第 8.2.1 条  屋面雪荷载的不均匀分布应按本规范表 8.2.1 采用，天沟、雨篷等部位应考虑积雪堆积。

第 9.1.1 条  温度作用应按当地气候条件和结构形式确定，超长结构应设置温度缝或采取其他措施。

第 10.1.1 条  荷载组合应按承载力极限状态和正常使用极限状态分别计算，基本组合取永久荷载控制或可变荷载控制两者中的不利值。
""",
}


def search_code(query: str) -> str:
    """查建筑规范条文，返回原文"""
    for key, text in _CODE_DB.items():
        if key in query:
            return text
    return "没找到相关条文"


def calc_load(area: float, live_load: float = 2.0) -> str:
    """按面积和活荷载标准值估算总荷载"""
    return (
        f"面积 {area} 平，活荷载标准值 {live_load} kN/m²，"
        f"活荷载总量 {area * live_load:.1f} kN，"
        f"按 1.4 分项系数取设计值 {area * live_load * 1.4:.1f} kN"
    )


BLOB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "blobs")


def save_blob(call_id: str, text: str) -> str:
    """原文落盘，文件名用 tool_call_id，不会重名"""
    os.makedirs(BLOB_DIR, exist_ok=True)
    path = os.path.join(BLOB_DIR, f"{call_id}.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def read_blob(path: str, start: int = 1, end: int = 30) -> str:
    """按行读回落盘的原文。路径只认 blobs 目录，别的一律拒"""
    real = os.path.realpath(path)
    if not real.startswith(os.path.realpath(BLOB_DIR) + os.sep):
        return "只允许读 blobs 目录下的文件"
    with open(real, encoding="utf-8") as f:
        lines = f.readlines()
    end = min(end, len(lines))
    return f"（共 {len(lines)} 行，这次给第 {start} 到 {end} 行）\n" + "".join(lines[start - 1:end])


TOOLS_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "search_code",
            "description": "查建筑规范条文，返回原文",
            "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calc_load",
            "description": "按面积和活荷载标准值估算总荷载",
            "parameters": {
                "type": "object",
                "properties": {
                    "area": {"type": "number", "description": "面积，单位平米"},
                    "live_load": {"type": "number", "description": "活荷载标准值 kN/m²，默认 2.0"},
                },
                "required": ["area"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_blob",
            "description": "读回落盘的工具结果原文，按行取，一次别读太多",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "落盘路径"},
                    "start": {"type": "integer", "description": "起始行，从 1 开始"},
                    "end": {"type": "integer", "description": "结束行，包含"},
                },
                "required": ["path"],
            },
        },
    },
]

TOOL_FUNCS = {"search_code": search_code, "calc_load": calc_load, "read_blob": read_blob}

# ---------- 下面才是这个文件的正题，上下文管理 ----------


def shrink_result(call_id: str, text: str, limit: int = 600) -> str:
    """只能压不能改意思的那一档。

    超限的原文落盘，上下文里只留头尾和路径。头尾都留是有意的，
    很多文档的结论在末尾，只截前面正好把结论截掉。
    规范这种按条分的内容，真项目里按条切更准，这里不知道结构只能折中。
    """
    if len(text) <= limit:
        return text
    path = save_blob(call_id, text)
    # 开头放最前面，因为后面压成摘要时只取开头几十个字，
    # 要是先写“已落盘 + 路径”，条文号就被挤掉了
    return (
        f"开头：{text[:300]}\n"
        f"……\n"
        f"结尾：{text[-200:]}\n"
        f"原文共 {len(text)} 字，超过单条上限，已落盘：{path}\n"
        f"要看中间部分，调 read_blob 带上这个路径"
    )


def to_dict(m) -> dict:
    """统一成 dict，后面裁剪才方便改"""
    if isinstance(m, dict):
        return m
    d = {"role": m.role, "content": m.content or ""}
    if getattr(m, "tool_calls", None):
        d["tool_calls"] = [tc.model_dump() for tc in m.tool_calls]
    return d


def turn_chars(turn: list) -> int:
    return sum(len(json.dumps(m, ensure_ascii=False)) for m in turn)


def summarize_turn(turn: list) -> str:
    """把一整轮压成一句话。

    示例用规则拼，真项目里这一步大多是让模型总结，但规矩一样：
    用户要什么、做了什么、结论是什么，这三样得留住，否掉的方案也要提一句。
    """
    parts = []
    for m in turn:
        text = (m.get("content") or "").replace("\n", " ")
        if m["role"] == "user":
            parts.append(f"用户问：{text[:60]}")
        elif m["role"] == "assistant" and text:
            parts.append(f"答：{text[:60]}")
        elif m["role"] == "tool":
            parts.append(f"工具结果：{text[:40]}")
    return "；".join(parts)


def build_context(turns: list, budget: int = 1200) -> list:
    """组装上下文。从最老的一轮开始压，压到装得下为止，最后一轮永远不压。

    1200 是示例值，真实项目里按模型窗口的六七成算，再留出回复的余量。
    按轮压的好处是 assistant 的 tool_calls 和它的 tool 结果天然在一起，
    不会出现只留一边的调用，那个不是取舍问题，是请求直接报错。
    """
    turns = list(turns)
    summary = []
    while len(turns) > 1 and sum(turn_chars(t) for t in turns) > budget:
        summary.append(summarize_turn(turns.pop(0)))

    # 摘要自己也会长，只留最近几条，更老的让它走
    if len(summary) > 6:
        summary = summary[-6:]

    system = system_prompt
    if summary:
        system += "\n\n之前几轮已经处理过的：\n" + "\n".join(summary)

    out = [{"role": "system", "content": system}]
    for t in turns:
        out.extend(t)
    return out


# api_key = os.getenv("DEEPSEEK_API_KEY")
# if not api_key:
#     raise RuntimeError("没读到 DEEPSEEK_API_KEY，先设置环境变量再跑")
api_key = "sk-90a43801ee0c4b4baf72d95bdfc7fa33"
client = OpenAI(api_key=api_key, base_url="https://api.deepseek.com")


def ask(turns: list, user_prompt: str, max_iterations: int = 6):
    # 历史按轮存，一轮一个列表。这样 assistant(tool_calls) 和它的 tool 结果
    # 天然在同一个列表里，裁剪时整轮一起处理，不用再检查配对
    turns.append([{"role": "user", "content": user_prompt}])

    for i in range(max_iterations):
        messages = build_context(turns)
        size = sum(len(json.dumps(m, ensure_ascii=False)) for m in messages)
        print(f"\n--- 第 {i + 1} 轮，上下文 {size} 字 ---")

        resp = client.chat.completions.create(
            model="deepseek-flash",
            messages=messages,
            tools=TOOLS_SCHEMA,
        )
        msg = to_dict(resp.choices[0].message)
        turns[-1].append(msg)

        if not msg.get("tool_calls"):
            print(f"最终回答: {msg.get('content')}")
            return

        for tc in msg["tool_calls"]:
            name = tc["function"]["name"]
            ok = True
            try:
                args = json.loads(tc["function"].get("arguments") or "{}")
            except json.JSONDecodeError as e:
                ok, observation = False, f"参数不是合法的 JSON: {e}"
            if ok:
                fn = TOOL_FUNCS.get(name)
                if fn is None:
                    ok, observation = False, f"未知工具: {name}"
                else:
                    try:
                        observation = str(fn(**args))
                    except Exception as e:
                        ok, observation = False, f"工具执行失败: {e}"

            # 大结果在回灌这一刻就压，别等后面统一处理，那时候原文已经在上下文里待过一圈了
            if ok:
                observation = shrink_result(tc["id"], observation)

            print(f"工具 {name} 回灌 {len(observation)} 字")
            turns[-1].append({"role": "tool", "tool_call_id": tc["id"], "content": observation})

    print("到了循环上限还没收工")


if __name__ == "__main__":
    # 一次会话里连着问两个，第二个问题引用了第一个的结论，
    # 预算调得很小，第一轮必然被压成摘要，正好看看压完还记不记得住关键信息
    session = []
    ask(session, "查一下住宅楼面活荷载的标准值，然后按 120 平算一下总荷载")
    ask(session, "刚才那个活荷载是哪本规范哪一条？再按 150 平算一遍")
    print("\n落盘文件在:", BLOB_DIR)






