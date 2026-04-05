# Agent Assessment Orchestration Design

## Purpose

Define how assessment agents should detect tagged or directed requests in target environments such as Confluence and SharePoint, retrieve the relevant content through MCP servers, assess it against Corpus A and Corpus B grounding data, and return results safely.

This document is a design decision, not an implementation-complete runtime specification.

Related contract artefacts:

- Shared schemas: `docs/contracts/shared-schemas.yaml`
- Standards-based skills: `.agents/skills/` (canonical source)
- Queue hand-off contract: `docs/contracts/orchestrator-queue-message.yaml`
- Provider event contracts:
  - `docs/contracts/provider-events-sharepoint.yaml`
  - `docs/contracts/provider-events-confluence.yaml`
  - `docs/contracts/provider-events-email.yaml`
- MCP tool contracts:
  - `docs/contracts/mcp-sharepoint-tools.yaml`
  - `docs/contracts/mcp-confluence-tools.yaml`
  - `docs/contracts/mcp-email-tools.yaml`
- Ownership ADR: `docs/adr/0001-assessment-orchestrator-mcp-boundary.md`

Initial runtime scaffold:

- `runtime/assessment_orchestration/models.py`
- `runtime/assessment_orchestration/validators.py`
- `runtime/assessment_orchestration/queue.py`
- `runtime/assessment_orchestration/intake.py`
- `runtime/assessment_orchestration/interfaces.py`
- `runtime/assessment_orchestration/mcp/`

Current scaffold progress notes:

- Intake adapter functions now convert provider/email trigger payloads into validated `AssessmentJob` and `QueueMessage` objects.
- Email MCP stub now includes deterministic notification parsing and recipient normalisation helpers to support intake and delivery workflows.
- Runtime wiring now discovers the standards-based skill pack from `.agents/skills/` and maps orchestrator stages to skill identifiers for execution trace metadata.

## Goals

- Support event-driven assessment when an agent is tagged or mentioned on a page or document.
- Support direct user-requested assessment of target content.
- Ensure access to target systems is mediated by MCP servers rather than direct agent-to-platform integrations.
- Preserve least privilege and auditable access boundaries.
- Support both inline response publication and email-only response patterns.

## Non-Goals

- Full provider-specific implementation details for every Confluence or SharePoint API variant.
- A single universal MCP server for all providers.
- Replacing the existing query web workflow for ad hoc interactive questioning.

## Decision Summary

Implementation-status note (April 2026): this document uses agent / skills / MCP
language as the target architecture pattern, but the current production runtime is
deterministic Python orchestration (non-agentic AI), with skills used for
development/governance traceability and provider "MCP servers" currently operating
closer to in-process adapters than fully externalised MCP client/server components.

For fuller current-state details, see:

- `Current Implementation Status (April 2026)` under `Required Agent Skills`
- `MCP Maturity Note (Current Stage)` under `MCP Server Boundary Decision`

The preferred architecture is:

1. Use a dedicated assessment orchestrator service.
2. Use provider-specific MCP servers for Confluence, SharePoint, and email.
3. Keep MCP servers responsible for:
   - provider authentication and token handling
   - access checks and identity mode enforcement
   - normalised read and write operations against the provider
   - webhook/event intake adaptation where needed
4. Keep the orchestrator responsible for:
   - job intake and scheduling
   - correlation and audit trail
   - retrieval from Corpus A and Corpus B
   - compliance assessment prompt execution
   - response policy selection (comment, email, or both)
   - retry, timeout, and escalation handling
5. Do not place compliance assessment logic inside MCP servers.

This draws a clean boundary: MCP servers are integration and access-control adapters; the orchestrator is the assessment brain and policy engine.

## Confluence Authentication Strategy (April 2026)

## Confluence Trigger Mechanism (April 2026)

### Decision: CQL Polling As Primary Trigger

Direct provider webhooks require a public ingress callback endpoint, which conflicts with the fully private enterprise deployment mode. The selected approach is CQL polling from within the private boundary using the Confluence REST API `mention` operator.

### How CQL Polling Works

The Confluence MCP server calls `GET /wiki/rest/api/search` with a CQL query on a configurable schedule (recommended: 60–90 seconds). The query uses the structured `mention` CQL operator to find any page or comment where the service account has been @mentioned, filtered to a configured since timestamp:

