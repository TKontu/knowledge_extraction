"""LLM Worker that processes requests from Redis queue."""

import asyncio
import json
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import structlog

from services.llm.json_repair import try_repair_json
from services.llm.models import LLMRequest, LLMResponse

if TYPE_CHECKING:
    from openai import AsyncOpenAI
    from redis.asyncio import Redis

logger = structlog.get_logger(__name__)


class LLMWorker:
    """Worker that processes LLM requests from Redis queue.

    Features:
    - Adaptive concurrency based on success/timeout ratio
    - Consumer group for distributed processing
    - Automatic request expiration handling
    - Uses prompts from payload when available (for consistency with client-side prompts)
    - Dead Letter Queue (DLQ) for failed requests after max retries

    Attributes:
        redis: Async Redis client.
        llm_client: OpenAI-compatible async client.
        worker_id: Unique identifier for this worker.
        stream_key: Redis stream key for requests.
        consumer_group: Redis consumer group name.
        dlq_key: Redis key for Dead Letter Queue.
        max_retries: Maximum retry attempts before moving to DLQ.
    """

    DLQ_KEY = "llm:dlq"

    def __init__(
        self,
        redis: "Redis",
        llm_client: "AsyncOpenAI",
        worker_id: str,
        stream_key: str = "llm:requests",
        consumer_group: str = "llm-workers",
        initial_concurrency: int = 10,
        max_concurrency: int = 50,
        min_concurrency: int = 5,
        model: str = "Qwen3-30B-A3B-Instruct-4bit",
        max_retries: int = 3,
        max_tokens: int = 4096,
        base_temperature: float = 0.1,
        temperature_increment: float = 0.05,
    ):
        """Initialize LLM worker.

        Args:
            redis: Async Redis client.
            llm_client: OpenAI-compatible async client.
            worker_id: Unique identifier for this worker.
            stream_key: Redis stream key for requests.
            consumer_group: Redis consumer group name.
            initial_concurrency: Starting concurrent request limit.
            max_concurrency: Maximum concurrent requests.
            min_concurrency: Minimum concurrent requests.
            model: LLM model name.
            max_retries: Maximum retry attempts before moving to DLQ.
            max_tokens: Maximum tokens for LLM response (prevents endless generation).
            base_temperature: Base temperature for LLM requests.
            temperature_increment: Temperature increase per retry attempt.
        """
        self.redis = redis
        self.llm_client = llm_client
        self.worker_id = worker_id
        self.stream_key = stream_key
        self.consumer_group = consumer_group
        self.model = model
        self.max_retries = max_retries
        self.max_tokens = max_tokens
        self.base_temperature = base_temperature
        self.temperature_increment = temperature_increment

        # Adaptive concurrency
        self.concurrency = initial_concurrency
        self.max_concurrency = max_concurrency
        self.min_concurrency = min_concurrency
        self.semaphore = asyncio.Semaphore(initial_concurrency)

        # Metrics for adaptive tuning
        self.success_count = 0
        self.timeout_count = 0
        self.last_adjustment = time.time()
        self.adjustment_interval = 10  # seconds

        # Track active tasks for safe semaphore adjustment
        self._active_count = 0
        self._active_lock = asyncio.Lock()
        self._pending_concurrency: int | None = None  # Deferred adjustment

        # Running state
        self._running = False

    def _response_channel(self, request_id: str) -> str:
        """Get pub/sub channel name for response notification.

        Args:
            request_id: Request ID to get channel for.

        Returns:
            Redis pub/sub channel name.
        """
        return f"llm:response:notify:{request_id}"

    async def initialize(self) -> None:
        """Initialize worker, creating consumer group if needed."""
        try:
            await self.redis.xgroup_create(
                self.stream_key,
                self.consumer_group,
                id="0",
                mkstream=True,
            )
            logger.info(
                "llm_worker_created_consumer_group",
                worker_id=self.worker_id,
                stream=self.stream_key,
                group=self.consumer_group,
            )
        except Exception as e:
            # Group already exists is OK
            if "BUSYGROUP" not in str(e):
                raise
            logger.debug(
                "llm_worker_consumer_group_exists",
                worker_id=self.worker_id,
                group=self.consumer_group,
            )

    async def start(self) -> None:
        """Start processing loop."""
        await self.initialize()
        self._running = True

        logger.info(
            "llm_worker_started",
            worker_id=self.worker_id,
            initial_concurrency=self.concurrency,
        )

        while self._running:
            try:
                await self.process_batch()
                await self.maybe_adjust_concurrency()
            except Exception as e:
                logger.error(
                    "llm_worker_error",
                    worker_id=self.worker_id,
                    error=str(e),
                )
                await asyncio.sleep(1)  # Back off on error

    async def stop(self) -> None:
        """Stop processing loop."""
        self._running = False
        logger.info("llm_worker_stopped", worker_id=self.worker_id)

    async def process_batch(self) -> None:
        """Read and process a batch of requests."""
        # Read up to `concurrency` messages
        messages = await self.redis.xreadgroup(
            groupname=self.consumer_group,
            consumername=self.worker_id,
            streams={self.stream_key: ">"},
            count=self.concurrency,
            block=1000,  # 1 second block
        )

        if not messages:
            return

        # Process all messages concurrently
        tasks = []
        for _stream_name, entries in messages:
            for entry_id, data in entries:
                task = asyncio.create_task(self._process_request(entry_id, data))
                tasks.append(task)

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _process_request(self, entry_id: str, data: dict) -> None:
        """Process a single LLM request.

        Args:
            entry_id: Redis stream entry ID.
            data: Request data from stream.
        """
        # Parse request
        request_data = data.get("data") or data.get(b"data")
        if isinstance(request_data, bytes):
            request_data = request_data.decode("utf-8")

        request = LLMRequest.from_json(request_data)

        async with self.semaphore:
            # Track active tasks for safe concurrency adjustment
            async with self._active_lock:
                self._active_count += 1

            start_time = time.time()
            response: LLMResponse

            try:
                # Check if request expired
                if request.is_expired():
                    response = LLMResponse(
                        request_id=request.request_id,
                        status="timeout",
                        result=None,
                        error="Request expired before processing",
                        processing_time_ms=0,
                        completed_at=datetime.now(UTC),
                    )
                    logger.warning(
                        "llm_request_expired",
                        request_id=request.request_id,
                        request_type=request.request_type,
                    )
                else:
                    # Execute LLM call
                    result = await self._execute_llm_call(request)

                    processing_time = int((time.time() - start_time) * 1000)
                    response = LLMResponse(
                        request_id=request.request_id,
                        status="success",
                        result=result,
                        error=None,
                        processing_time_ms=processing_time,
                        completed_at=datetime.now(UTC),
                    )
                    self.success_count += 1

                    logger.debug(
                        "llm_request_completed",
                        request_id=request.request_id,
                        request_type=request.request_type,
                        processing_time_ms=processing_time,
                    )

            except Exception as e:
                processing_time = int((time.time() - start_time) * 1000)
                error_msg = str(e)

                if "timeout" in error_msg.lower():
                    self.timeout_count += 1

                # Extract prompt preview from payload
                prompt_preview = None
                if "prompt" in request.payload:
                    prompt_preview = str(request.payload["prompt"])[:300]
                elif "content" in request.payload:
                    prompt_preview = str(request.payload["content"])[:300]

                logger.error(
                    "llm_request_failed",
                    request_id=request.request_id,
                    request_type=request.request_type,
                    error=error_msg,
                    error_type=type(e).__name__,
                    retry_count=request.retry_count,
                    prompt_preview=prompt_preview,
                    processing_time_ms=processing_time,
                    exc_info=True,
                )

                # Handle failure with retry/DLQ logic
                await self._handle_failure(request, error_msg, processing_time)

                # Acknowledge original message (requeued or moved to DLQ)
                await self.redis.xack(
                    self.stream_key,
                    self.consumer_group,
                    entry_id,
                )

                # Decrement active count and apply pending concurrency if idle
                async with self._active_lock:
                    self._active_count -= 1
                    if (
                        self._active_count == 0
                        and self._pending_concurrency is not None
                    ):
                        new_concurrency = self._pending_concurrency
                        self._pending_concurrency = None
                        self.concurrency = new_concurrency
                        self.semaphore = asyncio.Semaphore(new_concurrency)
                        logger.info(
                            "llm_worker_deferred_adjustment_applied",
                            worker_id=self.worker_id,
                            new_concurrency=new_concurrency,
                        )
                return  # Exit early after handling failure

            # Store response in Redis
            response_key = f"llm:response:{request.request_id}"
            await self.redis.setex(
                response_key,
                300,  # 5 minute TTL
                response.to_json(),
            )

            # Publish notification to wake up waiting clients
            channel = self._response_channel(request.request_id)
            await self.redis.publish(channel, "ready")

            # Acknowledge message
            await self.redis.xack(
                self.stream_key,
                self.consumer_group,
                entry_id,
            )

            # Decrement active count and apply pending concurrency if idle
            async with self._active_lock:
                self._active_count -= 1
                if self._active_count == 0 and self._pending_concurrency is not None:
                    new_concurrency = self._pending_concurrency
                    self._pending_concurrency = None
                    self.concurrency = new_concurrency
                    self.semaphore = asyncio.Semaphore(new_concurrency)
                    logger.info(
                        "llm_worker_deferred_adjustment_applied",
                        worker_id=self.worker_id,
                        new_concurrency=new_concurrency,
                    )

    async def _execute_llm_call(self, request: LLMRequest) -> dict[str, Any]:
        """Execute the actual LLM call based on request type.

        Args:
            request: LLM request to execute.

        Returns:
            Result dictionary from LLM.

        Raises:
            ValueError: If request type is unknown.
        """
        # Calculate temperature based on retry count (higher on retries to vary output)
        temperature = self.base_temperature + (
            request.retry_count * self.temperature_increment
        )

        if request.request_type == "extract_facts":
            return await self._extract_facts(
                request.payload, temperature, request.retry_count
            )
        elif request.request_type == "extract_field_group":
            return await self._extract_field_group(
                request.payload, temperature, request.retry_count
            )
        elif request.request_type == "extract_entities":
            return await self._extract_entities(
                request.payload, temperature, request.retry_count
            )
        elif request.request_type == "complete":
            return await self._complete(
                request.payload, temperature, request.retry_count
            )
        else:
            raise ValueError(f"Unknown request type: {request.request_type}")

    async def _extract_facts(
        self, payload: dict, temperature: float, retry_count: int
    ) -> dict:
        """Execute fact extraction.

        Uses prompts from payload if available (preferred), otherwise falls back
        to building prompts internally for backward compatibility.

        Args:
            payload: Request payload with content, categories, profile_name,
                    and optionally system_prompt, user_prompt, model.
            temperature: Temperature for this request (varies with retries).
            retry_count: Current retry attempt number.

        Returns:
            Extracted facts.
        """
        # Use prompts from payload if available (preferred)
        system_prompt = payload.get("system_prompt")
        user_prompt = payload.get("user_prompt")

        # Fallback to internal prompt building if not in payload
        if not system_prompt or not user_prompt:
            content = payload.get("content", "")
            categories = payload.get("categories", [])
            profile_name = payload.get("profile_name", "general")
            system_prompt = f"Extract facts from the content. Categories: {categories}. Profile: {profile_name}"
            user_prompt = content[:8000]

        # Add conciseness hint on retries
        if retry_count > 0:
            system_prompt += "\n\nIMPORTANT: Be concise. Output valid JSON only."

        # Use model from payload if provided, otherwise use worker's default
        model = payload.get("model", self.model)

        response = await self.llm_client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=temperature,
            max_tokens=self.max_tokens,
        )

        result_text = response.choices[0].message.content
        return try_repair_json(result_text, context="extract_facts")

    async def _extract_field_group(
        self, payload: dict, temperature: float, retry_count: int
    ) -> dict:
        """Execute field group extraction.

        Uses prompts from payload if available (preferred), otherwise falls back
        to building prompts internally for backward compatibility.

        Args:
            payload: Request payload with content, field_group, source_context,
                    and optionally system_prompt, user_prompt, model.
            temperature: Temperature for this request (varies with retries).
            retry_count: Current retry attempt number.

        Returns:
            Extracted field values.
        """
        # Use prompts from payload if available (preferred)
        system_prompt = payload.get("system_prompt")
        user_prompt = payload.get("user_prompt")

        # Fallback to internal prompt building if not in payload
        if not system_prompt or not user_prompt:
            content = payload.get("content", "")
            field_group = payload.get("field_group", {})
            # Support both source_context (new) and company_name (backward compat)
            source_context = payload.get("source_context") or payload.get(
                "company_name", ""
            )

            group_name = field_group.get("name", "unknown")
            group_desc = field_group.get("description", "")

            system_prompt = f"Extract {group_name} information: {group_desc}"
            # Use generic "Source:" label in fallback mode
            user_prompt = (
                f"Source: {source_context}\n\nContent:\n{content[:8000]}"
                if source_context
                else f"Content:\n{content[:8000]}"
            )

        # Add conciseness hint on retries
        if retry_count > 0:
            system_prompt += "\n\nIMPORTANT: Be concise. Output valid JSON only."

        # Use model from payload if provided, otherwise use worker's default
        model = payload.get("model", self.model)

        response = await self.llm_client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=temperature,
            max_tokens=self.max_tokens,
        )

        result_text = response.choices[0].message.content
        finish_reason = response.choices[0].finish_reason

        # Check for truncation due to max_tokens limit
        if finish_reason == "length":
            field_group = payload.get("field_group", {})
            is_entity_list = field_group.get("is_entity_list", False)
            group_name = field_group.get("name", "unknown")
            logger.warning(
                "field_group_extraction_truncated",
                field_group=group_name,
                is_entity_list=is_entity_list,
                response_length=len(result_text) if result_text else 0,
                max_tokens=self.max_tokens,
            )
            # For entity lists, truncation means incomplete JSON array
            if is_entity_list:
                try:
                    return try_repair_json(
                        result_text, context="extract_field_group_truncated"
                    )
                except Exception:
                    # Return empty list for entity lists on unrecoverable truncation
                    logger.warning(
                        "field_group_truncation_unrecoverable",
                        field_group=group_name,
                    )
                    return {group_name: [], "confidence": 0.0}

        return try_repair_json(result_text, context="extract_field_group")

    async def _extract_entities(
        self, payload: dict, temperature: float, retry_count: int
    ) -> dict:
        """Execute entity extraction.

        Uses prompts from payload if available (preferred), otherwise falls back
        to building prompts internally for backward compatibility.

        Args:
            payload: Request payload with extraction_data, entity_types,
                    and optionally system_prompt, user_prompt, model.
            temperature: Temperature for this request (varies with retries).
            retry_count: Current retry attempt number.

        Returns:
            Extracted entities.
        """
        # Use prompts from payload if available (preferred)
        system_prompt = payload.get("system_prompt")
        user_prompt = payload.get("user_prompt")

        # Fallback to internal prompt building if not in payload
        if not system_prompt or not user_prompt:
            extraction_data = payload.get("extraction_data", {})
            entity_types = payload.get("entity_types", [])

            system_prompt = f"Extract entities of types: {entity_types}"
            user_prompt = json.dumps(extraction_data)

        # Add conciseness hint on retries
        if retry_count > 0:
            system_prompt += "\n\nIMPORTANT: Be concise. Output valid JSON only."

        # Use model from payload if provided, otherwise use worker's default
        model = payload.get("model", self.model)

        response = await self.llm_client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=temperature,
            max_tokens=self.max_tokens,
        )

        result_text = response.choices[0].message.content
        return try_repair_json(result_text, context="extract_entities")

    async def _complete(
        self, payload: dict, temperature: float, retry_count: int
    ) -> dict:
        """Execute generic LLM completion.

        Used for report synthesis and other arbitrary LLM calls.

        Args:
            payload: Request payload with system_prompt, user_prompt,
                    and optionally response_format, temperature, model.
            temperature: Temperature for this request (varies with retries).
            retry_count: Current retry attempt number.

        Returns:
            LLM response as dictionary.
        """
        system_prompt = payload.get("system_prompt", "")
        user_prompt = payload.get("user_prompt", "")
        response_format = payload.get("response_format")

        # Use payload temperature if provided, otherwise use calculated temperature
        temp = payload.get("temperature") or temperature

        # Add conciseness hint on retries
        if retry_count > 0:
            system_prompt += "\n\nIMPORTANT: Be concise. Output valid JSON only."

        # Use model from payload if provided, otherwise use worker's default
        model = payload.get("model", self.model)

        kwargs = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temp,
            "max_tokens": self.max_tokens,
        }

        if response_format:
            kwargs["response_format"] = response_format

        response = await self.llm_client.chat.completions.create(**kwargs)
        result_text = response.choices[0].message.content

        # Parse as JSON if json_object format requested
        if response_format and response_format.get("type") == "json_object":
            return try_repair_json(result_text, context="complete")
        return {"text": result_text}

    async def maybe_adjust_concurrency(self) -> None:
        """Adjust concurrency based on success/timeout ratio.

        Uses deferred adjustment to avoid race conditions when tasks are active.
        The new semaphore is only created when no tasks are holding the old one.
        """
        now = time.time()
        if now - self.last_adjustment < self.adjustment_interval:
            return

        self.last_adjustment = now
        total = self.success_count + self.timeout_count

        if total < 10:  # Not enough data
            return

        timeout_rate = self.timeout_count / total
        new_concurrency = None

        if timeout_rate > 0.1:  # >10% timeouts, back off
            new_concurrency = max(self.min_concurrency, int(self.concurrency * 0.7))
            if new_concurrency != self.concurrency:
                logger.warning(
                    "llm_worker_backing_off",
                    worker_id=self.worker_id,
                    timeout_rate=f"{timeout_rate:.2%}",
                    old_concurrency=self.concurrency,
                    new_concurrency=new_concurrency,
                )

        elif timeout_rate < 0.02 and self.success_count > 50:  # <2% timeouts, scale up
            new_concurrency = min(self.max_concurrency, int(self.concurrency * 1.2))
            if new_concurrency != self.concurrency:
                logger.info(
                    "llm_worker_scaling_up",
                    worker_id=self.worker_id,
                    timeout_rate=f"{timeout_rate:.2%}",
                    old_concurrency=self.concurrency,
                    new_concurrency=new_concurrency,
                )

        # Apply adjustment safely - only when no tasks are active
        if new_concurrency is not None and new_concurrency != self.concurrency:
            async with self._active_lock:
                if self._active_count == 0:
                    # Safe to apply immediately
                    self.concurrency = new_concurrency
                    self.semaphore = asyncio.Semaphore(new_concurrency)
                    logger.info(
                        "llm_worker_concurrency_applied",
                        worker_id=self.worker_id,
                        new_concurrency=new_concurrency,
                    )
                else:
                    # Defer adjustment until tasks complete
                    self._pending_concurrency = new_concurrency
                    logger.info(
                        "llm_worker_concurrency_deferred",
                        worker_id=self.worker_id,
                        new_concurrency=new_concurrency,
                        active_count=self._active_count,
                    )

        # Reset counters
        self.success_count = 0
        self.timeout_count = 0

    async def _handle_failure(
        self, request: LLMRequest, error_msg: str, processing_time_ms: int
    ) -> None:
        """Handle a failed request with retry or DLQ logic.

        Args:
            request: The failed request.
            error_msg: Error message from the failure.
            processing_time_ms: Processing time in milliseconds.
        """
        if request.retry_count < self.max_retries - 1:
            # Requeue with incremented retry count
            await self._requeue_with_retry(request)
        else:
            # Move to DLQ and send error response to caller
            await self._move_to_dlq(request, error_msg)
            # Send error response so caller isn't left waiting
            response = LLMResponse(
                request_id=request.request_id,
                status="error",
                result=None,
                error=f"Request failed after {self.max_retries} attempts: {error_msg}",
                processing_time_ms=processing_time_ms,
                completed_at=datetime.now(UTC),
            )
            response_key = f"llm:response:{request.request_id}"
            await self.redis.setex(
                response_key,
                300,  # 5 minute TTL
                response.to_json(),
            )

            # Publish notification to wake up waiting clients
            channel = self._response_channel(request.request_id)
            await self.redis.publish(channel, "ready")

    async def _requeue_with_retry(self, request: LLMRequest) -> None:
        """Requeue a request with incremented retry count.

        Args:
            request: The request to requeue.
        """
        # Create new request with incremented retry count
        new_request = LLMRequest(
            request_id=request.request_id,
            request_type=request.request_type,
            payload=request.payload,
            priority=request.priority,
            created_at=request.created_at,
            timeout_at=request.timeout_at,
            retry_count=request.retry_count + 1,
        )

        await self.redis.xadd(
            self.stream_key,
            {
                "request_id": new_request.request_id,
                "data": new_request.to_json(),
            },
        )

        logger.info(
            "llm_request_requeued",
            request_id=request.request_id,
            retry_count=new_request.retry_count,
            max_retries=self.max_retries,
        )

    async def _move_to_dlq(self, request: LLMRequest, error_msg: str) -> None:
        """Move a failed request to the Dead Letter Queue.

        Args:
            request: The failed request.
            error_msg: Error message from the failure.
        """
        dlq_entry = {
            "request": {
                "request_id": request.request_id,
                "request_type": request.request_type,
                "payload": request.payload,
                "priority": request.priority,
                "created_at": request.created_at.isoformat(),
                "timeout_at": request.timeout_at.isoformat(),
                "retry_count": request.retry_count,
            },
            "error": error_msg,
            "failed_at": datetime.now(UTC).isoformat(),
            "worker_id": self.worker_id,
        }

        await self.redis.lpush(self.DLQ_KEY, json.dumps(dlq_entry))

        logger.warning(
            "llm_request_moved_to_dlq",
            request_id=request.request_id,
            request_type=request.request_type,
            retry_count=request.retry_count,
            error=error_msg,
        )

    async def get_dlq_stats(self) -> dict[str, Any]:
        """Get statistics about the Dead Letter Queue.

        Returns:
            Dictionary with DLQ statistics:
            - count: Total items in DLQ
            - recent: List of recent DLQ entries (up to 10)
        """
        count = await self.redis.llen(self.DLQ_KEY)
        recent_raw = await self.redis.lrange(self.DLQ_KEY, 0, 9)

        recent = []
        for item in recent_raw:
            if isinstance(item, bytes):
                item = item.decode("utf-8")
            recent.append(json.loads(item))

        return {
            "count": count,
            "recent": recent,
        }

    async def reprocess_dlq_item(self, dlq_item: str) -> None:
        """Reprocess an item from the Dead Letter Queue.

        Args:
            dlq_item: JSON string of the DLQ entry.
        """
        data = json.loads(dlq_item)
        request_data = data["request"]

        # Remove from DLQ
        await self.redis.lrem(self.DLQ_KEY, 1, dlq_item)

        # Create new request with reset retry count
        new_request = LLMRequest(
            request_id=request_data["request_id"],
            request_type=request_data["request_type"],
            payload=request_data["payload"],
            priority=request_data["priority"],
            created_at=datetime.fromisoformat(request_data["created_at"]),
            timeout_at=datetime.fromisoformat(request_data["timeout_at"]),
            retry_count=0,  # Reset retry count
        )

        await self.redis.xadd(
            self.stream_key,
            {
                "request_id": new_request.request_id,
                "data": new_request.to_json(),
            },
        )

        logger.info(
            "llm_dlq_item_reprocessed",
            request_id=new_request.request_id,
        )
