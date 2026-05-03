# ADR 0002: CAE -> AMW Managed Prometheus Ingestion Deferred

## Status

Deferred

## Date

2026-05-02

## Context

The platform attempted to enable Azure Monitor managed Prometheus ingestion from Azure Container Apps via Container App Environment (CAE) associations to Azure Monitor Workspace (AMW) ingestion settings.

Verified implementation state:

- Query app exposes `/metrics` and returns valid Prometheus exposition payload.
- EasyAuth excludes `/metrics`.
- CAE has DCR association and DCE association configured.
- CAE associations were moved to AMW default DCR/DCE in the `MA_amw-*` managed resource group.
- Required RBAC was granted for linked-scope association updates.

Despite this, AMW queries still return no active vectors for:

- `up`
- `python_gc_objects_collected_total`
- `scrape_samples_scraped`

No conclusive platform-side scrape error was identified from available logs.

## Decision

Pause further implementation work on CAE -> AMW managed Prometheus ingestion for now.

- Keep current configuration and diagnostics artifacts in place.
- Do not spend additional engineering cycles on speculative configuration changes.
- Resume only when this capability is re-prioritized, with Microsoft support-assisted investigation if needed.

## Rationale

- Core wiring and access prerequisites have been implemented and validated.
- Repeated verification still shows zero samples in AMW.
- Additional trial-and-error changes are unlikely to be efficient without deeper service-side diagnostics.

## Consequences

Positive:

- Prevents further time burn on low-confidence debugging.
- Preserves a reproducible evidence trail for later restart.
- Allows workstream focus to shift to higher-priority deliverables.

Negative:

- Managed Prometheus ingestion for Container Apps remains unavailable in this environment.
- Observability for app metrics remains limited to local and non-AMW paths.

## Evidence Summary

Key evidence and commands are documented in:

- [docs/troubleshoot.md](../troubleshoot.md)
- [docs/observability.md](../observability.md)

Notable verification points:

- CAE association list shows AMW default DCR/DCE attached.
- `/metrics` returns HTTP 200 with metric payload.
- AMW PromQL endpoint returns empty vectors for expected series.

## Resume Triggers

Re-open this ADR when any of the following occurs:

- The capability becomes a near-term delivery requirement.
- Microsoft support provides a prescriptive fix path.
- Azure documentation is updated with explicit Container Apps managed Prometheus onboarding requirements that differ from current implementation.

## Next Step On Resume

Use existing commands in [docs/troubleshoot.md](../troubleshoot.md) to re-run baseline checks, then open a support ticket with current correlation IDs and command outputs if vectors remain empty.