```
mention = "<SERVICE_ACCOUNT_ATLASSIAN_ID>" AND created >= "2026-04-04 10:00" AND space.key IN ("SEC", "COMP") ORDER BY created ASC
```

Results are normalised into structured mention events and a watermark is advanced to the latest `occurred_at` after each successful poll. The watermark should be persisted by the caller (e.g. in Cosmos DB or Redis) so polling survives process restarts without re-processing old mentions.

### Service Account User Setup (Required)

The API token service account needs to be provisioned as a real Confluence user so that users can @mention it:

1. Create a real mailbox (e.g. `compliance-agent@yourorg.com`) — the synthetic Atlassian auth account cannot receive email or appear in the @mention picker.
2. Register a Confluence user with that email address and a recognisable display name (e.g. "Compliance Agent").
3. Assign this user the minimum required space permissions (see below).
4. Generate an API token for this user in Atlassian account settings.
5. Configure `CONFLUENCE_ACCOUNT_ID` with the Atlassian account ID of this user (visible in the Confluence user profile URL or via `GET /wiki/rest/api/user/current`).

The MCP server uses this account ID in the `mention` CQL operator. Without it the server falls back to a less reliable text search.

### Minimum Space Permissions Required

| Permission | Why |
|---|---|
| View (read) on each approved space | CQL mention polling, content retrieval |
| Add Comment on each approved space | Write assessment back as comment |
| View User Profiles (site-level) | Resolve @mention metadata, owner/editor identity |

Effective Confluence Cloud API scopes:
- `read:confluence-content.all`
- `write:confluence-content`
- `read:confluence-user`
- `read:confluence-space.summary`

Do not grant site-admin, space-admin, or global write rights. Restrict permissions to the explicitly approved space list only.

### Watermark Management

The `MentionPoller` class in the Confluence MCP module maintains an in-memory watermark. For production deployments:

- Persist the watermark to durable storage (Cosmos DB job-state collection or Azure Cache for Redis) after each successful poll batch.
- Restore the watermark on startup before the first poll to avoid re-processing.
- If no persisted watermark exists on first start, poll is bounded by `initial_lookback` (default: `PT1H`).

### Single-Flight Polling And Overlap Control

Polling and assessment execution are synchronous and can exceed the nominal poll interval. The runtime must enforce single-flight execution so overlapping schedule ticks do not start concurrent runs.

Required behaviour:

- Acquire a distributed lease before each run; if lease is active, skip the tick.
- Bound each poll query to a closed window at cycle start so long processing does not miss events.
- Process events oldest to youngest, preserving deterministic ordering.
- Advance watermark after each completed event (not only at batch end) to allow safe mid-backlog restart.
- Keep an idempotency cache for processed event IDs to tolerate retries and restarts.
- Do not get stuck on repeated failures; after max attempts, quarantine/log the event and continue with later events.

Recommended timing window:

- `window_start`: last committed watermark (exclusive).
- `window_end`: cycle start timestamp (inclusive).
- Query shape: `created > window_start AND created <= window_end` plus mention and space filters.

Deterministic event ordering rule:

1. Primary sort: `occurred_at` ascending (oldest first).
2. Secondary sort when timestamps match: `title` ascending (alphabetical).
3. Final tie-breaker: stable event identifier (`event_id`) ascending.

If provider-side sorting cannot express all tie-breakers, apply in-memory stable sorting before processing.

Events created during processing (`created > window_end`) are intentionally handled by the next run.

### Cosmos State Schema (Recommended)

Use a dedicated Cosmos container (for example `orchestration-state`) with partition key `/source`.

State document (`id = "confluence-poller-state"`, `/source = "confluence"`):

| Field | Type | Notes |
|---|---|---|
| `id` | string | `confluence-poller-state` |
| `source` | string | `confluence` |
| `watermark` | string | ISO 8601 UTC timestamp of last committed boundary |
| `last_success_at` | string | ISO 8601 UTC timestamp |
| `poll_count` | number | Increment on successful cycle |
| `last_error` | object/null | Last failure summary |
| `last_processed_event_id` | string/null | Last successfully processed event ID |
| `_etag` | string | Used for optimistic concurrency |

Lease document (`id = "confluence-poller-lock"`, `/source = "confluence"`):

