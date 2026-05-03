"""Backfill diff content into git_commit points that were stored before diff capture was added."""

import json
import sys
from pathlib import Path

from git import Repo
from qdrant_client.models import FieldCondition, Filter, MatchValue, PointStruct

sys.path.insert(0, str(Path(__file__).parent.parent))

from memorai.db.store import VectorStore
from memorai.process import EventPreprocessor


def _build_embedding_text(data: dict, project: str) -> str:
    parts = []
    if data.get("message"):
        parts.append(data["message"])
    files = data.get("files_changed", [])
    if files:
        parts.append("files: " + ", ".join(files[:20]))
    if data.get("diff"):
        parts.append(data["diff"])
    text = " | ".join(parts) if parts else json.dumps(data)
    if project:
        text = f"{text} [{project}]"
    return text.strip()


def backfill(dry_run: bool = False) -> None:
    store = VectorStore()
    preprocessor = EventPreprocessor()
    client = store.client

    all_points = []
    next_offset = None
    while True:
        points, next_offset = client.scroll(
            collection_name=store.collection_name,
            scroll_filter=Filter(must=[
                FieldCondition(key="type", match=MatchValue(value="git_commit"))
            ]),
            limit=100,
            offset=next_offset,
            with_vectors=True,
            with_payload=True,
        )
        all_points.extend(points)
        if next_offset is None:
            break

    print(f"Found {len(all_points)} git_commit points")

    updated = 0
    skipped = 0
    errors = 0

    for point in all_points:
        payload = point.payload or {}
        raw = payload.get("raw_content", "")

        try:
            data = json.loads(raw)
            if data.get("diff"):
                skipped += 1
                continue
        except (json.JSONDecodeError, TypeError):
            data = {}

        commit_hash = data.get("hash") or (payload.get("metadata") or {}).get("commit_hash")
        repo_path = (payload.get("metadata") or {}).get("repo_path")

        # For truncated JSON, try to extract commit hash from the raw string
        if not commit_hash and '"hash":' in raw:
            try:
                commit_hash = raw.split('"hash": "')[1].split('"')[0]
            except IndexError:
                pass

        if not commit_hash or not repo_path or not Path(repo_path).exists():
            skipped += 1
            continue

        try:
            repo = Repo(repo_path)
            commit = repo.commit(commit_hash)
            diff_text = repo.git.diff(commit.parents[0].hexsha, commit_hash) if commit.parents else ""
        except Exception as e:
            print(f"  git error {commit_hash[:8]}: {e}")
            errors += 1
            continue

        if not diff_text:
            skipped += 1
            continue

        data["diff"] = diff_text
        new_raw = json.dumps(data, default=str)
        project = payload.get("project", "")
        text = _build_embedding_text(data, project)

        try:
            embedding = preprocessor.generate_embedding(text)
        except Exception as e:
            print(f"  embedding error {commit_hash[:8]}: {e}")
            errors += 1
            continue

        msg = data.get("message", "").replace("\n", " ")
        print(f"  {'[dry]' if dry_run else 'updating'} {commit_hash[:8]} {project}: {msg}")

        if not dry_run:
            client.upsert(
                collection_name=store.collection_name,
                points=[PointStruct(
                    id=point.id,
                    vector=embedding,
                    payload={**payload, "raw_content": new_raw},
                )],
            )
        updated += 1

    print(f"\nupdated={updated} skipped={skipped} errors={errors}")


def reembed(dry_run: bool = False) -> None:
    """Re-fetch all files for every stored git_commit and regenerate embeddings."""
    store = VectorStore()
    preprocessor = EventPreprocessor()
    client = store.client

    all_points = []
    next_offset = None
    while True:
        points, next_offset = client.scroll(
            collection_name=store.collection_name,
            scroll_filter=Filter(must=[
                FieldCondition(key="type", match=MatchValue(value="git_commit"))
            ]),
            limit=100,
            offset=next_offset,
            with_vectors=True,
            with_payload=True,
        )
        all_points.extend(points)
        if next_offset is None:
            break

    print(f"Found {len(all_points)} git_commit points")

    updated = 0
    skipped = 0
    errors = 0

    for point in all_points:
        payload = point.payload or {}
        raw = payload.get("raw_content", "")

        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            skipped += 1
            continue

        if not data.get("diff"):
            skipped += 1
            continue

        commit_hash = data.get("hash")
        repo_path = (payload.get("metadata") or {}).get("repo_path")

        if not commit_hash or not repo_path or not Path(repo_path).exists():
            skipped += 1
            continue

        try:
            repo = Repo(repo_path)
            commit = repo.commit(commit_hash)
            all_files = list(commit.stats.files.keys())
        except Exception as e:
            print(f"  git error {commit_hash[:8]}: {e}")
            errors += 1
            continue

        data["files_changed"] = all_files[:30]
        new_raw = json.dumps(data, default=str)
        project = payload.get("project", "")
        text = _build_embedding_text(data, project)

        try:
            embedding = preprocessor.generate_embedding(text)
        except Exception as e:
            print(f"  embedding error {commit_hash[:8]}: {e}")
            errors += 1
            continue

        msg = data.get("message", "").replace("\n", " ")
        print(f"  {'[dry]' if dry_run else 'updating'} {commit_hash[:8]} {project}: {msg} | {len(all_files)} files")

        if not dry_run:
            client.upsert(
                collection_name=store.collection_name,
                points=[PointStruct(
                    id=point.id,
                    vector=embedding,
                    payload={**payload, "raw_content": new_raw},
                )],
            )
        updated += 1

    print(f"\nupdated={updated} skipped={skipped} errors={errors}")


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    if "--reembed" in sys.argv:
        if dry_run:
            print("dry run — no changes will be written")
        reembed(dry_run=dry_run)
    else:
        if dry_run:
            print("dry run — no changes will be written")
        backfill(dry_run=dry_run)
