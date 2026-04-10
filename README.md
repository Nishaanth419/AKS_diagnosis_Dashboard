# SRE Bot  AKS AI RAG

An AI-powered SRE assistant that receives Microsoft Teams messages via Power Automate, diagnoses incidents using Azure OpenAI + vector RAG, and posts structured runbooks back to the Teams thread.

---

## Architecture at a Glance

```
Teams Message
     
     
Power Automate POST app.py (/api/teams)
                              
                               spawns background thread
                              
                        orchestrator.py   keywords.py (classify intent)
                              
               
                                            
         database.py     ai_handlers.py   github_grounding.py
          (RAG search)    (GPT prompts)    (live release data)
                             
                             
        azure_config.py   azure_config.py
        (AI Search)       (Azure OpenAI)
               
               
         teams_poster.py POST Power Automate  Teams
```

**Stack:** Azure AKS  Azure OpenAI (gpt-5.4-mini + text-embedding-3-small)  Azure AI Search  Microsoft Teams  Power Automate  Flask  Gunicorn  West Europe region

---

## File Reference

### `config.py`
Shared constants. Loaded by every module that makes HTTP calls.

| Object | What it is |
|--------|-----------|
| `TEAMS_WEBHOOK_URL` | Power Automate flow URL  the bot posts replies here |
| `session` | `requests.Session()`  reused by `teams_poster.py` and `github_grounding.py` to avoid opening a new TCP connection per request |

---

### `azure_config.py`
Creates all Azure service clients at startup. Every other file imports from here.

| Object / Function | What it does |
|------------------|-------------|
| `load_dotenv(Azure.env)` | Reads secrets at startup (API keys, endpoints) |
| `ai_client` | `AzureOpenAI` client  used for both **chat completions** and **embeddings** |
| `search_client` | `SearchClient`  talks to Azure AI Search index `sre-incidents` |
| `CHAT_MODEL` | `"gpt-5.4-mini"`  the GPT model used for all AI answers |
| `EMBED_MODEL` | `"text-embedding-3-small"`  converts text  512-dim vector |
| `get_embedding(text)` | Calls `ai_client.embeddings.create()`  returns a **512-dimensional float list** |

---

### `app.py`
Flask HTTP server  the single public entry point.

| Function | Route | What it does |
|----------|-------|-------------|
| `health_check()` | `GET /` | Returns 200 OK  used by K8s liveness/readiness probes |
| `teams_webhook()` | `POST /api/teams` | Receives JSON from Power Automate. Extracts `message_id`, `parent_id`, `subject`, `body`, `base64_images`. Sets `is_reply = (parent_id != message_id)`. Spawns a **background thread** calling `process_teams_message()` and immediately returns `{"status": "Success"}` so Power Automate does not time out |

---

### `keywords.py`
All text classification and keyword extraction. No AI calls  pure regex/heuristic logic.

#### Classification Functions

| Function | What it does |
|----------|-------------|
| `greeting_response(text)` | Matches simple greetings (`hi`, `hello`, `thanks`, `help`) with `_PURE_GREETING_RE`. Returns a canned string or `None` |
| `is_history_request(text)` | Checks `_HISTORY_RES` regex patterns  looks for "previous/past/earlier + issue/incident/chat" combinations |
| `is_incident_message(text, subject)` | **Scoring classifier**  adds points for incident keywords (`error`, `failed`, `timeout`, `OOMKilled`), log structure keywords (`kubectl`, `pod`, `namespace`), and known cluster names. Subtracts for question words without incident context. Score >= 2  classified as incident |

#### Keyword Extraction

| Function | What it does |
|----------|-------------|
| `extract_keywords(text, max_keywords=40)` | `@lru_cache(512)`  Step 1: Finds cluster-specific names (services, namespaces, nodepools) via `_CLUSTER_KW_RE` (word-boundary regex over 100+ known names). Step 2: Tokenizes on whitespace and log separators. Step 3: Filters out `_STOP_WORDS` (~100 English + K8s noise words), timestamps, hex hashes, IPs, version strings, pure numbers. Step 4: Returns up to 40 keywords as a space-separated string  this string is what gets vectorised |
| `_extract_history_query_keywords(query_text)` | Like `extract_keywords` but capped at 12 keywords  tighter focus for history searches |
| `_keyword_overlap_ratio(left, right)` | Jaccard index = intersection / union between two keyword strings |

#### Key Data Structures

