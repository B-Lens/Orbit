from unittest.mock import Mock, patch

from orbit.core.discord_manager import DiscordManager


class TestDiscordManager:
    @patch("orbit.core.discord_manager.record_notification")
    @patch("orbit.core.discord_manager.requests.post")
    @patch(
        "orbit.core.discord_manager.URLS.get_url", return_value="https://example.test"
    )
    def test_exception_alert_truncation_stays_within_embed_field_limit(
        self, _mock_get_url: Mock, mock_post: Mock, mock_record: Mock
    ) -> None:
        mock_post.return_value.status_code = 204
        fields = {f"field-{index}": index for index in range(26)}

        status_code = DiscordManager().send_to_webhook(
            "alerts",
            None,
            "Order placed successfully",
            fields,
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
            "alerts", "", "Order placed successfully", embed_fields
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

        DiscordManager().send_to_webhook(
            "alerts", None, "Order placed successfully", fields
        )

        embed_fields = mock_post.call_args.kwargs["json"]["embeds"][0]["fields"]
        assert len(embed_fields) == DiscordManager.MAX_FIELDS
        assert embed_fields[-1]["name"] == "⚠ Warning"

    @patch("orbit.core.discord_manager.record_notification")
    @patch("orbit.core.discord_manager.requests.post")
    @patch("orbit.core.discord_manager.URLS.get_url")
    def test_unsupported_notifications_are_discarded(
        self, mock_get_url: Mock, mock_post: Mock, mock_record: Mock
    ) -> None:
        assert DiscordManager().send_to_webhook("signal", None, "Order placed") is None

        mock_get_url.assert_not_called()
        mock_post.assert_not_called()
        mock_record.assert_not_called()

    def test_alerts_and_exceptions_use_their_webhooks(self) -> None:
        manager = DiscordManager()
        manager.send_to_webhook = Mock()

        manager.send_alerts("symbol", "warning")
        manager.send_exception("commit", "failure")

        assert manager.send_to_webhook.call_args_list == [
            (("alerts", "symbol", "warning", None), {}),
            (("exception", "commit", "failure"), {}),
        ]
