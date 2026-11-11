"""供应商注册表与工厂。

新增一个供应商 = 新建一个类 + 打上 @register，其他代码零改动。
"""

from __future__ import annotations

from typing import Any, Mapping

from .base import BaseChatModel

_REGISTRY: dict[str, type[BaseChatModel]] = {}


def register(cls: type[BaseChatModel]) -> type[BaseChatModel]:
    """类装饰器：把供应商实现登记进注册表。"""
    key = (cls.provider or "").strip().lower()
    if not key:
        raise ValueError(f"{cls.__name__} 缺少 provider 类属性，无法注册")

    existing = _REGISTRY.get(key)
    if existing is not None and existing is not cls:
        raise ValueError(f"供应商 {key!r} 已被 {existing.__name__} 注册，不能重复")

    _REGISTRY[key] = cls
    return cls


def available_providers() -> list[dict[str, Any]]:
    """给前端下拉框用：列出所有已注册供应商及其默认项。"""
    items = [
        {
            "provider": key,
            "label": cls.label or key,
            "kind": cls.kind,
            "base_url": cls.default_base_url,
            "default_model": cls.default_model,
        }
        for key, cls in sorted(_REGISTRY.items())
    ]
    # custom 放最后，符合"先选常见供应商"的使用习惯
    items.sort(key=lambda item: item["provider"] == "custom")
    return items


def get_model_class(provider: str) -> type[BaseChatModel]:
    key = (provider or "").strip().lower()
    cls = _REGISTRY.get(key)
    if cls is None:
        options = ", ".join(sorted(_REGISTRY)) or "无"
        raise ValueError(f"未注册的供应商 {provider!r}；已注册：{options}")
    return cls


def create_model(
    provider: str,
    model: str = "",
    api_key: str = "",
    **kwargs: Any,
) -> BaseChatModel:
    """按供应商名创建模型实例，签名与「供应商 + 模型名 + key」一致。"""
    return get_model_class(provider)(
        model=model, api_key=api_key, **kwargs
    )


def create_from_row(row: Mapping[str, Any]) -> BaseChatModel:
    """从 models 表的一行记录创建实例。"""
    return create_model(
        provider=row["provider"],
        model=row["model"],
        api_key=row["api_key"],
        base_url=row["base_url"],
        temperature=float(row["temperature"]),
        max_tokens=int(row["max_tokens"]),
    )
