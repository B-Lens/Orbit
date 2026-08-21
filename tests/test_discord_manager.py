from unittest.mock import Mock, patch

from orbit.core.discord_manager import DiscordManager


def _sent_payload(fields=None, description="description", data=""):
    response = Mock(status_code=204, text="")
    with (
        patch("orbit.core.discord_manager.URLS.get_url", return_value="https://example.test/webhook"),
        patch("orbit.core.discord_manager.requests.post", return_value=response) as post,
    ):
        DiscordManager().send_to_webhook("signal", data, description, fields)
    return post.call_args.kwargs["json"]


def test_truncation_warning_does_not_exceed_discord_field_limit():
    payload = _sent_payload({f"field-{index}": index for index in range(26)})

    fields = payload["embeds"][0]["fields"]
    assert len(fields) == DiscordManager.MAX_FIELDS
    assert fields[-1]["name"] == "⚠ Warning"


def test_empty_field_properties_are_made_valid():
    payload = _sent_payload({"": ""})

    field = payload["embeds"][0]["fields"][0]
    assert field["name"] == "(unnamed)"
    assert field["value"] == "(empty)"


def test_content_only_payload_omits_empty_embed():
    payload = _sent_payload(description="", data="message")

    assert payload == {"content": "message"}


def test_combined_embed_text_stays_within_discord_limit():
    payload = _sent_payload({str(index): "x" * 1024 for index in range(10)}, "y" * 4096)

    embed = payload["embeds"][0]
    total = len(embed["title"]) + len(embed["description"])
    total += sum(len(field["name"]) + len(field["value"]) for field in embed["fields"])
    assert total <= DiscordManager.MAX_EMBED_TEXT
