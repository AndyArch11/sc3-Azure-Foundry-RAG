# Observability Baseline

## Logging

- Provision one Log Analytics Workspace (LAW) per environment.
- Route platform diagnostic logs to LAW where supported.
- Add runtime structured logs with correlation IDs and conversation IDs.

## Metrics

- Emit application metrics in Prometheus format from runtime services.
- Instrument code with OpenTelemetry for traces and metrics.
- Export traces/metrics to Azure Monitor (collector or SDK pipeline) in later phases.

## Tracing

- Use OpenTelemetry span propagation across API and worker boundaries.
- Include service.name, environment, and tenant-safe identifiers.

## Dashboards and Alerts (Planned)

- Error-rate and latency dashboards.
- Ingestion throughput and failure-rate dashboards.
- Alerts for private endpoint reachability and agent response failures.