| Field | Type | Notes |
|---|---|---|
| `id` | string | `confluence-poller-lock` |
| `source` | string | `confluence` |
| `owner_run_id` | string | UUID for active run |
| `lease_expires_at` | string | ISO 8601 UTC timestamp |
| `heartbeat_at` | string | ISO 8601 UTC timestamp |
| `_etag` | string | Required for lease renewal/release CAS updates |

Idempotency document (optional, TTL enabled):

- `id = "<event_id>"`, `/source = "confluence"`, `processed_at`, `run_id`.
- TTL 24-72h to prevent duplicate responses across retries.

Failure-tracking document (recommended, TTL enabled):

- `id = "<event_id>"`, `/source = "confluence"`, `attempt_count`, `last_error`, `last_attempt_at`, `status`.
- `status` transitions: `pending` -> `failed_retryable` -> `failed_terminal`.
- When `attempt_count >= MAX_EVENT_ATTEMPTS`, mark `failed_terminal`, emit alert log, and continue processing remaining events.

### Poll Loop Pseudocode

```python
while True:
  tick_started = utcnow()
  run_id = uuid4()

  if not try_acquire_lease(source="confluence", owner=run_id, ttl_seconds=300):
    sleep_until_next_interval()
    continue

  try:
    state = load_state(source="confluence")
    window_start = state.watermark or (tick_started - initial_lookback)
    window_end = tick_started

    events = poll_mentions(window_start_exclusive=window_start, window_end_inclusive=window_end)
    events = stable_sort(events, keys=["occurred_at", "title", "event_id"])

    for event in events:
      if already_processed(event.event_id):
        advance_watermark_if_newer(event.occurred_at, event.event_id)
        continue

      try:
        assess_target_and_post_comment(event)
        mark_processed(event.event_id, ttl_hours=48)
        clear_failure_counter(event.event_id)
        commit_state(
          source="confluence",
          watermark=event.occurred_at,
          last_processed_event_id=event.event_id,
          last_success_at=utcnow(),
          poll_count_increment=1,
        )
      except Exception as exc:
        attempts = increment_failure_counter(event.event_id, error=exc)
        if attempts >= MAX_EVENT_ATTEMPTS:
          mark_failed_terminal(event.event_id, error=exc)
          # Skip poison event and continue so backlog is not blocked forever.
          continue
        # Retryable failure: do not advance watermark past this event.
        break

    # If all events in this window completed or were terminal-skipped,
    # watermark can be safely aligned to window_end.
    maybe_commit_window_end_boundary(window_end)

  except Exception as exc:
    record_last_error(source="confluence", error=exc)
    # Do not advance watermark beyond the last confirmed event.

  finally:
    release_lease(source="confluence", owner=run_id)

  sleep_until_next_interval()
```

Operational note: keep poller replica count at 1 for the first release. The lease remains mandatory as a guardrail against accidental scale-out, restarts, or future scheduler-based triggers.

### CQL Polling Configuration

| Env var | Purpose |
|---|---|
| `CONFLUENCE_ACCOUNT_ID` | Atlassian account ID of the service account user — required for structured `mention` CQL queries |
| `CONFLUENCE_BASE_URL` | Confluence site base URL |
| `CONFLUENCE_API_TOKEN` | API token for the service account |
| `CONFLUENCE_AUTH_MODE` | `basic` or `bearer` (default: `basic`) |

Space allowlist and polling interval are configured at the MCP server level, not via provider credentials.

### Current Implementation Direction

- Continue with service-account based app-only access for Confluence using API-token backed authentication (basic/bearer at the provider boundary).
- Treat this as the default and supported path for enterprise deployment, including private-network deployments.
- Keep delegated OAuth support out of the critical path for now.

### Why OAuth Is Deferred

- Atlassian OAuth 3LO onboarding requires app registration and site-level admin consent workflows that are often impractical for broad enterprise rollout.
- The trust relationship is tenant-specific and coupled to customer-managed app configuration, which reduces portability of a generic multi-client solution.
- OAuth 3LO callback handling introduces public ingress requirements, which conflicts with private-network-only deployment modes unless a separate public broker pattern is added.
- Many customer teams are not equipped to self-manage developer-portal app registration, callback URLs, scopes, and consent operations.

### Use-Case Mapping

- Mention/comment-triggered review across approved Confluence spaces:
  - Use app-only service-account execution.
  - Enforce scope allowlists and auditing in MCP + orchestrator.
- Explicit user-requested review in user context:
  - Target delegated identity mode in future.
  - Requires a dedicated OAuth onboarding and callback architecture.

