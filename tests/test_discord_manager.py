from unittest.mock import Mock, patch

from orbit.core.discord_manager import DiscordManager


class TestDiscordManager:
    @patch("orbit.core.discord_manager.requests.post")
    @patch(
        "orbit.core.discord_manager.URLS.get_url", return_value="https://example.test"
    )
    def test_truncation_warning_stays_within_embed_field_limit(
        self, _mock_get_url: Mock, mock_post: Mock
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
        assert len(embed_fields) == DiscordManager.MAX_FIELDS
        assert embed_fields[-1]["name"] == "⚠ Warning"

    @patch("orbit.core.discord_manager.requests.post")
    @patch(
        "orbit.core.discord_manager.URLS.get_url", return_value="https://example.test"
    )
    def test_value_truncation_reserves_warning_field_at_field_limit(
        self, _mock_get_url: Mock, mock_post: Mock
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
