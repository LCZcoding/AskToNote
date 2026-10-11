"""AskToNote 后端入口：挂载路由与 CORS（Plan §2）。

启动：在 `backend/` 下 `uv run uvicorn main:app --reload`。
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import router

app = FastAPI(title="AskToNote")

# 前端 dev 走 vite proxy（Plan §8「跨域」）；此处 CORS 兜底。
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)
