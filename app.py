"""Local-only FastAPI application. Run via start.bat or python launch.py."""
import asyncio
import json
import secrets
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ollama_manager import OllamaError, OllamaManager

ROOT = Path(__file__).parent
app = FastAPI(title="ollama-web", docs_url=None, redoc_url=None, openapi_url=None)
manager = OllamaManager()
TOKEN = secrets.token_urlsafe(32)


@app.middleware("http")
async def local_only(request: Request, call_next):
    # Host validation prevents DNS rebinding. No CORS access is enabled.
    host = request.headers.get("host", "")
    if host not in {"127.0.0.1:8080", "localhost:8080"}:
        return JSONResponse({"detail": "Use http://127.0.0.1:8080"}, status_code=403)
    if request.client and request.client.host not in {"127.0.0.1", "::1", "testclient"}:
        return JSONResponse({"detail": "Local connections only."}, status_code=403)
    origin = request.headers.get("origin")
    if origin and origin != f"http://{host}":
        return JSONResponse({"detail": "Cross-origin request blocked."}, status_code=403)
    if request.method not in {"GET", "HEAD"}:
        if not secrets.compare_digest(request.headers.get("x-ollama-web-token", ""), TOKEN):
            return JSONResponse({"detail": "Session expired. Refresh this page."}, status_code=403)
        try:
            length = int(request.headers.get("content-length", "0"))
        except ValueError:
            return JSONResponse({"detail": "Invalid request length."}, status_code=400)
        if length > 2_000_000:
            return JSONResponse({"detail": "Conversation is too large. Start a new chat."}, status_code=413)
    response = await call_next(request)
    response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"
    return response


@app.exception_handler(OllamaError)
async def ollama_error(request, exc):
    return JSONResponse({"detail": str(exc)}, status_code=503)


class ModelName(BaseModel):
    model: str = Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$")


class ConfirmModel(ModelName):
    confirm: Literal[True]


class Message(BaseModel):
    role: Literal["user", "assistant", "system"]
    content: str = Field(max_length=100_000)


class Chat(ModelName):
    messages: list[Message] = Field(min_length=1, max_length=200)


class Control(BaseModel):
    action: Literal["start", "stop", "restart"]
    confirm: bool = False


def cloud_model(model):
    name = model.get("name", "").lower()
    return bool(model.get("remote_host") or model.get("remote_model") or name.endswith(":cloud") or "-cloud" in name)


@app.get("/", response_class=HTMLResponse)
async def index():
    return (ROOT / "templates/index.html").read_text(encoding="utf-8").replace("__TOKEN__", TOKEN)


@app.get("/api/status")
async def status():
    version, system = await asyncio.gather(manager.alive(), asyncio.to_thread(manager.system))
    return {"running": version is not None, "version": (version or {}).get("version"), **system}


@app.get("/api/models")
async def models():
    data = await manager.request("GET", "/api/tags")
    for model in data.get("models", []):
        model["cloud"] = cloud_model(model)
    return data


@app.get("/api/running")
async def running():
    return await manager.request("GET", "/api/ps")


@app.post("/api/chat")
async def chat(body: Chat):
    installed = (await manager.request("GET", "/api/tags")).get("models", [])
    selected = next((m for m in installed if m.get("name") == body.model or m.get("model") == body.model), None)
    if selected is None:
        raise HTTPException(400, "Model is no longer installed. Refresh models or download it first.")
    if cloud_model(selected):
        raise HTTPException(400, "Choose a local model. Cloud models are disabled in ollama-web.")
    if sum(len(m.content) for m in body.messages) > 500_000:
        raise HTTPException(413, "Conversation is too large. Start a new chat.")
    return StreamingResponse(manager.stream("/api/chat", {**body.model_dump(), "stream": True}),
                             media_type="application/x-ndjson", headers={"X-Accel-Buffering": "no"})


@app.post("/api/models/pull")
async def pull(body: ModelName):
    if cloud_model({"name": body.model}):
        raise HTTPException(400, "Download a local model rather than a cloud model.")
    return StreamingResponse(manager.stream("/api/pull", {"model": body.model, "stream": True}),
                             media_type="application/x-ndjson")


@app.post("/api/models/delete")
async def delete(body: ConfirmModel):
    await manager.request("DELETE", "/api/delete", {"model": body.model})
    return {"message": f"Deleted {body.model}."}


@app.post("/api/models/unload")
async def unload(body: ConfirmModel):
    await manager.request("POST", "/api/generate", {"model": body.model, "keep_alive": 0, "stream": False})
    return {"message": f"Unloaded {body.model}."}


@app.post("/api/control")
async def control(body: Control):
    if body.action != "start" and not body.confirm:
        raise HTTPException(400, "Confirm before stopping or restarting Ollama.")
    return {"message": await manager.lifecycle(body.action)}


app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
