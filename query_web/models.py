"""Shared Pydantic request/response models used across query_web endpoints."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class AskRequest(BaseModel):
    """Request payload for the `/api/ask` endpoint."""

    question: str
    retrieve_k: int = Field(default=5, ge=1, le=20)
    controls_context_cap: int | None = Field(default=None, ge=1, le=2000)
    temperature: float = Field(default=1.0, ge=0.0, le=1.0)
    top_p: float = Field(default=1.0, ge=0.0, le=1.0)
    auth_token: str = ""
    thinking_mode: str = "balanced"
    controls_semantic: bool | None = None
    controls_framework: str | None = None
    controls_comparison_mode: str = "auto-detect"
    evidence_corpora_include: list[str] | None = None
    evidence_corpora_exclude: list[str] | None = None
    max_completion_tokens: int | None = Field(default=None, ge=256, le=8192)
    evaluator_max_completion_tokens: int | None = Field(default=None, ge=128, le=4096)
    advanced_mode: bool = False


class AskResponse(BaseModel):
    """Structured response returned by ask and RAG endpoints."""

    answer: str
    results: list[dict[str, Any]]
    controls_results: list[dict[str, Any]] = []
    controls_debug: dict[str, Any] | None = None
    evaluation: dict[str, Any] | None
    iterations: int | None
    metrics: dict[str, float] | None
    audit: dict[str, Any] | None = None
    error: str
