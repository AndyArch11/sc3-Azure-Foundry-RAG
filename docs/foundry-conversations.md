# Azure Foundry Chat Completion & Conversation Management

This document describes the chat completion integration with Azure Foundry API and conversation history management via CosmosDB.

## Architecture

### Components

1. **Azure Foundry Chat Completions API** — Provides LLM inference via the multi-tenant `openai.chat.completions` endpoint
2. **CosmosDB** — Stores conversation sessions and message history with partition by user ID
3. **Session Management** — Tracks conversation state across turns, supporting both per-user and per-session scopes

### Data Model

#### ConversationSession
```python
@dataclass
class ConversationSession:
    session_id: str  # Unique session identifier
    user_id: str  # Hashed auth_token or session identifier
    conversation_id: str  # Unique per conversation
    messages: list[ConversationMessage]  # Ordered message history
    created_at: str  # ISO 8601 timestamp
    updated_at: str  # ISO 8601 timestamp
    evaluation_threshold: float  # Threshold for answer acceptability
```

#### ConversationMessage
```python
@dataclass
class ConversationMessage:
    role: str  # "user" or "assistant"
    content: str  # Message text
    timestamp: str  # ISO 8601 timestamp
```

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `AZURE_COSMOS_ENDPOINT` | Yes | CosmosDB account endpoint (e.g., `https://cosmos-xxx.documents.azure.com:443/`) |
| `AZURE_COSMOS_DATABASE_NAME` | Yes | CosmosDB SQL database name for conversations (default: `rag-conversations`) |
| `AZURE_COSMOS_CONTAINER_NAME` | Yes | CosmosDB SQL container name for conversations (default: `conversations`) |
| `AZURE_OPENAI_ENDPOINT` | Yes | Foundry API endpoint (e.g., `https://foundry-xxx.openai.azure.com/`) |

## API Endpoints

### Conversation Management

#### Create Conversation
```http
POST /api/conversations/new
Content-Type: application/x-www-form-urlencoded

auth_token=<optional-token>
```

**Response:**
```json
{
  "session_id": "550e8400-e29b-41d4-a716-446655440000",
  "conversation_id": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
  "user_id": "5a4b8d2e"
}
```

---

#### List User's Conversations
```http
GET /api/conversations/{user_id}?auth_token=<token>
```

**Response:**
```json
{
  "conversations": [
    {
      "session_id": "...",
      "conversation_id": "...",
      "created_at": "2026-03-18T12:34:56Z",
      "updated_at": "2026-03-18T13:45:00Z",
      "messages": [...]
    }
  ]
}
```

---

#### Get Conversation History
```http
GET /api/conversations/{user_id}/{conversation_id}?auth_token=<token>
```

**Response:**
```json
{
  "session_id": "...",
  "conversation_id": "...",
  "created_at": "2026-03-18T12:34:56Z",
  "updated_at": "2026-03-18T13:45:00Z",
  "messages": [
    {
      "role": "user",
      "content": "What are the AESCSF controls?",
      "timestamp": "2026-03-18T12:35:00Z"
    },
    {
      "role": "assistant",
      "content": "The AESCSF....",
      "timestamp": "2026-03-18T12:35:05Z"
    }
  ]
}
```

---

#### Add Message to Conversation
```http
POST /api/conversations/{conversation_id}/message
Content-Type: application/x-www-form-urlencoded

user_id=<computed-user-id>
role=user|assistant
content=<message-text>
auth_token=<optional-token>
```

**Response:**
```json
{
  "message_id": 3,
  "timestamp": "2026-03-18T12:35:10Z",
  "updated_at": "2026-03-18T12:35:10Z"
}
```

---

#### Query with Conversation Context
```http
POST /ask
Content-Type: application/x-www-form-urlencoded

question=<question>
retrieve_k=5
temperature=1.0
auth_token=<optional>
session_id=<session-uuid>
conversation_id=<conversation-uuid>
```

The `/ask` endpoint now:
1. Loads prior conversation history if `session_id` and `conversation_id` are provided
2. Injects previous messages into the RAG context
3. Appends the user question and assistant response to the conversation history
4. Persists the updated session to CosmosDB

---

### Foundry Chat Completion

Called internally by `/ask` and `/api/ask` endpoints.

#### Implementation Notes

