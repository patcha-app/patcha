import numpy as np
from typing import List, Dict, Any, Tuple
from collections import Counter
from datetime import datetime, timedelta
from patcha.api_client import PatchaLLMClient

from patcha.db.models import Event, EventType, Category
from patcha.db.store import VectorStore
from patcha.process import EventPreprocessor


class EnhancedCategorizer:
    def __init__(self, vector_store: VectorStore, preprocessor: EventPreprocessor):
        self.vector_store = vector_store
        self.preprocessor = preprocessor
        self.client = PatchaLLMClient()
        self.model_name = "gpt-4o-mini"

    def categorize_with_rag(
        self,
        event: Event,
        k: int = 5,
        confidence_threshold: float = 0.7,
        fallback_to_ai: bool = True,
    ) -> Tuple[Category, float, Dict[str, Any]]:
        if not event.embedding:
            summary = self.preprocessor.generate_summary(event)
            embedding_text = f"{event.type.value}: {summary}"
            event.embedding = self.preprocessor.generate_embedding(embedding_text)
            event.summary = summary

        if not event.embedding:
            category = self.preprocessor._categorize_fallback(event)
            return category, 0.0, {"method": "fallback", "reason": "no_embedding"}

        similar_events = self.vector_store.search_events(
            query_vector=event.embedding, limit=k
        )

        if not similar_events:
            if fallback_to_ai:
                category = self.preprocessor.categorize_event(
                    event, event.summary or ""
                )
                return (
                    category,
                    0.5,
                    {"method": "ai_fallback", "reason": "no_similar_events"},
                )
            else:
                category = self.preprocessor._categorize_fallback(event)
                return (
                    category,
                    0.0,
                    {"method": "fallback", "reason": "no_similar_events"},
                )

        categories = []
        scores = []
        similar_summaries = []

        for similar_event in similar_events:
            if similar_event["payload"].get("category"):
                categories.append(similar_event["payload"]["category"])
                scores.append(similar_event["score"])
                similar_summaries.append(similar_event["payload"].get("summary", ""))

        if not categories:
            if fallback_to_ai:
                category = self.preprocessor.categorize_event(
                    event, event.summary or ""
                )
                return (
                    category,
                    0.5,
                    {"method": "ai_fallback", "reason": "no_categories_in_similar"},
                )
            else:
                category = self.preprocessor._categorize_fallback(event)
                return (
                    category,
                    0.0,
                    {"method": "fallback", "reason": "no_categories_in_similar"},
                )

        category_scores = {}
        total_weight = 0

        for cat, score in zip(categories, scores):
            if cat not in category_scores:
                category_scores[cat] = 0
            category_scores[cat] += score
            total_weight += score

        if total_weight > 0:
            for cat in category_scores:
                category_scores[cat] /= total_weight

        best_category = max(category_scores.items(), key=lambda x: x[1])
        predicted_category = Category(best_category[0])
        confidence = best_category[1]

        category_counts = Counter(categories)
        most_common_count = category_counts.most_common(1)[0][1]
        agreement_boost = min(0.2, most_common_count / len(categories))
        confidence += agreement_boost

        if confidence >= confidence_threshold:
            return (
                predicted_category,
                confidence,
                {
                    "method": "rag",
                    "similar_events_count": len(similar_events),
                    "category_distribution": dict(category_counts),
                    "top_similar_scores": scores[:3],
                    "confidence_breakdown": {
                        "base_confidence": best_category[1],
                        "agreement_boost": agreement_boost,
                    },
                },
            )
        elif fallback_to_ai:
            ai_category = self._categorize_with_context(
                event, similar_summaries[:3], list(category_counts.keys())
            )
            return (
                ai_category,
                confidence + 0.3,
                {
                    "method": "rag_enhanced_ai",
                    "rag_confidence": confidence,
                    "similar_categories": list(category_counts.keys()),
                    "similar_summaries_used": len(similar_summaries[:3]),
                },
            )
        else:
            return (
                predicted_category,
                confidence,
                {
                    "method": "rag_low_confidence",
                    "similar_events_count": len(similar_events),
                    "category_distribution": dict(category_counts),
                    "warning": "Low confidence RAG result",
                },
            )

    def _categorize_with_context(
        self,
        event: Event,
        similar_summaries: List[str],
        suggested_categories: List[str],
    ) -> Category:
        context = ""
        if similar_summaries:
            context = "\n\nSimilar activities in the past were:\n" + "\n".join(
                [f"- {summary}" for summary in similar_summaries]
            )

        if suggested_categories:
            context += f"\n\nBased on similar activities, likely categories are: {', '.join(suggested_categories)}"

        prompt = f"""
        Classify this activity into ONE of these categories:
        - Coding: Writing, editing, or reviewing code
        - Debugging: Fixing bugs, troubleshooting issues
        - Research: Reading documentation, articles, learning new concepts
        - Learning: Educational activities, tutorials, skill development
        - Writing: Documentation, notes, content creation
        - Communication: Emails, messages, meetings
        - Other: Activities that don't fit other categories

        Activity type: {event.type.value}
        Summary: {event.summary}
        Content sample: {event.raw_content[:200]}
        {context}

        Respond with only the category name (e.g., "Coding", "Research", etc.).
        """

        try:
            response = self.client.chat.completions.create(
                model=self.model_name,
                messages=[{"role": "user", "content": prompt}],
            )
            category_text = response.choices[0].message.content.strip()

            for category in Category:
                if category.value.lower() in category_text.lower():
                    return category

            return self.preprocessor._categorize_fallback(event)

        except Exception as e:
            print(f"Error in context-enhanced categorization: {e}")
            return self.preprocessor._categorize_fallback(event)

    def batch_categorize_with_rag(
        self, events: List[Event], k: int = 5, confidence_threshold: float = 0.7
    ) -> List[Tuple[Event, Category, float, Dict[str, Any]]]:
        results = []

        for event in events:
            category, confidence, metadata = self.categorize_with_rag(
                event, k, confidence_threshold
            )
            event.category = category
            results.append((event, category, confidence, metadata))

        return results

    def analyze_categorization_performance(self, num_days: int = 7) -> Dict[str, Any]:
        end_date = datetime.now().date()
        start_date = end_date - timedelta(days=num_days)

        all_events = []
        current_date = start_date
        while current_date <= end_date:
            daily_events = self.vector_store.get_events_by_date(current_date)
            all_events.extend(daily_events)
            current_date += timedelta(days=1)

        if len(all_events) < 10:
            return {
                "error": "Not enough events for analysis",
                "events_found": len(all_events),
                "recommendation": "Need at least 10 events for meaningful analysis",
            }

        sample_events = all_events[: min(50, len(all_events))]

        rag_results = []
        ai_results = []
        agreement_count = 0

        for event_data in sample_events:
            payload = event_data["payload"]

            test_event = Event(
                timestamp=datetime.fromisoformat(payload["timestamp"]),
                type=EventType(payload["type"]),
                source=payload["source"],
                raw_content=payload.get("raw_content", ""),
                summary=payload.get("summary", ""),
                metadata=payload.get("metadata", {}),
            )

            if not test_event.summary:
                test_event.summary = self.preprocessor.generate_summary(test_event)

            embedding_text = f"{test_event.type.value}: {test_event.summary}"
            test_event.embedding = self.preprocessor.generate_embedding(embedding_text)

            rag_category, rag_confidence, rag_metadata = self.categorize_with_rag(
                test_event, fallback_to_ai=False
            )

            ai_category = self.preprocessor.categorize_event(
                test_event, test_event.summary
            )

            original_category = payload.get("category")

            rag_results.append(
                {
                    "category": rag_category.value,
                    "confidence": rag_confidence,
                    "method": rag_metadata.get("method"),
                    "original": original_category,
                }
            )

            ai_results.append(
                {"category": ai_category.value, "original": original_category}
            )

            if rag_category == ai_category:
                agreement_count += 1

        rag_methods = Counter([r["method"] for r in rag_results])
        rag_avg_confidence = np.mean([r["confidence"] for r in rag_results])

        rag_accuracy = 0
        ai_accuracy = 0
        events_with_original = 0

        for rag_result, ai_result in zip(rag_results, ai_results):
            if rag_result["original"]:
                events_with_original += 1
                if rag_result["category"] == rag_result["original"]:
                    rag_accuracy += 1
                if ai_result["category"] == ai_result["original"]:
                    ai_accuracy += 1

        return {
            "analysis_period": {
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "days": num_days,
            },
            "sample_size": len(sample_events),
            "rag_vs_ai_agreement": {
                "total_agreements": agreement_count,
                "agreement_rate": agreement_count / len(sample_events),
                "disagreements": len(sample_events) - agreement_count,
            },
            "rag_performance": {
                "avg_confidence": float(rag_avg_confidence),
                "method_distribution": dict(rag_methods),
                "accuracy_vs_original": rag_accuracy / events_with_original
                if events_with_original > 0
                else None,
            },
            "ai_performance": {
                "accuracy_vs_original": ai_accuracy / events_with_original
                if events_with_original > 0
                else None
            },
            "events_with_original_categories": events_with_original,
            "recommendations": self._generate_categorization_recommendations(
                rag_methods, rag_avg_confidence, agreement_count / len(sample_events)
            ),
        }

    def _generate_categorization_recommendations(
        self, rag_methods: Counter, rag_avg_confidence: float, agreement_rate: float
    ) -> List[str]:
        recommendations = []

        if rag_avg_confidence < 0.5:
            recommendations.append(
                "RAG confidence is low. Consider increasing the number of similar events (k parameter) or lowering confidence threshold."
            )

        if rag_methods.get("fallback", 0) > rag_methods.get("rag", 0):
            recommendations.append(
                "RAG is falling back to basic categorization frequently. You may need more historical data."
            )

        if agreement_rate < 0.7:
            recommendations.append(
                "RAG and AI categorization disagree frequently. This suggests room for improvement in both approaches."
            )
        elif agreement_rate > 0.9:
            recommendations.append(
                "High agreement between RAG and AI. RAG is working well and could be used as the primary method."
            )

        if rag_methods.get("rag", 0) > 10:
            recommendations.append(
                "RAG is successfully using historical patterns for categorization. Consider increasing confidence threshold."
            )

        return recommendations