- `_CLUSTER_KEYWORDS`  `frozenset` of 100+ known service/namespace/nodepool names in the cluster
- `_STOP_WORDS`  `frozenset` of ~100 English + K8s structural noise words
- All regex patterns pre-compiled once at import time for performance

---

### `utils.py`
Reusable utilities. No Azure calls.

| Function | What it does |
|----------|-------------|
| `_strip_html(text)` | Decodes HTML entities, removes Teams `<at>` mention tags, strips remaining HTML tags, collapses whitespace |
| `_escape_html(text)` | Escapes `&`, `<`, `>` for safe HTML output |
| `_shorten(text, limit)` | Truncates to `limit` chars with `"..."` |
| `prepare_image_content_blocks(base64_images)` | Converts base64 strings  OpenAI vision `{"type": "image_url", ...}` blocks |
| `build_content_parts(text, base64_images)` | No images  returns plain `str` (cheaper). With images  returns `list[dict]` in OpenAI multimodal format (image blocks first, then text) |
| `parse_chat_history(chat_history)` | Tolerates string or list input, parses JSON, falls back gracefully |
| `append_chat_history(history, user_msg, bot_msg)` | Adds a `{role: user}` + `{role: assistant}` turn, returns JSON string |
| `trim_chat_history(messages, max_turns=6)` | Keeps only the last 6 turns (12 messages) to stay within token limits |

---

### `database.py`
All reads/writes to Azure AI Search. This is the RAG layer.

| Function | What it does |
|----------|-------------|
| `get_cached_embedding(text)` | `@lru_cache(256)` wrapper around `get_embedding()`  avoids duplicate embedding API calls within a session |
| `get_thread_context(thread_id)` | `search_client.get_document(key=thread_id)`  fetches a stored thread by its exact ID. Returns `{subject, log, diagnosis, chat_history}` |
| `save_or_update_incident(...)` | Upserts a document. If `skip_embedding=False`  extracts keywords  generates embedding  attaches `contentVector`. If `skip_embedding=True`  saves only updated fields (used for follow-ups where the log has not changed, saving an API call) |
| `find_similar_incident(log_text, subject)` | Vector + Jaccard hybrid search for RAG. Uses `k_nearest_neighbors=3`. Accepts only if `vec_score >= 0.60` AND `keyword_overlap >= 0.10`. Final score = `vec x 0.65 + kw x 0.35`. Returns best match `{subject, diagnosis, log, chat_history}` |
| `search_similar_topics(query_text, top_k=3)` | Broader search for history requests. `k_nearest_neighbors=6`. Softer threshold (combined >= 0.40). Score = `vec x 0.75 + kw x 0.25`. Returns top 3 matches |
| `format_history_explanation(matches)` | Fallback HTML formatter used when the AI call fails |

---

### `github_grounding.py`
Injects live GitHub release data into the AI system prompt when users ask version questions.

| Function | What it does |
|----------|-------------|
| `_detect_repos(text)` | Scans the message for known tool names (argo, helm, prometheus, etc.) and returns matching `owner/repo` slugs from `_GITHUB_REPO_MAP` (50+ tools) |
| `_build_release_context(user_message)` | If `_VERSION_QUERY_RE` matches the message (words like "latest", "version", "upgrade"), fetches the GitHub releases/latest API for up to 4 detected repos. Results cached in `_release_cache` dict with 1-hour TTL. Returns a markdown block injected into the AI system prompt |

---

### `ai_handlers.py`
Constructs prompts and calls Azure OpenAI. All four handlers share the `STACK_CONTEXT` constant (Azure AKS, Algo Workflows, West Europe).

| Function | Model Params | When used |
|----------|-------------|-----------|
| `ask_ai_incident(user_log, memory_data, base64_images)` | max_tokens=4096, temp=0.2 | New incident. If `memory_data` is set (RAG hit), prepends historical context. Outputs structured runbook: What Went Wrong  Impact  Fix Steps  Verify |
| `ask_ai_followup(question, log_context, chat_history_list, base64_images)` | max_tokens=2048 | Reply in an incident thread. Builds message list: system + trimmed chat history + new user message |
| `ask_ai_general(user_message, chat_history_list, thread_context, base64_images)` | max_tokens=2048, temp=0.4 | General Q&A, greetings, DevOps questions. Injects GitHub release data if version-related |
| `ask_ai_history_summary(query_text, matches)` | max_tokens=3072, temp=0.2 | History requests. Formats matched past incidents as structured blocks, asks the AI to summarise patterns and give next steps |

