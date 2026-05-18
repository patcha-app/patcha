"""RAG (Retrieval-Augmented Generation) system for contextual task analysis and enhanced summaries."""

import numpy as np
from typing import List, Dict, Any, Optional
from datetime import datetime, date, timedelta
from collections import Counter

from patcha.db.models import Event
from patcha.db.store import VectorStore
from patcha.process import EventPreprocessor
from patcha.api_client import PatchaLLMClient


class RAGSystem:
    """
    Retrieval-Augmented Generation system that enhances task analysis and summaries
    with relevant historical context, patterns, and similar activities.
    """

    def __init__(self, vector_store: VectorStore, preprocessor: EventPreprocessor):
        self.vector_store = vector_store
        self.preprocessor = preprocessor
        self.client = PatchaLLMClient()
        self.model_name = "gpt-4o-mini"

    def retrieve_context_for_task_analysis(
        self, activities: List[Event], context_limit: int = 10
    ) -> Dict[str, Any]:
        """
        Retrieve relevant context for task analysis including similar activities,
        related projects, and workflow patterns.

        Args:
            activities: Current activities being analyzed
            context_limit: Maximum number of context items to retrieve

        Returns:
            Dictionary with retrieved context including similar tasks, patterns, etc.
        """
        if not activities:
            return self._empty_context()

        # Generate query embedding from current activities
        query_embedding = self._generate_composite_embedding(activities)
        if not query_embedding:
            return self._empty_context()

        # Retrieve similar historical activities
        similar_activities = self._retrieve_similar_activities(
            query_embedding, activities, context_limit
        )

        # Extract patterns and insights
        context = {
            "similar_activities": similar_activities,
            "common_patterns": self._extract_workflow_patterns(similar_activities),
            "project_context": self._extract_project_context(
                activities, similar_activities
            ),
            "category_insights": self._extract_category_insights(
                activities, similar_activities
            ),
            "time_patterns": self._extract_temporal_patterns(
                activities, similar_activities
            ),
            "tool_usage": self._extract_tool_usage_patterns(
                activities, similar_activities
            ),
        }

        return context

    def retrieve_context_for_summary(
        self, target_date: date, activities: List[Event], context_limit: int = 15
    ) -> Dict[str, Any]:
        """
        Retrieve context for enhanced daily summaries including similar days,
        project progression, and productivity patterns.
        """
        if not activities:
            return self._empty_context()

        # Get activities from similar days (same day of week, similar workload)
        similar_days = self._retrieve_similar_workdays(target_date, context_limit // 3)

        # Get project progression context
        project_context = self._retrieve_project_progression(
            activities, context_limit // 3
        )

        # Get productivity and workflow trends
        productivity_context = self._retrieve_productivity_patterns(
            target_date, activities, context_limit // 3
        )

        return {
            "similar_days": similar_days,
            "project_progression": project_context,
            "productivity_patterns": productivity_context,
            "work_trends": self._analyze_work_trends(target_date, activities),
            "focus_areas": self._identify_focus_areas(activities),
        }

    def enhance_task_analysis_with_rag(
        self, activities: List[Event], base_analysis: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Use RAG to enhance basic task analysis with contextual insights.
        """
        # Retrieve relevant context
        context = self.retrieve_context_for_task_analysis(activities)

        if not context or all(not v for v in context.values()):
            return base_analysis

        # Generate enhanced analysis with retrieved context
        enhanced_analysis = self._generate_contextual_task_analysis(
            activities, base_analysis, context
        )

        return enhanced_analysis

    def enhance_daily_summary_with_rag(
        self, target_date: date, activities: List[Event], base_summary: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Enhance daily summary with historical context and patterns.
        """
        # Retrieve contextual information
        context = self.retrieve_context_for_summary(target_date, activities)

        if not context or all(not v for v in context.values()):
            return base_summary

        # Generate enhanced summary with context
        enhanced_summary = self._generate_contextual_daily_summary(
            target_date, activities, base_summary, context
        )

        return enhanced_summary

    def contextual_search(
        self, query: str, expand_context: bool = True, limit: int = 10
    ) -> List[Dict[str, Any]]:
        """
        Perform contextual search that expands queries with related concepts.
        """
        # Generate embedding for the query
        query_embedding = self.preprocessor.generate_embedding(query)
        if not query_embedding:
            return []

        # Perform initial search
        initial_results = self.vector_store.search_events(
            query_embedding, limit=limit * 2
        )

        if not expand_context:
            return initial_results[:limit]

        # Expand search with related concepts
        expanded_results = self._expand_search_with_context(
            query, initial_results, limit
        )

        return expanded_results

    def _generate_composite_embedding(
        self, activities: List[Event]
    ) -> Optional[List[float]]:
        """Generate a composite embedding representing the semantic content of activities."""
        embeddings = [a.embedding for a in activities if a.embedding]
        if not embeddings:
            return None

        # Use weighted average based on activity importance/duration
        weights = []
        for activity in activities:
            if activity.embedding:
                # Weight by content length and recency
                content_weight = (
                    len(activity.summary or activity.raw_content or "") / 100
                )
                time_weight = 1.0  # Could add time-based weighting
                weights.append(max(0.1, min(2.0, content_weight * time_weight)))

        if len(weights) != len(embeddings):
            weights = [1.0] * len(embeddings)

        # Compute weighted average
        weighted_embedding = np.average(embeddings, axis=0, weights=weights)
        return weighted_embedding.tolist()

    def _retrieve_similar_activities(
        self, query_embedding: List[float], current_activities: List[Event], limit: int
    ) -> List[Dict[str, Any]]:
        """Retrieve activities similar to the current ones, excluding current session."""
        # Get current activity timeframe to exclude
        current_start = min(a.timestamp for a in current_activities)
        current_end = max(a.timestamp for a in current_activities)
        buffer = timedelta(hours=1)  # Buffer to avoid same session

        # Search for similar activities
        all_results = self.vector_store.search_events(query_embedding, limit=limit * 3)

        # Filter out current activities and very recent ones
        filtered_results = []
        for result in all_results:
            activity_time = datetime.fromisoformat(result["payload"]["timestamp"])
            if not (current_start - buffer <= activity_time <= current_end + buffer):
                filtered_results.append(result)
                if len(filtered_results) >= limit:
                    break

        return filtered_results

    def _extract_workflow_patterns(
        self, similar_activities: List[Dict[str, Any]]
    ) -> List[str]:
        """Extract common workflow patterns from similar activities."""
        if not similar_activities:
            return []

        patterns = []

        # Analyze activity sequences
        activity_types = [a["payload"]["type"] for a in similar_activities]
        type_counter = Counter(activity_types)

        # Common activity types
        if type_counter:
            most_common = type_counter.most_common(3)
            patterns.append(
                f"Common activities: {', '.join([f'{t} ({c}x)' for t, c in most_common])}"
            )

        # Category patterns
        categories = [
            a["payload"].get("category")
            for a in similar_activities
            if a["payload"].get("category")
        ]
        if categories:
            cat_counter = Counter(categories)
            top_cats = cat_counter.most_common(2)
            patterns.append(
                f"Typical categories: {', '.join([f'{c} ({n}x)' for c, n in top_cats])}"
            )

        # Project patterns
        projects = [
            a["payload"].get("project")
            for a in similar_activities
            if a["payload"].get("project")
        ]
        if projects:
            proj_counter = Counter(projects)
            if len(proj_counter) <= 3:
                patterns.append(f"Related projects: {', '.join(proj_counter.keys())}")

        return patterns[:5]  # Limit to most relevant patterns

    def _extract_project_context(
        self, current_activities: List[Event], similar_activities: List[Dict[str, Any]]
    ) -> List[str]:
        """Extract project-related context and progression."""
        context = []

        # Current project focus
        current_projects = [a.project for a in current_activities if a.project]
        if current_projects:
            proj_counter = Counter(current_projects)
            main_project = proj_counter.most_common(1)[0][0]
            context.append(f"Primary project: {main_project}")

            # Find related project activities
            related_count = sum(
                1
                for a in similar_activities
                if a["payload"].get("project") == main_project
            )
            if related_count > 0:
                context.append(
                    f"Similar {main_project} activities found: {related_count}"
                )

        return context

    def _extract_category_insights(
        self, current_activities: List[Event], similar_activities: List[Dict[str, Any]]
    ) -> List[str]:
        """Extract category-based insights and patterns."""
        insights = []

        current_categories = [
            a.category.value for a in current_activities if a.category
        ]
        if not current_categories:
            return insights

        current_cat_counter = Counter(current_categories)
        similar_categories = [
            a["payload"].get("category")
            for a in similar_activities
            if a["payload"].get("category")
        ]

        if similar_categories:
            similar_cat_counter = Counter(similar_categories)

            # Find category trends
            for category in current_cat_counter:
                if category in similar_cat_counter:
                    ratio = similar_cat_counter[category] / len(similar_activities)
                    if ratio > 0.3:
                        insights.append(
                            f"{category} work is common in similar sessions ({ratio:.0%})"
                        )

        return insights[:3]

    def _extract_temporal_patterns(
        self, current_activities: List[Event], similar_activities: List[Dict[str, Any]]
    ) -> List[str]:
        """Extract time-based patterns and insights."""
        patterns = []

        # Current session duration
        if len(current_activities) > 1:
            duration = max(a.timestamp for a in current_activities) - min(
                a.timestamp for a in current_activities
            )
            duration_hours = duration.total_seconds() / 3600

            if duration_hours > 2:
                patterns.append(f"Extended work session: {duration_hours:.1f} hours")

        # Time of day patterns from similar activities
        times = []
        for activity in similar_activities:
            try:
                timestamp = datetime.fromisoformat(activity["payload"]["timestamp"])
                times.append(timestamp.hour)
            except Exception:
                continue

        if times:
            avg_hour = sum(times) / len(times)
            if 6 <= avg_hour <= 12:
                patterns.append("Similar activities typically occur in morning")
            elif 12 <= avg_hour <= 18:
                patterns.append("Similar activities typically occur in afternoon")
            elif 18 <= avg_hour <= 24:
                patterns.append("Similar activities typically occur in evening")

        return patterns

    def _extract_tool_usage_patterns(
        self, current_activities: List[Event], similar_activities: List[Dict[str, Any]]
    ) -> List[str]:
        """Extract tool and source usage patterns."""
        patterns = []

        # Current sources
        current_sources = [a.source for a in current_activities if a.source]
        source_counter = Counter(current_sources)

        if source_counter:
            top_source = source_counter.most_common(1)[0]
            patterns.append(
                f"Primary tool: {top_source[0]} ({top_source[1]} activities)"
            )

        # Similar activity sources
        similar_sources = [
            a["payload"].get("source")
            for a in similar_activities
            if a["payload"].get("source")
        ]

        if similar_sources:
            similar_source_counter = Counter(similar_sources)
            common_tools = [
                tool
                for tool in source_counter.keys()
                if tool in similar_source_counter and similar_source_counter[tool] > 2
            ]

            if common_tools:
                patterns.append(f"Consistent tools: {', '.join(common_tools)}")

        return patterns

    def _retrieve_similar_workdays(
        self, target_date: date, limit: int
    ) -> List[Dict[str, Any]]:
        """Retrieve activities from similar workdays (same day of week, similar patterns)."""
        similar_days = []

        for weeks_back in range(1, 5):  # Look back 4 weeks
            similar_date = target_date - timedelta(weeks=weeks_back)
            day_activities = self.vector_store.get_events_by_date(similar_date)

            if day_activities and len(day_activities) > 5:  # Filter for active days
                similar_days.append(
                    {
                        "date": similar_date.isoformat(),
                        "activities": day_activities,
                        "activity_count": len(day_activities),
                    }
                )

            if len(similar_days) >= limit:
                break

        return similar_days

    def _retrieve_project_progression(
        self, current_activities: List[Event], limit: int
    ) -> List[Dict[str, Any]]:
        """Retrieve project progression context."""
        progression = []

        # Get current projects
        current_projects = list(set(a.project for a in current_activities if a.project))

        for project in current_projects[:2]:  # Limit to top 2 projects
            # Search for project activities in last 30 days
            end_date = datetime.now().date()

            # This is simplified - ideally we'd have date range search in vector store
            project_activities = []
            for days_back in range(30):
                check_date = end_date - timedelta(days=days_back)
                day_activities = self.vector_store.get_events_by_date(check_date)

                project_day_activities = [
                    a for a in day_activities if a["payload"].get("project") == project
                ]

                if project_day_activities:
                    project_activities.extend(project_day_activities)

            if project_activities:
                progression.append(
                    {
                        "project": project,
                        "recent_activities": len(project_activities),
                        "timeline": "Last 30 days",
                    }
                )

        return progression

    def _retrieve_productivity_patterns(
        self, target_date: date, activities: List[Event], limit: int
    ) -> Dict[str, Any]:
        """Retrieve productivity and focus patterns."""
        patterns = {
            "activity_intensity": len(activities),
            "focus_score": self._calculate_focus_score(activities),
            "productivity_indicators": [],
        }

        # Compare with recent days
        recent_days = []
        for days_back in range(1, 8):  # Last week
            compare_date = target_date - timedelta(days=days_back)
            day_activities = self.vector_store.get_events_by_date(compare_date)
            if day_activities:
                recent_days.append(len(day_activities))

        if recent_days:
            avg_activity = sum(recent_days) / len(recent_days)
            if len(activities) > avg_activity * 1.2:
                patterns["productivity_indicators"].append(
                    "Above average activity level"
                )
            elif len(activities) < avg_activity * 0.8:
                patterns["productivity_indicators"].append(
                    "Below average activity level"
                )

        return patterns

    def _calculate_focus_score(self, activities: List[Event]) -> float:
        """Calculate a focus score based on activity diversity and patterns."""
        if not activities:
            return 0.0

        # Category diversity (lower diversity = higher focus)
        categories = [a.category.value for a in activities if a.category]
        if categories:
            unique_categories = len(set(categories))
            category_focus = max(0, 1.0 - (unique_categories - 1) * 0.2)
        else:
            category_focus = 0.5

        # Project consistency
        projects = [a.project for a in activities if a.project]
        if projects:
            unique_projects = len(set(projects))
            project_focus = max(0, 1.0 - (unique_projects - 1) * 0.3)
        else:
            project_focus = 0.5

        # Combine scores
        focus_score = (category_focus + project_focus) / 2
        return min(1.0, max(0.0, focus_score))

    def _analyze_work_trends(
        self, target_date: date, activities: List[Event]
    ) -> List[str]:
        """Analyze work trends and patterns."""
        trends = []

        # Category distribution
        categories = [a.category.value for a in activities if a.category]
        if categories:
            cat_counter = Counter(categories)
            dominant_category = cat_counter.most_common(1)[0]
            if dominant_category[1] > len(activities) * 0.4:
                trends.append(f"Heavy focus on {dominant_category[0]} work")

        # Time distribution
        hours = [a.timestamp.hour for a in activities]
        if hours:
            if max(hours) - min(hours) > 8:
                trends.append("Extended work day (8+ hours)")
            elif all(9 <= h <= 17 for h in hours):
                trends.append("Standard business hours")

        return trends

    def _identify_focus_areas(self, activities: List[Event]) -> List[str]:
        """Identify main focus areas from activities."""
        focus_areas = []

        # Keyword extraction from summaries
        summaries = [a.summary for a in activities if a.summary]
        if summaries:
            # Simple keyword extraction (could be enhanced with NLP)
            all_words = " ".join(summaries).lower().split()
            # Filter common words and find technical terms
            technical_words = [
                word
                for word in all_words
                if len(word) > 4
                and word not in ["and", "the", "for", "with", "this", "that"]
            ]

            if technical_words:
                word_counter = Counter(technical_words)
                top_keywords = word_counter.most_common(3)
                focus_areas.extend([word for word, count in top_keywords if count > 1])

        return focus_areas[:5]

    def _generate_contextual_task_analysis(
        self,
        activities: List[Event],
        base_analysis: Dict[str, Any],
        context: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Generate enhanced task analysis using retrieved context."""

        # Build context prompt
        context_prompt = self._build_task_context_prompt(context)

        # Prepare activity summary
        activity_content = []
        for i, activity in enumerate(activities, 1):
            summary = (
                activity.summary
                or activity.raw_content
                or f"{activity.type.value} activity"
            )
            timestamp = activity.timestamp.strftime("%H:%M")
            activity_content.append(f"{i}. [{timestamp}] {summary}")

        activity_text = "\n".join(activity_content)

        prompt = f"""
        Analyze this task with the following historical context:

        {context_prompt}

        CURRENT TASK ACTIVITIES:
        {activity_text}

        BASIC ANALYSIS:
        Title: {base_analysis.get("title", "Unknown Task")}
        Description: {base_analysis.get("description", "No description")}
        Accomplishments: {base_analysis.get("accomplishments", [])}

        Please provide an ENHANCED analysis that incorporates the historical context:

        1. ENHANCED_TITLE: A more specific title considering similar past work
        2. CONTEXTUAL_INSIGHTS: How this task relates to your historical patterns
        3. ENHANCED_ACCOMPLISHMENTS: More detailed accomplishments considering context
        4. PRODUCTIVITY_NOTES: Insights about work patterns and efficiency
        5. RECOMMENDATIONS: Suggestions based on similar past activities

        Format as:
        ENHANCED_TITLE: [title]
        CONTEXTUAL_INSIGHTS: [insights]
        ENHANCED_ACCOMPLISHMENTS:
        - [accomplishment 1]
        - [accomplishment 2]
        - [accomplishment 3]
        PRODUCTIVITY_NOTES: [notes]
        RECOMMENDATIONS: [recommendations]
        """

        try:
            response = self.client.chat.completions.create(
                model=self.model_name,
                messages=[{"role": "user", "content": prompt}],
            )
            enhanced_analysis = self._parse_contextual_analysis_response(
                response.choices[0].message.content, base_analysis
            )
            return enhanced_analysis
        except Exception as e:
            print(f"Error generating contextual task analysis: {e}")
            return base_analysis

    def _generate_contextual_daily_summary(
        self,
        target_date: date,
        activities: List[Event],
        base_summary: Dict[str, Any],
        context: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Generate enhanced daily summary with historical context."""

        context_prompt = self._build_summary_context_prompt(context)

        prompt = f"""
        Create an enhanced daily summary for {target_date.strftime("%B %d, %Y")} using this context:

        {context_prompt}

        TODAY'S STATISTICS:
        - Total Activities: {len(activities)}
        - Categories: {list(set(a.category.value for a in activities if a.category))}
        - Duration: {self._calculate_total_duration(activities)} hours

        BASE SUMMARY:
        {base_summary.get("overview", "No base summary available")}

        Please create an ENHANCED summary that includes:

        1. CONTEXTUAL_OVERVIEW: How today fits into recent work patterns
        2. PROGRESS_INSIGHTS: What progress was made on ongoing projects/goals
        3. PATTERN_ANALYSIS: Notable patterns compared to similar days
        4. PRODUCTIVITY_ASSESSMENT: Efficiency and focus insights
        5. FORWARD_LOOKING: Implications for upcoming work

        Keep the summary engaging and insightful, focusing on productivity patterns and progress.
        """

        try:
            response = self.client.chat.completions.create(
                model=self.model_name,
                messages=[{"role": "user", "content": prompt}],
            )
            enhanced_summary = base_summary.copy()
            enhanced_summary["contextual_overview"] = response.choices[
                0
            ].message.content
            enhanced_summary["rag_enhanced"] = True
            return enhanced_summary
        except Exception as e:
            print(f"Error generating contextual daily summary: {e}")
            return base_summary

    def _build_task_context_prompt(self, context: Dict[str, Any]) -> str:
        """Build context prompt for task analysis."""
        parts = []

        if context.get("common_patterns"):
            parts.append("HISTORICAL PATTERNS:")
            parts.extend([f"- {pattern}" for pattern in context["common_patterns"]])

        if context.get("project_context"):
            parts.append("\nPROJECT CONTEXT:")
            parts.extend([f"- {ctx}" for ctx in context["project_context"]])

        if context.get("category_insights"):
            parts.append("\nCATEGORY INSIGHTS:")
            parts.extend([f"- {insight}" for insight in context["category_insights"]])

        if context.get("time_patterns"):
            parts.append("\nTIME PATTERNS:")
            parts.extend([f"- {pattern}" for pattern in context["time_patterns"]])

        return "\n".join(parts) if parts else "No historical context available."

    def _build_summary_context_prompt(self, context: Dict[str, Any]) -> str:
        """Build context prompt for daily summary."""
        parts = []

        if context.get("similar_days"):
            parts.append("SIMILAR DAYS CONTEXT:")
            for day in context["similar_days"][:2]:
                parts.append(f"- {day['date']}: {day['activity_count']} activities")

        if context.get("project_progression"):
            parts.append("\nPROJECT PROGRESSION:")
            for proj in context["project_progression"]:
                parts.append(
                    f"- {proj['project']}: {proj['recent_activities']} activities in {proj['timeline']}"
                )

        if context.get("productivity_patterns"):
            patterns = context["productivity_patterns"]
            parts.append("\nPRODUCTIVITY CONTEXT:")
            parts.append(
                f"- Activity level: {patterns['activity_intensity']} activities"
            )
            parts.append(f"- Focus score: {patterns['focus_score']:.1f}/1.0")
            if patterns.get("productivity_indicators"):
                parts.extend(
                    [
                        f"- {indicator}"
                        for indicator in patterns["productivity_indicators"]
                    ]
                )

        return "\n".join(parts) if parts else "No historical context available."

    def _parse_contextual_analysis_response(
        self, response_text: str, base_analysis: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Parse the contextual analysis response."""
        enhanced = base_analysis.copy()

        lines = response_text.split("\n")
        current_section = None
        accomplishments = []

        for line in lines:
            line = line.strip()
            if line.startswith("ENHANCED_TITLE:"):
                enhanced["title"] = line.replace("ENHANCED_TITLE:", "").strip()
            elif line.startswith("CONTEXTUAL_INSIGHTS:"):
                enhanced["contextual_insights"] = line.replace(
                    "CONTEXTUAL_INSIGHTS:", ""
                ).strip()
            elif line.startswith("ENHANCED_ACCOMPLISHMENTS:"):
                current_section = "accomplishments"
            elif line.startswith("PRODUCTIVITY_NOTES:"):
                enhanced["productivity_notes"] = line.replace(
                    "PRODUCTIVITY_NOTES:", ""
                ).strip()
                current_section = None
            elif line.startswith("RECOMMENDATIONS:"):
                enhanced["recommendations"] = line.replace(
                    "RECOMMENDATIONS:", ""
                ).strip()
                current_section = None
            elif current_section == "accomplishments" and line.startswith("- "):
                accomplishments.append(line[2:])

        if accomplishments:
            enhanced["accomplishments"] = accomplishments

        enhanced["rag_enhanced"] = True
        return enhanced

    def _expand_search_with_context(
        self, query: str, initial_results: List[Dict[str, Any]], limit: int
    ) -> List[Dict[str, Any]]:
        """Expand search results with contextual information."""
        # For now, return filtered initial results
        # Could be enhanced with query expansion, related terms, etc.
        return initial_results[:limit]

    def _calculate_total_duration(self, activities: List[Event]) -> float:
        """Calculate total duration of activities in hours."""
        if not activities:
            return 0.0

        start_time = min(a.timestamp for a in activities)
        end_time = max(a.timestamp for a in activities)
        duration = (end_time - start_time).total_seconds() / 3600
        return round(duration, 1)

    def _empty_context(self) -> Dict[str, Any]:
        """Return empty context structure."""
        return {
            "similar_activities": [],
            "common_patterns": [],
            "project_context": [],
            "category_insights": [],
            "time_patterns": [],
            "tool_usage": [],
        }
