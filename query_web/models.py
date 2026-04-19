"""Shared Pydantic request/response models used across query_web endpoints."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class AskRequest(BaseModel):
    question: str
    retrieve_k: int = Field(default=5, ge=1, le=20)
    temperature: float = Field(default=1.0, ge=0.0, le=1.0)
    auth_token: str = ""
    controls_semantic: bool | None = None
    controls_framework: str | None = None
    controls_comparison_mode: str = "auto-detect"
    evidence_corpora_include: list[str] | None = None
    evidence_corpora_exclude: list[str] | None = None
    advanced_mode: bool = False


class AskResponse(BaseModel):
    answer: str
    results: list[dict[str, Any]]
    controls_results: list[dict[str, Any]] = []
    controls_debug: dict[str, Any] | None = None
    evaluation: dict[str, Any] | None
    iterations: int | None
    metrics: dict[str, float] | None
    audit: dict[str, Any] | None = None
    error: str