- Uses Azure OpenAI Python SDK (`openai >= 1.51.0`)
- Authenticates via `DefaultAzureCredential` (supports Managed Identity, CLI login, shared credentials, etc.)
- Endpoints use Foundry API schema:
  - Model: deployment name (passed as `model` parameter)
  - API version: `2024-08-01-preview` (Foundry-compatible)

#### Example Usage

```python
from openai import AzureOpenAI

client = AzureOpenAI(
    api_key=credential.get_token("https://cognitiveservices.azure.com/.default").token,
    api_version="2024-08-01-preview",
    azure_endpoint=config.openai_endpoint,
)

response = client.chat.completions.create(
    model="gpt-5.1-chat",  # Deployment name
    messages=[
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "What is cybersecurity?"},
    ],
    max_tokens=600,
    temperature=1.0,
)
```

---

## CosmosDB Schema

### Database & Container

- **Database:** `rag-conversations`
- **Container:** `conversations`
  - **Partition Key:** `/user_id`
  - **Document ID:** `{user_id}#{conversation_id}`

### Document Structure

```json
{
  "id": "5a4b8d2e#f47ac10b-58cc-4372-a567-0e02b2c3d479",
  "session_id": "550e8400-e29b-41d4-a716-446655440000",
  "user_id": "5a4b8d2e",
  "conversation_id": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
  "messages": [
    {
      "role": "user",
      "content": "...",
      "timestamp": "2026-03-18T12:35:00Z"
    },
    {
      "role": "assistant",
      "content": "...",
      "timestamp": "2026-03-18T12:35:05Z"
    }
  ],
  "created_at": "2026-03-18T12:34:56Z",
  "updated_at": "2026-03-18T13:45:00Z",
  "evaluation_threshold": 0.72,
  "type": "conversation"
}
```

---

## Session Scoping

### Per-User (`auth_token`)

If an `auth_token` is provided and matches `config.auth_token`:
- User ID is derived from `sha256(auth_token)[:16]`
- Conversations persist across browser sessions under this user ID
- **Benefit:** Share conversation state across devices

### Per-Session (Cookie-based)

If no `auth_token` or auth is disabled:
- Session ID is generated on first request (stored in browser cookie)
- User ID is derived from the session ID
- Conversations isolated to the browser session
- **Benefit:** Anonymous conversation tracking

### Both (Hybrid)

The app supports both scopes simultaneously:
1. Authenticated users see only their conversations (auth-scoped)
2. Anonymous users see only their session's conversations (session-scoped)
3. Switching auth tokens isolates conversation space

---

## Security Considerations

### Authentication & Authorization

- `DefaultAzureCredential` handles token acquisition (no credential storage in environment)
- Optional `QUERY_WEB_AUTH_TOKEN` can gate conversation access
- CosmosDB partition key on `user_id` ensures query isolation

### Data Privacy

- Conversation history is stored in CosmosDB with partition separation
- Plan: Implement purge policies (e.g., TTL on messages > 30 days)
- Consider: Data classification & encryption at rest

### Fallback Behavior

If CosmosDB is unavailable:
- In-memory conversation tracking persists only for the current app instance
- Messages are not persisted across restarts
- Logs warning (`CosmosDB unavailable: ...`)

---

## Deployment Checklist

- [ ] Terraform provides `AZURE_COSMOS_ENDPOINT` to the Container App
- [ ] Managed identity has `Cosmos DB Built-in Data Contributor` on CosmosDB account
- [ ] CosmosDB database `rag-conversations` and container `conversations` created (or auto-provisioned)
- [ ] Service principal/Managed Identity has CosmosDB contributor role
- [ ] `openai==1.51.0` and `azure-cosmos==4.7.0` in `requirements.txt`
- [ ] Test conversation creation and history retrieval in `/api/ask` flow

---

## Future Enhancements

1. **Conversation Threading** — Support multiple parallel conversation branches
2. **Message Edit/Delete** — Allow amendments to prior turns
3. **Export to Markdown** — Download conversation as `.md` file
4. **TTL Expiry** — Auto-purge conversations older than N days
5. **Rate Limiting** — Per-user conversation creation quotas
6. **Analytics** — Track conversation patterns, response latency histograms
7. **Search Within Conversation** — Full-text search over message content