### Deferred Scope

- OAuth delegated flow remains a planned capability, not a near-term dependency.
- The team should continue building and hardening the app-only Confluence path, including provider normalisation, auditing, and policy enforcement.

### Auth Mode Matrix

| Mode | Credential Shape | Current Status | Best Fit | User Context | Private-Network Compatibility | Notes |
|---|---|---|---|---|---|---|
| basic | Service account email + API token | Supported now | Direct Confluence site API integration where basic auth is accepted | No | High | Operationally simple but depends on endpoint-level basic-auth acceptance. |
| bearer | Service account API token in Authorisation header | Supported now (default path) | App-only automation across approved spaces using `api.atlassian.com/ex/confluence/{cloudId}` | No | High | Recommended baseline for current rollout. |
| oauth (client credentials) | Client ID + client secret (token endpoint) | Implemented in code, not production-ready | Future app-only alternative if tenant app registration and scope governance are mature | No | Medium | Tenant onboarding and scope behaviour vary; treat as exploratory. |
| oauth (3LO delegated) | User consent via browser callback | Deferred | Explicit user-requested review under user identity | Yes | Low without public broker | Requires app registration, callback URLs, tenant consent, and public ingress design. |

Interpretation for current roadmap:

- Proceed with bearer/basic for enterprise rollout and private-network scenarios.
- Keep OAuth delegated (3LO) parked until customer onboarding and callback architecture are productised.
- Keep OAuth client-credentials as optional future capability, not a dependency for near-term delivery.

## Why This Boundary

If the MCP server does too little, the orchestrator must embed provider-specific logic and permissions handling, which defeats the point of the abstraction.

If the MCP server does too much, the compliance logic becomes duplicated per provider and harder to test, govern, and evolve consistently.

The correct split is for MCP servers to expose provider-normalised operations, while the orchestrator owns the assessment workflow.

## Target Components

### 1. Assessment Orchestrator

Responsibilities:

- Accept assessment requests from triggers, polling, or direct user action.
- Resolve whether the request is system-triggered or user-delegated.
- Call the relevant MCP server to fetch the target content and metadata.
- Retrieve matching controls from Corpus A.
- Retrieve interpretive context from Corpus B.
- Generate structured assessment output.
- Decide delivery path:
  - write back as page comment
  - send by email
  - do both
- Persist job state, audit metadata, and delivery outcome.

Suggested deployment pattern:

- Azure Container Apps or Azure Functions for orchestration API and workers.
- Azure Queue or Service Bus for decoupled execution.
- Cosmos DB or SQL for job state and audit records.

### 2. Provider MCP Servers

At minimum:

- Confluence MCP server
- SharePoint / Microsoft 365 MCP server
- Email MCP server

Responsibilities:

- Hold provider-specific integration logic.
- Enforce whether the call is app-only or delegated.
- Expose normalised tools such as:
  - `get_content_by_id`
  - `get_content_by_url`
  - `get_recent_mentions`
  - `get_flagged_item_context`
  - `post_comment`
  - `send_email`
  - `resolve_page_owner`
  - `resolve_last_editor`
  - `check_user_access`
- Return normalised metadata including:
  - provider
  - object ID
  - canonical URL
  - title
  - owner
  - last editor
  - mentioner/requester
  - permissions context
  - version/modified time

### 3. Assessment Agent

This is the logical agent that performs the assessment task. It may be implemented as one agent with tools or as a small agent set.

Preferred initial model:

- One primary Assessment Agent
- One orchestration runtime around it

Optional later split:

- Trigger Intake Agent
- Content Retrieval Agent
- Assessment Agent
- Delivery Agent

The initial single-agent model is simpler and should be preferred unless workload or policy separation demands multiple agents.

## Required Agent Skills

### Current Implementation Status (April 2026)

The agent / skills / MCP pattern is being used as an architectural guide, but the
current runtime is deterministic Python orchestration rather than agentic AI.

Current-state clarification:

- Skills are defined and mapped for design discipline, stage traceability, and governance consistency.
- Skills do not currently drive autonomous runtime behaviour or dynamic tool planning.
- Assessment and delivery paths are implemented through explicit code paths and deterministic branching.
- This means the present value of skills is primarily development and governance support, with associated maintenance overhead.

MCP boundary clarification for this stage:

- Current provider "MCP servers" are closer to in-process provider adapters used by the orchestrator runtime.
- They provide normalised provider access and policy enforcement, but are not yet a fully realised MCP client/server deployment pattern.
- For a reference on a full MCP architecture model, see:
  - https://modelcontextprotocol.io/docs/learn/architecture

Yes, defining required skills improves implementation quality.

Without explicit skills, too much behaviour is left inside broad prompt instructions, which makes the system harder to test, harder to govern, and more likely to drift between providers or delivery paths.

Skills should be treated as capability contracts for the logical Assessment Agent and any future split agents.

### Why Skills Help

- They make agent responsibilities explicit rather than implied.
- They support reuse across Confluence, SharePoint, email, and future providers.
- They allow narrower testing of individual behaviours.
- They reduce prompt sprawl by separating workflow capability from task wording.
- They make it easier to decide what belongs in the orchestrator, the agent, and the MCP server.

### Recommended Skill Set

The initial implementation should define at least these logical skills.

#### 1. Trigger Intake Skill

Purpose:

- Interpret incoming mention, tag, email, or user-request events into a normalised assessment job.

Responsibilities:

- classify trigger type
- extract provider and target reference
- identify requester or mentioner
- determine whether request is delegated or app-only
- populate the assessment job contract

Inputs:

- email notification payload
- provider event payload
- direct user request payload

Outputs:

- normalised assessment job record

#### 2. Content Resolution Skill

Purpose:

- Resolve page, file, or object references into a concrete provider target before retrieval.

Responsibilities:

- normalise URLs and IDs
- choose the correct MCP server
- request content metadata lookup
- detect unsupported target types early

Inputs:

- job target reference

Outputs:

- resolved provider object metadata

#### 3. Access Validation Skill

Purpose:

- Ensure the request is executed under the correct access mode and that access constraints are enforced.

Responsibilities:

- confirm delegated vs app-only mode
- require delegated mode for user-initiated requests
- reject silent privilege escalation
- record access decision in audit trail

Inputs:

- requester identity context
- provider target metadata
- requested operation

Outputs:

- access decision
- identity mode used

#### 4. Content Retrieval Skill

Purpose:

- Retrieve the flagged content and relevant surrounding context through the MCP server.

Responsibilities:

- retrieve body, title, URL, metadata, owner, and last editor
- optionally retrieve adjacent comments or discussion context
- normalise provider response into orchestrator-ready form

Inputs:

- resolved target
- identity mode

Outputs:

- normalsed assessed artifact package

#### 5. Corpus Retrieval Skill

Purpose:

- Retrieve relevant Corpus A controls and Corpus B guidance for the target artifact.

Responsibilities:

- generate retrieval query from the target content
- retrieve authoritative requirements from Corpus A
- retrieve interpretive guidance from Corpus B
- apply any framework or precedence constraints

Inputs:

- assessed artifact package

Outputs:

- corpus grounding package

#### 6. Assessment Skill

Purpose:

- Produce structured assessment findings from the target artifact and grounding package.

Responsibilities:

- compare target artifact content against Corpus A requirements
- use Corpus B only as interpretive context
- generate structured findings with citations
- mark missing evidence explicitly
- attach schema version and validation state

Inputs:

- assessed artifact package
- corpus grounding package

Outputs:

- structured assessment report
- markdown rendering
- delivery summary

#### 7. Delivery Decision Skill

Purpose:

- Determine where the assessment should be returned.

Responsibilities:

- apply delivery policy
- choose inline comment, email, or both
- resolve email recipients according to policy order
- decide fallback path when inline write-back fails

Inputs:

- structured assessment report
- job metadata
- provider capabilities

Outputs:

- delivery plan

#### 8. Publication Skill

Purpose:

- Execute the selected delivery plan through MCP servers.

Responsibilities:

- post comments
- send emails
- capture delivery result and failures
- write delivery audit metadata

Inputs:

- delivery plan
- report artifacts

Outputs:

- delivery outcome record

#### 9. Audit And Trace Skill

Purpose:

- Ensure every assessment can be reconstructed and governed.

Responsibilities:

- assign correlation IDs
- record provider, target, actor, and identity mode
- record retrieval and delivery decisions
- persist validation failures and retries

Inputs:

- all orchestrator stage outputs

Outputs:

- audit events and job state updates

### Skill Ownership Model

Not every skill must be implemented as a separate agent.

