"""Context retrieval for agent consumption — Layer 1 (working memory) and Layer 2 (semantic search)."""

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, List, Dict, Any, Optional

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity as sklearn_cosine

from patcha.config import config

if TYPE_CHECKING:
    from patcha.db.store import VectorStore
    from patcha.process import EventPreprocessor

log = logging.getLogger(__name__)


def _cosine_similarity(a: List[float], b: List[float]) -> float:
    va, vb = np.array(a), np.array(b)
    denom = np.linalg.norm(va) * np.linalg.norm(vb)
    return float(np.dot(va, vb) / denom) if denom else 0.0


def _format_line(payload: dict) -> str:
    ts = payload.get("timestamp", "")
    hhmm = (ts[:10] + " " + ts[11:16]) if len(ts) >= 16 else "??:??"
    event_type = payload.get("type", "unknown")
    meta = payload.get("metadata") or {}
    raw = payload.get("raw_content", "")

    if event_type == "terminal":
        try:
            detail = json.loads(raw).get("command", raw)[:120]
        except Exception:
            detail = raw[:120]

    elif event_type == "browser":
        try:
            data = json.loads(raw)
            title = data.get("title", "")
            domain = data.get("domain", "")
            detail = f"{title} | {domain}" if domain else title
        except Exception:
            detail = raw[:80]

    elif event_type in ("git_commit", "git_stash"):
        try:
            data = json.loads(raw)
            msg = data.get("message", "")
            files = data.get("files_changed", [])
            detail = msg + (" | " + ", ".join(files[:5]) if files else "")
        except Exception:
            detail = raw[:120]

    elif event_type == "git_staged":
        detail = raw[:120]

    elif event_type in ("screen", "window"):
        gist = meta.get("gist")
        if gist:
            detail = gist
        else:
            app = meta.get("app_name", "")
            title = meta.get("window_title", "")
            detail = f"{app} — {title}" if title else app
        if meta.get("transition") == "switch":
            detail = f"[app switch] {detail}"

    else:
        detail = raw[:120]

    return f"[{hhmm}] {event_type}: {detail}"


def _dedup_by_similarity(
    rows: List[Dict[str, Any]], threshold: float
) -> List[Dict[str, Any]]:
    # rows must be sorted ascending by timestamp
    last_vector: dict[str, List[float]] = {}
    kept = []

    for row in rows:
        p = row.get("payload", {})
        vector = row.get("vector")
        event_type = p.get("type", "")

        if vector and event_type in last_vector:
            sim = _cosine_similarity(vector, last_vector[event_type])
            if sim >= threshold:
                for i in range(len(kept) - 1, -1, -1):
                    if kept[i].get("payload", {}).get("type") == event_type:
                        kept[i] = row
                        break
                last_vector[event_type] = vector
                continue

        kept.append(row)
        if vector:
            last_vector[event_type] = vector

    return kept


_RRF_K = 60


