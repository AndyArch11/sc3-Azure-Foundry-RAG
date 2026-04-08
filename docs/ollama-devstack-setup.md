# Local LLM Devstack with Ollama

This guide explains how to use Ollama (local LLMs) instead of Azure OpenAI for development iteration. This is useful for:

- **Faster iteration**: No Azure API auth/rate-limiting delays
- **Reduced Azure costs**: No LLM API calls during dev cycles
- **Offline development**: Works without internet connectivity
- **Compliance testing**: JSON report generation without Azure dependency

## Architecture

The Foundry backend supports **two LLM backends**:

1. **Azure OpenAI** (production/default):
   - GPT-5.1 chat model for compliance report generation
   - Text embedding Ada-002 for semantic search
   - Configured via `AZURE_OPENAI_ENDPOINT` environment variable

2. **Ollama** (local development):
    - Compatible models (Mistral, Gemma 3, Neural-Chat)
   - Local embedding models (Nomic-Embed-Text, All-MiniLM)
   - No authentication required
   - Configured via `LLM_BACKEND=ollama` environment variable

## Installation

### 1. Install Ollama

**macOS**:
```bash
curl https://ollama.ai/install.sh | bash
# Or: brew install ollama
```

**Linux** (Ubuntu):
```bash
curl https://ollama.ai/install.sh | bash
```

**Windows**:
- Download from https://ollama.ai
- Or: `winget install Ollama.Ollama`

### 2. Pull Models

The recommended development setup is:

```bash
# Primary: Fast, good quality local validation and dev iteration
ollama pull mistral:latest

# Secondary (optional): Alternative model for comparison only
ollama pull gemma3:27b

# Embeddings (optional): For keyword-free dev
# Note: skip this if using keyword-only search during dev
ollama pull nomic-embed-text
```

**Model Sizes and Recommendations**:

The compliance applicability-review task requires a model that reliably outputs valid JSON and stays aligned with the heuristic classifier. Models differ significantly on this. In this repo, Mistral is the recommended local model because it matched the heuristic best in live testing.

| Model | Tag | RAM | Notes |
|-------|-----|-----|-------|
| Mistral 7B | `mistral:latest` | 4.4 GB | **Recommended local validator** — best agreement in live tests, fast on GPU |
| Gemma 3 27B | `gemma3:27b` | ~17 GB+ | Tested alternative; significantly worse agreement for applicability scope |
| Llama 4 Scout | `llama4:scout` | ~58 GB system memory required | Could not be validated in this setup due to memory limits |
| Neural Chat 7B | `neural-chat:7b` | ~4-5 GB | Untested fallback option |

**Avoid**: `llama2`, `llama2:13b` — outdated recommendation, not used in the current workflow, and not validated for this repo's applicability-review task.

Pull your chosen model:
```bash
ollama pull mistral:latest   # recommended
ollama pull nomic-embed-text  # embeddings (optional)
```

**Required env vars when using a non-default model**:
```bash
export OLLAMA_CHAT_MODEL=mistral:latest
export OLLAMA_NUM_CTX=32768   # Increase if your prompt or document context requires it
```

### 3. Start Ollama

```bash
ollama serve
# Listens on: http://localhost:11434
```

Or run in background:
```bash
ollama serve &
```

Verify it's running:
```bash
curl http://localhost:11434/api/tags
# Should return JSON list of available models
```

## Development Workflow

### Using Ollama Backend

Set environment variables to enable Ollama:

```bash
export LLM_BACKEND=ollama
export OLLAMA_BASE_URL=http://localhost:11434
export OLLAMA_CHAT_MODEL=mistral:latest
export OLLAMA_EMBED_MODEL=nomic-embed-text
```

Or use Azure (default):

```bash
export LLM_BACKEND=azure
# ... set AZURE_OPENAI_ENDPOINT, etc.
```

### Running Assessment with Ollama

```bash
# Python code
from runtime.assessment_orchestration import SearchBackedAssessmentAgent, AssessmentRuntimeConfig

# With LLM_BACKEND=ollama set:
config = AssessmentRuntimeConfig.from_env()
agent = SearchBackedAssessmentAgent(config=config)

# Assessment now uses local Ollama instead of Azure
report = agent.generate_assessment(artifact, grounding)
```

### Checking Backend Status

```python
from runtime.assessment_orchestration import get_llm_backend
from runtime.assessment_orchestration.ollama_client import is_ollama_available

backend = get_llm_backend()
print(f"Active backend: {backend}")

if is_ollama_available():
    print("✓ Ollama is running and ready")
else:
    print("✗ Ollama not available; will fall back to Azure")
```

## Architecture Comparison

### Chat Completion