Recommended initial ownership:

- Orchestrator workflow code:
  - Trigger Intake Skill
  - Access Validation Skill
  - Delivery Decision Skill
  - Audit And Trace Skill
- Assessment Agent:
  - Corpus Retrieval Skill
  - Assessment Skill
- MCP servers:
  - Content Resolution Skill support
  - Content Retrieval Skill support
  - Publication Skill support

This is important: MCP servers support some skills, but they should not own the skill policy or assessment reasoning.

### Minimum Skill Contract Requirements

Each skill should eventually define:

- purpose
- inputs
- outputs
- failure modes
- idempotency expectations
- audit fields produced
- whether it may run under app-only mode, delegated mode, or both

### Recommendation

Add skills to the implementation design now.

The design should continue to describe components and flows, but skills provide the operational layer that makes the design buildable. They will improve implementation by making agent capability boundaries explicit and testable before provider-specific code is written.

The canonical skill implementation pack is defined in `.agents/skills/*/SKILL.md` using the open Agent Skills format.

Shared normalised object shapes are defined in `docs/contracts/shared-schemas.yaml`.

## Triggering Model

Two request classes are required.

### A. Platform-Triggered Assessment

Examples:

- User tags the agent on a Confluence page.
- User mentions the agent in a SharePoint comment or request field.
- A workflow sends a notification email to the agent inbox.

Recommended flow:

1. Target platform emits an event or notification.
2. If the platform cannot directly push usable events, route through email notification.
3. Email MCP server reads the agent mailbox.
4. Orchestrator parses the notification into a job.
5. Orchestrator asks the relevant provider MCP server for the referenced content.
6. Orchestrator runs the assessment.
7. Orchestrator publishes response to page, email, or both.

### B. User-Requested Assessment

Examples:

- User enters a Confluence page URL in the query web UI.
- User asks for a SharePoint document assessment from an internal app.

Recommended flow:

1. User authenticates to the request surface.
2. The orchestrator receives the request with user identity context.
3. The relevant MCP server uses delegated access for that user.
4. The MCP server confirms the user can read the target content.
5. Orchestrator performs the assessment.
6. Results are returned to the requesting user, and optionally posted back to the page if policy allows.

## Authentication And Identity Model

Two identity modes are required.

### 1. App-Only Mode

Use for system-triggered assessments where the platform itself has requested agent action.

Characteristics:

- Service identity or provider app identity.
- Used to read flagged content and optionally post comments.
- Must be scope-limited to approved sites, spaces, or content roots.
- Must be auditable as non-human access.

### 2. Delegated Mode

Use for user-initiated assessments.

Characteristics:

- Provider access is evaluated using the requesting user identity.
- Prevents a user from assessing content they cannot access.
- The orchestrator must pass delegated token context or a token reference to the MCP server.
- The MCP server must reject fallback to app-only mode for delegated requests.

Decision:

- User-requested assessments must use delegated access.
- Platform-triggered assessments may use app-only access, but only for approved provider scopes.

## MCP Server Boundary Decision

### MCP Maturity Note (Current Stage)

The design intentionally follows MCP-style boundaries, but implementation maturity
is currently adapter-first:

- The runtime calls provider integration components in-process.
- Transport-level MCP concerns such as independent MCP server lifecycle, externalised MCP transport boundaries, and broader tool-host separation are only partially represented.
- This is a pragmatic interim step, not the final target architecture.

The target direction remains provider MCP servers as clean capability boundaries,
with progression over time toward a more fully realised MCP deployment model.

Decision:

- MCP servers should do more than connection management.
- They should own normalsed provider actions and permission enforcement.
- They should not own compliance retrieval or assessment logic.

### MCP Server Responsibilities

- OAuth/app token acquisition and refresh
- delegated vs app-only mode enforcement
- provider API wrapping
- provider object lookup by URL or ID
- content/body retrieval
- metadata retrieval
- owner/editor resolution
- comment publication
- email send/read for inbox-trigger workflow

The first provider MCP tool contract sets are defined in:

- `docs/contracts/mcp-sharepoint-tools.yaml`
- `docs/contracts/mcp-confluence-tools.yaml`
- `docs/contracts/mcp-email-tools.yaml`

### Orchestrator Responsibilities

- queueing
- deduplication of repeated triggers
- idempotency keys
- corpus retrieval
- prompt assembly
- structured output validation
- delivery policy selection
- audit log and retry handling

