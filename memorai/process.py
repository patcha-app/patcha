"""Event preprocessing with summarization and embeddings."""

import asyncio
import logging
import re
from typing import List, Optional, Tuple, Union
from urllib.parse import urlparse

from openai import OpenAI

from memorai.config import config
from memorai.db.models import Category, Event, EventType

log = logging.getLogger(__name__)


class EventPreprocessor:
    def __init__(self):
        self.client = OpenAI(api_key=config.openai_api_key)
        self.model_name = "gpt-4o-mini"
        self.embedding_model = "text-embedding-3-small"

        # Enhanced URL pattern mappings for better topic detection (pre-compiled)
        self.url_patterns = [
            # Documentation patterns
            (re.compile(r'docs?\.', re.IGNORECASE), 'documentation'),
            (re.compile(r'/docs?/', re.IGNORECASE), 'documentation'),
            (re.compile(r'/api/', re.IGNORECASE), 'API documentation'),
            (re.compile(r'/reference/', re.IGNORECASE), 'reference documentation'),
            (re.compile(r'/guide/', re.IGNORECASE), 'tutorial guide'),
            (re.compile(r'/tutorial/', re.IGNORECASE), 'tutorial'),
            (re.compile(r'/getting-started/', re.IGNORECASE), 'getting started guide'),

            # Code repositories
            (re.compile(r'github\.com/.+/issues/', re.IGNORECASE), 'GitHub issue research'),
            (re.compile(r'github\.com/.+/pull/', re.IGNORECASE), 'GitHub pull request review'),
            (re.compile(r'github\.com/.+/wiki/', re.IGNORECASE), 'GitHub wiki research'),
            (re.compile(r'github\.com/.+/blob/', re.IGNORECASE), 'code file examination'),
            (re.compile(r'github\.com/.+/tree/', re.IGNORECASE), 'repository exploration'),

            # Q&A and forums
            (re.compile(r'stackoverflow\.com/questions/', re.IGNORECASE), 'programming problem solving'),
            (re.compile(r'stackexchange\.com', re.IGNORECASE), 'technical Q&A research'),
            (re.compile(r'reddit\.com/r/', re.IGNORECASE), 'community discussion research'),
            (re.compile(r'discourse\.|forum\.', re.IGNORECASE), 'forum discussion'),

            # Learning platforms
            (re.compile(r'youtube\.com/watch', re.IGNORECASE), 'video learning'),
            (re.compile(r'coursera\.org', re.IGNORECASE), 'online course'),
            (re.compile(r'udemy\.com', re.IGNORECASE), 'online course'),
            (re.compile(r'pluralsight\.com', re.IGNORECASE), 'technical training'),
            (re.compile(r'leetcode\.com', re.IGNORECASE), 'algorithm practice'),
            (re.compile(r'hackerrank\.com', re.IGNORECASE), 'coding challenge'),

            # News and articles
            (re.compile(r'medium\.com', re.IGNORECASE), 'technical article reading'),
            (re.compile(r'dev\.to', re.IGNORECASE), 'developer blog reading'),
            (re.compile(r'hackernews', re.IGNORECASE), 'tech news research'),
            (re.compile(r'techcrunch\.com', re.IGNORECASE), 'tech news research'),

            # Tools and platforms
            (re.compile(r'npmjs\.com', re.IGNORECASE), 'package research'),
            (re.compile(r'pypi\.org', re.IGNORECASE), 'Python package research'),
            (re.compile(r'crates\.io', re.IGNORECASE), 'Rust crate research'),
            (re.compile(r'docker\.com', re.IGNORECASE), 'containerization research'),
        ]

        # Pre-compiled question patterns for keyword extraction
        self.question_patterns = [
            re.compile(r'how to ([^?]+)', re.IGNORECASE),
            re.compile(r'what is ([^?]+)', re.IGNORECASE),
            re.compile(r'why does ([^?]+)', re.IGNORECASE),
            re.compile(r'when to ([^?]+)', re.IGNORECASE),
            re.compile(r'([^\s]+) tutorial', re.IGNORECASE),
            re.compile(r'([^\s]+) guide', re.IGNORECASE),
            re.compile(r'understanding ([^\s]+)', re.IGNORECASE),
        ]
        self._youtube_re = re.compile(r'youtube:?\s*', re.IGNORECASE)
        self._non_alnum_re = re.compile(r'[^a-zA-Z0-9]+')

    def generate_summary(self, event: Event) -> str:
        # Add project context to prompts if available
        project_context = f" in the '{event.project}' project" if event.project else ""

        # For browser events, enhance with topic detection
        if event.type == EventType.BROWSER:
            return self._generate_browser_summary(event, project_context)

        prompt_templates = {
            EventType.GIT_COMMIT: f"Summarize the SPECIFIC TASK or GOAL accomplished in this commit{project_context}. Be concrete about what feature, functionality, or problem was addressed. Include relevant domain/topic keywords. Format: 'Implemented/Fixed/Added [specific thing] for [specific purpose/problem]': {{content}}",

            EventType.GIT_STASH: f"Summarize the SPECIFIC WORK IN PROGRESS that was stashed{project_context}. Describe what particular task, feature, or problem was being worked on: {{content}}",

            EventType.GIT_STAGED: f"Summarize what the developer is actively working on{project_context} based on the files they staged for commit. Be concrete about the feature, fix, or change in progress. Format: 'Working on [specific thing] — staged [files]': {{content}}",

            EventType.TERMINAL: f"Summarize the SPECIFIC TASK being accomplished{project_context}. Be concrete about what was being built, tested, deployed, or configured. Include relevant technical context. Format: 'Executed [specific task] to [specific goal]': {{content}}",

            EventType.NOTE: f"Summarize the SPECIFIC CONTENT and PURPOSE{project_context}. Be concrete about what topic, problem, or knowledge was documented. Include relevant domain keywords: {{content}}",

            EventType.WINDOW: f"Summarize what the user was working on{project_context} based on the application and window title. Be concrete about the task or context. Format: 'Working on [specific thing] in [app]': {{content}}",

            EventType.SCREEN: f"Summarize what the user is reading or working on{project_context} based on this on-screen text. Be concrete about the topic, task, or content. Format: 'Reading/Editing [specific content] in [app]': {{content}}"
        }

        template = prompt_templates.get(event.type, "Summarize this activity in 1-2 sentences: {content}")
        content = event.raw_content[:2000]  # Limit content length

        try:
            response = self.client.chat.completions.create(
                model=self.model_name,
                messages=[{"role": "user", "content": template.format(content=content)}],
            )
            summary = response.choices[0].message.content.strip()
            log.debug("summary generated for %s: %s", event.type, summary[:80])
            return summary
        except Exception as e:
            log.error("error generating summary for event %s: %s", event.type, e)
            log.debug("falling back to template summary for %s", event.type)
            return self._generate_fallback_summary(event)

    def _generate_fallback_summary(self, event: Event) -> str:
        # Try to extract more context from metadata or content
        project_part = f" in {event.project}" if event.project else ""

        # Extract domain/URL for browser activities
        domain = ""
        if event.type == EventType.BROWSER and event.metadata:
            domain_name = event.metadata.get('domain', '')
            if domain_name:
                # Map common domains to more specific activities
                domain_mappings = {
                    'stackoverflow.com': 'programming problem-solving',
                    'github.com': 'code repository exploration',
                    'docs.python.org': 'Python documentation research',
                    'leetcode.com': 'algorithm problem solving',
                    'wikipedia.org': 'knowledge research',
                    'youtube.com': 'video learning',
                    'medium.com': 'technical article reading',
                    'reddit.com': 'community discussion research'
                }
                for site, activity in domain_mappings.items():
                    if site in domain_name:
                        domain = f" - {activity}"
                        break
                else:
                    domain = f" on {domain_name}"

        window_summary = ""
        if event.type == EventType.WINDOW and event.metadata:
            app = event.metadata.get("app_name", "")
            title = event.metadata.get("window_title", "")
            duration = event.metadata.get("duration_seconds", 0)
            label = title if title else app
            mins = duration // 60
            window_summary = f"Used {app}: {label}" + (f" for {mins}m" if mins else "")

        fallback_summaries = {
            EventType.GIT_COMMIT: f"Implemented code changes{project_part}",
            EventType.GIT_STASH: f"Saved work in progress{project_part}",
            EventType.GIT_STAGED: f"Staged changes for commit{project_part}",
            EventType.BROWSER: f"Conducted research{domain}{project_part}",
            EventType.TERMINAL: f"Executed development task{project_part}",
            EventType.NOTE: f"Created documentation{project_part}",
            EventType.WINDOW: window_summary or f"Active in application{project_part}",
            EventType.SCREEN: f"Viewed on-screen content{project_part}",
        }
        return fallback_summaries.get(event.type, f"Completed work activity{project_part}")

    def generate_embedding(self, text: str) -> Optional[List[float]]:
        log.debug("generating embedding for text (len=%d)", len(text))
        try:
            response = self.client.embeddings.create(
                model=self.embedding_model,
                input=text,
            )
            embedding = response.data[0].embedding
            log.debug("embedding generated: dim=%d", len(embedding))
            return embedding
        except Exception as e:
            log.error("error generating embedding: %s", e)
            return None

    def categorize_event(self, event: Event, summary: str) -> Category:
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
        Summary: {summary}
        Content sample: {event.raw_content[:200]}

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
                    log.debug("categorized %s → %s", event.type, category.value)
                    return category

            log.debug("unrecognized category response '%s', using fallback for %s", category_text, event.type)
            return self._categorize_fallback(event)

        except Exception as e:
            log.error("error categorizing event %s: %s", event.type, e)
            log.debug("falling back to default category for %s", event.type)
            return self._categorize_fallback(event)

    def _categorize_fallback(self, event: Event) -> Category:
        fallback_categories = {
            EventType.GIT_COMMIT: Category.CODING,
            EventType.GIT_STASH: Category.CODING,
            EventType.BROWSER: Category.RESEARCH,
            EventType.TERMINAL: Category.CODING,
            EventType.NOTE: Category.WRITING,
            EventType.SCREEN: Category.OTHER,
        }
        return fallback_categories.get(event.type, Category.OTHER)

    def process_event(self, event: Event) -> Event:
        log.debug("processing event type=%s source=%s", event.type, event.source)

        summary = self.generate_summary(event)
        event.summary = summary

        category = self.categorize_event(event, summary)
        event.category = category

        embedding_text = f"Task: {summary}"
        if event.project:
            embedding_text += f" in {event.project} project"

        embedding = self.generate_embedding(embedding_text)
        event.embedding = embedding

        log.debug("event processed: type=%s category=%s has_embedding=%s", event.type, category, embedding is not None)
        return event

    def process_events(self, events: List[Event]) -> List[Event]:
        processed_events = []

        for event in events:
            try:
                processed_event = self.process_event(event)
                processed_events.append(processed_event)
            except Exception as e:
                log.error("error processing event: %s", e)
                processed_events.append(event)

        return processed_events

    def process_event_with_rag(self, event: Event, enhanced_categorizer=None) -> Event:
        """Process event with optional RAG-based categorization."""
        # Generate summary
        summary = self.generate_summary(event)
        event.summary = summary

        # Generate embedding first, focused on task content
        embedding_text = f"Task: {summary}"
        if event.project:
            embedding_text += f" in {event.project} project"

        embedding = self.generate_embedding(embedding_text)
        event.embedding = embedding

        # Use enhanced categorization if available
        if enhanced_categorizer:
            try:
                category, confidence, metadata = enhanced_categorizer.categorize_with_rag(event)
                event.category = category
                # Store categorization metadata
                if not event.metadata:
                    event.metadata = {}
                event.metadata['categorization'] = {
                    'confidence': confidence,
                    'method': metadata.get('method'),
                    'details': metadata
                }
            except Exception as e:
                log.error("error with enhanced categorization: %s — falling back to standard", e)
                event.category = self.categorize_event(event, summary)
        else:
            # Fallback to standard categorization
            event.category = self.categorize_event(event, summary)

        return event

    def process_events_with_rag(
        self,
        events: List[Event],
        enhanced_categorizer=None
    ) -> List[Event]:
        """Process events with enhanced categorization."""
        processed_events = []

        for event in events:
            try:
                processed_event = self.process_event_with_rag(event, enhanced_categorizer)
                processed_events.append(processed_event)
            except Exception as e:
                log.error("error processing event with RAG: %s", e)
                try:
                    processed_event = self.process_event(event)
                    processed_events.append(processed_event)
                except Exception as e2:
                    log.error("error with fallback processing: %s", e2)
                    processed_events.append(event)

        return processed_events

    async def process_events_async(self, events: List[Event]) -> List[Event]:
        """Process events asynchronously for better performance."""
        tasks = []
        for event in events:
            task = asyncio.create_task(self._process_event_async(event))
            tasks.append(task)

        processed_events = await asyncio.gather(*tasks, return_exceptions=True)

        # Filter out exceptions and return valid events
        valid_events = [
            event for event in processed_events
            if isinstance(event, Event)
        ]

        return valid_events

    async def _process_event_async(self, event: Event) -> Event:
        """Async wrapper for processing a single event."""
        return self.process_event(event)

    def process_events_with_tasks(
        self,
        events: List[Event],
        task_identifier=None,
        task_store=None
    ) -> Tuple[List[Event], List]:
        """
        Process events and identify tasks from them.

        Returns:
            Tuple of (processed_events, identified_tasks)
        """

        # First process events normally
        processed_events = self.process_events(events)

        # Then identify tasks if task_identifier is provided
        identified_tasks = []
        if task_identifier and processed_events:
            identified_tasks = task_identifier.identify_tasks_from_activities(processed_events)

            # Store tasks if task_store is provided
            if task_store:
                for task in identified_tasks:
                    task_store.store_task(task)

                    # Update events with task IDs
                    for activity_id in task.activities:
                        # Find the corresponding event and update it
                        for event in processed_events:
                            event_id = f"{event.type.value}_{event.timestamp.isoformat()}"
                            if event_id == activity_id:
                                event.task_id = task.id
                                event.task_confidence = task.confidence_score
                                event.task_assignment_method = "clustering"
                                break

        return processed_events, identified_tasks

    def batch_process_with_tasks(
        self,
        events: List[Event],
        batch_size: int = 50,
        task_identifier=None,
        task_store=None
    ) -> Tuple[List[Event], List]:
        """
        Process events in batches and identify tasks.
        Useful for processing large amounts of historical data.
        """
        all_processed_events = []
        all_identified_tasks = []

        # Process events in batches
        for i in range(0, len(events), batch_size):
            batch = events[i:i + batch_size]
            processed_events, identified_tasks = self.process_events_with_tasks(
                batch, task_identifier, task_store
            )

            all_processed_events.extend(processed_events)
            all_identified_tasks.extend(identified_tasks)

            log.debug("processed batch %d/%d", i // batch_size + 1, (len(events) + batch_size - 1) // batch_size)

        return all_processed_events, all_identified_tasks

    def _generate_browser_summary(self, event: Union[Event, dict], project_context: str) -> str:
        """Enhanced browser activity summarization using page title and URL patterns."""
        try:
            # Extract metadata from event - handle both Event objects and dictionaries
            if isinstance(event, dict):
                metadata = event.get('metadata', {})
            else:
                metadata = getattr(event, 'metadata', None) or {}
            title = metadata.get('title', '')
            url = metadata.get('url', '')
            domain = metadata.get('domain', '')

            # Parse URL for additional context
            topic_context = self._extract_topic_from_url(url)
            title_keywords = self._extract_keywords_from_title(title)

            # Build enhanced context for summarization
            enhanced_content = self._build_browser_context(title, url, domain, topic_context, title_keywords)

            # Create topic-focused prompt with emphasis on core topic identification
            prompt = f"""Analyze this browser activity and identify the CORE TOPIC being researched{project_context}.

CRITICAL: Focus on the main subject/product/technology being investigated, NOT the platform or action.

Examples of GOOD topic identification:
- "Meta AR glasses development and features" (NOT "YouTube video learning")
- "iPhone 17 Pro Max specifications and reviews" (NOT "Product review research")
- "Qdrant vector database administration" (NOT "Database management")

Context: {enhanced_content}

Key guidelines:
1. Identify the PRIMARY subject/product/company/technology
2. Include specific model numbers, versions, or event names when mentioned
3. Group related concepts (e.g., "Meta Connect 2025 AR glasses" covers both the event and product)
4. Avoid generic platform terms (YouTube, Google, etc.) unless they're the actual topic
5. Format: '[Company/Product] [Specific Topic/Feature/Event]' when possible

Original content sample: {event.get('content', '') if isinstance(event, dict) else getattr(event, 'raw_content', '')[:500]}

Respond with a focused topic summary:"""

            response = self.client.chat.completions.create(
                model=self.model_name,
                messages=[{"role": "user", "content": prompt}],
            )
            return response.choices[0].message.content.strip()

        except Exception as e:
            log.error("error generating enhanced browser summary: %s", e)
            return self._generate_enhanced_browser_fallback(event, project_context)

    def _generate_enhanced_browser_summary(self, event: Union[Event, dict], project_context: str) -> str:
        """Enhanced browser activity summarization using page title and URL patterns."""
        try:
            # Extract metadata from event - handle both Event objects and dictionaries
            if isinstance(event, dict):
                metadata = event.get('metadata', {})
            else:
                metadata = getattr(event, 'metadata', None) or {}
            title = metadata.get('title', '')
            url = metadata.get('url', '')
            domain = metadata.get('domain', '')

            # Parse URL for additional context
            topic_context = self._extract_topic_from_url(url)
            title_keywords = self._extract_keywords_from_title(title)

            # Build enhanced context for summarization
            enhanced_content = self._build_browser_context(title, url, domain, topic_context, title_keywords)

            prompt = f"""Analyze this browser activity and identify the CORE TOPIC being researched{project_context}.

Context: {enhanced_content}

Original content sample: {event.get('content', '') if isinstance(event, dict) else getattr(event, 'raw_content', '')[:500]}

Respond with a focused topic summary:"""

            response = self.client.chat.completions.create(
                model=self.model_name,
                messages=[{"role": "user", "content": prompt}],
            )
            return response.choices[0].message.content.strip()

        except Exception as e:
            log.error("error generating enhanced browser summary: %s", e)
            return self._generate_enhanced_browser_fallback(event, project_context)

    def _extract_topic_from_url(self, url: str) -> str:
        """Extract topic context from URL patterns."""
        if not url:
            return ""

        # Check against our URL patterns
        for pattern, topic in self.url_patterns:
            if pattern.search(url):
                return topic

        # Extract additional context from URL structure
        parsed = urlparse(url)
        path_parts = [part for part in parsed.path.split('/') if part]

        # Look for common indicators
        context_indicators = []
        for part in path_parts:
            if any(keyword in part.lower() for keyword in ['tutorial', 'guide', 'how-to', 'example']):
                context_indicators.append('tutorial content')
            elif any(keyword in part.lower() for keyword in ['api', 'reference', 'docs']):
                context_indicators.append('documentation')
            elif any(keyword in part.lower() for keyword in ['blog', 'article', 'post']):
                context_indicators.append('article')

        return ', '.join(context_indicators) if context_indicators else 'web research'

    def _extract_keywords_from_title(self, title: str) -> List[str]:
        """Extract relevant keywords from page title."""
        if not title:
            return []

        # Common technical terms and programming languages
        tech_keywords = {
            'python', 'javascript', 'react', 'node', 'django', 'flask', 'fastapi',
            'docker', 'kubernetes', 'aws', 'gcp', 'azure', 'terraform',
            'api', 'rest', 'graphql', 'sql', 'mongodb', 'postgresql',
            'machine learning', 'ai', 'data science', 'tensorflow', 'pytorch',
            'git', 'github', 'cicd', 'devops', 'testing', 'deployment'
        }

        # Technology categories (generic patterns)
        tech_categories = {
            'ar', 'vr', 'ai', 'ml', 'iot', 'api', 'sdk', 'ide', 'cli', 'gui',
            'saas', 'paas', 'iaas', 'cdn', 'dns', 'ssl', 'tls', 'oauth',
            'jwt', 'crud', 'mvp', 'poc', 'beta', 'alpha'
        }

        # Product type patterns (generic)
        product_patterns = {
            'smartphone', 'tablet', 'laptop', 'desktop', 'server', 'database',
            'framework', 'library', 'platform', 'service', 'application',
            'headset', 'glasses', 'watch', 'speaker', 'display', 'monitor',
            'review', 'demo', 'tutorial', 'guide', 'documentation', 'specification',
            'keynote', 'conference', 'summit', 'event', 'launch', 'announcement'
        }

        keywords = []
        title_lower = title.lower()

        # Extract tech keywords
        for keyword in tech_keywords:
            if keyword in title_lower:
                keywords.append(keyword)

        # Extract tech category keywords
        for keyword in tech_categories:
            if keyword in title_lower:
                keywords.append(keyword)

        # Extract product type keywords
        for keyword in product_patterns:
            if keyword in title_lower:
                keywords.append(keyword)

        # Extract question subjects using pre-compiled patterns
        for pattern in self.question_patterns:
            matches = pattern.findall(title_lower)
            keywords.extend(matches)

        # Special handling for YouTube video titles to extract main topics
        if any(platform in title_lower for platform in ['youtube:', 'yt:', 'video:']):
            video_title = self._youtube_re.sub('', title_lower)
            stop_words = {'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for', 'of', 'with', 'by', 'from', 'up', 'about', 'into', 'through', 'during', 'before', 'after', 'above', 'below', 'between', 'among', 'around'}

            video_words = [word.strip() for word in self._non_alnum_re.split(video_title)
                          if len(word.strip()) >= 3 and word.strip().lower() not in stop_words]

            keywords.extend(video_words[:3])

        return keywords[:8]  # Increased limit for better topic coverage

    def _build_browser_context(self, title: str, url: str, domain: str, topic_context: str, keywords: List[str]) -> str:
        """Build comprehensive context for browser activity summarization."""
        context_parts = []

        if title:
            context_parts.append(f"Page title: {title}")

        if domain:
            context_parts.append(f"Website: {domain}")

        if topic_context:
            context_parts.append(f"Content type: {topic_context}")

        if keywords:
            context_parts.append(f"Key topics: {', '.join(keywords)}")

        if url:
            # Extract meaningful path segments
            parsed = urlparse(url)
            meaningful_parts = [part for part in parsed.path.split('/') if part and not part.isdigit()][:3]
            if meaningful_parts:
                context_parts.append(f"URL context: {' > '.join(meaningful_parts)}")

        return '\n'.join(context_parts)

    def _generate_enhanced_browser_fallback(self, event: Union[Event, dict], project_context: str) -> str:
        """Enhanced fallback for browser activities using available metadata."""
        if isinstance(event, dict):
            metadata = event.get('metadata', {})
        else:
            metadata = getattr(event, 'metadata', None) or {}
        title = metadata.get('title', '')
        domain = metadata.get('domain', '')
        url = metadata.get('url', '')

        # Extract topic from URL
        topic_context = self._extract_topic_from_url(url)

        # Build smarter fallback
        if title and any(keyword in title.lower() for keyword in ['how to', 'tutorial', 'guide']):
            return f"Followed tutorial or guide{project_context}: {title[:50]}..."
        elif title and any(keyword in title.lower() for keyword in ['error', 'fix', 'troubleshoot']):
            return f"Researched solution for technical issue{project_context}: {title[:50]}..."
        elif topic_context and topic_context != 'web research':
            return f"Conducted {topic_context}{project_context} on {domain}"
        elif domain:
            # Use enhanced domain mapping
            domain_activities = {
                'stackoverflow.com': 'programming problem solving',
                'github.com': 'code repository exploration',
                'docs.python.org': 'Python documentation research',
                'developer.mozilla.org': 'web development research',
                'leetcode.com': 'algorithm practice',
                'medium.com': 'technical article reading',
                'youtube.com': 'video learning',
                'reddit.com': 'community discussion research'
            }

            activity = domain_activities.get(domain, f'research on {domain}')
            return f"Conducted {activity}{project_context}"
        else:
            return f"Conducted web research{project_context}"
