"""模型层对外入口。

分层：
    base.py       BaseChatModel 抽象 + OpenAI 兼容实现 + LLMError
    registry.py   注册表（@register）与工厂（create_model / create_from_row）
    providers.py  内置供应商，一个类一个供应商

上层只用 create_model / create_from_row，不关心具体供应商。
"""

from __future__ import annotations

from .base import BaseChatModel, LLMError, OpenAICompatModel
from .registry import (
    available_providers,
    create_from_row,
    create_model,
    get_model_class,
    register,
)

# 副作用导入：触发内置供应商注册，删掉会让 /api/providers 返回空列表
from . import providers

__all__ = [
    "BaseChatModel",
    "LLMError",
    "OpenAICompatModel",
    "available_providers",
    "create_from_row",
    "create_model",
    "get_model_class",
    "register",
]
