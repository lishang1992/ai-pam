"""AI 安全堡垒机 V1 —— 应用入口。

启动：
    python run.py                # 或
    uvicorn backend.app.main:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, Dict

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .api import approvals, audit, auth, catalog, dashboard, demo, realtime, sessions
from .seeds import init_all
from .settings import DEMO_MODE, FRONTEND_DIR, llm_enabled


@asynccontextmanager
async def lifespan(app: FastAPI):
    info = init_all()
    app.state.init_info = info
    print("[AI Bastion] 初始化完成：%s" % info)
    print("[AI Bastion] 演示模式=%s，外部大模型=%s" % (DEMO_MODE, "已接入" if llm_enabled() else "未接入"))
    yield


app = FastAPI(
    title="AI 安全堡垒机 V1",
    description="面向企业 AI Agent 的特权访问、操作控制与全链路审计平台",
    version=__version__,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

for router in (auth.router, catalog.router, sessions.router, approvals.router,
               audit.router, dashboard.router, demo.router, realtime.router):
    app.include_router(router)


@app.get("/api/health")
def health() -> Dict[str, Any]:
    return {
        "status": "ok",
        "version": __version__,
        "demo_mode": DEMO_MODE,
        "llm": llm_enabled(),
    }


if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(str(FRONTEND_DIR / "index.html"))

    @app.get("/favicon.ico")
    def favicon() -> JSONResponse:
        return JSONResponse({}, status_code=204)

