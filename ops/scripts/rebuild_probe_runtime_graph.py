"""Script to rebuild and probe the runtime graph via HTTP API calls."""
import json
import urllib.request

BASE = "http://127.0.0.1:8080"


def get_json(path: str):
    """Send a GET request to the specified path and return the JSON response.

    Args:
        path: The API endpoint path.

    Returns:
        The JSON-decoded response from the server.
    """
    with urllib.request.urlopen(BASE + path, timeout=180) as response:
        return json.load(response)


def post_json(path: str, payload: dict):
    """Send a POST request with a JSON payload and return the JSON response.

    Args:
        path: The API endpoint path.
        payload: The JSON-serializable payload to send in the request body.

    Returns:
        The JSON-decoded response from the server.
    """
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        BASE + path,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=300) as response:
        return json.load(response)


status = get_json("/api/graph/status")
controls_total = int(status.get("controls_count") or 0)
chunks_total = int(status.get("corpus_b_count") or 0)
print(
    "status",
    json.dumps(
        {
            "controls_count": controls_total,
            "corpus_b_count": chunks_total,
            "graph_ready": bool(status.get("graph_ready")),
        }
    ),
)

controls = get_json(f"/api/corpus-a/list?limit={controls_total}").get("items", [])
chunks = get_json(f"/api/corpus-b/list?limit={chunks_total}").get("items", [])
print("payload_sizes", json.dumps({"controls": len(controls), "chunks": len(chunks)}))

build = post_json(
    "/api/graph/build",
    {
        "auth_token": "",
        "controls": controls,
        "chunks": chunks,
        "persist_store": True,
    },
)
output = (build.get("report") or {}).get("output") or {}
print(
    "build",
    json.dumps(
        {
            "status": build.get("status"),
            "nodes_total": int(output.get("nodes_total") or 0),
            "edges_total": int(output.get("edges_total") or 0),
        }
    ),
)

export = get_json("/api/graph/export?format=json&max_nodes=100000&max_edges=100000")
keys = sorted((export.get("community_summaries") or {}).keys())
print("top_community_ids")
for key in keys[:80]:
    print(key)

noisy_prefixes = (
    "Topic after",
    "Topic each",
    "Topic its",
    "Topic newco",
    "Topic office",
    "Topic verify",
    "Topic required",
    "Topic review",
)
noisy = [key for key in keys if key.startswith(noisy_prefixes)]
print("noisy_count", len(noisy))
if noisy:
    print("noisy_examples")
    for key in noisy[:20]:
        print(key)
