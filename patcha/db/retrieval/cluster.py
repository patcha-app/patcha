"""Activity clustering using cosine similarity and enhanced categorization."""

import numpy as np
from datetime import date, timedelta
from typing import List, Dict, Any
from sklearn.cluster import DBSCAN, KMeans, HDBSCAN
from sklearn.metrics.pairwise import cosine_similarity
from sklearn.preprocessing import StandardScaler
from collections import Counter, defaultdict

from patcha.db.models import Event, EventType
from patcha.db.store import VectorStore

_SOURCE_IDS = {
    EventType.GIT_COMMIT: 0,
    EventType.GIT_STASH: 0,
    EventType.GIT_STAGED: 0,
    EventType.BROWSER: 1,
    EventType.TERMINAL: 2,
    EventType.WINDOW: 3,
    EventType.SCREEN: 4,
    EventType.NOTE: 5,
}


def cluster_raw(events: List[Event], min_cluster_size: int = 3) -> List[Dict[str, Any]]:
    """Cluster events by time + source without embeddings (observability layer).

    Features: normalized timestamp, time gap to previous event, source type.
    Returns clusters sorted by start time, including a noise bucket.
    """
    if not events:
        return []

    sorted_events = sorted(events, key=lambda e: e.timestamp)

    timestamps = np.array(
        [e.timestamp.hour * 60 + e.timestamp.minute for e in sorted_events], dtype=float
    )

    gaps = np.zeros(len(sorted_events))
    for i in range(1, len(sorted_events)):
        delta = (
            sorted_events[i].timestamp - sorted_events[i - 1].timestamp
        ).total_seconds() / 60
        gaps[i] = min(delta, 120)

    source_ids = np.array(
        [_SOURCE_IDS.get(e.type, 5) for e in sorted_events], dtype=float
    )

    features = np.column_stack([timestamps, gaps, source_ids])
    features = StandardScaler().fit_transform(features)

    if len(sorted_events) < min_cluster_size:
        labels = np.zeros(len(sorted_events), dtype=int)
    else:
        labels = HDBSCAN(
            min_cluster_size=min_cluster_size,
            min_samples=2,
            metric="euclidean",
            copy=True,
        ).fit_predict(features)

    buckets: Dict[int, List[Event]] = defaultdict(list)
    for event, label in zip(sorted_events, labels):
        buckets[int(label)].append(event)

    results = []
    for label, cluster_events in sorted(buckets.items()):
        sources = Counter(e.type.value for e in cluster_events)
        apps = Counter(
            e.metadata.get("app_name") or e.source
            for e in cluster_events
            if e.metadata.get("app_name") or e.source
        )
        results.append(
            {
                "cluster_id": label,
                "noise": label == -1,
                "size": len(cluster_events),
                "start_time": cluster_events[0].timestamp.isoformat(),
                "end_time": cluster_events[-1].timestamp.isoformat(),
                "dominant_source": sources.most_common(1)[0][0] if sources else None,
                "dominant_app": apps.most_common(1)[0][0] if apps else None,
                "source_breakdown": dict(sources),
                "events": cluster_events,
            }
        )

    results.sort(key=lambda c: c["start_time"])
    return results


