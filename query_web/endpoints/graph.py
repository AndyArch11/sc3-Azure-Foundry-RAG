"""Graph build/query/export endpoints for local relationship-graph baseline."""

from __future__ import annotations

import json
import logging
import os
import re
import statistics
import time
from collections import deque
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from query_web.endpoints.problem_details import problem_response as _problem_response
from query_web.graph_backend import GraphStoreBackend, create_graph_store, resolve_graph_backend
from query_web.graph_batch import build_local_graph_artifacts
from query_web.metrics import observe_graph_operation
from query_web.pipeline.search import _client_search
from query_web.request_context import get_correlation_id

logger = logging.getLogger(__name__)


class GraphBuildRequest(BaseModel):
    """Request model for local graph build endpoint.

    Attributes:
        controls: List of control dictionaries.
        chunks: List of chunk dictionaries.
        output_dir: Optional output directory for graph artifacts.
        persist_store: Whether to persist the graph store.
        auth_token: Authentication token for the request."""

    controls: list[dict[str, Any]] = Field(default_factory=list)
    chunks: list[dict[str, Any]] = Field(default_factory=list)
    output_dir: str | None = None
    persist_store: bool = True
    min_community_size: int = Field(default=3, ge=2, le=100)
    auth_token: str = ""


class GraphQueryRequest(BaseModel):
    """Bounded read-only graph query for external agent/MCP consumers."""

    seed_node_id: str | None = Field(default=None, max_length=256)
    depth: int = Field(default=1, ge=1, le=8)
    max_nodes: int = Field(default=200, ge=1, le=5000)
    max_edges: int = Field(default=500, ge=1, le=5000)
    framework: str | None = Field(default=None, max_length=128)
    node_type: str | None = Field(default=None, max_length=128)
    edge_type: str | None = Field(default=None, max_length=128)
    community: str | None = Field(default=None, max_length=128)
    min_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    auth_token: str = ""


def _utc_now_iso() -> str:
    """Return current UTC time in ISO format.

    Returns:
        A string representing the current UTC time in ISO 8601 format.
    """

    return datetime.now(UTC).isoformat()


