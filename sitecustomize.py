"""Runtime compatibility shims for local development.

This module is imported automatically by Python when present on sys.path.
It patches optional third-party import paths that moved across dependency
versions so local tooling remains stable.
"""

from __future__ import annotations

import importlib.util
import sys
import types


def _ensure_langchain_vertexai_shim() -> None:
    """Provide legacy langchain_community ChatVertexAI import path for ragas."""
    try:
        if importlib.util.find_spec("langchain_community.chat_models.vertexai") is not None:
            return
    except ModuleNotFoundError:
        pass

    try:
        from langchain_google_vertexai import ChatVertexAI  # type: ignore
    except ImportError:

        class ChatVertexAI:  # type: ignore
            """Fallback placeholder used when vertexai extras are not installed."""

            pass

    shim = types.ModuleType("langchain_community.chat_models.vertexai")
    setattr(shim, "ChatVertexAI", ChatVertexAI)
    sys.modules["langchain_community.chat_models.vertexai"] = shim


_ensure_langchain_vertexai_shim()