class ActivityClusterer:
    def __init__(self, vector_store: VectorStore):
        self.vector_store = vector_store

    def cluster_activities_by_similarity(
        self,
        target_date: date,
        min_samples: int = 2,
        eps: float = 0.3,
        method: str = "hdbscan",
        min_cluster_size: int = 3,
    ) -> Dict[str, Any]:
        """
        Cluster activities by cosine similarity of their embeddings.

        Args:
            target_date: Date to cluster activities for
            min_samples: Minimum samples for DBSCAN clusters
            eps: Maximum distance for DBSCAN clustering (1-cosine_similarity)
            method: Clustering method ('dbscan', 'hdbscan', or 'kmeans')
            min_cluster_size: Minimum cluster size for HDBSCAN
        """
        # Get events for the target date
        events_data = self.vector_store.get_events_by_date(target_date)

        if len(events_data) < 2:
            return {
                "date": target_date.isoformat(),
                "clusters": [],
                "total_events": len(events_data),
                "method": method,
                "message": "Not enough events to cluster",
            }

        # Extract embeddings and metadata
        embeddings = []
        event_details = []

        for event_data in events_data:
            # Use the embedding that's already in the event_data
            if "vector" in event_data and event_data["vector"]:
                embeddings.append(event_data["vector"])
                event_details.append(event_data["payload"])

        if len(embeddings) < 2:
            return {
                "date": target_date.isoformat(),
                "clusters": [],
                "total_events": len(embeddings),
                "method": method,
                "message": "Not enough valid embeddings to cluster",
            }

        embeddings_array = np.array(embeddings)

        # Perform clustering
        if method == "dbscan":
            # Use cosine distance for DBSCAN
            similarity_matrix = cosine_similarity(embeddings_array)
            distance_matrix = 1 - similarity_matrix

            clusterer = DBSCAN(eps=eps, min_samples=min_samples, metric="precomputed")
            cluster_labels = clusterer.fit_predict(distance_matrix)

        elif method == "hdbscan":
            # HDBSCAN with cosine metric - much better for variable density clusters
            if len(embeddings_array) < min_cluster_size:
                # Not enough events for HDBSCAN, create single cluster or noise
                cluster_labels = (
                    [-1] * len(embeddings_array)
                    if len(embeddings_array) < 2
                    else [0] * len(embeddings_array)
                )
            else:
                clusterer = HDBSCAN(
                    min_cluster_size=min_cluster_size,
                    min_samples=min_samples,
                    metric="cosine",
                    cluster_selection_epsilon=eps if eps < 1.0 else None,
                    cluster_selection_method="eom",  # Excess of Mass for better hierarchy
                )
                cluster_labels = clusterer.fit_predict(embeddings_array)

        elif method == "kmeans":
            # Determine optimal number of clusters (max 5)
            n_clusters = min(max(2, len(embeddings) // 3), 5)
            clusterer = KMeans(n_clusters=n_clusters, random_state=42, n_init=10)
            cluster_labels = clusterer.fit_predict(embeddings_array)

        else:
            raise ValueError(f"Unsupported clustering method: {method}")

        # Group events by cluster
        clusters = defaultdict(list)
        for idx, label in enumerate(cluster_labels):
            clusters[label].append(
                {"event": event_details[idx], "embedding_index": idx}
            )

        # Generate cluster summaries
        cluster_results = []
        for cluster_id, cluster_events in clusters.items():
            if cluster_id == -1:  # DBSCAN noise
                cluster_name = "Unclustered Activities"
                description = "Activities that don't fit into clear patterns"
            else:
                cluster_name = self._generate_cluster_name(cluster_events)
                description = self._generate_cluster_description(cluster_events)

            # Calculate cluster statistics
            categories = [event["event"].get("category") for event in cluster_events]
            category_counts = Counter(filter(None, categories))

            types = [event["event"].get("type") for event in cluster_events]
            type_counts = Counter(types)

            cluster_results.append(
                {
                    "cluster_id": int(cluster_id) if cluster_id != -1 else -1,
                    "name": cluster_name,
                    "description": description,
                    "size": len(cluster_events),
                    "events": [event["event"] for event in cluster_events],
                    "dominant_category": category_counts.most_common(1)[0][0]
                    if category_counts
                    else None,
                    "category_distribution": dict(category_counts),
                    "type_distribution": dict(type_counts),
                    "avg_similarity": self._calculate_avg_cluster_similarity(
                        [event["embedding_index"] for event in cluster_events],
                        embeddings_array,
                    ),
                }
            )

        # Sort clusters by size
        cluster_results.sort(key=lambda x: x["size"], reverse=True)

        return {
            "date": target_date.isoformat(),
            "clusters": cluster_results,
            "total_events": len(embeddings),
            "num_clusters": len([c for c in cluster_results if c["cluster_id"] != -1]),
            "num_noise": len(clusters.get(-1, [])),
            "method": method,
            "parameters": {"eps": eps, "min_samples": min_samples}
            if method == "dbscan"
            else {"min_cluster_size": min_cluster_size, "min_samples": min_samples}
            if method == "hdbscan"
            else {
                "n_clusters": len(set(cluster_labels))
                - (1 if -1 in cluster_labels else 0)
            },
        }

    def _generate_cluster_name(self, cluster_events: List[Dict[str, Any]]) -> str:
        """Generate a meaningful name for a cluster based on its events."""
        summaries = [event["event"].get("summary", "") for event in cluster_events]
        categories = [event["event"].get("category") for event in cluster_events]
        types = [event["event"].get("type") for event in cluster_events]

        # Most common category
        category_counts = Counter(filter(None, categories))
        dominant_category = (
            category_counts.most_common(1)[0][0] if category_counts else "Mixed"
        )

        # Most common type
        type_counts = Counter(types)
        dominant_type = type_counts.most_common(1)[0][0] if type_counts else "mixed"

        # Try to find common keywords in summaries
        all_words = []
        for summary in summaries:
            if summary:
                words = summary.lower().split()
                # Filter out common words
                filtered_words = [
                    w
                    for w in words
                    if len(w) > 3
                    and w
                    not in [
                        "this",
                        "that",
                        "with",
                        "from",
                        "they",
                        "were",
                        "been",
                        "have",
                    ]
                ]
                all_words.extend(filtered_words)

        word_counts = Counter(all_words)
        common_words = [word for word, count in word_counts.most_common(3) if count > 1]

        if common_words:
            keywords = " + ".join(common_words[:2])
            return f"{dominant_category}: {keywords}"
        else:
            return f"{dominant_category} Activities ({dominant_type})"

    def _generate_cluster_description(
        self, cluster_events: List[Dict[str, Any]]
    ) -> str:
        """Generate a description for a cluster."""
        size = len(cluster_events)
        categories = [event["event"].get("category") for event in cluster_events]
        category_counts = Counter(filter(None, categories))

        if len(category_counts) == 1:
            category = list(category_counts.keys())[0]
            return f"Group of {size} similar {category.lower()} activities"
        else:
            main_categories = [cat for cat, count in category_counts.most_common(2)]
            return f"Group of {size} activities, mainly {' and '.join(main_categories).lower()}"

    def _calculate_avg_cluster_similarity(
        self, embedding_indices: List[int], embeddings_array: np.ndarray
    ) -> float:
        """Calculate average cosine similarity within a cluster."""
        if len(embedding_indices) < 2:
            return 1.0

        cluster_embeddings = embeddings_array[embedding_indices]
        similarity_matrix = cosine_similarity(cluster_embeddings)

        # Get upper triangle of similarity matrix (excluding diagonal)
        upper_triangle = np.triu(similarity_matrix, k=1)
        similarities = upper_triangle[upper_triangle > 0]

        return float(np.mean(similarities)) if len(similarities) > 0 else 0.0

    def find_activity_patterns(
        self, start_date: date, end_date: date, min_pattern_size: int = 3
    ) -> Dict[str, Any]:
        """
        Find recurring activity patterns across multiple days.
        """
        date_range = []
        current_date = start_date
        while current_date <= end_date:
            date_range.append(current_date)
            current_date += timedelta(days=1)

        # Cluster each day's activities
        daily_clusters = {}
        all_cluster_signatures = []

        for target_date in date_range:
            daily_result = self.cluster_activities_by_similarity(target_date)
            daily_clusters[target_date.isoformat()] = daily_result

            # Create signatures for each cluster
            for cluster in daily_result["clusters"]:
                if cluster["cluster_id"] != -1:  # Skip noise
                    signature = {
                        "date": target_date.isoformat(),
                        "dominant_category": cluster["dominant_category"],
                        "type_distribution": cluster["type_distribution"],
                        "size": cluster["size"],
                        "name": cluster["name"],
                    }
                    all_cluster_signatures.append(signature)

        # Find patterns by grouping similar cluster signatures
        patterns = self._find_recurring_patterns(
            all_cluster_signatures, min_pattern_size
        )

        return {
            "date_range": {
                "start": start_date.isoformat(),
                "end": end_date.isoformat(),
            },
            "daily_clusters": daily_clusters,
            "recurring_patterns": patterns,
            "total_days_analyzed": len(date_range),
        }

    def _find_recurring_patterns(
        self, cluster_signatures: List[Dict[str, Any]], min_pattern_size: int
    ) -> List[Dict[str, Any]]:
        """Find recurring patterns in cluster signatures."""
        # Group by dominant category and similar characteristics
        pattern_groups = defaultdict(list)

        for signature in cluster_signatures:
            # Create a pattern key based on category and main activity type
            main_type = max(signature["type_distribution"].items(), key=lambda x: x[1])[
                0
            ]
            pattern_key = f"{signature['dominant_category']}_{main_type}"
            pattern_groups[pattern_key].append(signature)

        patterns = []
        for pattern_key, signatures in pattern_groups.items():
            if len(signatures) >= min_pattern_size:
                # Calculate pattern statistics
                sizes = [sig["size"] for sig in signatures]
                dates = [sig["date"] for sig in signatures]

                patterns.append(
                    {
                        "pattern_id": pattern_key,
                        "frequency": len(signatures),
                        "dates": dates,
                        "avg_cluster_size": np.mean(sizes),
                        "dominant_category": signatures[0]["dominant_category"],
                        "description": f"Recurring {signatures[0]['dominant_category'].lower()} pattern appearing {len(signatures)} times",
                        "example_names": [sig["name"] for sig in signatures[:3]],
                    }
                )

        return sorted(patterns, key=lambda x: x["frequency"], reverse=True)