## Delivery Pattern Decision

Default recommendation:

- Support both inline page response and email response.
- Make email fallback mandatory when inline comment write-back is unavailable or denied.

### Inline Response

Best when:

- Teams want assessment context attached to the page.
- The agent has write/comment rights.
- There is value in visible collaborative discussion.

Risks:

- Over-sharing results on broadly readable pages.
- Provider formatting constraints.
- Comment spam on frequently edited pages.

### Email Response

Best when:

- Assessment contains sensitive findings.
- Platform write-back is restricted.
- The provider has inconsistent comment APIs.

Risks:

- Reduced visibility to collaborators.
- Harder to maintain page-local audit trail.

Decision:

- Implement a delivery policy per provider/workspace.
- Policy values should be:
  - `inline_only`
  - `email_only`
  - `inline_and_email`
  - `inline_else_email`

Preferred default:

- `inline_else_email`

## Email Recipient Decision

When email delivery is used, recipients should not be guessed loosely.

Decision order:

1. Explicit requester or mentioner if present.
2. Workflow-configured owner override if present.
3. Content owner.
4. Last editor.
5. Shared team mailbox if none of the above can be resolved safely.

Rules:

- Do not default to broad distribution lists.
- Do not infer recipients from document viewers.
- Record exactly why each recipient was selected.

## Event Intake Options

### Preferred

- Provider-native webhook or event subscription into orchestrator or an intake function.

The first normalised provider event contract set is defined in:

- `docs/contracts/provider-events-sharepoint.yaml`
- `docs/contracts/provider-events-confluence.yaml`
- `docs/contracts/provider-events-email.yaml`

Important private-network constraint:

- In a fully private deployment, provider-hosted webhook callbacks usually cannot reach an internal-only orchestrator directly.
- This preference only holds if there is an approved ingress pattern for event delivery.
- Do not assume public SaaS platforms such as Confluence Cloud or Microsoft 365 can call private endpoints inside the VNet without an explicit bridging design.

Implication:

- If the solution remains private-only with no public callback surface, direct provider webhooks will generally not be available.
- In that case, use one of these patterns instead:
  - email notification intake through the Email MCP server
  - provider polling through an MCP server running inside the private boundary
  - an approved public event intake endpoint that immediately relays events into the private orchestrator through a controlled queue or broker

Security note:

- If a public webhook intake endpoint is introduced, it must be treated as a narrow event-ingress component rather than a general public API.
- It should perform source validation, authentication where supported, payload minimisation, and immediate relay into private processing.
- The assessment orchestrator itself should still remain private unless there is a separate approved reason to expose it.

### Fallback

- Email notifications to agent mailbox, consumed through Email MCP server.

Decision:

- Prefer provider-native events where reliable and supportable.
- In fully private deployments, reinterpret this as "prefer provider-native events only when there is an approved ingress or relay path".
- Use email-driven intake as a compatibility fallback, not the primary control plane, unless the target platform cannot supply reliable structured events.

## Deployment Mode Distinctions

The intake pattern should vary by deployment mode rather than assuming one default works everywhere.

### 1. Fully Private Enterprise Mode

Characteristics:

- Orchestrator and MCP servers run inside a private network boundary.
- No public callback endpoint is exposed for SaaS webhook delivery.
- Enterprise ingress is tightly controlled or disallowed.

Recommended intake pattern:

- Primary: provider polling from within the private boundary, or email-trigger intake.
- Secondary: provider-native webhook only if an approved relay or ingress component exists.

Implications:

- Direct SaaS webhook callback to the orchestrator is normally not possible.
- Email MCP and provider polling become the most practical baseline options.
- Delegated user-requested assessment remains straightforward because the request starts from an authenticated internal surface.

Preferred delivery posture:

- Keep orchestrator private.
- Keep MCP servers private.
- Avoid exposing the assessment control plane publicly.

### 2. Hybrid Mode With Controlled Public Ingress

Characteristics:

- Core orchestrator and MCP servers remain private.
- A narrow public ingress component is allowed for event intake.
- Public ingress is designed only for verified provider callbacks or relay submission.

Recommended intake pattern:

- Primary: provider-native webhooks into a dedicated public ingress or intake function.
- Secondary: email-trigger intake where provider webhook capability is weak or inconsistent.

Implications:

