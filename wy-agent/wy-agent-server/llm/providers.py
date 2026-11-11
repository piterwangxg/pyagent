"""内置供应商实现。

新增一个「OpenAI 兼容」的供应商：照抄下面任意一个类，改 4 个类属性即可，
不需要动 registry、main.py 或前端（下拉框是从 /api/providers 动态取的）。

新增一个「非 OpenAI 协议」的供应商：继承 BaseChatModel，实现 _setup() 和 chat()，
同样打上 @register，然后在本包的 __init__.py 里 import 一下即可。
"""

from __future__ import annotations

from .base import OpenAICompatModel
from .registry import register


@register
class CustomOpenAICompatModel(OpenAICompatModel):
    """自定义接入点：base_url 和模型名都必须由用户填写。"""

    provider = "custom"
    label = "自定义（OpenAI 兼容）"
    default_base_url = ""
    default_model = ""


@register
class DeepSeekModel(OpenAICompatModel):
    provider = "deepseek"
    label = "DeepSeek"
    default_base_url = "https://api.deepseek.com/v1"
    default_model = "deepseek-chat"


@register
class OpenAIModel(OpenAICompatModel):
    provider = "openai"
    label = "OpenAI"
    default_base_url = "https://api.openai.com/v1"
    default_model = "gpt-4o-mini"


@register
class MoonshotModel(OpenAICompatModel):
    provider = "moonshot"
    label = "Moonshot"
    default_base_url = "https://api.moonshot.cn/v1"
    default_model = "moonshot-v1-8k"


@register
class QwenModel(OpenAICompatModel):
    provider = "qwen"
    label = "通义千问"
    default_base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    default_model = "qwen-plus"
