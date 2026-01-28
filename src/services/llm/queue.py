"""Redis-based LLM request queue."""

import asyncio
import time
from typing import TYPE_CHECKING

import structlog

from services.llm.models import LLMRequest, LLMResponse

if TYPE_CHECKING:
    from redis.asyncio import Redis

logger = structlog.get_logger(__name__)


class QueueFullError(Exception):
    """Raised when queue is at maximum capacity."""

    pass


class RequestTimeoutError(Exception):
    """Raised when waiting for result times out."""

    pass


class LLMRequestQueue:
    """Redis Streams-based queue for LLM requests.

    Provides submit/wait_for_result pattern with backpressure monitoring.

    Attributes:
        redis: Async Redis client.
        stream_key: Redis stream key for requests.
        max_queue_depth: Maximum allowed queue depth.
        backpressure_threshold: Queue depth that triggers backpressure.
    """

    def __init__(
        self,
        redis: "Redis",
        stream_key: str = "llm:requests",
        max_queue_depth: int = 1000,
        backpressure_threshold: int = 500,
        response_ttl: int = 300,
        poll_interval: float = 0.1,
        poll_fallback_interval: float = 5.0,
    ):
        """Initialize LLM request queue.

        Args:
            redis: Async Redis client.
            stream_key: Redis stream key for requests.
            max_queue_depth: Maximum allowed queue depth.
            backpressure_threshold: Queue depth that triggers backpressure.
            response_ttl: TTL for response keys in seconds.
            poll_interval: Interval for polling responses in seconds (deprecated, kept for compatibility).
            poll_fallback_interval: Interval for fallback polling when pub/sub is used.
        """
        self.redis = redis
        self.stream_key = stream_key
        self.max_queue_depth = max_queue_depth
        self.backpressure_threshold = backpressure_threshold
        self.response_ttl = response_ttl
        self.poll_interval = poll_interval
        self.poll_fallback_interval = poll_fallback_interval

    async def submit(self, request: LLMRequest) -> str:
        """Submit request to queue.

        Args:
            request: LLM request to submit.

        Returns:
            Request ID for correlation.

        Raises:
            QueueFullError: If queue is at maximum capacity.
        """
        # Check backpressure
        depth = await self.get_queue_depth()
        if depth >= self.max_queue_depth:
            logger.warning(
                "llm_queue_full",
                depth=depth,
                max_depth=self.max_queue_depth,
                request_id=request.request_id,
            )
            raise QueueFullError(
                f"Queue depth {depth} exceeds max {self.max_queue_depth}"
            )

        # Add to stream
        await self.redis.xadd(
            self.stream_key,
            {
                "request_id": request.request_id,
                "data": request.to_json(),
            },
            maxlen=self.max_queue_depth * 2,  # Auto-trim old entries
        )

        logger.debug(
            "llm_request_submitted",
            request_id=request.request_id,
            request_type=request.request_type,
            queue_depth=depth + 1,
        )

        return request.request_id

    def _response_channel(self, request_id: str) -> str:
        """Get pub/sub channel name for response notification.

        Args:
            request_id: Request ID to get channel for.

        Returns:
            Redis pub/sub channel name.
        """
        return f"llm:response:notify:{request_id}"

    def _parse_response(self, result: bytes | str) -> LLMResponse:
        """Parse response from Redis.

        Args:
            result: Raw response from Redis.

        Returns:
            Parsed LLMResponse.
        """
        # Handle bytes from Redis
        if isinstance(result, bytes):
            result = result.decode("utf-8")
        return LLMResponse.from_json(result)

    async def wait_for_result(
        self,
        request_id: str,
        timeout: float = 300.0,  # noqa: ASYNC109
    ) -> LLMResponse:
        """Wait for response using pub/sub notification with fallback polling.

        Args:
            request_id: Request ID to wait for.
            timeout: Maximum time to wait in seconds.

        Returns:
            LLMResponse when available.

        Raises:
            RequestTimeoutError: If response not received within timeout.
        """
        response_key = f"llm:response:{request_id}"
        channel = self._response_channel(request_id)

        # Check if already complete (in case response arrived before subscribe)
        result = await self.redis.get(response_key)
        if result:
            response = self._parse_response(result)

            # Clean up response key after reading
            try:
                await self.redis.delete(response_key)
            except Exception as e:
                logger.warning(
                    "llm_response_key_cleanup_failed",
                    request_id=request_id,
                    error=str(e),
                )

            return response

        # Subscribe and wait for notification
        pubsub = self.redis.pubsub()
        try:
            await pubsub.subscribe(channel)

            # Wait for message with timeout and fallback polling
            deadline = time.time() + timeout
            last_poll = time.time()

            while time.time() < deadline:
                # Check for pub/sub message (non-blocking with short timeout)
                try:
                    message = await asyncio.wait_for(
                        pubsub.get_message(ignore_subscribe_messages=True),
                        timeout=0.1,
                    )

                    if message and message["type"] == "message":
                        # Notification received, fetch result
                        result = await self.redis.get(response_key)
                        if result:
                            response = self._parse_response(result)

                            # Clean up response key after reading
                            try:
                                await self.redis.delete(response_key)
                            except Exception as e:
                                logger.warning(
                                    "llm_response_key_cleanup_failed",
                                    request_id=request_id,
                                    error=str(e),
                                )

                            return response
                except TimeoutError:
                    # No pub/sub message, continue loop
                    pass

                # Fallback polling in case pub/sub message was missed
                if time.time() - last_poll >= self.poll_fallback_interval:
                    result = await self.redis.get(response_key)
                    if result:
                        logger.info(
                            "llm_response_found_via_fallback_poll",
                            request_id=request_id,
                        )
                        response = self._parse_response(result)

                        # Clean up response key after reading
                        try:
                            await self.redis.delete(response_key)
                        except Exception as e:
                            logger.warning(
                                "llm_response_key_cleanup_failed",
                                request_id=request_id,
                                error=str(e),
                            )

                        return response
                    last_poll = time.time()

            # Timeout reached
            logger.error(
                "llm_request_timeout",
                request_id=request_id,
                timeout=timeout,
            )
            raise RequestTimeoutError(
                f"Request {request_id} timed out after {timeout}s"
            )
        finally:
            # Always clean up subscription
            try:
                await pubsub.unsubscribe(channel)
                await pubsub.aclose()
            except Exception as e:
                logger.warning(
                    "llm_pubsub_cleanup_failed",
                    request_id=request_id,
                    error=str(e),
                )

    async def get_queue_depth(self) -> int:
        """Get current queue depth.

        Returns:
            Number of pending requests in queue.
        """
        return await self.redis.xlen(self.stream_key)

    async def get_backpressure_status(self) -> dict:
        """Get backpressure status for upstream components.

        Returns:
            Dict with keys:
            - should_wait: True if callers should wait before submitting
            - status: "ok", "slow", or "full"
            - queue_depth: Current number of pending requests
            - threshold: The backpressure threshold value
        """
        depth = await self.get_queue_depth()

        # Determine status string
        if depth >= self.backpressure_threshold:
            status = "full"
        elif depth >= self.backpressure_threshold * 0.5:
            status = "slow"
        else:
            status = "ok"

        # should_wait is True when at 80% or more of threshold
        should_wait = depth >= self.backpressure_threshold * 0.8

        return {
            "should_wait": should_wait,
            "status": status,
            "queue_depth": depth,
            "threshold": self.backpressure_threshold,
        }

    async def store_response(self, response: LLMResponse) -> None:
        """Store response in Redis.

        Args:
            response: LLMResponse to store.
        """
        response_key = f"llm:response:{response.request_id}"
        await self.redis.setex(
            response_key,
            self.response_ttl,
            response.to_json(),
        )

        logger.debug(
            "llm_response_stored",
            request_id=response.request_id,
            status=response.status,
            processing_time_ms=response.processing_time_ms,
        )