def _graph_audit_payload(
    *,
    operation: str,
    outcome: str,
    started_at: str,
    duration_s: float,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build common graph audit payload for responses and structured logs.

    Args:
        operation: The graph operation being performed (e.g., "build", "query").
        outcome: The outcome of the operation (e.g., "success", "unauthorised", "disabled").
        started_at: The ISO timestamp when the operation started.
        duration_s: The duration of the operation in seconds.
        details: Optional dictionary containing additional details about the operation.
    Returns:
        A dictionary containing the audit payload for the graph operation.
    """

    payload = {
        "operation": operation,
        "outcome": outcome,
        "started_at": started_at,
        "duration_s": round(max(0.0, float(duration_s)), 6),
        "correlation_id": get_correlation_id(),
    }
    if details:
        payload["details"] = details
    return payload


def register_graph_endpoints(
    app: FastAPI,
    svc: Any | None = None,
    *,
    deps: dict[str, Any] | None = None,
) -> None:
    """Register graph build/query/export endpoints.

    Args:
        app: FastAPI application instance.
        svc: Optional service object.
        deps: Optional dependency overrides.
    """

    if svc is None:
        svc = {}

    svc_obj = svc

    class _SvcAdapter:
        def __getattr__(self, name: str) -> Any:
            if isinstance(deps, dict) and name in deps:
                candidate = deps[name]
                return candidate() if callable(candidate) else candidate
            if isinstance(svc_obj, dict):
                if name in svc_obj:
                    candidate = svc_obj[name]
                    return candidate() if callable(candidate) else candidate
                raise AttributeError(name)
            return getattr(svc_obj, name)

    svc = _SvcAdapter()

    def _authorised_or_401(request: Request, auth_token: str) -> JSONResponse | None:
        """Check if the request is authorised and return a 401 response if not.

        Args:
            request: The FastAPI request object.
            auth_token: The authentication token for the request.

        Returns:
            A JSONResponse with a 401 status code if the request is unauthorised, otherwise None.
        """
        if not bool(svc._is_authorised_request(auth_token, request)):
            return _problem_response(
                status=401,
                title="Unauthorized",
                detail=str(svc._unauthorised_message(request)),
                instance=str(request.url.path),
                type_uri="https://datatracker.ietf.org/doc/html/rfc9110#section-15.5.2",
            )
        return None

    def _default_output_dir() -> str:
        """Return the default output directory for graph artifacts.

        Returns:
            A string representing the default output directory path for graph artifacts.
        """
        root = os.getenv("GRAPH_ARTIFACTS_DIR", "local_state/graph")
        return root.strip() or "local_state/graph"

    def _graph_backend_config() -> Any:
        """Return the graph backend configuration from the service object.

        Returns:
            The graph backend configuration object from the service.
        """
        base_config = svc.config
        extra: dict[str, Any] = {}
        for attr_name in ("graph_azure_storage_client", "graph_aws_storage_client"):
            try:
                value = getattr(svc, attr_name)
            except AttributeError:
                continue
            if value is not None:
                extra[attr_name] = value
        if not extra:
            return base_config
        return SimpleNamespace(**vars(base_config), **extra)

    def _graph_store_or_503() -> tuple[GraphStoreBackend | None, JSONResponse | None]:
        """Check if the graph store can be created and return a 503 response if not.

        Returns:
            A tuple containing the graph store backend (or None) and a JSONResponse with a 503 status code if the graph store cannot be created (or None).
        """
        backend_config = _graph_backend_config()
        try:
            return create_graph_store(backend_config), None
        except (NotImplementedError, ValueError) as exc:
            backend = resolve_graph_backend(backend_config)
            return None, _problem_response(
                status=503,
                title="Service Unavailable",
                detail="The graph backend is unavailable.",
                instance="/api/graph",
                type_uri="https://datatracker.ietf.org/doc/html/rfc9110#section-15.6.4",
                extensions={"backend": backend},
            )

    def _graph_snapshot_key(prefix: str, name: str) -> str:
        """Construct a storage key for graph snapshot artifacts.

        Args:
            prefix: The prefix for the storage key.
            name: The name of the artifact file.

        Returns:
            A string representing the constructed storage key for the graph snapshot artifact.
        """
        clean_prefix = prefix.strip().strip("/")
        return f"{clean_prefix}/{name}" if clean_prefix else name

    def _safe_graph_artifact_path(path_value: str, expected_name: str) -> Path:
        """Resolve a graph artifact path and require the configured artifact root."""
        root = Path(_default_output_dir()).expanduser().resolve()
        candidate = Path(path_value).expanduser().resolve(strict=True)
        if candidate.parent != root or candidate.name != expected_name:
            raise ValueError("Graph artifact path is outside the configured artifact directory.")
        return candidate

    def _last_successful_build_at() -> str:
        """Return last successful graph build timestamp from local report file."""
        output_dir = Path(_default_output_dir())
        report_path = output_dir / "graph_build_report.json"
        if not report_path.is_file():
            return ""
        try:
            payload = json.loads(report_path.read_text(encoding="utf-8"))
        except Exception:
            return ""
        return str(payload.get("built_at") or "").strip()

    def _community_min_size() -> int:
        """Return configured minimum community size from latest build metadata."""
        default_value = max(2, int(getattr(svc.config, "graph_min_community_size", 3) or 3))
        output_dir = Path(_default_output_dir())
        report_path = output_dir / "graph_build_report.json"
        if not report_path.is_file():
            return default_value
        try:
            payload = json.loads(report_path.read_text(encoding="utf-8"))
            raw_value = (
                payload.get("community_settings", {}).get("min_community_size")
                if isinstance(payload.get("community_settings"), dict)
                else None
            )
            if raw_value is None:
                return default_value
            return max(2, int(raw_value))
        except Exception:
            return default_value

    def _resolve_graph_seed_node_id(
        store: GraphStoreBackend,
        requested_node_id: str,
    ) -> str | None:
        """Resolve a requested seed node ID to a persisted node ID.

        Attempts exact-match first, then Corpus A canonical-ID fallback,
        then a case-insensitive fallback scan over node_id/label/requirement_id.

        Args:
            store: The graph store backend to query for node existence.
            requested_node_id: The requested seed node ID to resolve.
        Returns:
            The resolved node ID if found, otherwise None.
        """
        candidate = str(requested_node_id or "").strip()
        if not candidate:
            return None
        try:
            store.get_node(node_id=candidate)
            return candidate
        except KeyError:
            pass

        # Common client input uses raw control IDs (for example PCIDSS-6_5_3)
        # while graph node IDs are persisted as a:{requirement_id_lower}.
        if ":" not in candidate:
            corpus_a_candidate = f"a:{candidate.lower()}"
            try:
                store.get_node(node_id=corpus_a_candidate)
                return corpus_a_candidate
            except KeyError:
                pass

        try:
            nodes = store.export_graph(max_nodes=500000, max_edges=1).get("nodes", [])
        except Exception:
            return None

        needle = candidate.casefold()
        matches = sorted(
            {
                str(node.get("node_id") or "").strip()
                for node in nodes
                if str(node.get("node_id") or "").strip().casefold() == needle
            }
        )
        if matches:
            return matches[0]

        alias_matches = sorted(
            {
                str(node.get("node_id") or "").strip()
                for node in nodes
                if (
                    str(node.get("label") or "").strip().casefold() == needle
                    or str(
                        (
                            (node.get("attributes") or {}).get("requirement_id")
                            if isinstance(node.get("attributes"), dict)
                            else ""
                        )
                        or ""
                    )
                    .strip()
                    .casefold()
                    == needle
                )
            }
        )
        return alias_matches[0] if alias_matches else None

    def _community_groups(
        nodes: list[dict[str, Any]],
        edges: list[dict[str, Any]],
        *,
        min_community_size: int,
    ) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
        """Identify communities in the graph based on connected components.

        Args:
            nodes: List of node dictionaries.
            edges: List of edge dictionaries.
        Returns:
            A tuple containing the annotated nodes and a dictionary of community groups with their associated metadata.
        """
        adjacency: dict[str, set[str]] = {}
        node_by_id: dict[str, dict[str, Any]] = {}
        semantic_edge_types = {
            "GUIDANCE_SUPPORTS_CONTROL",
            "GUIDANCE_RELATES_TO_FAMILY",
        }
        structural_edge_types = {
            "BELONGS_TO_FRAMEWORK",
            "IN_FAMILY",
            "DERIVED_FROM_SOURCE",
        }
        topic_stopwords = {
            "about",
            "accordance",
            "across",
            "ad",
            "address",
            "after",
            "agent",
            "all",
            "also",
            "another",
            "and",
            "applicable",
            "applied",
            "applying",
            "appropriate",
            "approved",
            "approval",
            "assessed",
            "assessment",
            "australian",
            "available",
            "auth",
            "basis",
            "before",
            "being",
            "between",
            "can",
            "cannot",
            "changes",
            "chunk",
            "classified",
            "been",
            "code",
            "control",
            "controls",
            "corpus",
            "could",
            "csf",
            "cyber",
            "document",
            "each",
            "effective",
            "effectively",
            "evidence",
            "establish",
            "examine",
            "external",
            "impact",
            "facing",
            "family",
            "following",
            "for",
            "framework",
            "frameworks",
            "from",
            "government",
            "guidance",
            "have",
            "having",
            "hoc",
            "how",
            "level",
            "important",
            "include",
            "includes",
            "including",
            "into",
            "its",
            "itself",
            "known",
            "local",
            "maintain",
            "microsoft",
            "mission",
            "mitigate",
            "multi",
            "must",
            "necessary",
            "newco",
            "nist",
            "need",
            "occur",
            "occurs",
            "office",
            "perform",
            "performing",
            "place",
            "plan",
            "policies",
            "prior",
            "program",
            "protect",
            "protected",
            "provided",
            "provides",
            "regular",
            "relevant",
            "required",
            "responsibilities",
            "restricting",
            "review",
            "performed",
            "policy",
            "process",
            "query",
            "related",
            "requirement",
            "requirements",
            "risk",
            "security",
            "should",
            "source",
            "support",
            "supports",
            "than",
            "their",
            "there",
            "these",
            "third",
            "this",
            "through",
            "types",
            "using",
            "vendor",
            "verify",
            "what",
            "when",
            "where",
            "which",
            "who",
            "whom",
            "why",
            "with",
        }
        preferred_topic_tokens = {
            "mfa",
            "authentication",
            "authorization",
            "identity",
            "credential",
            "password",
            "confidential",
            "confidentiality",
            "integrity",
            "encryption",
            "encrypt",
            "encrypted",
            "availability",
            "backup",
            "restore",
            "recovery",
            "patch",
            "patching",
            "vulnerability",
            "vulnerabilities",
            "logging",
            "monitoring",
            "incident",
            "malware",
            "firewall",
            "network",
            "endpoint",
            "privilege",
            "privileged access",
            "access",
            "asset",
            "inventory",
            "configuration",
            "external facing",
        }

        def _canonical_topic_token(token: str) -> str:
            """Normalise lexical topic variants to a canonical token.

            Handles common spelling differences and light inflection reduction
            so related topics collapse into one community bucket.
            """
            value = str(token or "").strip().lower()
            if not value:
                return ""

            explicit_map = {
                "authorisation": "authorization",
                "authorization": "authorization",
                "authorise": "authorization",
                "authorised": "authorization",
                "authorize": "authorization",
                "authorized": "authorization",
                "authentication": "authentication",
                "authentications": "authentication",
                "credential": "credential",
                "credentials": "credential",
                "backups": "backup",
                "backup": "backup",
                "restores": "restore",
                "restore": "restore",
                "recoveries": "recovery",
                "recovery": "recovery",
                "patches": "patch",
                "patching": "patch",
                "patch": "patch",
                "vulnerabilities": "vulnerability",
                "vulnerability": "vulnerability",
                "logging": "logging",
                "logs": "logging",
                "logged": "logging",
                "log": "logging",
                "encrypt": "encryption",
                "encrypted": "encryption",
                "encrypting": "encryption",
                "encryption": "encryption",
                "organisation": "organisation",
                "organization": "organisation",
                "party": "party",
                "parties": "party",
            }
            if value in explicit_map:
                return explicit_map[value]
            return value

        def _topic_tokens(node: dict[str, Any]) -> list[str]:
            """Extract lexical topic tokens from a node's label and attributes.

            Args:
                node: A dictionary representing a graph node with its attributes.
            Returns:
                A list of unique topic tokens extracted from the node's label and attributes.
            """
            node_type = str(node.get("node_type") or "").strip().casefold()
            if node_type == "sourcedocument":
                return []
            attrs = node.get("attributes") if isinstance(node.get("attributes"), dict) else {}
            parts = [
                str((attrs or {}).get("requirement_text") or ""),
                str((attrs or {}).get("guidance_text") or ""),
                str((attrs or {}).get("content") or ""),
                str((attrs or {}).get("control_family") or ""),
                str(node.get("label") or ""),
            ]
            raw = " ".join(parts).lower()
            candidates = re.findall(r"[a-z]{3,}", raw)
            filtered: list[str] = []
            seen: set[str] = set()
            for raw_token in candidates:
                token = _canonical_topic_token(raw_token)
                if token in seen:
                    continue
                if token in topic_stopwords:
                    continue
                if token in {"aescsf", "ism", "pspf", "pcidss", "essential", "eight"}:
                    continue
                if token.startswith("nist") or token.startswith("cis") or token.startswith("pspf"):
                    continue
                if len(token) >= 20:
                    # Skip likely identifier fragments that do not represent usable topics.
                    continue
                seen.add(token)
                filtered.append(token)
                if len(filtered) >= 24:
                    break
            return filtered

        for node in nodes:
            node_id = str(node.get("node_id") or "").strip()
            if not node_id:
                continue
            node_by_id[node_id] = node
            adjacency.setdefault(node_id, set())

        semantic_links = 0
        for edge in edges:
            left = str(edge.get("from_id") or "").strip()
            right = str(edge.get("to_id") or "").strip()
            if not left or not right:
                continue
            etype = str(edge.get("edge_type") or "").strip().upper()
            confidence = float(edge.get("confidence") or 0.0)
            if left not in adjacency or right not in adjacency:
                continue

            is_semantic = etype in semantic_edge_types
            is_weighted_non_structural = etype not in structural_edge_types and confidence >= 0.55
            if is_semantic or is_weighted_non_structural:
                adjacency[left].add(right)
                adjacency[right].add(left)
                semantic_links += 1

        # Add cross-framework lexical topic bridges so communities can form around themes
        # like backup, patching, vulnerability, etc., rather than source/framework lineage.
        token_to_nodes: dict[str, set[str]] = {}
        framework_by_node: dict[str, str] = {}
        for node_id, node in node_by_id.items():
            attrs = node.get("attributes") if isinstance(node.get("attributes"), dict) else {}
            framework_by_node[node_id] = str((attrs or {}).get("framework") or "").strip().lower()
            for token in _topic_tokens(node):
                token_to_nodes.setdefault(token, set()).add(node_id)

        for _, node_ids in token_to_nodes.items():
            if len(node_ids) < 2 or len(node_ids) > 80:
                continue
            by_framework: dict[str, list[str]] = {}
            for node_id in sorted(node_ids):
                fw_key = framework_by_node.get(node_id, "")
                by_framework.setdefault(fw_key, []).append(node_id)

            non_empty_frameworks = [fw for fw in by_framework.keys() if fw]
            if len(non_empty_frameworks) < 2:
                continue

            anchors = [sorted(by_framework[fw])[0] for fw in sorted(non_empty_frameworks)]
            root = anchors[0]
            for other in anchors[1:]:
                if other == root:
                    continue
                adjacency[root].add(other)
                adjacency[other].add(root)
                semantic_links += 1

        min_community_size = max(2, int(min_community_size))
        community_of: dict[str, str] = {}
        groups: dict[str, dict[str, Any]] = {}
        seq = 1
        for node_id in sorted(adjacency.keys()):
            if node_id in community_of:
                continue
            cid = f"Community {seq}"
            seq += 1
            q: deque[str] = deque([node_id])
            groups[cid] = {
                "community_id": cid,
                "node_ids": [],
                "edge_types": {},
                "frameworks": {},
                "sample_labels": [],
            }
            while q:
                cur = q.popleft()
                if cur in community_of:
                    continue
                community_of[cur] = cid
                groups[cid]["node_ids"].append(cur)
                node = node_by_id.get(cur) or {}
                attrs = node.get("attributes") if isinstance(node.get("attributes"), dict) else {}
                label = str(node.get("label") or cur).strip()
                if (
                    label
                    and label not in groups[cid]["sample_labels"]
                    and len(groups[cid]["sample_labels"]) < 6
                ):
                    groups[cid]["sample_labels"].append(label)
                fw = str((attrs or {}).get("framework") or "").strip()
                if fw:
                    groups[cid]["frameworks"][fw] = int(groups[cid]["frameworks"].get(fw, 0)) + 1
                for nxt in sorted(adjacency.get(cur, set())):
                    if nxt not in community_of:
                        q.append(nxt)

        valid_ids = {
            cid
            for cid, group in groups.items()
            if len(group.get("node_ids") or []) >= min_community_size
        }

        # Build additional overlapping topical communities from strong lexical themes.
        topic_groups: dict[str, dict[str, Any]] = {}
        topic_candidates: list[tuple[str, list[str]]] = []
        for token, node_ids in token_to_nodes.items():
            if len(node_ids) < min_community_size or len(node_ids) > 80:
                continue
            topic_candidates.append((token, sorted(node_ids)))
        topic_candidates.sort(
            key=lambda item: (
                0 if item[0] in preferred_topic_tokens else 1,
                -len(item[1]),
                item[0],
            )
        )
        for token, candidate_node_ids in topic_candidates[:40]:
            cid = f"Topic {token}"
            topic_groups[cid] = {
                "community_id": cid,
                "node_ids": list(candidate_node_ids),
                "edge_types": {},
                "frameworks": {},
                "sample_labels": [],
            }
            for node_id in candidate_node_ids:
                node = node_by_id.get(node_id) or {}
                attrs = node.get("attributes") if isinstance(node.get("attributes"), dict) else {}
                label = str(node.get("label") or node_id).strip()
                if (
                    label
                    and label not in topic_groups[cid]["sample_labels"]
                    and len(topic_groups[cid]["sample_labels"]) < 6
                ):
                    topic_groups[cid]["sample_labels"].append(label)
                fw = str((attrs or {}).get("framework") or "").strip()
                if fw:
                    topic_groups[cid]["frameworks"][fw] = (
                        int(topic_groups[cid]["frameworks"].get(fw, 0)) + 1
                    )

        memberships_by_node: dict[str, set[str]] = {}
        for node_id, cid in community_of.items():
            if cid in valid_ids:
                memberships_by_node.setdefault(node_id, set()).add(cid)
        for cid, group in topic_groups.items():
            for node_id in group.get("node_ids") or []:
                memberships_by_node.setdefault(node_id, set()).add(cid)

        for edge in edges:
            left = str(edge.get("from_id") or "").strip()
            right = str(edge.get("to_id") or "").strip()
            etype = str(edge.get("edge_type") or "edge").strip() or "edge"
            left_memberships = memberships_by_node.get(left, set())
            right_memberships = memberships_by_node.get(right, set())
            for cid_value in sorted(left_memberships.intersection(right_memberships)):
                target_group = groups.get(cid_value) or topic_groups.get(cid_value)
                if not target_group:
                    continue
                et = target_group["edge_types"]
                et[etype] = int(et.get(etype, 0)) + 1

        for node_id in node_by_id.keys():
            node_entry = node_by_id.get(node_id)
            if not isinstance(node_entry, dict):
                continue
            raw_attrs = node_entry.get("attributes")
            node_attrs: dict[str, Any] = dict(raw_attrs) if isinstance(raw_attrs, dict) else {}
            node_attrs["__communities"] = sorted(memberships_by_node.get(node_id, set()))
            node_entry["attributes"] = node_attrs

        filtered_groups = {cid: groups[cid] for cid in sorted(valid_ids)}
        for cid in sorted(topic_groups.keys()):
            filtered_groups[cid] = topic_groups[cid]
        return nodes, filtered_groups

    def _community_summary_text(group: dict[str, Any], *, total_communities: int) -> str:
        """Generate a concise summary text for a community based on its attributes.

        Args:
            group: A dictionary representing the community group with its attributes.
            total_communities: The total number of communities in the graph.
        Returns:
            A string containing a concise summary of the community."""
        cid = str(group.get("community_id") or "Community")
        node_count = int(len(group.get("node_ids") or []))
        edge_types = group.get("edge_types") or {}
        frameworks = group.get("frameworks") or {}
        labels = list(group.get("sample_labels") or [])

        llm_fn = getattr(svc, "_chat_completion_with_empty_retry", None)
        deployment = str(
            getattr(getattr(svc, "config", None), "query_deployment", "") or ""
        ).strip()
        clean_fn = getattr(svc, "_clean_markdown_whitespace", None)
        if callable(llm_fn) and deployment:
            edge_items = (
                ", ".join(
                    f"{k}({v})"
                    for k, v in sorted(edge_types.items(), key=lambda kv: (-int(kv[1]), kv[0]))[:6]
                )
                or "none"
            )
            fw_items = (
                ", ".join(
                    f"{k}({v})"
                    for k, v in sorted(frameworks.items(), key=lambda kv: (-int(kv[1]), kv[0]))[:6]
                )
                or "none"
            )
            label_items = "; ".join(labels[:5]) or "none"
            messages = [
                {
                    "role": "system",
                    "content": (
                        "You summarise cybersecurity graph communities. "
                        "Return exactly 2 concise sentences, grounded only in provided stats."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Community: {cid}\n"
                        f"Total communities in graph: {total_communities}\n"
                        f"Node count: {node_count}\n"
                        f"Framework distribution: {fw_items}\n"
                        f"Edge type distribution: {edge_items}\n"
                        f"Sample labels: {label_items}\n"
                    ),
                },
            ]
            try:
                summary = llm_fn(
                    messages,
                    deployment=deployment,
                    temperature=0.2,
                )
                summary_text = str(summary or "").strip()
                if callable(clean_fn):
                    summary_text = str(clean_fn(summary_text)).strip()
                if summary_text:
                    return summary_text
            except Exception:
                pass

        dominant_fw = ""
        if frameworks:
            dominant_fw = max(frameworks.items(), key=lambda kv: int(kv[1]))[0]
        dominant_edge = ""
        if edge_types:
            dominant_edge = max(edge_types.items(), key=lambda kv: int(kv[1]))[0]
        parts = [f"{cid} contains {node_count} nodes"]
        if dominant_fw:
            parts.append(f"is mostly aligned to {dominant_fw}")
        if dominant_edge:
            parts.append(f"and is linked primarily through {dominant_edge} edges")
        return " ".join(parts) + "."

    def _normalise_community_title(raw_title: str, *, fallback: str) -> str:
        """Clean model output into a concise community title.

        Args:
            raw_title: The raw title string generated by the model.
            fallback: The fallback title to use if the raw title is empty or invalid.
        Returns:
            A cleaned and concise community title string.
        """
        title = str(raw_title or "").strip()
        if not title:
            return fallback
        title = title.splitlines()[0].strip()
        title = re.sub(r"^[\-\*\d\.)\s]+", "", title)
        title = title.strip("`\"' ")
        title = re.sub(r"\s+", " ", title)
        if len(title) > 64:
            title = title[:64].rstrip()
        return title or fallback

    def _community_title_text(group: dict[str, Any], *, total_communities: int) -> str:
        """Generate a concise semantic title for a community.

        Args:
            group: A dictionary representing a community with its attributes.
            total_communities: The total number of communities in the graph.

        Returns:
            A concise semantic title for the community.
        """
        fallback = str(group.get("community_id") or "Community")
        if fallback.startswith("Topic "):
            return _normalise_community_title(fallback, fallback=fallback)
        node_count = int(len(group.get("node_ids") or []))
        edge_types = group.get("edge_types") or {}
        frameworks = group.get("frameworks") or {}
        labels = list(group.get("sample_labels") or [])

        llm_fn = getattr(svc, "_chat_completion_with_empty_retry", None)
        deployment = str(
            getattr(getattr(svc, "config", None), "query_deployment", "") or ""
        ).strip()
        clean_fn = getattr(svc, "_clean_markdown_whitespace", None)
        if callable(llm_fn) and deployment:
            edge_items = (
                ", ".join(
                    f"{k}({v})"
                    for k, v in sorted(edge_types.items(), key=lambda kv: (-int(kv[1]), kv[0]))[:6]
                )
                or "none"
            )
            fw_items = (
                ", ".join(
                    f"{k}({v})"
                    for k, v in sorted(frameworks.items(), key=lambda kv: (-int(kv[1]), kv[0]))[:6]
                )
                or "none"
            )
            label_items = "; ".join(labels[:5]) or "none"
            messages = [
                {
                    "role": "system",
                    "content": (
                        "Name cybersecurity graph communities. "
                        "Return exactly one concise title (2-6 words), no punctuation."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Total communities in graph: {total_communities}\n"
                        f"Node count: {node_count}\n"
                        f"Framework distribution: {fw_items}\n"
                        f"Edge type distribution: {edge_items}\n"
                        f"Sample labels: {label_items}\n"
                    ),
                },
            ]
            try:
                title = llm_fn(
                    messages,
                    deployment=deployment,
                    temperature=0.1,
                )
                title_text = str(title or "").strip()
                if callable(clean_fn):
                    title_text = str(clean_fn(title_text)).strip()
                cleaned = _normalise_community_title(title_text, fallback=fallback)
                if cleaned:
                    return cleaned
            except Exception:
                pass

        dominant_fw = ""
        if frameworks:
            dominant_fw = max(frameworks.items(), key=lambda kv: int(kv[1]))[0]
        if dominant_fw:
            return _normalise_community_title(f"{dominant_fw} Community", fallback=fallback)
        return fallback

    def _community_summaries_payload(
        nodes: list[dict[str, Any]],
        edges: list[dict[str, Any]],
        *,
        min_community_size: int,
    ) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
        """Generate annotated nodes and community summaries for the graph.

        Args:
            nodes: List of node dictionaries.
            edges: List of edge dictionaries.
            min_community_size: The minimum size of a community to be considered.
        Returns:
            A tuple containing the annotated nodes and a dictionary of community summaries.
        """
        annotated_nodes, groups = _community_groups(
            nodes,
            edges,
            min_community_size=min_community_size,
        )
        remap: dict[str, str] = {}
        used_titles: set[str] = set()
        for cid in sorted(groups.keys()):
            group = groups[cid]
            base_title = _community_title_text(group, total_communities=max(1, len(groups)))
            resolved_title = base_title
            suffix = 2
            while resolved_title in used_titles:
                resolved_title = f"{base_title} ({suffix})"
                suffix += 1
            used_titles.add(resolved_title)
            remap[cid] = resolved_title
            group["community_id"] = resolved_title

        if remap:
            for node in annotated_nodes:
                attrs = node.get("attributes") if isinstance(node.get("attributes"), dict) else {}
                if not isinstance(attrs, dict):
                    continue
                raw_memberships = attrs.get("__communities")
                current_values = (
                    [str(item).strip() for item in raw_memberships if str(item).strip()]
                    if isinstance(raw_memberships, list)
                    else []
                )
                if not current_values:
                    continue
                mapped_values: list[str] = []
                seen_values: set[str] = set()
                for value in current_values:
                    mapped = remap.get(value, value)
                    if mapped and mapped not in seen_values:
                        seen_values.add(mapped)
                        mapped_values.append(mapped)
                attrs["__communities"] = mapped_values
                node["attributes"] = attrs

        summaries: dict[str, dict[str, Any]] = {}
        total = max(1, len(groups))
        for cid in sorted(groups.keys()):
            group = groups[cid]
            named_id = str(group.get("community_id") or cid)
            summary_text = _community_summary_text(group, total_communities=total)
            summaries[named_id] = {
                "community_id": named_id,
                "summary": summary_text,
                "node_count": len(group.get("node_ids") or []),
                "edge_type_counts": dict(group.get("edge_types") or {}),
                "framework_counts": dict(group.get("frameworks") or {}),
                "sample_labels": list(group.get("sample_labels") or []),
            }
        return annotated_nodes, summaries

    def _graph_index_state() -> dict[str, Any]:
        """Retrieve the current state of the graph index.

        Returns:
            A dictionary containing the state of the graph index.
        """
        backend_config = _graph_backend_config()
        backend = resolve_graph_backend(backend_config)
        state: dict[str, Any] = {
            "graph_enabled": True,
            "backend": backend,
            "last_successful_build_at": _last_successful_build_at(),
            "community_min_size": _community_min_size(),
            "controls_loaded": False,
            "controls_count": 0,
            "corpus_b_loaded": False,
            "corpus_b_count": 0,
            "index_loaded": False,
            "index_count": 0,
            "graph_built": False,
            "graph_ready": False,
            "graph_stale": True,
            "graph_needs_rebuild": True,
            "graph_available_for_visualisation": False,
            "graph_available_for_ask_expansion": False,
            "allow_build": False,
            "allow_visualisation": False,
            "allow_ask_expansion": False,
            "reason": "index_not_loaded",
        }

        def _presence_probe(client: Any, *, filter_expr: str) -> int:
            """Return 1 if at least one document matches filter, otherwise 0.

            Args:
                client: The search client to query.
                filter_expr: The filter expression to apply to the search query.
            Returns:
                1 if at least one document matches the filter, otherwise 0.
            """
            try:
                pager = _client_search(
                    client,
                    filter_expr=filter_expr,
                    top=1,
                    select=["id"],
                    include_total_count=False,
                )
                for _ in pager:
                    return 1
            except Exception:
                return 0
            return 0

        controls_count = 0
        try:
            controls_client = getattr(svc, "controls_search_client", None)
            if controls_client is not None and hasattr(
                svc, "_count_search_documents_total_by_filter"
            ):
                controls_count = int(
                    svc._count_search_documents_total_by_filter(controls_client, filter_expr="")
                )
        except Exception:
            controls_count = 0

        if controls_count <= 0 and controls_client is not None:
            controls_count = _presence_probe(controls_client, filter_expr="")

        state["controls_count"] = controls_count
        state["controls_loaded"] = controls_count > 0
        corpus_b_count = 0
        try:
            evidence_client = getattr(svc, "search_client", None)
            if evidence_client is not None and hasattr(
                svc, "_count_search_documents_total_by_filter"
            ):
                corpus_b_count = int(
                    svc._count_search_documents_total_by_filter(
                        evidence_client,
                        filter_expr="corpus eq 'b'",
                    )
                )
        except Exception:
            corpus_b_count = 0

        if corpus_b_count <= 0 and evidence_client is not None:
            corpus_b_count = _presence_probe(evidence_client, filter_expr="corpus eq 'b'")

        state["corpus_b_count"] = corpus_b_count
        state["corpus_b_loaded"] = corpus_b_count > 0
        state["index_loaded"] = bool(state["controls_loaded"] or state["corpus_b_loaded"])
        state["index_count"] = int(state["controls_count"] + state["corpus_b_count"])
        state["allow_build"] = bool(state["index_loaded"])
        state["graph_built"] = False
        state["graph_ready"] = False
        state["graph_stale"] = True
        state["graph_needs_rebuild"] = not state["allow_build"]

        if state["index_loaded"]:
            state["reason"] = "index_loaded_no_graph"

        if state["allow_build"]:
            try:
                store, store_error = _graph_store_or_503()
                if store is not None:
                    counts = store.counts()
                    nodes_total = int(counts.get("nodes") or 0)
                    edges_total = int(counts.get("edges") or 0)
                    state["graph_built"] = nodes_total > 0 or edges_total > 0
                    state["graph_ready"] = state["graph_built"]
                    state["graph_stale"] = not state["graph_built"]
                    state["graph_needs_rebuild"] = not state["graph_built"]
                    state["graph_available_for_visualisation"] = state["graph_built"]
                    state["graph_available_for_ask_expansion"] = state["graph_built"]
                    state["allow_visualisation"] = state["graph_built"]
                    state["allow_ask_expansion"] = state["graph_built"]
                    state["reason"] = "ready" if state["graph_built"] else "index_loaded_no_graph"
                else:
                    state["reason"] = "backend_unavailable"
            except Exception:
                state["reason"] = "backend_unavailable"

        return state

    def _hyper_connected_indicator_from_edges(
        *,
        edges_jsonl_path: str,
        total_nodes_hint: int = 0,
    ) -> dict[str, Any]:
        """Calculate the hyper-connected indicator from the edges JSONL file.

        Args:
            edges_jsonl_path: Path to the edges JSONL file.
            total_nodes_hint: Optional hint for the total number of nodes.

        Returns:
            A dictionary containing the hyper-connected indicator metrics.
        """
        degree: dict[str, int] = {}
        structural_edge_types = {
            "BELONGS_TO_FRAMEWORK",
            "IN_FAMILY",
            "DERIVED_FROM_SOURCE",
        }
        analysed_edges = 0
        ignored_structural_edges = 0
        try:
            edges_path = _safe_graph_artifact_path(edges_jsonl_path, "edges.jsonl")
            if not edges_path.is_file():
                return {
                    "detected": False,
                    "hyper_connected_node_count": 0,
                    "hyper_connected_node_ratio": 0.0,
                    "threshold_degree": 25,
                    "median_node_degree": 0.0,
                    "top_node_degree": 0,
                    "total_edges": 0,
                    "ignored_structural_edges": 0,
                    "examples": [],
                    "warning": "No edge artifact file available for hyper-connected analysis.",
                }

            with edges_path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    text = line.strip()
                    if not text:
                        continue
                    try:
                        rec = json.loads(text)
                    except Exception:
                        continue
                    edge_type = str(rec.get("edge_type") or "").strip().upper()
                    if edge_type in structural_edge_types:
                        ignored_structural_edges += 1
                        continue
                    left = str(rec.get("from_id") or "").strip()
                    right = str(rec.get("to_id") or "").strip()
                    if not left or not right:
                        continue
                    degree[left] = int(degree.get(left, 0)) + 1
                    degree[right] = int(degree.get(right, 0)) + 1
                    analysed_edges += 1
        except Exception:
            return {
                "detected": False,
                "hyper_connected_node_count": 0,
                "hyper_connected_node_ratio": 0.0,
                "threshold_degree": 25,
                "median_node_degree": 0.0,
                "top_node_degree": 0,
                "total_edges": 0,
                "ignored_structural_edges": 0,
                "examples": [],
                "warning": "Hyper-connected analysis failed unexpectedly.",
            }

        if not degree:
            return {
                "detected": False,
                "hyper_connected_node_count": 0,
                "hyper_connected_node_ratio": 0.0,
                "threshold_degree": 25,
                "median_node_degree": 0.0,
                "top_node_degree": 0,
                "total_edges": analysed_edges,
                "ignored_structural_edges": ignored_structural_edges,
                "examples": [],
                "warning": "No connected non-structural nodes detected in edge set.",
            }

        values = list(degree.values())
        median_degree = float(statistics.median(values)) if values else 0.0
        threshold_degree = int(max(25, round(5.0 * median_degree)))
        hyper_nodes = sorted(
            [node_id for node_id, deg in degree.items() if int(deg) >= threshold_degree]
        )
        node_population = int(max(total_nodes_hint, len(degree), 1))
        ratio = float(len(hyper_nodes) / node_population)
        top_degree = int(max(values) if values else 0)

        has_hyper_nodes = len(hyper_nodes) > 0
        detected = bool(
            has_hyper_nodes
            and (
                ratio >= 0.02
                or (analysed_edges > 0 and (top_degree / max(1, analysed_edges)) >= 0.10)
            )
        )
        warning = (
            "Hyper-connected non-structural nodes detected. Consider adaptive hierarchical depth pruning, forced sub-communities around hubs, or map-reduce summarisation for hub neighbourhoods."
            if detected
            else "No hyper-connected non-structural node concentration detected."
        )
        return {
            "detected": detected,
            "hyper_connected_node_count": len(hyper_nodes),
            "hyper_connected_node_ratio": round(ratio, 6),
            "threshold_degree": threshold_degree,
            "median_node_degree": round(median_degree, 4),
            "top_node_degree": top_degree,
            "total_edges": analysed_edges,
            "ignored_structural_edges": ignored_structural_edges,
            "examples": hyper_nodes[:10],
            "warning": warning,
        }

    def _publish_graph_artifacts_to_object_storage(
        *, report: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Publish graph artifacts to object storage if enabled and return the published snapshot info.

        Args:
            report: A dictionary containing the graph report and its associated files.

        Returns:
            A dictionary containing the published snapshot information, or None if publishing is disabled or fails.
        """
        backend_config = _graph_backend_config()
        backend = resolve_graph_backend(backend_config)

        bucket_or_container = ""
        prefix = ""
        storage_client: Any | None = None
        enabled = False
        if backend == "azure":
            enabled = bool(getattr(backend_config, "graph_azure_publish_enabled", False))
            bucket_or_container = str(
                getattr(backend_config, "graph_azure_artifacts_container", "") or ""
            ).strip()
            prefix = str(getattr(backend_config, "graph_azure_artifacts_prefix", "") or "").strip()
            storage_client = getattr(backend_config, "graph_azure_storage_client", None)
        elif backend == "aws":
            enabled = bool(getattr(backend_config, "graph_aws_publish_enabled", False))
            bucket_or_container = str(
                getattr(backend_config, "graph_aws_artifacts_bucket", "")
                or getattr(backend_config, "s3_bucket_name", "")
                or ""
            ).strip()
            prefix = str(getattr(backend_config, "graph_aws_artifacts_prefix", "") or "").strip()
            storage_client = getattr(backend_config, "graph_aws_storage_client", None)

        if not enabled:
            return None
        if (
            not bucket_or_container
            or storage_client is None
            or not hasattr(storage_client, "put_object")
        ):
            return None

        files = report.get("files", {})
        if not isinstance(files, dict):
            return None

        published: dict[str, str] = {}
        for field_name, object_name in (
            ("nodes_jsonl", "nodes.jsonl"),
            ("edges_jsonl", "edges.jsonl"),
            ("graph_build_report_json", "graph_build_report.json"),
        ):
            file_path = _safe_graph_artifact_path(
                str(files.get(field_name) or "").strip(), object_name
            )
            if not file_path.is_file():
                continue
            key = _graph_snapshot_key(prefix, object_name)
            storage_client.put_object(
                bucket_or_container,
                key,
                file_path.read_bytes(),
                metadata={"graph_schema_version": str(report.get("graph_schema_version") or "")},
            )
            published[field_name] = key

        if not published:
            return None
        return {
            "bucket_or_container": bucket_or_container,
            "prefix": prefix,
            "backend": backend,
            "objects": published,
        }

    @app.post("/api/graph/build")
    def graph_build(request: Request, payload: GraphBuildRequest) -> JSONResponse:
        """Build a local relationship graph from provided controls and chunks, optionally persisting to the graph store.

        Args:
            request: The incoming HTTP request.
            payload: The payload containing controls, chunks, and other parameters.

        Returns:
            A JSONResponse containing the result of the graph build operation.
        """
        started = time.perf_counter()
        started_at = _utc_now_iso()
        unauth = _authorised_or_401(request, payload.auth_token)
        if unauth is not None:
            duration_s = time.perf_counter() - started
            observe_graph_operation(
                operation="build", duration_s=duration_s, outcome="unauthorised"
            )
            return unauth

        configured_output_dir = Path(_default_output_dir()).expanduser().resolve()
        try:
            if payload.output_dir:
                requested_output_dir = Path(payload.output_dir).expanduser().resolve()
                if requested_output_dir != configured_output_dir:
                    raise ValueError
        except ValueError:
            return _problem_response(
                status=400,
                title="Invalid Request",
                detail="output_dir must match the configured graph artifact directory.",
                instance=str(request.url.path),
            )
        output_dir = str(configured_output_dir)
        report = build_local_graph_artifacts(
            controls=list(payload.controls),
            chunks=list(payload.chunks),
            output_dir=output_dir,
            config=svc.config,
            min_community_size=int(payload.min_community_size),
        )
        published_snapshot = _publish_graph_artifacts_to_object_storage(report=report)
        if published_snapshot is not None:
            report["published_snapshot"] = published_snapshot

        persisted: dict[str, int] | None = None
        if payload.persist_store:
            store, store_error = _graph_store_or_503()
            if store is None:
                duration_s = time.perf_counter() - started
                observe_graph_operation(
                    operation="build", duration_s=duration_s, outcome="backend_unavailable"
                )
                return store_error or _problem_response(
                    status=503,
                    title="Service Unavailable",
                    detail="Graph backend unavailable.",
                    instance=str(request.url.path),
                    type_uri="https://datatracker.ietf.org/doc/html/rfc9110#section-15.6.4",
                )
            persisted = store.load_artifact_files(
                nodes_jsonl=str(report["files"]["nodes_jsonl"]),
                edges_jsonl=str(report["files"]["edges_jsonl"]),
            )

        duration_s = time.perf_counter() - started
        nodes_written = int((persisted or {}).get("nodes_written") or 0)
        edges_written = int((persisted or {}).get("edges_written") or 0)
        observe_graph_operation(
            operation="build",
            duration_s=duration_s,
            outcome="success",
            nodes_written=nodes_written,
            edges_written=edges_written,
        )

        audit = _graph_audit_payload(
            operation="build",
            outcome="success",
            started_at=started_at,
            duration_s=duration_s,
            details={
                "output_dir": output_dir,
                "persist_store": bool(payload.persist_store),
                "min_community_size": int(payload.min_community_size),
                "nodes_total": int(report.get("output", {}).get("nodes_total", 0)),
                "edges_total": int(report.get("output", {}).get("edges_total", 0)),
            },
        )
        logger.info("graph_api_audit", extra={"graph_audit": audit})

        response: dict[str, Any] = {
            "status": "ok",
            "report": report,
            "audit": audit,
        }
        response["hyper_connected_indicator"] = _hyper_connected_indicator_from_edges(
            edges_jsonl_path=str(report["files"]["edges_jsonl"]),
            total_nodes_hint=int(report.get("output", {}).get("nodes_total", 0)),
        )
        if persisted is not None:
            response["persisted"] = persisted
        return JSONResponse(response)

    @app.get("/api/graph/nodes/{node_id}")
    def graph_node(
        request: Request, node_id: str, auth_token: str = Query(default="")
    ) -> JSONResponse:
        """Retrieve a node from the graph by its ID.

        Args:
            request: The incoming HTTP request.
            node_id: The ID of the node to retrieve.
            auth_token: The authentication token.

        Returns:
            A JSONResponse containing the node data or an error message.
        """
        started = time.perf_counter()
        started_at = _utc_now_iso()
        unauth = _authorised_or_401(request, auth_token)
        if unauth is not None:
            duration_s = time.perf_counter() - started
            observe_graph_operation(
                operation="node_get", duration_s=duration_s, outcome="unauthorised"
            )
            return unauth

        store, store_error = _graph_store_or_503()
        if store is None:
            duration_s = time.perf_counter() - started
            observe_graph_operation(
                operation="node_get", duration_s=duration_s, outcome="backend_unavailable"
            )
            return store_error or _problem_response(
                status=503,
                title="Service Unavailable",
                detail="Graph backend unavailable.",
                instance=str(request.url.path),
                type_uri="https://datatracker.ietf.org/doc/html/rfc9110#section-15.6.4",
            )
        try:
            node = store.get_node(node_id=node_id)
        except KeyError:
            duration_s = time.perf_counter() - started
            observe_graph_operation(
                operation="node_get", duration_s=duration_s, outcome="not_found"
            )
            return _problem_response(
                status=404,
                title="Not Found",
                detail=f"Node not found: {node_id}",
                instance=str(request.url.path),
                type_uri="https://datatracker.ietf.org/doc/html/rfc9110#section-15.5.5",
            )
        duration_s = time.perf_counter() - started
        observe_graph_operation(operation="node_get", duration_s=duration_s, outcome="success")
        node["audit"] = _graph_audit_payload(
            operation="node_get",
            outcome="success",
            started_at=started_at,
            duration_s=duration_s,
            details={"node_id": node_id},
        )
        return JSONResponse(node)

    @app.get("/api/graph/related")
    def graph_related(
        request: Request,
        node_id: str,
        depth: int = Query(default=1, ge=1, le=8),
        max_edges: int = Query(default=500, ge=1, le=5000),
        auth_token: str = Query(default=""),
    ) -> JSONResponse:
        """Retrieve a subgraph of related nodes and edges starting from a seed node.

        Args:
            request: The incoming HTTP request.
            node_id: The ID of the seed node.
            depth: The depth of the subgraph to retrieve (default is 1).
            max_edges: The maximum number of edges to return (default is 500).
            auth_token: The authentication token.
        Returns:
            A JSONResponse containing the subgraph data or an error message.
        """
        started = time.perf_counter()
        started_at = _utc_now_iso()
        unauth = _authorised_or_401(request, auth_token)
        if unauth is not None:
            duration_s = time.perf_counter() - started
            observe_graph_operation(
                operation="related", duration_s=duration_s, outcome="unauthorised"
            )
            return unauth

        store, store_error = _graph_store_or_503()
        if store is None:
            duration_s = time.perf_counter() - started
            observe_graph_operation(
                operation="related", duration_s=duration_s, outcome="backend_unavailable"
            )
            return store_error or _problem_response(
                status=503,
                title="Service Unavailable",
                detail="Graph backend unavailable.",
                instance=str(request.url.path),
                type_uri="https://datatracker.ietf.org/doc/html/rfc9110#section-15.6.4",
            )
        resolved_node_id = _resolve_graph_seed_node_id(store, node_id)
        if not resolved_node_id:
            duration_s = time.perf_counter() - started
            observe_graph_operation(operation="related", duration_s=duration_s, outcome="not_found")
            return _problem_response(
                status=404,
                title="Not Found",
                detail=f"Seed node not found: {node_id}",
                instance=str(request.url.path),
                type_uri="https://datatracker.ietf.org/doc/html/rfc9110#section-15.5.5",
                extensions={"requested_node_id": node_id},
            )

        result = store.subgraph(seed_node_id=resolved_node_id, depth=depth, max_edges=max_edges)
        duration_s = time.perf_counter() - started
        edge_count = len(result.get("edges", []))
        truncated = edge_count >= int(max_edges)
        observe_graph_operation(
            operation="related",
            duration_s=duration_s,
            outcome="success",
            truncated=truncated,
        )
        response_payload: dict[str, Any] = {
            "nodes": result.get("nodes", []),
            "edges": result.get("edges", []),
        }
        annotated_nodes, community_summaries = _community_summaries_payload(
            list(response_payload.get("nodes", [])),
            list(response_payload.get("edges", [])),
            min_community_size=_community_min_size(),
        )
        response_payload["nodes"] = annotated_nodes
        response_payload["community_summaries"] = community_summaries
        response_payload["community_settings"] = {"min_community_size": _community_min_size()}
        response_payload["audit"] = _graph_audit_payload(
            operation="related",
            outcome="success",
            started_at=started_at,
            duration_s=duration_s,
            details={
                "node_id": node_id,
                "resolved_node_id": resolved_node_id,
                "min_community_size": _community_min_size(),
                "depth": depth,
                "max_edges": max_edges,
                "returned_edges": edge_count,
                "truncated": truncated,
            },
        )
        logger.info("graph_api_audit", extra={"graph_audit": response_payload["audit"]})
        return JSONResponse(response_payload)

    @app.post("/api/graph/query")
    def graph_query(request: Request, payload: GraphQueryRequest) -> JSONResponse:
        """Run a bounded, read-only graph query for agent and MCP consumers.

        Args:
            request (Request): The incoming HTTP request.
            payload (GraphQueryRequest): The payload containing the graph query parameters.

        Returns:
            JSONResponse: The JSON response containing the query results or error information.
        """
        started = time.perf_counter()
        started_at = _utc_now_iso()
        unauth = _authorised_or_401(request, payload.auth_token)
        if unauth is not None:
            duration_s = time.perf_counter() - started
            observe_graph_operation(
                operation="query", duration_s=duration_s, outcome="unauthorised"
            )
            return unauth

        store, store_error = _graph_store_or_503()
        if store is None:
            duration_s = time.perf_counter() - started
            observe_graph_operation(
                operation="query", duration_s=duration_s, outcome="backend_unavailable"
            )
            return store_error or _problem_response(
                status=503,
                title="Service Unavailable",
                detail="Graph backend unavailable.",
                instance=str(request.url.path),
                type_uri="https://datatracker.ietf.org/doc/html/rfc9110#section-15.6.4",
            )

        seed_node_id = str(payload.seed_node_id or "").strip()
        if seed_node_id:
            resolved_node_id = _resolve_graph_seed_node_id(store, seed_node_id)
            if not resolved_node_id:
                duration_s = time.perf_counter() - started
                observe_graph_operation(
                    operation="query", duration_s=duration_s, outcome="not_found"
                )
                return _problem_response(
                    status=404,
                    title="Not Found",
                    detail=f"Seed node not found: {seed_node_id}",
                    instance=str(request.url.path),
                    type_uri="https://datatracker.ietf.org/doc/html/rfc9110#section-15.5.5",
                    extensions={"requested_node_id": seed_node_id},
                )
            result = store.subgraph(
                seed_node_id=resolved_node_id,
                depth=payload.depth,
                max_edges=payload.max_edges,
            )
        else:
            result = store.export_graph(max_nodes=payload.max_nodes, max_edges=payload.max_edges)

        candidate_nodes = list(result.get("nodes", []))
        candidate_edges = list(result.get("edges", []))
        annotated_nodes, _ = _community_summaries_payload(
            candidate_nodes,
            candidate_edges,
            min_community_size=_community_min_size(),
        )

        framework = str(payload.framework or "").strip().casefold()
        node_type = str(payload.node_type or "").strip().casefold()
        edge_type = str(payload.edge_type or "").strip().casefold()
        community = str(payload.community or "").strip()
        visible_nodes = []
        for node in annotated_nodes:
            attrs_value = node.get("attributes")
            attrs: dict[str, Any] = attrs_value if isinstance(attrs_value, dict) else {}
            memberships = [str(value or "").strip() for value in attrs.get("__communities", [])]
            if framework and str(attrs.get("framework") or "").strip().casefold() != framework:
                continue
            if node_type and str(node.get("node_type") or "").strip().casefold() != node_type:
                continue
            if community and community not in memberships:
                continue
            visible_nodes.append(node)

        visible_nodes = visible_nodes[: payload.max_nodes]
        visible_node_ids = {str(node.get("node_id") or "") for node in visible_nodes}
        visible_edges = [
            edge
            for edge in candidate_edges
            if str(edge.get("from_id") or "") in visible_node_ids
            and str(edge.get("to_id") or "") in visible_node_ids
            and (not edge_type or str(edge.get("edge_type") or "").strip().casefold() == edge_type)
            and float(edge.get("confidence") or 0.0) >= payload.min_confidence
        ][: payload.max_edges]

        truncated = (
            len(candidate_nodes) > len(visible_nodes)
            or len(candidate_edges) > len(visible_edges)
            or len(result.get("edges", [])) >= payload.max_edges
        )
        _, community_summaries = _community_summaries_payload(
            visible_nodes,
            visible_edges,
            min_community_size=_community_min_size(),
        )
        duration_s = time.perf_counter() - started
        observe_graph_operation(
            operation="query", duration_s=duration_s, outcome="success", truncated=truncated
        )
        response_payload = {
            "nodes": visible_nodes,
            "edges": visible_edges,
            "community_summaries": community_summaries,
            "community_settings": {"min_community_size": _community_min_size()},
            "truncated": truncated,
            "limits": {"max_nodes": payload.max_nodes, "max_edges": payload.max_edges},
            "audit": _graph_audit_payload(
                operation="query",
                outcome="success",
                started_at=started_at,
                duration_s=duration_s,
                details={
                    "seed_node_id": seed_node_id,
                    "depth": payload.depth,
                    "framework": payload.framework,
                    "node_type": payload.node_type,
                    "edge_type": payload.edge_type,
                    "community": payload.community,
                    "min_confidence": payload.min_confidence,
                    "returned_nodes": len(visible_nodes),
                    "returned_edges": len(visible_edges),
                    "truncated": truncated,
                },
            ),
        }
        logger.info("graph_api_audit", extra={"graph_audit": response_payload["audit"]})
        return JSONResponse(response_payload)

    @app.get("/api/graph/export")
    def graph_export(
        request: Request,
        format: str = Query(default="json"),
        max_nodes: int = Query(default=100000, ge=1, le=1000000),
        max_edges: int = Query(default=100000, ge=1, le=1000000),
        auth_token: str = Query(default=""),
    ) -> JSONResponse:
        """Export the entire graph in the specified format, with optional limits on nodes and edges.

        Args:
            request: The incoming HTTP request.
            format: The export format (currently only "json" is supported).
            max_nodes: The maximum number of nodes to include in the export (default is 100000).
            max_edges: The maximum number of edges to include in the export (default is 100000).
            auth_token: The authentication token.

        Returns:
            A JSONResponse containing the exported graph data or an error message.
        """
        started = time.perf_counter()
        started_at = _utc_now_iso()
        unauth = _authorised_or_401(request, auth_token)
        if unauth is not None:
            duration_s = time.perf_counter() - started
            observe_graph_operation(
                operation="export", duration_s=duration_s, outcome="unauthorised"
            )
            return unauth

        if format.lower() != "json":
            duration_s = time.perf_counter() - started
            observe_graph_operation(
                operation="export", duration_s=duration_s, outcome="bad_request"
            )
            return _problem_response(
                status=400,
                title="Bad Request",
                detail="Only format=json is currently supported in local baseline.",
                instance=str(request.url.path),
                type_uri="https://datatracker.ietf.org/doc/html/rfc9110#section-15.5.1",
            )

        store, store_error = _graph_store_or_503()
        if store is None:
            duration_s = time.perf_counter() - started
            observe_graph_operation(
                operation="export", duration_s=duration_s, outcome="backend_unavailable"
            )
            return store_error or _problem_response(
                status=503,
                title="Service Unavailable",
                detail="Graph backend unavailable.",
                instance=str(request.url.path),
                type_uri="https://datatracker.ietf.org/doc/html/rfc9110#section-15.6.4",
            )
        payload = store.export_graph(max_nodes=max_nodes, max_edges=max_edges)
        payload["counts"] = store.counts()
        annotated_nodes, community_summaries = _community_summaries_payload(
            list(payload.get("nodes", [])),
            list(payload.get("edges", [])),
            min_community_size=_community_min_size(),
        )
        payload["nodes"] = annotated_nodes
        payload["community_summaries"] = community_summaries
        payload["community_settings"] = {"min_community_size": _community_min_size()}
        duration_s = time.perf_counter() - started
        truncated = len(payload.get("nodes", [])) >= int(max_nodes) or len(
            payload.get("edges", [])
        ) >= int(max_edges)
        observe_graph_operation(
            operation="export",
            duration_s=duration_s,
            outcome="success",
            truncated=truncated,
        )
        payload["audit"] = _graph_audit_payload(
            operation="export",
            outcome="success",
            started_at=started_at,
            duration_s=duration_s,
            details={
                "format": format.lower(),
                "max_nodes": max_nodes,
                "max_edges": max_edges,
                "min_community_size": _community_min_size(),
                "returned_nodes": len(payload.get("nodes", [])),
                "returned_edges": len(payload.get("edges", [])),
                "truncated": truncated,
            },
        )
        logger.info("graph_api_audit", extra={"graph_audit": payload["audit"]})
        return JSONResponse(payload)

    @app.get("/api/graph/status")
    def graph_status(request: Request, auth_token: str = Query(default="")) -> JSONResponse:
        """Retrieve the current status of the graph, including its readiness and availability for operations.

        Args:
            request: The incoming HTTP request.
            auth_token: The authentication token.

        Returns:
            A JSONResponse containing the graph status or an error message.
        """
        started = time.perf_counter()
        started_at = _utc_now_iso()
        unauth = _authorised_or_401(request, auth_token)
        if unauth is not None:
            duration_s = time.perf_counter() - started
            observe_graph_operation(
                operation="status", duration_s=duration_s, outcome="unauthorised"
            )
            return unauth

        payload = _graph_index_state()
        duration_s = time.perf_counter() - started
        observe_graph_operation(operation="status", duration_s=duration_s, outcome="success")
        payload["audit"] = _graph_audit_payload(
            operation="status",
            outcome="success",
            started_at=started_at,
            duration_s=duration_s,
            details={
                "controls_count": payload.get("controls_count", 0),
                "graph_built": payload.get("graph_built", False),
                "graph_needs_rebuild": payload.get("graph_needs_rebuild", True),
                "backend": payload.get("backend", "local"),
            },
        )
        return JSONResponse(payload)
