"""FastAPI entrypoint for 规证AI."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.funding import router as funding_router
from app.api.materials import router as materials_router
from app.api.policies import router as policies_router
from app.api.projects import router as projects_router
from app.api.rules import router as rules_router
from app.config import get_settings
from app.db import init_db
from app.schemas import HealthResponse


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="规证AI",
        description="高校项目申报材料形式审查 — B05 规则编辑、启用与版本绑定",
        version="0.5.0",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(projects_router)
    app.include_router(materials_router)
    app.include_router(funding_router)
    app.include_router(policies_router)
    app.include_router(rules_router)

    @app.get("/api/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse(
            status="ok",
            llm_configured=settings.llm_configured,
        )

    return app


app = create_app()