- This is the cleanest way to preserve private processing while still using provider-native events.
- The public component must be minimal, authenticated or source-validated where possible, and should immediately relay into private queues or brokers.
- The orchestrator itself should still remain private.

Preferred delivery posture:

- Public ingress only for intake.
- Private processing for retrieval, assessment, and publication.

### 3. Standalone Or Sandbox Mode

Characteristics:

- Simpler deployment posture.
- Security controls may be narrower in scope than enterprise production.
- Used for prototyping, evaluation, or isolated demo environments.

Recommended intake pattern:

- Provider-native webhook intake is acceptable if the exposure is explicitly approved for the sandbox.
- Email-trigger intake remains a valid fallback.

Implications:

- This mode is operationally simpler, but should not automatically become the enterprise production reference model.
- Any public ingress introduced here should be treated as environment-specific and not assumed acceptable in private production deployments.

### Recommendation By Mode

- Fully private enterprise mode: prefer polling or email-trigger intake; use provider-native webhooks only with an approved relay path.
- Hybrid mode with controlled public ingress: prefer provider-native webhooks through a narrow public intake component.
- Standalone or sandbox mode: provider-native webhooks are acceptable when explicitly approved for that environment.

## End-To-End Flows

### Flow 1: Mention On Confluence Page

1. User tags agent in Confluence.
2. Confluence MCP server polls CQL for new mentions of the service account (every 60–90 seconds).
3. CQL match returned; poller normalises result into a `mention_notification` event and advances watermark.
4. Intake layer creates job with provider, page ID, mentioner account ID, and correlation ID.
5. Orchestrator calls Confluence MCP server in app-only mode to retrieve page content.
6. MCP returns page content, comments, metadata, owner, and last editor.
7. Orchestrator retrieves Corpus A and B context.
8. Assessment Agent generates structured findings.
9. Orchestrator writes page comment via MCP server.
10. If comment fails, orchestrator sends email via Email MCP server.

### Flow 2: User Requests Assessment Of SharePoint Document

1. User submits document URL from internal UI.
2. UI passes user identity context to orchestrator.
3. Orchestrator invokes SharePoint MCP server in delegated mode.
4. MCP validates user can read the document.
5. MCP returns normalised document content and metadata.
6. Orchestrator retrieves Corpus A and B context.
7. Assessment Agent generates findings.
8. Results return to the user in UI, and optional write-back occurs only if policy permits.

## Minimum Data Contract For An Assessment Job

```json
{
  "job_id": "uuid",
  "source_type": "confluence|sharepoint|email|manual_request",
  "provider": "confluence|sharepoint|m365|email",
  "target_id": "provider-object-id",
  "target_url": "https://...",
  "trigger_type": "mention|tag|email_notification|user_request",
  "request_identity_mode": "app_only|delegated",
  "requester_id": "optional-user-id",
  "requester_email": "optional-email",
  "delivery_policy": "inline_else_email",
  "response_target": {
    "commentable": true,
    "email_recipients": ["owner@example.com"]
  },
  "status": "queued|running|completed|failed",
  "correlation_id": "trace-id"
}
```

## Security Requirements

- Every MCP call must declare whether it is delegated or app-only.
- Delegated requests must never silently downgrade to app-only.
- Provider scopes must be resource-limited where possible.
- Assessment outputs may include sensitive findings; delivery path must respect confidentiality policy.
- All write-back operations must be auditable.

## Recommended Implementation Sequence

### Phase 1

- Define orchestrator job contract.
- Build Email MCP server.
- Build one provider MCP server first, preferably SharePoint/M365 if delegated access is easiest to establish in the tenant.
- Implement email-only response mode first.

### Phase 2

- Add inline write-back support.
- Add provider-native webhook intake.
- Add deduplication and idempotency handling for repeated mentions.

### Phase 3

- Add Confluence MCP support if not first.
- Add delivery policies and recipient resolution rules.
- Add richer policy controls for sensitive assessments.

## Recommendation

Start with one assessment orchestrator and provider-specific MCP servers.

Use this decision model:

- MCP server: provider access, permission checks, normalised CRUD-like tools.
- Orchestrator: intake, retrieval, assessment, validation, delivery, and audit.
- Delegated mode for user-driven requests.
- App-only mode only for system-triggered requests within approved content scopes.
- Default delivery policy: `inline_else_email`.

This gives the cleanest security model and the lowest long-term maintenance cost.