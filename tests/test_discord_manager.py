from unittest.mock import Mock, patch

from orbit.core.discord_manager import DiscordManager


class TestDiscordManager:
    @patch("orbit.core.discord_manager.record_notification")
    @patch("orbit.core.discord_manager.requests.post")
    @patch(
        "orbit.core.discord_manager.URLS.get_url", return_value="https://example.test"
    )
    def test_truncation_warning_stays_within_embed_field_limit(
        self, _mock_get_url: Mock, mock_post: Mock, mock_record: Mock
    ) -> None:
        mock_post.return_value.status_code = 204
        fields = {f"field-{index}": index for index in range(26)}

        status_code = DiscordManager().send_signal_updates(
            data=None,
            description="Order placed successfully",
            fields=fields,
        )

        embed_fields = mock_post.call_args.kwargs["json"]["embeds"][0]["fields"]
        assert status_code == 204
        assert (
            mock_post.call_args.kwargs["timeout"]
            == DiscordManager.REQUEST_TIMEOUT_SECONDS
        )
        assert len(embed_fields) == DiscordManager.MAX_FIELDS
        assert embed_fields[-1]["name"] == "⚠ Warning"
        mock_record.assert_called_once_with(
            "signal", "", "Order placed successfully", embed_fields
        )

    @patch("orbit.core.discord_manager.record_notification")
    @patch("orbit.core.discord_manager.requests.post")
    @patch(
        "orbit.core.discord_manager.URLS.get_url", return_value="https://example.test"
    )
    def test_value_truncation_reserves_warning_field_at_field_limit(
        self, _mock_get_url: Mock, mock_post: Mock, _mock_record: Mock
    ) -> None:
        mock_post.return_value.status_code = 204
        fields = {f"field-{index}": index for index in range(25)}
        fields["field-0"] = "x" * (DiscordManager.MAX_FIELD_VALUE + 1)

        DiscordManager().send_signal_updates(
            data=None,
            description="Order placed successfully",
            fields=fields,
        )

        embed_fields = mock_post.call_args.kwargs["json"]["embeds"][0]["fields"]
        assert len(embed_fields) == DiscordManager.MAX_FIELDS
        assert embed_fields[-1]["name"] == "⚠ Warning"

    @patch("orbit.core.discord_manager.time.sleep")
    @patch("orbit.core.discord_manager.record_notification")
    @patch("orbit.core.discord_manager.requests.post")
    @patch(
        "orbit.core.discord_manager.URLS.get_url", return_value="https://example.test"
    )
    def test_transient_webhook_failure_is_retried(
        self,
        _mock_get_url: Mock,
        mock_post: Mock,
        mock_record: Mock,
        mock_sleep: Mock,
    ) -> None:
        unavailable = Mock(status_code=503, text="temporarily unavailable")
        delivered = Mock(status_code=204, text="")
        mock_post.side_effect = [unavailable, delivered]

        status_code = DiscordManager().send_logs(None, "Runtime status")

        assert status_code == 204
        assert mock_post.call_count == 2
        mock_sleep.assert_called_once_with(DiscordManager.RETRY_BACKOFF_SECONDS)
        mock_record.assert_called_once()

    @patch("orbit.core.discord_manager.time.sleep")
    @patch("orbit.core.discord_manager.logger.error")
    @patch("orbit.core.discord_manager.requests.post")
    @patch(
        "orbit.core.discord_manager.URLS.get_url", return_value="https://example.test"
    )
    def test_exhausted_webhook_failure_logs_without_exception_traceback(
        self,
        _mock_get_url: Mock,
        mock_post: Mock,
        mock_error: Mock,
        _mock_sleep: Mock,
    ) -> None:
        mock_post.return_value = Mock(status_code=503, text="x" * 600)

        status_code = DiscordManager().send_logs(None, "Runtime status")

        assert status_code == 503
        assert mock_post.call_count == DiscordManager.MAX_WEBHOOK_ATTEMPTS
        mock_error.assert_called_once_with(
            "Failed webhook | Status: %s | Response: %.500s | key: %s",
            503,
            "x" * DiscordManager.MAX_ERROR_RESPONSE_LENGTH,
            "logs",
        )
