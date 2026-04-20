# Local LLM Comparison: Ollama vs Llama.cpp

## Goal
Provide a practical local execution option for query and assessment workflows without cloud credentials.

## Option A: Ollama
Pros:
- Already integrated in this repository.
- Simple model lifecycle (pull/run/list).
- Good developer ergonomics for quick switching.

Cons:
- Requires background server process.
- Additional runtime memory overhead.

## Option B: Llama.cpp
Pros:
- Lightweight native runtime.
- Fast startup for direct local inference.
- Good fit for constrained environments with pre-quantized GGUF models.

Cons:
- Additional integration effort and runtime compatibility testing.
- Model management UX is less standardised than Ollama.

## Recommendation for This Repo
- Keep Ollama as default local backend.
- Add Llama.cpp as optional backend behind the existing backend factory pattern.
- Defer final default decision until benchmark pass is complete.

## Decision Criteria
- Quality: JSON reliability and compliance-assessment output usefulness.
- Latency: time to first token and end-to-end request duration.
- Resource profile: memory and CPU under normal dev load.
- Reliability: startup robustness and failure modes.
- Developer experience: setup complexity and day-to-day friction.

## Evaluation Plan
1. Run the same prompt set against Ollama and Llama.cpp.
2. Compare output validity rate for strict JSON tasks.
3. Measure median and p95 latency.
4. Capture memory footprint during test runs.
5. Record setup time and common failure cases.

## Exit Criteria
- A documented decision in docs/LOCAL_LLM_DECISION.md.
- Backend toggle documented via environment variables.
- Local setup instructions updated in docs.
