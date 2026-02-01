"""Tests for browser recycling in Camoufox scraper."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.camoufox.config import CamoufoxSettings
from src.services.camoufox.scraper import CamoufoxScraper


class TestBrowserRecycling:
    """Tests for browser recycling based on request count."""

    def test_should_recycle_browser_returns_true_at_threshold(self):
        """Test recycling triggers when count reaches threshold."""
        config = CamoufoxSettings(recycle_after_requests=10)
        scraper = CamoufoxScraper(config=config)
        scraper._browser_request_counts = [10, 5, 3]

        assert scraper._should_recycle_browser(0) is True
        assert scraper._should_recycle_browser(1) is False
        assert scraper._should_recycle_browser(2) is False

    def test_should_recycle_browser_returns_true_above_threshold(self):
        """Test recycling triggers when count exceeds threshold."""
        config = CamoufoxSettings(recycle_after_requests=10)
        scraper = CamoufoxScraper(config=config)
        scraper._browser_request_counts = [15]

        assert scraper._should_recycle_browser(0) is True

    def test_should_recycle_browser_returns_false_below_threshold(self):
        """Test recycling does not trigger below threshold."""
        config = CamoufoxSettings(recycle_after_requests=100)
        scraper = CamoufoxScraper(config=config)
        scraper._browser_request_counts = [99, 50, 1]

        assert scraper._should_recycle_browser(0) is False
        assert scraper._should_recycle_browser(1) is False
        assert scraper._should_recycle_browser(2) is False

    def test_should_recycle_browser_disabled_when_threshold_zero(self):
        """Test recycling disabled when threshold is 0."""
        config = CamoufoxSettings(recycle_after_requests=0)
        scraper = CamoufoxScraper(config=config)
        scraper._browser_request_counts = [1000]  # Even very high count

        assert scraper._should_recycle_browser(0) is False

    def test_should_recycle_browser_disabled_when_threshold_negative(self):
        """Test recycling disabled when threshold is negative."""
        config = CamoufoxSettings(recycle_after_requests=-1)
        scraper = CamoufoxScraper(config=config)
        scraper._browser_request_counts = [1000]

        assert scraper._should_recycle_browser(0) is False

    def test_should_recycle_browser_handles_invalid_index(self):
        """Test recycling handles out-of-bounds index gracefully."""
        config = CamoufoxSettings(recycle_after_requests=10)
        scraper = CamoufoxScraper(config=config)
        scraper._browser_request_counts = [15]

        # Index out of bounds should return False, not crash
        assert scraper._should_recycle_browser(5) is False
        assert scraper._should_recycle_browser(-1) is False

    def test_should_recycle_browser_handles_empty_counters(self):
        """Test recycling handles empty counter list."""
        config = CamoufoxSettings(recycle_after_requests=10)
        scraper = CamoufoxScraper(config=config)
        scraper._browser_request_counts = []

        assert scraper._should_recycle_browser(0) is False

    @pytest.mark.asyncio
    async def test_scrape_increments_request_counter(self):
        """Test that scrape increments the browser request counter."""
        config = CamoufoxSettings(recycle_after_requests=100)
        scraper = CamoufoxScraper(config=config)

        # Mock browser pool
        mock_browser = MagicMock()
        mock_browser.is_connected.return_value = True
        scraper._browsers = [mock_browser]
        scraper._browser_request_counts = [0]

        # Mock _do_scrape to return success
        scraper._do_scrape = AsyncMock(return_value={"content": "test"})

        # Create a mock request
        mock_request = MagicMock()
        mock_request.url = "https://example.com"

        await scraper.scrape(mock_request)

        assert scraper._browser_request_counts[0] == 1

    @pytest.mark.asyncio
    async def test_scrape_triggers_recycle_at_threshold(self):
        """Test that scrape triggers browser recycle at threshold."""
        config = CamoufoxSettings(recycle_after_requests=5)
        scraper = CamoufoxScraper(config=config)

        # Mock browser pool
        mock_browser = MagicMock()
        mock_browser.is_connected.return_value = True
        scraper._browsers = [mock_browser]
        scraper._browser_request_counts = [4]  # One more to hit threshold

        # Mock _do_scrape to return success
        scraper._do_scrape = AsyncMock(return_value={"content": "test"})

        # Mock _schedule_browser_restart to track calls
        scraper._schedule_browser_restart = MagicMock()

        # Create a mock request
        mock_request = MagicMock()
        mock_request.url = "https://example.com"

        await scraper.scrape(mock_request)

        # Counter should now be 5 (at threshold)
        assert scraper._browser_request_counts[0] == 5
        # Restart should be scheduled
        scraper._schedule_browser_restart.assert_called_once_with(0)

    @pytest.mark.asyncio
    async def test_scrape_does_not_trigger_recycle_below_threshold(self):
        """Test that scrape does not trigger recycle below threshold."""
        config = CamoufoxSettings(recycle_after_requests=100)
        scraper = CamoufoxScraper(config=config)

        # Mock browser pool
        mock_browser = MagicMock()
        mock_browser.is_connected.return_value = True
        scraper._browsers = [mock_browser]
        scraper._browser_request_counts = [50]

        # Mock _do_scrape to return success
        scraper._do_scrape = AsyncMock(return_value={"content": "test"})

        # Mock _schedule_browser_restart to track calls
        scraper._schedule_browser_restart = MagicMock()

        # Create a mock request
        mock_request = MagicMock()
        mock_request.url = "https://example.com"

        await scraper.scrape(mock_request)

        # Counter incremented but not at threshold
        assert scraper._browser_request_counts[0] == 51
        # Restart should NOT be scheduled
        scraper._schedule_browser_restart.assert_not_called()

    @pytest.mark.asyncio
    async def test_restart_browser_resets_counter(self):
        """Test that _restart_browser resets the request counter."""
        config = CamoufoxSettings(recycle_after_requests=100)
        scraper = CamoufoxScraper(config=config)

        # Set up counters with a high value
        scraper._browser_request_counts = [95, 50]

        # Mock camoufox instance and browser
        mock_camoufox = MagicMock()
        mock_camoufox.__aexit__ = AsyncMock()
        scraper._camoufox_instances = [mock_camoufox, MagicMock()]
        scraper._browsers = [MagicMock(), MagicMock()]

        # Mock AsyncCamoufox and browser creation
        mock_new_browser = MagicMock()
        mock_new_camoufox = MagicMock()
        mock_new_camoufox.start = AsyncMock(return_value=mock_new_browser)

        with patch(
            "src.services.camoufox.scraper.AsyncCamoufox",
            return_value=mock_new_camoufox,
        ):
            result = await scraper._restart_browser(0)

        assert result == mock_new_browser
        # Counter should be reset to 0
        assert scraper._browser_request_counts[0] == 0
        # Other counter unchanged
        assert scraper._browser_request_counts[1] == 50

    @pytest.mark.asyncio
    async def test_restart_browser_does_not_reset_on_failure(self):
        """Test that failed restart does not reset counter."""
        config = CamoufoxSettings(recycle_after_requests=100)
        scraper = CamoufoxScraper(config=config)

        # Set up counters with a high value
        scraper._browser_request_counts = [95]

        # Mock camoufox instance
        mock_camoufox = MagicMock()
        mock_camoufox.__aexit__ = AsyncMock()
        scraper._camoufox_instances = [mock_camoufox]
        scraper._browsers = [MagicMock()]

        # Mock AsyncCamoufox to raise exception
        with patch(
            "src.services.camoufox.scraper.AsyncCamoufox",
            side_effect=Exception("Browser start failed"),
        ):
            result = await scraper._restart_browser(0)

        assert result is None
        # Counter should NOT be reset (still at original value)
        assert scraper._browser_request_counts[0] == 95

    @pytest.mark.asyncio
    async def test_start_initializes_counters(self):
        """Test that start() initializes request counters."""
        config = CamoufoxSettings(browser_count=3, recycle_after_requests=100)
        scraper = CamoufoxScraper(config=config)

        # Mock AsyncCamoufox and browser creation
        mock_browser = MagicMock()
        mock_camoufox = MagicMock()
        mock_camoufox.start = AsyncMock(return_value=mock_browser)

        with patch(
            "src.services.camoufox.scraper.AsyncCamoufox",
            return_value=mock_camoufox,
        ):
            await scraper.start()

        # Should have 3 counters, all initialized to 0
        assert len(scraper._browser_request_counts) == 3
        assert scraper._browser_request_counts == [0, 0, 0]

    @pytest.mark.asyncio
    async def test_stop_clears_counters(self):
        """Test that stop() clears request counters."""
        config = CamoufoxSettings(recycle_after_requests=100)
        scraper = CamoufoxScraper(config=config)

        # Set up state as if started
        mock_camoufox = MagicMock()
        mock_camoufox.__aexit__ = AsyncMock()
        scraper._browsers = [MagicMock()]
        scraper._camoufox_instances = [mock_camoufox]
        scraper._browser_request_counts = [50, 75, 100]

        await scraper.stop()

        assert scraper._browser_request_counts == []


class TestConfigRecycleAfterRequests:
    """Tests for recycle_after_requests configuration."""

    def test_default_value(self):
        """Test default recycle threshold is 100."""
        config = CamoufoxSettings()
        assert config.recycle_after_requests == 100

    def test_custom_value(self):
        """Test custom recycle threshold."""
        config = CamoufoxSettings(recycle_after_requests=50)
        assert config.recycle_after_requests == 50

    def test_disabled_with_zero(self):
        """Test recycling can be disabled with 0."""
        config = CamoufoxSettings(recycle_after_requests=0)
        assert config.recycle_after_requests == 0

    def test_from_environment(self):
        """Test configuration from environment variable."""
        with patch.dict("os.environ", {"CAMOUFOX_RECYCLE_AFTER_REQUESTS": "200"}):
            config = CamoufoxSettings()
            assert config.recycle_after_requests == 200