| Dimension | Azure OpenAI | Mistral 7B (Ollama) |
|-----------|--------------|----------------------|
| Model | GPT-5.1 | `mistral:latest` |
| Response time | ~3-5s | ~10-20s on CPU; ~0.5-1s per control on warmed GPU for applicability review |
| Context size | 128K tokens | Configurable via `OLLAMA_NUM_CTX` |
| JSON adherence | Excellent | Good enough for local validation with retry and schema extraction |
| Cost | $$ per call | Free (local) |
| Status | Production-only | Dev-recommended |

### Embeddings

| Dimension | Ada-002 (Azure) | Nomic-Embed (Ollama) |
|-----------|-----------------|---------------------|
| Dimensions | 1536 | 768 |
| Speed | ~0.5s | ~0.2s (local) |
| Semantic quality | Excellent | Good |
| Compatibility | Azure Search native | Requires workaround |

## Known Limitations

### 1. JSON Response Quality
**Issue**: Local models may produce invalid JSON occasionally.

**Solution**: The compliance report generator has a built-in fallback:
```python
try:
    report = validate_compliance_report_payload(report_payload)
except Exception:
    if validation_mode == "hard":
        raise
    report = _fallback_report(...)  # Graceful degradation
```

**Recommendation**: Run with `validation_mode="lenient"` during Ollama dev.

### 2. Different Embedding Dimensions
**Issue**: Ollama embeddings are 768-dim vs Azure's 1536-dim.

**Solution (two options)**:

**Option A** (Recommended for dev): Use keyword-only search
```python
# When Azure Search is configured, the hybrid search pipeline already supports
# fallback to keyword search if embeddings are unavailable
```

**Option B**: Disable vector search
```bash
export SEARCH_STRATEGY=keyword-only
```

### 3. Token Limits
**Issue**: Ollama doesn't expose `max_tokens` parameter the same way.

**Solution**: Ollama streaming is truncated server-side; completes successfully.

### 4. Context Window
**Issue**: Local models may truncate large artifacts if `OLLAMA_NUM_CTX` is too small.

**Solution**: Increase `OLLAMA_NUM_CTX` as needed. Do not switch to Llama 2 for this workflow; it is not the recommended path in this repo.

## Troubleshooting

### "Failed to connect to Ollama"
```bash
# Check Ollama is running
curl http://localhost:11434/api/tags

# If not running, start it:
ollama serve
```

### "Model not found"
```bash
# List available models:
ollama list

# Pull the model:
ollama pull mistral
```

### Slow responses (>30s)
```bash
# Check CPU usage - may be CPU-bound
# Solution: Use GPU if available:
# - Metal (macOS): Automatic with M1/M2/M3
# - CUDA (Nvidia): Install CUDA driver + restart Ollama
# - AMD: Install ROCm drivers

# Or switch to smaller model:
ollama pull neural-chat:7b  # 4.7GB, faster than llama2
```

### JSON validation failures
```bash
# Use lenient validation:
# In assessment code, use: validation_mode="lenient"
# This activates the fallback report handler
```

## Monitoring & Debugging

### Check Active Backend
```python
import os
backend = os.environ.get("LLM_BACKEND", "azure")
print(f"LLM Backend: {backend}")
```

### Enable Debug Logging
```python
import logging
logging.basicConfig(level=logging.DEBUG)

# Now all LLM calls are logged with backend info
```

### Performance Profiling
```python
import time
from runtime.assessment_orchestration import SearchBackedAssessmentAgent

config = AssessmentRuntimeConfig.from_env()
agent = SearchBackedAssessmentAgent(config=config)

start = time.time()
report = agent.generate_assessment(artifact, grounding)
elapsed = time.time() - start

print(f"Assessment took {elapsed:.2f}s using {get_llm_backend()} backend")
```

## Production Notes

⚠️ **Important**: Ollama is **development-only**. Do not use in production because:

1. Limited functionality, included to support limited local testing
2. No authentication or rate-limiting (security risk)
3. No audit logging integration
4. Not covered by compliance SLAs

For production, always use Azure OpenAI (set `LLM_BACKEND=azure`).

## Next Steps

1. **Install Ollama**: Follow installation section above
2. **Pull models**: `ollama pull mistral`
3. **Start Ollama**: `ollama serve`
4. **Test backend**: `curl http://localhost:11434/api/tags`
5. **Set env vars**: `export LLM_BACKEND=ollama`
6. **Run assessment**: Compliance report generation now uses local LLM

## References

- **Ollama**: https://ollama.ai/
- **Mistral Model**: https://mistral.ai/
- **Nomic-Embed-Text**: https://huggingface.co/nomic-ai/nomic-embed-text
- **Assessment Code**: `runtime/assessment_orchestration/assessment_runtime.py`
- **Backend Factory**: `runtime/assessment_orchestration/dev_llms.py`
- **Ollama Client**: `runtime/assessment_orchestration/ollama_client.py`