All handlers use `build_content_parts()`  sends multimodal content if images are present, otherwise plain text (cheaper).

---

### `response_metrics.py`
Scores every AI response for quality. Runs locally  no AI calls.

| Metric | Incident Weight | What it checks |
|--------|----------------|---------------|
| Structural Completeness | 20% | Presence of root cause, impact, resolution steps, verification sections |
| Actionability | 25% | Count of bash code blocks and kubectl/az/helm commands |
| Relevance | 15% | Input log keywords reflected in the response |
| Conciseness | 10% | Word count in range (incident: 150-1200 words) |
| Code Block Quality | 15% | Language hints present, no placeholder text, not empty |
| Verification Coverage | 10% | Presence of verify/rollback/health check language |
| Response Latency | 5% | <=5s excellent, <=15s acceptable, <=30s poor |

Grades: A >= 0.9  B >= 0.8  C >= 0.65  D >= 0.5  F < 0.5

---

### `teams_poster.py`
Posts the bot reply back to the Teams thread via Power Automate.

| Function | What it does |
|----------|-------------|
| `post_incident_card(tid, title, raw_log, ai_diagnosis, is_cached)` | Wraps the AI runbook in HTML. Converts newlines to `<br>` and `**` to `<b>`. Posts `{messageId, replyText}` to `TEAMS_WEBHOOK_URL` |
| `post_chat_card(tid, question, answer)` | Same pattern but lighter formatting  used for general chat and history replies |

---

## Complete Data Flows

### Path A  New Incident

```
Teams POST /api/teams
   app.py: extract fields, spawn thread
        orchestrator.py: process_teams_message()
            
             keywords.is_incident_message(body)  True
            
             database.find_similar_incident(body)
                 keywords.extract_keywords(subject + body)
                         "oomkilled algo-workflows redis crashloopbackoff ..."
                 azure_config.get_cached_embedding(keywords)
                         [0.021, -0.043, ... 512 floats]
                 search_client.search(vector_query, k=3)
                         Azure AI Search returns top 3 candidates
                 score = vec x 0.65 + jaccard x 0.35
                      past incident returned if both thresholds pass
            
             ai_handlers.ask_ai_incident(body, memory_data=past_incident)
                 ai_client.chat.completions.create(
                     system = STACK_CONTEXT + runbook_template,
                     user   = historical_context + current_log
                   )  markdown runbook
            
             response_metrics.evaluate_response(runbook, body, elapsed)
                 quality score + grade printed to logs
            
             database.save_or_update_incident(tid, subject, body, runbook, chat_json)
                 keywords.extract_keywords(subject + body)
                 azure_config.get_embedding(keywords)  512-dim vector
                 search_client.upload_documents([{...contentVector}])
                      stored in Azure AI Search for future RAG hits
            
             teams_poster.post_incident_card(tid, subject, body, runbook)
                  session.post(TEAMS_WEBHOOK_URL, {messageId, replyText})
                       Power Automate  Teams thread reply
```

### Path B  Follow-up Reply

```
Teams POST (parent_id set  is_reply=True)
   orchestrator.py
        database.get_thread_context(tid)        fetch original log from Search
        is_incident_message(stored_log)  T/F
        ai_handlers.ask_ai_followup(
             question,
             original_log,
             last 6 chat turns
         )  focused answer (no re-running full runbook)
        database.save_or_update_incident(..., skip_embedding=True)
            updates chat_history only  NO new embedding (saves API cost)
        teams_poster.post_chat_card(tid, question, answer)
```

### Path C  History Request

```
"show me past issues with redis"
   keywords.is_history_request(body)  True
        database.search_similar_topics(query, top_k=3)
            extract_keywords(query)  "redis issues"
            get_cached_embedding("redis issues")  512-dim vector
            search_client.search(k=6)  6 candidates
            score = vec x 0.75 + jaccard x 0.25, threshold 0.40
                 up to 3 past incidents returned with chat history
        ai_handlers.ask_ai_history_summary(query, matches)
             formats each match: Subject / Log / Fix / Chat (up to 6 turns)
               asks AI to summarise patterns + give next steps
```

### Path D  Greeting

```
"hi" / "hello" / "thanks"
   keywords.greeting_response(body)  canned string
        teams_poster.post_chat_card(tid, body, canned_response)
            (no AI call, no database write)
```

