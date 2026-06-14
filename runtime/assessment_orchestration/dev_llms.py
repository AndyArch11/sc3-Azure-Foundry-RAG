"""LLM backend factory for Azure vs local Ollama.

Environment variables:
    LLM_BACKEND: 'azure' (default) or 'ollama'
    OLLAMA_HOST: Ollama endpoint — Ollama's own env var (e.g. http://host.docker.internal:11434)
    OLLAMA_BASE_URL: Alternative endpoint override (OLLAMA_HOST takes precedence if both set)
    OLLAMA_MODEL: Ollama chat model (default: gemma3:27b)
    OLLAMA_CHAT_MODEL: Legacy chat model alias (preferred when both are set)
    OLLAMA_EMBEDDING_MODEL: Ollama embedding model (default: nomic-embed-text)
    OLLAMA_EMBED_MODEL: Legacy embedding model alias (used when OLLAMA_EMBEDDING_MODEL is unset)
    OLLAMA_NUM_CTX: Context window in tokens (default: adaptive by GPU VRAM when
        available, else host RAM/CPU; approx 4K on constrained hosts up to 128K on
        high-memory hosts). Adaptive detection probes the local runtime environment;
        if Ollama is running on a different host (WSL, remote container, etc.) set
        this explicitly so the context window matches that host's actual resources.
    OLLAMA_FORCE_JSON: Enable Ollama JSON mode (default: true)
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from azure.identity import DefaultAzureCredential

    from .assessment_runtime import AssessmentRuntimeConfig

logger = logging.getLogger(__name__)


def _detect_host_resources() -> tuple[float, int]:
    """Best-effort host resource detection (RAM GiB, CPU cores)."""
    cpu_count = os.cpu_count() or 1
    try:
        page_size = os.sysconf("SC_PAGE_SIZE")
        page_count = os.sysconf("SC_PHYS_PAGES")
        if (
            isinstance(page_size, int)
            and isinstance(page_count, int)
            and page_size > 0
            and page_count > 0
        ):
            ram_bytes = float(page_size * page_count)
            return (ram_bytes / (1024**3), cpu_count)
    except (AttributeError, OSError, ValueError):
        pass

    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as meminfo:
            for line in meminfo:
                if line.startswith("MemTotal:"):
                    parts = line.split()
                    if len(parts) >= 2 and parts[1].isdigit():
                        ram_kib = int(parts[1])
                        return (ram_kib / (1024**2), cpu_count)
    except OSError:
        pass

    return (8.0, cpu_count)


def _detect_gpu_vram_gib() -> float | None:
    """Best-effort GPU VRAM detection in GiB.

    Returns the largest detected VRAM value across all visible GPUs, or None when
    no reliable signal is available.
    """

    values_gib: list[float] = []
    values_gib.extend(_detect_nvidia_gpu_vram_gibs())
    values_gib.extend(_detect_linux_drm_gpu_vram_gibs())
    if not values_gib:
        return None
    return max(values_gib)


def _detect_nvidia_gpu_vram_gibs() -> list[float]:
    """Detect NVIDIA GPU VRAM values in GiB using nvidia-smi."""
    nvidia_smi = shutil.which("nvidia-smi")
    values_gib: list[float] = []
    if nvidia_smi:
        try:
            result = subprocess.run(
                [nvidia_smi, "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
                capture_output=True,
                check=True,
                text=True,
                timeout=2,
            )
            values_mib: list[int] = []
            for line in result.stdout.splitlines():
                token = line.strip().split(" ")[0]
                if token.isdigit():
                    values_mib.append(int(token))
            if values_mib:
                values_gib.extend([value / 1024.0 for value in values_mib])
        except (OSError, ValueError, subprocess.SubprocessError):
            pass

    return values_gib


def _detect_linux_drm_gpu_vram_gibs() -> list[float]:
    """Detect Linux DRM GPU VRAM values in GiB from sysfs when available."""
    values_gib: list[float] = []

    # Linux AMD path (when sysfs is exposed by the kernel/driver stack).
    try:
        values_bytes: list[int] = []
        for path in Path("/sys/class/drm").glob("card*/device/mem_info_vram_total"):
            text = path.read_text(encoding="utf-8").strip()
            if text.isdigit():
                values_bytes.append(int(text))
        if values_bytes:
            values_gib.extend([value / (1024.0**3) for value in values_bytes])
    except OSError:
        pass

    return values_gib


def _recommended_ollama_num_ctx_from_host(ram_gib: float, cpu_count: int) -> int:
    """Heuristic context window from host RAM/CPU only."""
    if ram_gib < 8 or cpu_count <= 4:
        return 8192
    if ram_gib < 16 or cpu_count <= 8:
        return 16384
    if ram_gib < 32:
        return 32768
    if ram_gib < 64:
        return 65536
    return 131072


def _recommended_ollama_num_ctx_from_gpu(gpu_vram_gib: float) -> int:
    """Heuristic context window from GPU VRAM only."""
    if gpu_vram_gib < 6:
        return 4096
    if gpu_vram_gib < 10:
        return 8192
    if gpu_vram_gib < 16:
        return 16384
    if gpu_vram_gib < 24:
        return 32768
    if gpu_vram_gib < 40:
        return 65536
    return 131072


def _is_remote_ollama_url(url: str) -> bool:
    """Return True when the Ollama URL targets a host other than localhost/loopback.

    Addresses that are considered local:
      - localhost / 127.x.x.x / ::1
      - 0.0.0.0  (all-interfaces bind; Ollama itself uses this)
    Addresses that are considered remote (Ollama runs in a different environment):
      - host.docker.internal, wsl.localhost, named hosts, external IPs
    """
    try:
        from urllib.parse import urlparse

        host = urlparse(url).hostname or ""
    except Exception:
        return False
    host = host.lower()
    if host in {"localhost", "0.0.0.0", "[::1]", "::1"}:
        return False
    if host.startswith("127."):
        return False
    return True


def _recommended_ollama_num_ctx(ollama_url: str | None = None) -> int:
    """Adaptive default context window for Ollama on local/dev hosts."""
    ram_gib, cpu_count = _detect_host_resources()
    host_ctx = _recommended_ollama_num_ctx_from_host(ram_gib, cpu_count)
    gpu_vram_gib = _detect_gpu_vram_gib()
    if gpu_vram_gib is None:
        ctx = host_ctx
    else:
        gpu_ctx = _recommended_ollama_num_ctx_from_gpu(gpu_vram_gib)
        # Keep a host-memory guardrail while still preferring GPU capability.
        host_guardrail = min(131072, max(host_ctx, 16384))
        ctx = min(gpu_ctx, host_guardrail)

    if ollama_url and _is_remote_ollama_url(ollama_url):
        logger.warning(
            "Ollama is running at a remote endpoint (%s) but OLLAMA_NUM_CTX is not set. "
            "The adaptive context window default (%s tokens) is based on the local runtime "
            "environment and may not reflect the resources available to Ollama. "
            "Set OLLAMA_NUM_CTX explicitly to match the Ollama host's capabilities.",
            ollama_url,
            ctx,
        )
    return ctx


def get_llm_backend() -> str:
    """Get configured LLM backend: 'azure' or 'ollama'."""
    backend = os.environ.get("LLM_BACKEND", "azure").lower().strip()
    if backend not in ("azure", "ollama"):
        logger.warning(f"Invalid LLM_BACKEND '{backend}'; defaulting to 'azure'")
        return "azure"
    return backend


def create_chat_completion_fn(
    backend: str | None = None,
    *,
    config: AssessmentRuntimeConfig | None = None,
    credential: DefaultAzureCredential | None = None,
) -> Callable[[list[dict[str, str]]], str]:
    """Create a chat completion function for the specified backend.

    Args:
        backend: 'azure' or 'ollama' (default: from LLM_BACKEND env var)
        config: Azure runtime config (required if backend='azure')
        credential: Azure credential (required if backend='azure')

    Returns:
        Callable that accepts OpenAI-format messages and returns string response

    Example:
        chat_fn = create_chat_completion_fn()
        response = chat_fn([
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "What is 2+2?"}
        ])
    """
    backend = backend or get_llm_backend()

    if backend == "ollama":
        from .ollama_client import _resolve_base_url, is_ollama_available, ollama_chat_completion

        ollama_url = _resolve_base_url(os.environ.get("OLLAMA_BASE_URL") or None)
        ollama_model = (
            os.environ.get("OLLAMA_CHAT_MODEL") or os.environ.get("OLLAMA_MODEL") or "gemma3:27b"
        )

        if not is_ollama_available(ollama_url):
            logger.warning(
                f"Ollama not available at {ollama_url}; falling back to Azure. "
                f"Start with: ollama serve"
            )
            backend = "azure"
        else:
            logger.info(f"Using Ollama backend: {ollama_model} @ {ollama_url}")

            num_ctx_env = os.environ.get("OLLAMA_NUM_CTX", "").strip()
            if num_ctx_env:
                num_ctx = max(2048, int(num_ctx_env))
            else:
                num_ctx = _recommended_ollama_num_ctx(ollama_url)
                logger.info("OLLAMA_NUM_CTX not set; using adaptive default: %s", num_ctx)
            force_json = os.environ.get("OLLAMA_FORCE_JSON", "true").lower() not in (
                "0",
                "false",
                "no",
            )

            def ollama_wrapper(messages: list[dict[str, str]]) -> str:
                """Run ollama wrapper."""
                return ollama_chat_completion(
                    messages,
                    model=ollama_model,
                    base_url=ollama_url,
                    temperature=1.0,
                    timeout=120,
                    force_json=force_json,
                    num_ctx=num_ctx,
                )

            return ollama_wrapper

    if backend == "azure":
        from . import assessment_runtime

        if config is None or credential is None:
            raise ValueError("config and credential required for Azure backend")

        logger.info(
            f"Using Azure backend: {config.query_deployment} " f"@ {config.openai_endpoint}"
        )

        def azure_wrapper(messages: list[dict[str, str]]) -> str:
            """Run azure wrapper."""
            return assessment_runtime._chat_completion(
                messages, config=config, credential=credential
            )

        return azure_wrapper

    raise ValueError(f"Unknown LLM backend: {backend}")


def create_embedding_fn(
    backend: str | None = None,
    *,
    config: AssessmentRuntimeConfig | None = None,
    credential: DefaultAzureCredential | None = None,
) -> Callable[[str], list[float]]:
    """Create an embedding function for the specified backend.

    Args:
        backend: 'azure' or 'ollama' (default: from LLM_BACKEND env var)
        config: Azure runtime config (required if backend='azure')
        credential: Azure credential (required if backend='azure')

    Returns:
        Callable that accepts string text and returns embedding vector

    Note:
        For development (ollama backend), consider keyword-only search
        since Ollama embeddings have different dimensionality than ada-002.

    Example:
        embed_fn = create_embedding_fn()
        vector = embed_fn("What is machine learning?")
    """
    backend = backend or get_llm_backend()

    if backend == "ollama":
        from .ollama_client import _resolve_base_url, is_ollama_available, ollama_embedding

        ollama_url = _resolve_base_url(os.environ.get("OLLAMA_BASE_URL") or None)
        ollama_model = (
            os.environ.get("OLLAMA_EMBEDDING_MODEL")
            or os.environ.get("OLLAMA_EMBED_MODEL")
            or "nomic-embed-text"
        )

        if not is_ollama_available(ollama_url):
            logger.warning(
                f"Ollama not available at {ollama_url}; "
                f"falling back to Azure or returning zero vector"
            )
            backend = "azure"
        else:
            logger.info(f"Using Ollama embeddings: {ollama_model} @ {ollama_url}")

            def ollama_wrapper(text: str) -> list[float]:
                """Run ollama wrapper."""
                return ollama_embedding(
                    text,
                    model=ollama_model,
                    base_url=ollama_url,
                    timeout=60,
                )

            return ollama_wrapper

    if backend == "azure":
        from . import assessment_runtime

        if config is None or credential is None:
            raise ValueError("config and credential required for Azure backend")

        logger.info(
            f"Using Azure embeddings: {config.embedding_deployment} " f"@ {config.openai_endpoint}"
        )

        def azure_wrapper(text: str) -> list[float]:
            """Run azure wrapper."""
            return assessment_runtime._embed_query(text, config=config, credential=credential)

        return azure_wrapper

    raise ValueError(f"Unknown LLM backend: {backend}")
