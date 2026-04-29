# Embedding Pipeline

`memorai/process.py`

`EventPreprocessor` handles converting raw `Event` objects into vector embeddings ready for storage in Qdrant.

## Model

`text-embedding-3-small` via OpenAI. Configured via `config.openai_api_key`. The vector dimension is set in `config.vector_size` and must match the Qdrant collection configuration.

## Text extraction

Before embedding, `_build_embedding_text(event)` extracts a meaningful text string from the event's `raw_content` based on type:

| Event type | Extracted text |
|------------|---------------|
| `browser` | `title \| domain` from JSON payload |
| `terminal` | `command` field from JSON payload |
| `git_commit` / `git_stash` | `message` + `files: file1, file2, ...` (up to 20 files) |
| all others | raw `raw_content` string |

If the event has a `project` set, it is appended as `[project_name]` to provide project-scoped context to the embedding.

## Chunking

Long texts are split via `chunk_text(text, max_tokens, overlap)` using token estimates (`config.max_embedding_tokens`, `config.embedding_chunk_overlap`). Most events produce a single chunk. When an event splits into multiple chunks:

- Each chunk becomes a separate `Event` copy with `metadata.chunk_index` and `metadata.total_chunks` added
- If `source_doc_id` is set on the original event, chunks get IDs like `{source_doc_id}::chunk::{i}` — this feeds the deterministic UUID deduplication in `VectorStore.store_event`

## API

### `generate_embedding(text) -> Optional[List[float]]`

Calls the OpenAI embeddings API and returns the embedding vector, or `None` on failure.

### `process_event(event) -> List[Event]`

Processes a single event:
1. Extracts text with `_build_embedding_text`
2. Chunks the text
3. Generates an embedding per chunk
4. Returns one or more `Event` objects with `.embedding` populated

### `process_events(events) -> List[Event]`

Batch wrapper around `process_event`. Errors on individual events are logged and the unembedded event is passed through rather than dropped.
