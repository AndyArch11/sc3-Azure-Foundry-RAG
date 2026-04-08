# Control LLM Review Integration

## Overview

Mistral (via Ollama or Azure) can optionally validate the heuristic control classifier's applicability assessments during the assessment workflow. This adds confidence scores to retrieved controls without changing the core compliance assessment logic.

## Configuration

### Enable in Production

Set these environment variables to activate optional LLM review:

```bash
# Enable the feature (default: false)
CONTROL_LLM_REVIEW_ENABLED=true

# Heuristic confidence threshold for LLM review
# Only controls below this threshold are reviewed (default: 0.75)
CONTROL_LLM_REVIEW_HEURISTIC_THRESHOLD=0.75

# Choose LLM backend (default: azure)
LLM_BACKEND=azure  # or 'ollama' for local

# If using Ollama backend:
OLLAMA_HOST=http://host.docker.internal:11434  # or your Ollama endpoint
OLLAMA_CHAT_MODEL=mistral:latest  # or your preferred model
OLLAMA_NUM_CTX=65536
OLLAMA_FORCE_JSON=true
```

## Behaviour

When enabled:

1. **Control Retrieval**: Assessment retrieves relevant controls via semantic search
2. **Heuristic Classification**: Each control has initial classifier scope + confidence score
3. **LLM Enrichment** (if enabled): Controls below threshold are sent to Mistral for secondary review
4. **Enrichment Feedback**: Each control gains:
   - `llm_scope`: Mistral's classification (technical|process|governance|mixed)
   - `llm_confidence`: Mistral's confidence (0.0-1.0)
   - `llm_rationale`: Explanation of Mistral's classification
   - `llm_agrees_with_heuristic`: Boolean match with heuristic scope
5. **Assessment Generation**: Uses enriched controls for compliance evaluation

## Graceful Fallback

If LLM review fails (API error, timeout, model unavailable), the workflow:
- Logs a warning
- Continues with heuristic classifications only
- Does NOT block the assessment

This ensures production reliability even if Ollama or Azure OpenAI is temporarily unavailable.

## Performance Notes

- **Azure Backend**: ~5-10s per control (includes network latency)
- **Ollama Local (GPU)**: ~0.5-2s per control (RTX 5090 with CUDA 12.0, 31.8 GiB VRAM)
- **Ollama Local (CPU)**: ~10-20s per control (fallback if CUDA unavailable)
- By default, only ambiguous controls (< 0.75 confidence) are reviewed
- Adjust `CONTROL_LLM_REVIEW_HEURISTIC_THRESHOLD` to review more/fewer controls

## Model Recommendations

### Tested Models

- **Mistral 7B (mistral:latest)**: 87.5% agreement with heuristic classifier
  - Good balance of speed and accuracy
  - Stable JSON output with format constraint
  - **GPU Performance**: ~0.5-1s per control (RTX 5090 with CUDA 13.1)
  - Recommended for production
  
- **Gemma3 27B (gemma3:27b)**: 12.5% agreement, systematic bias toward "process"
  - Not recommended for applicability review
  - Different classification philosophy than heuristic
  
- **Llama4 Scout**: Requires 57.9GB memory (WSL limited to ~50GB)
  - Not currently testable without memory increase

## CLI Testing

Validate LLM review without triggering full assessment:

```bash
# Test with default 25 ambiguous controls
python -m runtime.assessment_orchestration.validate_control_applicability \
  0.75 \
  --llm-review \
  --llm-max-controls 25 \
  --max-results 1000

# Test with Ollama backend
LLM_BACKEND=ollama \
OLLAMA_CHAT_MODEL=mistral:latest \
python -m runtime.assessment_orchestration.validate_control_applicability \
  0.75 \
  --llm-review \
  --llm-max-controls 8 \
  --max-results 400
```

## Cost Considerations

### Azure OpenAI
- Tokens per control: ~200-400 tokens (GPT-4 pricing model)
- Cost: ~$0.003-0.006 per control
- For 100 ambiguous controls: ~$0.30-0.60

### Ollama (Local with CUDA GPU)
- Cost: $0 (runs on your hardware)
- Time: ~0.5-2s per control (GPU-accelerated, RTX 5090 + CUDA 12.0)
- No external dependencies or API costs
- 100 ambiguous controls: ~1-3 minutes total

### Ollama (Local CPU-only fallback)
- Cost: $0 (runs on your hardware)
- Time: ~10-20s per control (CPU-bound)
- 100 ambiguous controls: ~17-33 minutes total

## Disabling LLM Review

To disable and use only heuristic classifications:

```bash
CONTROL_LLM_REVIEW_ENABLED=false  # or omit (default)
```

The workflow will skip LLM enrichment and use heuristic scope/confidence only.

## Next Steps

1. **Staging Environment**: Enable with `CONTROL_LLM_REVIEW_ENABLED=true` on a test assessment
2. **Monitor**: Check logs for LLM enrichment success/failure rates
3. **Tune Threshold**: Adjust `CONTROL_LLM_REVIEW_HEURISTIC_THRESHOLD` based on time/cost constraints
4. **Production**: Roll out gradually with monitoring

## References

- [Prompt Tuning Results](../docs/agent-assessment-orchestration.md#llm-model-selection)
- [Control Applicability Validator](../runtime/assessment_orchestration/validate_control_applicability.py)
- [Assessment Runtime](../runtime/assessment_orchestration/assessment_runtime.py#_apply_llm_control_applicability_review)
