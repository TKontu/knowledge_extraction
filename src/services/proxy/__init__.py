"""HTTP proxy adapter for routing Firecrawl requests through FlareSolverr when accessing anti-bot protected domains."""

from .flaresolverr_adapter import ProxyAdapter
from .flaresolverr_client import FlareSolverrClient

__all__ = ["FlareSolverrClient", "ProxyAdapter"]