def _tfidf_rank(query: str, candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not candidates:
        return []

    corpus = [c["payload"].get("raw_content", "") or "" for c in candidates]
    if not any(corpus):
        return candidates

    try:
        vectorizer = TfidfVectorizer(
            min_df=1,
            max_features=20000,
            sublinear_tf=True,
            strip_accents="unicode",
            analyzer="word",
            ngram_range=(1, 2),
        )
        all_texts = corpus + [query]
        tfidf_matrix = vectorizer.fit_transform(all_texts)
        query_vec = tfidf_matrix[-1]
        doc_vecs = tfidf_matrix[:-1]
        sims = sklearn_cosine(query_vec, doc_vecs).flatten()
        ranked = sorted(zip(sims, candidates), key=lambda x: x[0], reverse=True)
        return [{**c, "score": float(sim)} for sim, c in ranked]
    except Exception as e:
        log.warning("TF-IDF ranking failed, returning unranked: %s", e)
        return candidates


def _reciprocal_rank_fusion(
    vector_results: List[Dict[str, Any]],
    text_results: List[Dict[str, Any]],
    limit: int,
) -> List[Dict[str, Any]]:
    scores: Dict[str, Dict[str, Any]] = {}

    for rank_0, result in enumerate(vector_results):
        doc_id = str(result["id"])
        contribution = 1.0 / (_RRF_K + rank_0 + 1)
        if doc_id not in scores:
            scores[doc_id] = {"payload": result["payload"], "rrf_score": 0.0}
        scores[doc_id]["rrf_score"] += contribution

    for rank_0, result in enumerate(text_results):
        doc_id = str(result["id"])
        contribution = 1.0 / (_RRF_K + rank_0 + 1)
        if doc_id not in scores:
            scores[doc_id] = {"payload": result["payload"], "rrf_score": 0.0}
        scores[doc_id]["rrf_score"] += contribution

    merged = sorted(
        [{"id": doc_id, **item} for doc_id, item in scores.items()],
        key=lambda x: x["rrf_score"],
        reverse=True,
    )
    return [
        {"id": x["id"], "score": x["rrf_score"], "payload": x["payload"]}
        for x in merged
    ][:limit]


def get_working_memory(store: "VectorStore", minutes: int = 15) -> str:
    since = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    rows = store.get_recent_events_with_vectors(since)

    rows = _dedup_by_similarity(rows, config.working_memory_dedup_threshold)

    lines = [_format_line(r.get("payload", {})) for r in rows]

    if not lines:
        return f"# Working memory (last {minutes}m)\nNo activity recorded."

    return f"# Working memory (last {minutes}m)\n" + "\n".join(lines)


def get_recent_activity(
    store: "VectorStore", hours: int = 3, app_filter: Optional[str] = None
) -> str:
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    rows = store.get_recent_events_with_vectors(since)

    if app_filter:
        rows = [
            r
            for r in rows
            if (r.get("payload") or {}).get("metadata", {}).get("app_name")
            == app_filter
        ]

    rows = _dedup_by_similarity(rows, config.working_memory_dedup_threshold)
    lines = [_format_line(r.get("payload", {})) for r in rows]
    app_tag = f" (app={app_filter})" if app_filter else ""
    if not lines:
        return f"# Recent activity (last {hours}h){app_tag}\nNo activity recorded."
    return f"# Recent activity (last {hours}h){app_tag}\n" + "\n".join(lines)


def _format_detail(payload: dict) -> str:
    event_type = payload.get("type", "")
    if event_type in ("git_commit", "git_stash"):
        try:
            data = json.loads(payload.get("raw_content", ""))
            ts = payload.get("timestamp", "")
            hhmm = (ts[:10] + " " + ts[11:16]) if len(ts) >= 16 else "??:??"
            project = payload.get("project", "")
            msg = data.get("message", "").strip()
            files = data.get("files_changed", [])
            diff = data.get("diff", "")

            project_tag = f" [{project}]" if project else ""
            header = f"[{hhmm}] {event_type}: {msg}{project_tag}"
            if files:
                header += f"\nFiles: {', '.join(files)}"
            if diff:
                return header + f"\n\n{diff}"
            return header + "\n(no diff stored)"
        except (json.JSONDecodeError, TypeError):
            pass

    base = _format_line(payload)
    if event_type == "git_staged":
        raw = payload.get("raw_content", "")
        if "Diff:\n" in raw:
            return base + "\n" + raw[raw.index("Diff:\n") :]
    if event_type in ("screen", "window"):
        meta = payload.get("metadata") or {}
        raw_source = meta.get("raw_text_source")
        raw = payload.get("raw_content", "")
        detail_lines = []
        if raw_source and raw_source != "ax":
            detail_lines.append(f"capture: {raw_source}")
        if raw:
            snippet = raw[raw.find(": ") + 2 :] if ": " in raw else raw
            detail_lines.append(snippet[:300])
        if detail_lines:
            return base + "\n" + "\n".join(detail_lines)
    return base


def search_activity(
    store: "VectorStore",
    preprocessor: "EventPreprocessor",
    query: str,
    limit: int = 5,
    app_filter: Optional[str] = None,
) -> str:
    try:
        embedding = preprocessor.generate_embedding(query)
    except Exception as e:
        return (
            f'# Search results for "{query}"\nEmbedding failed: {type(e).__name__}: {e}'
        )
    if not embedding:
        return f'# Search results for "{query}"\nEmbedding failed — no vector returned.'

    vector_results = store.search_events(embedding, limit=limit, app_filter=app_filter)

    ft_candidates = store.fetch_fulltext_candidates(
        query=query,
        limit=min(limit * 5, 100),
        app_filter=app_filter,
    )
    ft_ranked = _tfidf_rank(query, ft_candidates)[:limit]

    merged = (
        _reciprocal_rank_fusion(vector_results, ft_ranked, limit=limit)
        if ft_ranked
        else vector_results
    )

    if not merged:
        return f'# Search results for "{query}"\nNo results found.'

    lines = []
    for r in merged:
        score = round(r.get("score", 0), 3)
        p = r.get("payload", {})
        ts = p.get("timestamp", "")[:16].replace("T", " ")
        line = _format_detail(p)
        lines.append(f"[score={score} | {ts}] {line.split('] ', 1)[-1]}")

    app_tag = f" (app={app_filter})" if app_filter else ""
    return f'# Search results for "{query}"{app_tag}\n' + "\n\n".join(lines)
