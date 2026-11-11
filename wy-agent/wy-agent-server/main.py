"""wy-agent-server：桌面端/移动端共用的后端服务。

模型配置存在本地 SQLite（data/app.db），API Key 明文入库，
但所有对外响应只返回掩码，不返回明文。
"""

import json
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

import db
from llm import LLMError, available_providers, create_from_row

app = FastAPI(title="wy-agent-server", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# 建表动作是幂等的，随进程启动执行
db.init_db()


# ---------------------------------------------------------------- schemas


class ModelIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    provider: str = Field(default="openai", max_length=32)
    base_url: str = Field(default="", max_length=512)
    api_key: str = Field(default="", max_length=512)
    model: str = Field(default="", max_length=128)
    temperature: float = Field(default=0.7, ge=0, le=2)
    max_tokens: int = Field(default=2048, ge=1, le=200_000)


class ChatRequest(BaseModel):
    message: str
    model_id: Optional[int] = None


# ---------------------------------------------------------------- helpers


def mask_key(key: str) -> str:
    """只暴露首尾少量字符，其余打码。"""
    if not key:
        return ""
    if len(key) <= 8:
        return "****"
    return f"{key[:3]}****{key[-4:]}"


def serialize(row: dict) -> dict:
    """数据库行 -> 对外响应（api_key 一律脱敏）。"""
    return {
        "id": row["id"],
        "name": row["name"],
        "provider": row["provider"],
        "base_url": row["base_url"],
        "model": row["model"],
        "temperature": row["temperature"],
        "max_tokens": row["max_tokens"],
        "is_active": bool(row["is_active"]),
        "has_api_key": bool(row["api_key"]),
        "api_key": mask_key(row["api_key"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def to_columns(payload: ModelIn) -> dict:
    return payload.model_dump()


def resolve_target(model_id: Optional[int]) -> Optional[dict]:
    """指定了 id 就取那一条，否则取当前启用的一条；指定了但不存在的 id 直接 404。"""
    if model_id is not None:
        target = db.get_model(model_id)
        if target is None:
            raise HTTPException(status_code=404, detail="指定的模型配置不存在")
        return target
    return db.get_active_model()


# ---------------------------------------------------------------- health


@app.get("/health")
def health():
    return {"status": "ok", "db": str(db.DB_PATH)}


# ---------------------------------------------------------------- providers


@app.get("/api/providers")
def list_providers():
    """已注册的供应商列表，前端下拉框直接用它渲染。"""
    return {"items": available_providers()}


# ---------------------------------------------------------------- models


@app.get("/api/models")
def list_models():
    rows = db.list_models()
    items = [serialize(r) for r in rows]
    active = next((i for i in items if i["is_active"]), None)
    return {"items": items, "active_id": active["id"] if active else None}


@app.post("/api/models", status_code=201)
def create_model(payload: ModelIn):
    row = db.create_model(to_columns(payload))
    return serialize(row)


@app.put("/api/models/{model_id}")
def update_model(model_id: int, payload: ModelIn):
    # api_key 传空字符串表示不修改原密钥
    row = db.update_model(model_id, to_columns(payload))
    if row is None:
        raise HTTPException(status_code=404, detail="模型配置不存在")
    return serialize(row)


@app.delete("/api/models/{model_id}")
def delete_model(model_id: int):
    if not db.delete_model(model_id):
        raise HTTPException(status_code=404, detail="模型配置不存在")
    return {"deleted": model_id}


@app.post("/api/models/{model_id}/activate")
def activate_model(model_id: int):
    row = db.activate_model(model_id)
    if row is None:
        raise HTTPException(status_code=404, detail="模型配置不存在")
    return serialize(row)


@app.post("/api/models/{model_id}/test")
def test_model(model_id: int):
    """用保存好的配置真实发一条最短请求，用来验证 Key/Base URL/模型名。"""
    row = db.get_model(model_id)
    if row is None:
        raise HTTPException(status_code=404, detail="模型配置不存在")
    try:
        return create_from_row(row).test()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LLMError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


# ---------------------------------------------------------------- chat


@app.post("/agent/chat")
def chat(req: ChatRequest):
    target = resolve_target(req.model_id)

    if target is None:
        return {
            "reply": "尚未启用任何模型配置，先去「模型配置」里添加并启用一个。",
            "model": None,
        }

    try:
        client = create_from_row(target)
        reply = client.complete(req.message)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LLMError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return {
        "reply": reply,
        "model": {"id": target["id"], "name": target["name"], "model": target["model"]},
    }


# ---------------------------------------------------------------- chat (stream)

# 关掉中间层缓冲，否则增量会被攢成一整块才到前端
STREAM_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
}


def sse(event: str, data: dict) -> str:
    """一条 SSE 帧：event 决定前端怎么处理，data 统一是 JSON。"""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@app.post("/agent/chat/stream")
def chat_stream(req: ChatRequest):
    """参数与 /agent/chat 一致，但用 SSE 逐段返回，前端边收边渲染。"""
    target = resolve_target(req.model_id)

    if target is None:
        hint = "尚未启用任何模型配置，先去「模型配置」里添加并启用一个。"

        def empty():
            yield sse("delta", {"text": hint})
            yield sse("done", {"model": None})

        return StreamingResponse(
            empty(), media_type="text/event-stream", headers=STREAM_HEADERS
        )

    # client 在开始吐数据前建好，配置类错误还能正常返回 400 / 502
    try:
        client = create_from_row(target)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LLMError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    model_info = {"id": target["id"], "name": target["name"], "model": target["model"]}

    def generate():
        try:
            for piece in client.stream_chat([{"role": "user", "content": req.message}]):
                yield sse("delta", {"text": piece})
        except LLMError as exc:
            yield sse("error", {"message": str(exc)})
            return
        except Exception as exc:  # 兜底，不然前端会一直等下去
            yield sse("error", {"message": f"{type(exc).__name__}: {exc}"})
            return
        yield sse("done", {"model": model_info})

    return StreamingResponse(
        generate(), media_type="text/event-stream", headers=STREAM_HEADERS
    )
