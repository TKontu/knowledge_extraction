"""Tests for the alerting service."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import httpx

from services.alerting.models import Alert, AlertLevel, AlertType
from services.alerting.service import AlertService, get_alert_service, reset_alert_service


class TestAlertModels:
    """Tests for alert models."""

    def test_alert_to_webhook_payload(self):
        """Test converting alert to webhook payload."""
        project_id = uuid4()
        alert = Alert(
            type=AlertType.EMBEDDING_FAILURE,
            level=AlertLevel.ERROR,
            title="Test Alert",
            message="Test message",
            project_id=project_id,
            details={"count": 5},
        )

        payload = alert.to_webhook_payload()

        assert payload["type"] == "embedding_failure"
        assert payload["level"] == "error"
        assert payload["title"] == "Test Alert"
        assert payload["message"] == "Test message"
        assert payload["project_id"] == str(project_id)
        assert payload["details"]["count"] == 5
        assert "timestamp" in payload

    def test_alert_to_slack_payload(self):
        """Test converting alert to Slack format."""
        alert = Alert(
            type=AlertType.JOB_FAILED,
            level=AlertLevel.CRITICAL,
            title="Job Failed",
            message="Something went wrong",
            job_id=uuid4(),
        )

        payload = alert.to_slack_payload()

        assert "attachments" in payload
        assert len(payload["attachments"]) == 1
        attachment = payload["attachments"][0]
        assert attachment["color"] == "#ff0000"  # Critical = red
        assert "[CRITICAL]" in attachment["title"]


class TestAlertService:
    """Tests for AlertService."""

    @pytest.fixture
    def service_no_webhook(self):
        """Alert service without webhook."""
        return AlertService(webhook_url=None, enabled=True)

    @pytest.fixture
    def service_with_webhook(self):
        """Alert service with webhook configured."""
        return AlertService(
            webhook_url="https://example.com/webhook",
            webhook_format="json",
            enabled=True,
        )

    @pytest.fixture
    def service_disabled(self):
        """Disabled alert service."""
        return AlertService(webhook_url="https://example.com/webhook", enabled=False)

    @pytest.mark.asyncio
    async def test_send_logs_alert_without_webhook(self, service_no_webhook):
        """Test that alerts are logged even without webhook."""
        alert = Alert(
            type=AlertType.EMBEDDING_FAILURE,
            level=AlertLevel.ERROR,
            title="Test",
            message="Test message",
        )

        with patch("services.alerting.service.logger") as mock_logger:
            result = await service_no_webhook.send(alert)

        assert result is True
        mock_logger.error.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_disabled_returns_true(self, service_disabled):
        """Test that disabled service returns True without doing anything."""
        alert = Alert(
            type=AlertType.EMBEDDING_FAILURE,
            level=AlertLevel.ERROR,
            title="Test",
            message="Test message",
        )

        result = await service_disabled.send(alert)

        assert result is True

    @pytest.mark.asyncio
    async def test_send_webhook_success(self, service_with_webhook):
        """Test successful webhook delivery."""
        alert = Alert(
            type=AlertType.EMBEDDING_FAILURE,
            level=AlertLevel.ERROR,
            title="Test",
            message="Test message",
        )

        mock_response = MagicMock()
        mock_response.status_code = 200

        with patch.object(
            service_with_webhook, "_get_client"
        ) as mock_get_client:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_get_client.return_value = mock_client

            result = await service_with_webhook.send(alert)

        assert result is True
        mock_client.post.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_webhook_failure(self, service_with_webhook):
        """Test webhook delivery failure."""
        alert = Alert(
            type=AlertType.EMBEDDING_FAILURE,
            level=AlertLevel.ERROR,
            title="Test",
            message="Test message",
        )

        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"

        with patch.object(
            service_with_webhook, "_get_client"
        ) as mock_get_client:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_get_client.return_value = mock_client

            result = await service_with_webhook.send(alert)

        assert result is False

    @pytest.mark.asyncio
    async def test_send_webhook_network_error(self, service_with_webhook):
        """Test webhook network error handling."""
        alert = Alert(
            type=AlertType.EMBEDDING_FAILURE,
            level=AlertLevel.ERROR,
            title="Test",
            message="Test message",
        )

        with patch.object(
            service_with_webhook, "_get_client"
        ) as mock_get_client:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(
                side_effect=httpx.RequestError("Connection failed")
            )
            mock_get_client.return_value = mock_client

            result = await service_with_webhook.send(alert)

        assert result is False

    @pytest.mark.asyncio
    async def test_alert_embedding_failure_convenience_method(self, service_no_webhook):
        """Test the alert_embedding_failure convenience method."""
        project_id = uuid4()
        source_id = uuid4()

        with patch.object(service_no_webhook, "send", new_callable=AsyncMock) as mock_send:
            mock_send.return_value = True
            result = await service_no_webhook.alert_embedding_failure(
                project_id=project_id,
                source_id=source_id,
                extractions_affected=10,
                error="Test error",
            )

        assert result is True
        mock_send.assert_called_once()
        alert = mock_send.call_args[0][0]
        assert alert.type == AlertType.EMBEDDING_FAILURE
        assert alert.level == AlertLevel.ERROR
        assert alert.project_id == project_id
        assert alert.source_id == source_id
        assert alert.details["extractions_affected"] == 10

    @pytest.mark.asyncio
    async def test_alert_recovery_completed_success(self, service_no_webhook):
        """Test recovery completion alert with all successes."""
        project_id = uuid4()

        with patch.object(service_no_webhook, "send", new_callable=AsyncMock) as mock_send:
            mock_send.return_value = True
            await service_no_webhook.alert_recovery_completed(
                project_id=project_id,
                recovered=50,
                failed=0,
            )

        alert = mock_send.call_args[0][0]
        assert alert.type == AlertType.RECOVERY_COMPLETED
        assert alert.level == AlertLevel.INFO  # No failures = INFO

    @pytest.mark.asyncio
    async def test_alert_recovery_completed_with_failures(self, service_no_webhook):
        """Test recovery completion alert with some failures."""
        project_id = uuid4()

        with patch.object(service_no_webhook, "send", new_callable=AsyncMock) as mock_send:
            mock_send.return_value = True
            await service_no_webhook.alert_recovery_completed(
                project_id=project_id,
                recovered=40,
                failed=10,
            )

        alert = mock_send.call_args[0][0]
        assert alert.level == AlertLevel.WARNING  # Has failures = WARNING

    @pytest.mark.asyncio
    async def test_slack_format_webhook(self):
        """Test Slack-formatted webhook."""
        service = AlertService(
            webhook_url="https://hooks.slack.com/test",
            webhook_format="slack",
            enabled=True,
        )

        alert = Alert(
            type=AlertType.EMBEDDING_FAILURE,
            level=AlertLevel.ERROR,
            title="Test",
            message="Test message",
        )

        mock_response = MagicMock()
        mock_response.status_code = 200

        with patch.object(service, "_get_client") as mock_get_client:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_get_client.return_value = mock_client

            await service.send(alert)

            # Verify Slack format was used
            call_args = mock_client.post.call_args
            payload = call_args.kwargs["json"]
            assert "attachments" in payload


class TestGetAlertService:
    """Tests for singleton alert service factory."""

    def test_get_alert_service_returns_singleton(self):
        """Test that get_alert_service returns same instance."""
        reset_alert_service()

        # Mock the settings module where it's imported from (inside the function)
        with patch("config.settings") as mock_settings:
            mock_settings.alerting_enabled = True
            mock_settings.alert_webhook_url = None
            mock_settings.alert_webhook_format = "json"

            service1 = get_alert_service()
            service2 = get_alert_service()

        assert service1 is service2
        reset_alert_service()

    def test_get_alert_service_respects_config(self):
        """Test that get_alert_service uses config values."""
        reset_alert_service()

        with patch("config.settings") as mock_settings:
            mock_settings.alerting_enabled = False
            mock_settings.alert_webhook_url = "https://test.com/webhook"
            mock_settings.alert_webhook_format = "slack"

            service = get_alert_service()

        assert service._enabled is False
        assert service._webhook_url == "https://test.com/webhook"
        assert service._webhook_format == "slack"
        reset_alert_service()