---

## Vectorisation Pipeline

```
Raw text  (subject + log body from Teams)
   
   
utils._strip_html()
   Remove Teams HTML markup, <at> mention tags, decode HTML entities
   
   
keywords.extract_keywords()
   Keep:  cluster service names, namespace names, error terms, action words
   Drop:  timestamps, IP addresses, hex hashes, pod UUIDs, version strings,
          stop words (~100 English + K8s structural noise)
   
   
"redis crashloopbackoff argo-events oomkilled workflows"
   (up to 40 keywords, space-separated)
   
   
azure_config.get_embedding()
   POST to Azure OpenAI    text-embedding-3-small
   
   
[0.021, -0.043, 0.118, ...]
   512 floats  one vector per document
   
   
search_client.upload_documents(contentVector = vector)
   
   
Azure AI Search index  (sre-incidents)
   Fields: id  subject  log  diagnosis  chat_history  contentVector
```

> **Why embed keywords instead of raw text?**
> Raw logs contain thousands of noisy tokens (timestamps, hex hashes, IP addresses, pod UUIDs) that would dominate the vector and make similarity matches imprecise. Extracting ~40 high-signal keywords first gives the embedding model a clean, semantically rich input  resulting in much more accurate RAG retrieval.

---

## Azure AI Search  Document Schema

| Field | Type | Purpose |
|-------|------|---------|
| `id` | string | Unique thread ID (Teams message_id or timestamp for uploaded docs) |
| `subject` | string | Incident title / Teams message subject |
| `log` | string | Original error log or user message body |
| `diagnosis` | string | AI-generated runbook or answer |
| `chat_history` | string | JSON array of {role, content}  every follow-up turn in the thread |
| `contentVector` | Collection(Edm.Single) | 512-dim embedding of extracted keywords  used for vector similarity search |

---

## Scoring Thresholds

| Search Function | Vector Weight | Keyword Weight | Accept Threshold |
|----------------|--------------|----------------|-----------------|
| `find_similar_incident()` | 65% | 35% | vec >= 0.60 AND kw_overlap >= 0.10 |
| `search_similar_topics()` | 75% | 25% | combined >= 0.40 |

---

## Environment Variables (`Azure.env`)

| Variable | Purpose |
|----------|---------|
| `AZURE_OPENAI_ENDPOINT` | Azure OpenAI resource URL |
| `AZURE_OPENAI_API_VERSION` | API version (e.g. 2024-12-01-preview) |
| `AZURE_OPENAI_API_KEY` | Azure OpenAI API key |
| `CHAT_MODEL` | Chat completion deployment name (e.g. gpt-5.4-mini) |
| `EMBED_MODEL` | Embedding deployment name (e.g. text-embedding-3-small) |
| `AZURE_SEARCH_ENDPOINT` | Azure AI Search endpoint URL |
| `AZURE_SEARCH_INDEX` | Index name (default: sre-incidents) |
| `AZURE_SEARCH_API_KEY` | Azure AI Search API key |

---

## Running Locally

```bash
pip install -r requirements.txt
python app.py
```

## Running in AKS

```bash
# Build and push image to ACR
az acr build \
  --registry <YOUR_ACR_NAME> \
  --image sre-bot:latest \
  --location westeurope \
  .

# Attach ACR to AKS (one-time)
az aks update \
  --name <YOUR_AKS_CLUSTER> \
  --resource-group <YOUR_RG> \
  --attach-acr <YOUR_ACR_NAME>

# Apply K8s manifests
kubectl apply -f k8s/secret.yaml
kubectl apply -f k8s/deployment.yaml

# Get the public IP
kubectl get service sre-bot-service
```

---

## Caching Summary

| Cache | Location | Size | What it caches |
|-------|----------|------|---------------|
| `@lru_cache(512)` | `keywords.extract_keywords()` | 512 entries | Keyword extraction results |
| `@lru_cache(256)` | `database.get_cached_embedding()` | 256 entries | Embedding API call results |
| TTL dict (3600s) | `github_grounding._release_cache` | unbounded | GitHub releases/latest responses |
| `skip_embedding=True` | `database.save_or_update_incident()` |  | Skips re-embedding on follow-up saves |
| `trim_chat_history(max_turns=6)` | `utils.trim_chat_history()` |  | Limits chat history to last 6 turns for token budget |
