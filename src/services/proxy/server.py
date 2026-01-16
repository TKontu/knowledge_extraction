"""Proxy server entry point."""

import asyncio
import signal

import aiohttp.web
import structlog

from config import settings

from .flaresolverr_adapter import ProxyAdapter

logger = structlog.get_logger(__name__)


async def start_proxy_server() -> None:
    """Start the proxy server."""
    # Create ProxyAdapter instance
    adapter = ProxyAdapter(
        flaresolverr_url=settings.flaresolverr_url,
        blocked_domains=settings.flaresolverr_blocked_domains,
        max_timeout=settings.flaresolverr_max_timeout,
    )

    # Create aiohttp application
    app = aiohttp.web.Application()

    # Add routes
    app.router.add_get("/health", adapter.health_check)
    app.router.add_route("*", "/{path:.*}", adapter.handle_request)

    # Create runner
    runner = aiohttp.web.AppRunner(app)
    await runner.setup()

    # Create site
    site = aiohttp.web.TCPSite(runner, "0.0.0.0", settings.proxy_adapter_port)
    await site.start()

    logger.info(
        "proxy_server_started",
        port=settings.proxy_adapter_port,
        flaresolverr_url=settings.flaresolverr_url,
    )

    # Setup graceful shutdown
    shutdown_event = asyncio.Event()

    def signal_handler(signum, frame):
        logger.info("shutdown_signal_received", signal=signum)
        shutdown_event.set()

    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    # Wait for shutdown signal
    try:
        await shutdown_event.wait()
    finally:
        logger.info("shutting_down")
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(start_proxy_server())
