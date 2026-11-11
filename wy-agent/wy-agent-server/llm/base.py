"""模型接口基类：所有供应商实现都继承它。

约定三件事：
1. 类属性声明 provider / label / 默认 base_url / 默认模型名；
2. __init__ 负责把「供应商 + 模型名 + key」归一化并校验；
3. 子类只需要实现 chat()。

要做非 OpenAI 协议的供应商（如原生 Anthropic、Gemini、本地 runtime），
继承本类重写 _setup() 与 chat() 即可，上层调用方式不变。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from time import perf_counter
from typing import Any, Iterator, Optional, Sequence


class LLMError(RuntimeError):
    """模型调用失败。message 直接可以给界面展示。"""


class BaseChatModel(ABC):
    # 注册表用的小写标识，也是数据库里 provider 字段的取值
    provider: str = ""
    # 界面上展示的名字
    label: str = ""
    # 协议类型，方便前端/日志区分；openai_compat 表示走 OpenAI 兼容协议
    kind: str = "native"
    # 供应商默认接入点与默认模型，两者都允许调用方覆盖
    default_base_url: str = ""
    default_model: str = ""

    def __init__(
        self,
        model: str = "",
        api_key: str = "",
        *,
        base_url: str = "",
        temperature: float = 0.7,
        max_tokens: int = 2048,
        timeout: float = 60.0,
    ) -> None:
        self.model = (model or "").strip() or self.default_model
        self.base_url = (base_url or "").strip() or self.default_base_url
        self.api_key = (api_key or "").strip()
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout

        self._validate()
        self._setup()

    # ---------------- 子类可覆盖的钩子 ----------------

    def _validate(self) -> None:
        """基础校验，特殊供应商可以覆盖后追加自己的规则。"""
        if not self.api_key:
            raise ValueError("缺少 API Key")
        if not self.base_url:
            raise ValueError(f"供应商 {self.provider} 没有内置 Base URL，请手动填写")
        if not self.model:
            raise ValueError("缺少模型名称")

    def _setup(self) -> None:
        """初始化 client 等重资源。默认什么都不做。"""

    # ---------------- 统一对外能力 ----------------

    @abstractmethod
    def chat(
        self,
        messages: Sequence[dict[str, Any]],
        *,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> str:
        """多轮对话，返回 assistant 的纯文本。"""

    def complete(self, prompt: str, *, system: Optional[str] = None) -> str:
        """单轮问答的便捷写法。"""
        messages: list[dict[str, Any]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        return self.chat(messages)

    def stream(self, prompt: str, *, system: Optional[str] = None) -> Iterator[str]:
        """单轮流式问答：用法同 complete()，只是把结果拆成多段吐出来。"""
        messages: list[dict[str, Any]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        return self.stream_chat(messages)

    def stream_chat(
        self,
        messages: Sequence[dict[str, Any]],
        *,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> Iterator[str]:
        """多轮流式对话。默认退化成一次性返回，子类可覆盖成真流式。"""
        yield self.chat(messages, temperature=temperature, max_tokens=max_tokens)

    def test(self) -> dict[str, Any]:
        """连通性自检：发一条最短请求，返回耗时和回复。"""
        started = perf_counter()
        reply = self.complete("ping", system="只回复 pong")
        elapsed = round((perf_counter() - started) * 1000)
        return {"ok": True, "latency_ms": elapsed, "reply": reply[:80]}

    def describe(self) -> dict[str, Any]:
        """给前端/日志用的元信息，刻意不含 api_key。"""
        return {
            "provider": self.provider,
            "label": self.label,
            "kind": self.kind,
            "model": self.model,
            "base_url": self.base_url,
        }

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(provider={self.provider!r}, "
            f"model={self.model!r}, base_url={self.base_url!r})"
        )


class OpenAICompatModel(BaseChatModel):
    """OpenAI 兼容协议的公共实现（DeepSeek / Moonshot / 通义 等都属于这一类）。

    具体供应商只需继承它并填 4 个类属性，不必重复写 chat()。
    """

    kind = "openai_compat"

    def _setup(self) -> None:
        try:
            from openai import OpenAI
        except ImportError as exc:  # 依赖缺失时只在使用模型时报错
            raise LLMError(
                "缺少 openai 依赖，请先执行 pip install -r requirements.txt"
            ) from exc

        self._client = OpenAI(
            api_key=self.api_key, base_url=self.base_url, timeout=self.timeout
        )

    def chat(
        self,
        messages: Sequence[dict[str, Any]],
        *,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> str:
        try:
            resp = self._client.chat.completions.create(
                model=self.model,
                messages=list(messages),
                temperature=self.temperature if temperature is None else temperature,
                max_tokens=self.max_tokens if max_tokens is None else max_tokens,
            )
        except Exception as exc:
            raise LLMError(_friendly_message(exc, self.base_url)) from exc

        if not resp.choices:
            return ""
        return (resp.choices[0].message.content or "").strip()

    def stream_chat(
        self,
        messages: Sequence[dict[str, Any]],
        *,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> Iterator[str]:
        """真流式：逐段返回 delta 文本，取完后生成器自然结束。"""
        try:
            stream = self._client.chat.completions.create(
                model=self.model,
                messages=list(messages),
                temperature=self.temperature if temperature is None else temperature,
                max_tokens=self.max_tokens if max_tokens is None else max_tokens,
                stream=True,
            )
        except Exception as exc:
            raise LLMError(_friendly_message(exc, self.base_url)) from exc

        try:
            for chunk in stream:
                if not chunk.choices:
                    continue
                text = getattr(chunk.choices[0].delta, "content", None)
                if text:
                    yield text
        except Exception as exc:
            # 吐到一半断流，同样翻译成界面能看懂的提示
            raise LLMError(_friendly_message(exc, self.base_url)) from exc


def _friendly_message(exc: Exception, base_url: str) -> str:
    """把 SDK 异常翻译成能直接展示给用户的中文。按类名判断，避免强依赖 openai 异常类。"""
    name = exc.__class__.__name__
    if name == "AuthenticationError":
        return "API Key 无效或已过期"
    if name in ("APIConnectionError", "APITimeoutError"):
        return f"无法连接 {base_url}，请检查网络或代理"
    if name == "RateLimitError":
        return "触发限流或额度不足，请稍后再试"
    if name == "NotFoundError":
        return "模型名或 Base URL 不正确（404）"
    if name == "BadRequestError":
        return f"请求被拒绝：{exc}"
    return f"{name}: {exc}"
