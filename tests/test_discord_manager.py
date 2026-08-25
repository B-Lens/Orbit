from unittest import TestCase
from unittest.mock import Mock, patch

from orbit.core.discord_manager import DiscordManager


class TestDiscordManager(TestCase):
    @patch("orbit.core.discord_manager.requests.post")
    @patch("orbit.core.discord_manager.URLS.get_url", return_value="https://example.test/webhook")
    def test_oversized_field_mapping_stays_within_discord_field_limit(
        self, _mock_get_url, mock_post
    ):
        mock_post.return_value = Mock(status_code=204)

        status = DiscordManager().send_signal_updates(
            data=None,
            description="Order filled",
            fields={f"field-{index}": index for index in range(30)},
        )

        self.assertEqual(status, 204)
        payload = mock_post.call_args.kwargs["json"]
        embed_fields = payload["embeds"][0]["fields"]
        self.assertEqual(len(embed_fields), DiscordManager.MAX_FIELDS)
        self.assertEqual(embed_fields[-1]["name"], "⚠ Warning")

    @patch("orbit.core.discord_manager.requests.post")
    @patch("orbit.core.discord_manager.URLS.get_url", return_value="https://example.test/webhook")
    def test_exact_field_limit_does_not_add_warning(self, _mock_get_url, mock_post):
        mock_post.return_value = Mock(status_code=204)

        DiscordManager().send_signal_updates(
            data=None,
            description="Order filled",
            fields={f"field-{index}": index for index in range(25)},
        )

        payload = mock_post.call_args.kwargs["json"]
        embed_fields = payload["embeds"][0]["fields"]
        self.assertEqual(len(embed_fields), DiscordManager.MAX_FIELDS)
        self.assertNotEqual(embed_fields[-1]["name"], "⚠ Warning")

    @patch("orbit.core.discord_manager.requests.post")
    @patch("orbit.core.discord_manager.URLS.get_url", return_value="https://example.test/webhook")
    def test_truncated_value_at_field_limit_reserves_warning_field(
        self, _mock_get_url, mock_post
    ):
        mock_post.return_value = Mock(status_code=204)
        fields = {f"field-{index}": index for index in range(24)}
        fields["long-field"] = "x" * (DiscordManager.MAX_FIELD_VALUE + 1)

        DiscordManager().send_signal_updates(
            data=None, description="Order filled", fields=fields
        )

        payload = mock_post.call_args.kwargs["json"]
        embed_fields = payload["embeds"][0]["fields"]
        self.assertEqual(len(embed_fields), DiscordManager.MAX_FIELDS)
        self.assertEqual(embed_fields[-1]["name"], "⚠ Warning")
