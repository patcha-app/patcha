# Embedding Pipeline

`rust/patcha/src/process.rs` (text extraction + `EventPreprocessor`),
`rust/patcha/src/embedding.rs` (`Embedder`)

The embedding pipeline turns raw `Event` objects into vectors ready for storage
in the local `sqlite-vec` store. It runs entirely **on-device** — no API key and
no network call.

## Model

Embeddings come from a local [fastembed](https://github.com/Anush008/fastembed-rs)
model, `BAAI/bge-base-en-v1.5` by default (768-dim, 512 native tokens). Configure
with `EMBEDDING_MODEL`; models are downloaded once and cached under
`EMBEDDING_CACHE_DIR` (`~/.patcha/models`).

BGE is an **asymmetric** retrieval model: user queries are prefixed with an
instruction (`query_instruction_prefix`) before embedding, while stored documents
are embedded as-is. The `Embedder` handles this distinction.

## Text extraction

Before embedding, `build_embedding_text(event)` derives a meaningful string from
the event's `raw_content` based on its type:

| Event type                     | Extracted text                                    |
| ------------------------------ | ------------------------------------------------- |
| `Browser`                      | `title \| domain`                                 |
| `Terminal`                     | the command                                       |
| `GitCommit` / `GitStash`       | `message` + `files: file1, file2, ...` (up to 20) |
| `Screen`                       | `gist \| <on-screen text>` (VLM gist prepended)   |
| others                         | the raw `raw_content` string                      |

For screen events the on-device FastVLM **gist** is prepended so retrieval works
on *what the user was doing*, not just the literal OCR text (see
[collectors.md](collectors.md)).

## Chunking

Long texts are split into overlapping chunks sized to the model's effective token
budget. The budget is computed from the model's native limit with a safety factor
(`TOKEN_SAFETY = 0.8`), because BGE's WordPiece tokenizer produces more tokens
than the `cl100k` tokenizer used for estimation. Overlap is set by
`EMBEDDING_CHUNK_OVERLAP`.

Most events produce a single chunk. When an event splits into several, each chunk
is embedded separately and stored with chunk-index metadata so the deterministic
IDs used by the store keep chunks associated with their source event.

## Pipeline (`EventPreprocessor`)

`EventPreprocessor` (in `process.rs`) owns the `Embedder` and drives processing:

1. `build_embedding_text` extracts text for the event
2. the text is chunked to the token budget
3. each chunk is embedded via the local model
4. one or more `Event` objects with populated embeddings are returned

Events that fail to embed are passed through un-embedded rather than dropped, and
pending events can be persisted and re-processed later (`process_pending` /
`save_pending`).
