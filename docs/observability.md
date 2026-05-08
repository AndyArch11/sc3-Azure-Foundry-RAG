# Observability Baseline

## Logging

- Provision one Log Analytics Workspace (LAW) per environment.
- Route platform diagnostic logs to LAW where supported.
- Add runtime structured logs with correlation IDs and conversation IDs.
- Each Cosmos read/write emits a structured `cosmos_schema_access` log line (see [Cosmos schema evolution docs](compliance-rag-recommended-approach.md#cosmos-schema-evolution-strategy-rolling-changes)).

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

## Local Compose Observability (Implemented)

The repository includes a local observability overlay and provisioning assets:

- [docker-compose.observability.yml](../docker-compose.observability.yml)
- [ops/observability/prometheus/prometheus.local.yml](../ops/observability/prometheus/prometheus.local.yml)
- [ops/observability/prometheus/alerts.local.yml](../ops/observability/prometheus/alerts.local.yml)
- [ops/observability/loki/loki.local.yml](../ops/observability/loki/loki.local.yml)
- [ops/observability/promtail/promtail.local.yml](../ops/observability/promtail/promtail.local.yml)
- [ops/observability/grafana/provisioning/datasources/prometheus.yml](../ops/observability/grafana/provisioning/datasources/prometheus.yml)
- [ops/observability/grafana/provisioning/datasources/loki.yml](../ops/observability/grafana/provisioning/datasources/loki.yml)
- [ops/observability/grafana/provisioning/dashboards/dashboards.yml](../ops/observability/grafana/provisioning/dashboards/dashboards.yml)
- [ops/observability/grafana/dashboards/query-web-local-observability.json](../ops/observability/grafana/dashboards/query-web-local-observability.json)

Start local app + observability together:

```bash
docker compose \
  -f docker-compose.local.yml \
  -f docker-compose.observability.yml \
  --env-file .env.local \
  --profile observability \
  up --build
```

Default local URLs:

- Query web: http://localhost:8080
- Prometheus: http://localhost:9090
- Grafana: http://localhost:3000
- Loki: http://localhost:3100
- Alertmanager: http://localhost:9093
- Webhook echo (alert sink): http://localhost:8090

The dashboard **Query Web Local Observability** is auto-provisioned and includes:

- HTTP latency p95 and request rate from `http_request_duration_seconds`
- Route/status latency trends from histogram buckets
- Trace header drop rates from `trace_traceparent_dropped_total`
- Trace salvage and correlation-id sanitisation rates from `trace_traceparent_salvaged_total` and `trace_correlation_id_sanitised_total`

Grafana also provisions a Loki datasource for local log exploration. Promtail scrapes Docker stdout for the local Python services and forwards those JSON log lines to Loki. The starter dashboard includes a built-in **Recent Query Web Logs** panel backed by Loki. This path is intentionally local-only; deployed Azure and AWS environments should continue using their cloud-native logging backends.

Prometheus local alert rules are enabled and evaluated every 15 seconds. Fired alerts are forwarded to **Alertmanager** (`alertmanager:9093`), which routes them to the **webhook-echo** container for local inspection.

| Alert | Severity | Condition |
|---|---|---|
| `QueryWebHighP95Latency` | warning | p95 > 1 s for 10 m |
| `QueryWebHighP99Latency` | critical | p99 > 2 s for 10 m |
| `QueryWebTraceparentDropSpike` | warning | drop rate > 0.1/s for 5 m |
| `QueryWebCorrelationIdSanitisationSpike` | warning | sanitised rate > 0.05/s for 5 m |
| `QueryWebTargetDown` | critical | `up == 0` for 2 m |
| `QueryWebNoTraffic` | warning | no non-metrics traffic for 10 m |

Critical alerts are repeated every 5 minutes; warnings every 15 minutes. A critical alert also inhibits its matching warning (same `alertname`), suppressing duplicate noise.

**Asset inventory:**

| File | Purpose |
|---|---|
| `ops/observability/prometheus/prometheus.local.yml` | Prometheus main config; scrape + alerting stanza |
| `ops/observability/prometheus/alerts.local.yml` | Alert rule groups |
| `ops/observability/loki/loki.local.yml` | Local single-binary Loki config |
| `ops/observability/promtail/promtail.local.yml` | Docker stdout scrape + Loki push config |
| `ops/observability/alertmanager/alertmanager.local.yml` | Alertmanager routes + webhook receiver |
| `ops/observability/grafana/provisioning/datasources/prometheus.yml` | Auto-provisioned Grafana datasource |
| `ops/observability/grafana/provisioning/datasources/loki.yml` | Auto-provisioned Grafana Loki datasource |
| `ops/observability/grafana/provisioning/dashboards/dashboards.yml` | Dashboard provisioning config |
| `ops/observability/grafana/dashboards/query-web-local-observability.json` | Starter dashboard JSON |

To view alert delivery:

```bash
# Live tail the webhook sink
docker logs -f rag-webhook-echo-local

# Alertmanager status
curl -s http://localhost:9093/api/v2/status

# Alertmanager active alerts
curl -s http://localhost:9093/api/v2/alerts
```

To query local logs in Grafana Explore, use LogQL against low-cardinality labels and parse the JSON body at query time:

```logql
{service="query-web"} | json | __error__ = "" | correlation_id != ""
```

Keep high-cardinality fields like `correlation_id`, `traceparent`, `user_id`, and `conversation_id` in the JSON body rather than promoting them to Loki labels.

---

## Current Implementation Reality

What is documented above versus what is actually wired:

| Concern | Documented | Implemented today |
|---|---|---|
| Structured logging | ✅ | ✅ Standard Python `logging`; `cosmos_schema_access` structured log added |
| Per-request timings | ✅ | ✅ `rag_retrieval_s`, `embedding_s`, `search_s`, `total_s` etc. emitted in API response JSON |
| Log Analytics Workspace | ✅ | ✅ `azurerm_log_analytics_workspace` provisioned; wired to Container App Environment |
| Azure Monitor Workspace (AMW) | ✅ | ⚠️ `azurerm_monitor_workspace` provisioned and CAE DCR/DCE associations configured, but **no Prometheus samples observed in AMW** |
| Prometheus scrape endpoint (`/metrics`) | ✅ | ✅ Implemented in query-web with `prometheus_client`; local scrape wired via compose overlay |
| OTel SDK | ✅ | ❌ Not installed — not in `query_web/requirements.txt` or `runtime/requirements.txt` |
| Diagnostic settings (LAW → services) | ✅ | ❌ No `azurerm_monitor_diagnostic_setting` resource exists anywhere |
| OTel trace propagation | ✅ | ❌ No span creation or context propagation |

The timing dicts (`rag_retrieval_s` etc.) are **inline response fields only** — they are never scraped, never pushed to any metric store, and are invisible to dashboards. The AMW resource is effectively unused infrastructure.

---

## Prometheus Implementation Plan

### What Prometheus needs from the application

Prometheus is pull-based: it scrapes a plain-text HTTP endpoint (`/metrics`) that the application exposes. The `prometheus_client` Python library manages the registry and renders the text format.

**Minimum viable instrumentation for query-web:**

```python
# query_web/metrics.py
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
from fastapi import Response

REQUEST_LATENCY = Histogram(
    "rag_request_duration_seconds",
    "End-to-end RAG request latency",
    labelnames=["endpoint"],
    buckets=[0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
)
RETRIEVAL_LATENCY = Histogram(
    "rag_retrieval_duration_seconds",
    "Search + embedding latency",
    labelnames=["corpus"],
    buckets=[0.05, 0.1, 0.25, 0.5, 1.0, 2.5],
)
LLM_LATENCY = Histogram(
    "llm_reply_duration_seconds",
    "LLM completion latency",
    buckets=[0.5, 1.0, 2.5, 5.0, 10.0, 30.0],
)
REQUEST_ERRORS = Counter(
    "rag_request_errors_total",
    "Count of failed RAG requests",
    labelnames=["endpoint", "error_type"],
)
COSMOS_SCHEMA_VERSION_READS = Counter(
    "cosmos_schema_version_reads_total",
    "Cosmos documents read by schema version (for deprecation monitoring)",
    labelnames=["schema_version", "container", "service"],
)

def metrics_endpoint() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
```

The existing timing dicts (`rag_retrieval_s`, `embedding_s` etc.) already capture all needed values — the only change needed in the pipeline is to call `.observe()` on the histograms with those values, rather than just returning them in the response dict.

`COSMOS_SCHEMA_VERSION_READS` directly backs the KQL queries described in the schema evolution runbook but in a scrape-friendly counter form.

**Route to add in `app.py`:**

```python
app.add_route("/metrics", metrics_endpoint)
```

**Dependency to add to `query_web/requirements.txt`:**

```
prometheus-client>=0.20.0
```

---

### Local Prometheus setup

The simplest local configuration uses Docker Compose alongside the existing dev stack.

**`docker-compose.observability.yml`** (add to repo root):

```yaml
services:
  prometheus:
    image: prom/prometheus:v2.52.0
    ports:
      - "9090:9090"
    volumes:
      - ./ops/observability/prometheus-local.yml:/etc/prometheus/prometheus.yml:ro
    extra_hosts:
      - "host.docker.internal:host-gateway"   # reach query-web on the host

  grafana:
    image: grafana/grafana:10.4.3
    ports:
      - "3000:3000"
    environment:
      GF_AUTH_ANONYMOUS_ENABLED: "true"
      GF_AUTH_ANONYMOUS_ORG_ROLE: Admin
    volumes:
      - ./ops/observability/grafana-datasource.yml:/etc/grafana/provisioning/datasources/prometheus.yml:ro
```

**`ops/observability/prometheus-local.yml`**:

```yaml
global:
  scrape_interval: 15s

scrape_configs:
  - job_name: query-web
    static_configs:
      - targets: ["host.docker.internal:8000"]   # adjust port if needed
    metrics_path: /metrics

  - job_name: polling-worker
    static_configs:
      - targets: ["host.docker.internal:9100"]   # if/when worker exposes metrics
    metrics_path: /metrics
```

**`ops/observability/grafana-datasource.yml`**:

```yaml
apiVersion: 1
datasources:
  - name: Prometheus
    type: prometheus
    url: http://prometheus:9090
    isDefault: true
```

Start with: `docker compose -f docker-compose.observability.yml up -d`

No Kubernetes, no OTel collector, no cloud credentials needed locally. Grafana at `http://localhost:3000`.

---

### Azure: wiring the existing AMW

Azure Managed Prometheus (AMW) uses an OTel-compatible remote-write or Azure Monitor Agent scrape rule rather than a raw Prometheus scrape. The Azure Terraform stacks now wire the existing AMW into the Container App Environment and route Container App platform logs to Log Analytics.

**1. Data Collection Endpoint (DCE) + Data Collection Rule (DCR)**

Azure needs a DCE and DCR to route Container App metrics into AMW.

```hcl
# infra/terraform/azure/modules/observability/main.tf  (additions)

resource "azurerm_monitor_data_collection_endpoint" "this" {
  name                = "dce-${var.workspace_name}"
  location            = var.location
  resource_group_name = var.resource_group_name
  tags                = var.tags
}

resource "azurerm_monitor_data_collection_rule" "prometheus" {
  name                = "dcr-prometheus-${var.workspace_name}"
  location            = var.location
  resource_group_name = var.resource_group_name
  tags                = var.tags

  data_collection_endpoint_id = azurerm_monitor_data_collection_endpoint.this.id

  destinations {
    monitor_account {
      monitor_account_id = azurerm_monitor_workspace.prometheus.id
      name               = "amw-destination"
    }
  }

  data_flow {
    streams      = ["Microsoft-PrometheusMetrics"]
    destinations = ["amw-destination"]
  }

  data_sources {
    prometheus_forwarder {
      name    = "PrometheusDataSource"
      streams = ["Microsoft-PrometheusMetrics"]
    }
  }
}
```

**2. Associate the AMW default DCR and DCE to the Container App Environment**

For Azure Monitor managed Prometheus on Container Apps, the CAE should use the Azure Monitor Workspace's default ingestion settings.
That means associating both:
- the AMW default DCR
- the AMW default DCE

`openTelemetryConfiguration` is a separate OTLP push-export feature and is not used here.

```hcl
resource "azurerm_monitor_data_collection_rule_association" "cae" {
  name                    = "dcra-cae-prometheus"
  target_resource_id      = azurerm_container_app_environment.this.id
  data_collection_rule_id = azurerm_monitor_workspace.prometheus.default_data_collection_rule_id
}

resource "azurerm_monitor_data_collection_rule_association" "cae_dce" {
  target_resource_id          = azurerm_container_app_environment.this.id
  data_collection_endpoint_id = azurerm_monitor_workspace.prometheus.default_data_collection_endpoint_id
}
```

**3. Diagnostic settings for platform logs**

```hcl
resource "azurerm_monitor_diagnostic_setting" "query_web" {
  name                       = "diag-query-web"
  target_resource_id         = azurerm_container_app.query_web[0].id
  log_analytics_workspace_id = var.log_analytics_workspace_id

  enabled_log { category = "ContainerAppConsoleLogs" }
  enabled_log { category = "ContainerAppSystemLogs" }

  enabled_metric { category = "AllMetrics" }
}
```

Repeat for `confluence_poller` and `ingestion` job resources.

**Summary of Azure infra additions:**

| Resource | File | Status |
|---|---|---|
| `azurerm_monitor_data_collection_endpoint` | `modules/observability/main.tf` | ✅ implemented |
| `azurerm_monitor_data_collection_rule` (Prometheus) | `modules/observability/main.tf` | ✅ implemented |
| `azurerm_monitor_data_collection_rule_association` (CAE -> DCR) | `modules/agent_hosting/main.tf` | ✅ implemented |
| `azurerm_monitor_data_collection_rule_association` (CAE -> DCE) | `modules/agent_hosting/main.tf` | ✅ implemented |
| `azurerm_monitor_diagnostic_setting` (ingestion/query_web/confluence_poller) | `modules/agent_hosting/main.tf` | ✅ implemented |

### Current Status (Paused)

Status: **Implementation does not currently produce data in AMW and is paused for now.**

As of 2026-05-02:

- `/metrics` endpoint is reachable and returns valid Prometheus payload.
- CAE is associated to AMW default DCR and DCE.
- EasyAuth excludes `/metrics`.
- AMW Prometheus endpoint queries (`up`, application metric names) return empty vectors.

This indicates the remaining issue is not Terraform wiring syntax but unresolved runtime/service ingestion behaviour. Resume later with support-assisted investigation if needed.

---

### AWS: Prometheus on ECS / EKS

If services are deployed on ECS (Fargate) or EKS the approach differs by compute:

**ECS Fargate (sidecar pattern)**

ECS has no built-in Prometheus scraper. The AWS Terraform stack now provisions an AMP workspace, grants the ECS task role permission to remote-write, and injects an ADOT (AWS Distro for OpenTelemetry) sidecar into the `query_web` task definition so it can:

1. Scrapes the application's `/metrics` endpoint on `localhost`.
2. Remote-writes to Amazon Managed Service for Prometheus (AMP) workspace.

```json
// Sidecar container definition fragment for ECS task
{
  "name": "adot-collector",
  "image": "public.ecr.aws/aws-observability/aws-otel-collector:v0.40.0",
  "essential": false,
  "command": ["--config=/etc/ecs/ecs-default-config.yaml"],
  "environment": [
    { "name": "AWS_REGION", "value": "ap-southeast-2" },
    { "name": "AMP_WORKSPACE_URL", "value": "https://aps-workspaces.ap-southeast-2.amazonaws.com/workspaces/<id>/api/v1/remote_write" }
  ],
  "logConfiguration": { ... }
}
```

Terraform resource additions (AWS):

```hcl
resource "aws_prometheus_workspace" "this" {
  alias = "sc3-rag-${var.naming_suffix}"
}

resource "aws_iam_role_policy" "task_amp_remote_write" {
  role = aws_iam_role.task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["aps:RemoteWrite", "aps:GetSeries", "aps:GetLabels", "aps:GetMetricMetadata"]
      Resource = var.amp_workspace_arn
    }]
  })
}
```

Current implementation note: the ECS sidecar is wired for the `query_web` service only, because that is the deployed AWS service that currently exposes `/metrics`. The ingestion task has CloudWatch logging and AMP workspace support available, but does not yet expose a scrapeable metrics endpoint.

**EKS**

Use the AWS Managed Prometheus add-on + ADOT operator, which auto-injects scrapers via `PodMonitor` / `ServiceMonitor` CRDs (same model as kube-prometheus-stack).

```hcl
resource "aws_eks_addon" "adot" {
  cluster_name = aws_eks_cluster.this.name
  addon_name   = "adot"
}
```

**Grafana (both ECS and EKS)**

Amazon Managed Grafana can be pointed at the AMP workspace via a SigV4-authenticated data source — no self-hosted Grafana needed.

Current status:

| Concern | Status |
|---|---|
| ECS AMP workspace | ✅ implemented |
| ECS task-role AMP remote-write permission | ✅ implemented |
| ECS `query_web` ADOT sidecar | ✅ implemented |
| EKS ADOT add-on / PodMonitor path | ❌ not implemented |

---

### Recommended implementation order

1. **Add `/metrics` endpoint** to `query_web` (`prometheus-client`, `metrics.py`, route in `app.py`) — no infra changes needed, immediately usable locally.
2. **Observe existing timing values** — call `.observe()` on histograms using the values already in the timing dicts, replacing inline response-only metrics.
3. **Add `docker-compose.observability.yml`** — unblocks local dashboarding without cloud dependency.
4. **Add `azurerm_monitor_diagnostic_setting`** for each Container App — low-risk, routes platform logs to existing LAW.
5. **Add DCE + DCR + associations** in Azure infra — wires AMW to actually receive application Prometheus metrics.
6. **Add OTel SDK** for trace propagation — highest effort, lowest immediate operational value; defer until dashboards are stable.

