"""Google Antigravity client used as a Codex sentiment-analysis fallback."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import logging
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Callable, Optional, cast
import urllib.error
import urllib.parse
import urllib.request
import uuid

DEFAULT_BACKEND_URL = (
    "https://daily-cloudcode-pa.googleapis.com/v1internal:generateContent"
)
DEFAULT_TOKEN_URL = "https://oauth2.googleapis.com/token"
DEFAULT_MODEL = "gemini-3.7-flash-tiered"
DEFAULT_USER_AGENT = "antigravity/1.1.22 (orbit; direct-backend)"

logger = logging.getLogger("Orbit")


def default_token_file() -> Path:
    """Return the Antigravity CLI OAuth credential location."""
    configured = os.getenv("ANTIGRAVITY_TOKEN_FILE")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".gemini/antigravity-cli/antigravity-oauth-token"


def default_project_file() -> Path:
    """Return the Antigravity CLI cached project location."""
    configured = os.getenv("ANTIGRAVITY_PROJECT_FILE")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".gemini/antigravity-cli/cache/default_project_id.txt"


class AntigravityClient:
    """Invoke Antigravity directly using a provisioned CLI OAuth credential."""

    def __init__(
        self,
        token_file: Optional[os.PathLike[str] | str] = None,
        project_file: Optional[os.PathLike[str] | str] = None,
        project: Optional[str] = None,
        oauth_client_id: Optional[str] = None,
        oauth_client_secret: Optional[str] = None,
        model: Optional[str] = None,
        timeout: Optional[float] = None,
        backend_url: Optional[str] = None,
        token_url: Optional[str] = None,
        urlopen: Callable[..., Any] = urllib.request.urlopen,
    ) -> None:
        self.token_file = (
            Path(token_file).expanduser() if token_file else default_token_file()
        )
        self.project_file = (
            Path(project_file).expanduser() if project_file else default_project_file()
        )
        self.project = project or os.getenv("ANTIGRAVITY_PROJECT")
        self.oauth_client_id = oauth_client_id or os.getenv(
            "ANTIGRAVITY_OAUTH_CLIENT_ID"
        )
        self.oauth_client_secret = oauth_client_secret or os.getenv(
            "ANTIGRAVITY_OAUTH_CLIENT_SECRET"
        )
        self.model = model or os.getenv("ANTIGRAVITY_MODEL", DEFAULT_MODEL)
        self.timeout = timeout or float(os.getenv("ANTIGRAVITY_TIMEOUT", "120"))
        if self.timeout <= 0:
            raise ValueError("ANTIGRAVITY_TIMEOUT must be positive")
        self.backend_url = (
            backend_url or os.getenv("ANTIGRAVITY_BACKEND_URL") or DEFAULT_BACKEND_URL
        )
        self.token_url = (
            token_url or os.getenv("ANTIGRAVITY_TOKEN_URL") or DEFAULT_TOKEN_URL
        )
        self._urlopen = urlopen

    def invoke(self, prompt: str) -> str:
        """Generate a response without enabling Google Search."""
        return self._invoke(prompt, web_search=False)

    def invoke_web_search(self, prompt: str) -> str:
        """Generate a response grounded with Google Search."""
        return self._invoke(prompt, web_search=True)

    def validate_configuration(self) -> None:
        """Fail early when the provisioned fallback cannot serve requests."""
        credential = self._load_credential()
        self._load_project()
        token = credential["token"]
        expires = self._parse_expiry(token.get("expiry"))
        if expires > datetime.now(timezone.utc) + timedelta(minutes=5):
            return
        if not token.get("refresh_token"):
            raise RuntimeError(
                "Expired Antigravity OAuth credential has no refresh token"
            )
        if not self.oauth_client_id or not self.oauth_client_secret:
            raise RuntimeError(
                "Expired Antigravity credential requires "
                "ANTIGRAVITY_OAUTH_CLIENT_ID and ANTIGRAVITY_OAUTH_CLIENT_SECRET"
            )

    def _invoke(self, prompt: str, web_search: bool) -> str:
        if not prompt or not prompt.strip():
            raise ValueError("prompt must not be empty")

        credential = self._load_credential()
        request_payload: dict[str, Any] = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}]
        }
        if web_search:
            request_payload["tools"] = [{"googleSearch": {}}]
        payload = {
            "model": self.model,
            "project": self._load_project(),
            "user_prompt_id": str(uuid.uuid4()),
            "request": request_payload,
        }
        request = urllib.request.Request(
            self.backend_url,
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={
                "Authorization": f"Bearer {self._access_token(credential)}",
                "Content-Type": "application/json",
                "User-Agent": DEFAULT_USER_AGENT,
            },
        )
        try:
            with self._urlopen(request, timeout=self.timeout) as response:
                result = cast(dict[str, Any], json.load(response))
        except urllib.error.HTTPError as error:
            raise RuntimeError(f"Antigravity HTTP {error.code}") from error
        except urllib.error.URLError as error:
            raise RuntimeError(f"Antigravity request failed: {error.reason}") from error
        return self._extract_text(result)

    def _load_credential(self) -> dict[str, Any]:
        try:
            loaded = json.loads(self.token_file.read_text(encoding="utf-8"))
            if not isinstance(loaded, dict):
                raise TypeError("credential root is not an object")
            credential = cast(dict[str, Any], loaded)
            token = credential["token"]
        except (OSError, json.JSONDecodeError, KeyError, TypeError) as error:
            raise RuntimeError(
                f"Could not read Antigravity OAuth credential: {self.token_file}"
            ) from error
        if not isinstance(token, dict) or not token.get("access_token"):
            raise RuntimeError("Antigravity OAuth credential has no access token")
        return credential

    def _load_project(self) -> str:
        if self.project:
            return self.project
        try:
            project = self.project_file.read_text(encoding="utf-8").strip()
        except OSError as error:
            raise RuntimeError(
                f"Could not read Antigravity project: {self.project_file}"
            ) from error
        if not project:
            raise RuntimeError("Antigravity project is empty")
        return project

    def _access_token(self, credential: dict[str, Any]) -> str:
        token = credential["token"]
        if self._parse_expiry(token.get("expiry")) > datetime.now(
            timezone.utc
        ) + timedelta(minutes=5):
            return str(token["access_token"])
        return self._refresh_access_token(credential)

    @staticmethod
    def _parse_expiry(value: object) -> datetime:
        if not isinstance(value, str) or not value:
            return datetime.min.replace(tzinfo=timezone.utc)
        # Go may emit nanoseconds, while Python supports microseconds. Only
        # truncate the fractional component; timezone-offset digits are not
        # part of the fraction.
        value = re.sub(r"(\.\d{6})\d+(?=Z|[+-]\d{2}:\d{2}$)", r"\1", value)
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(
                timezone.utc
            )
        except ValueError:
            return datetime.min.replace(tzinfo=timezone.utc)

    def _refresh_access_token(self, credential: dict[str, Any]) -> str:
        if not self.oauth_client_id or not self.oauth_client_secret:
            raise RuntimeError(
                "Expired Antigravity credential requires "
                "ANTIGRAVITY_OAUTH_CLIENT_ID and ANTIGRAVITY_OAUTH_CLIENT_SECRET"
            )
        token = credential["token"]
        refresh_token = token.get("refresh_token")
        if not refresh_token:
            raise RuntimeError("Antigravity OAuth credential has no refresh token")
        form = urllib.parse.urlencode(
            {
                "client_id": self.oauth_client_id,
                "client_secret": self.oauth_client_secret,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            self.token_url,
            data=form,
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        try:
            with self._urlopen(request, timeout=self.timeout) as response:
                refreshed = json.load(response)
        except urllib.error.HTTPError as error:
            raise RuntimeError(
                f"Antigravity OAuth refresh failed with HTTP {error.code}"
            ) from error
        except urllib.error.URLError as error:
            raise RuntimeError(
                f"Antigravity OAuth refresh failed: {error.reason}"
            ) from error
        token.update(refreshed)
        token["expiry"] = (
            datetime.now(timezone.utc)
            + timedelta(seconds=int(refreshed.get("expires_in", 3600)))
        ).isoformat()
        try:
            self._save_credential(credential)
        except OSError as error:
            # Mounted secret paths are commonly read-only. The fresh token is
            # still valid for this process even when it cannot be persisted.
            logger.warning(
                "Could not persist refreshed Antigravity credential at %s: %s",
                self.token_file,
                type(error).__name__,
            )
        return str(token["access_token"])

    def _save_credential(self, credential: dict[str, Any]) -> None:
        """Atomically persist a refreshed owner-only OAuth credential."""
        self.token_file.parent.mkdir(parents=True, exist_ok=True)
        temporary_name: Optional[str] = None
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=self.token_file.parent,
                prefix=f".{self.token_file.name}.",
                delete=False,
            ) as temporary:
                temporary_name = temporary.name
                json.dump(credential, temporary, separators=(",", ":"))
                temporary.write("\n")
                temporary.flush()
                os.fsync(temporary.fileno())
            os.chmod(temporary_name, 0o600)
            os.replace(temporary_name, self.token_file)
        finally:
            if temporary_name and os.path.exists(temporary_name):
                os.unlink(temporary_name)

    @staticmethod
    def _extract_text(result: dict[str, Any]) -> str:
        try:
            parts = result["response"]["candidates"][0]["content"]["parts"]
        except (KeyError, IndexError, TypeError) as error:
            raise RuntimeError("Antigravity returned no response candidate") from error
        text = "".join(
            str(part.get("text", ""))
            for part in parts
            if isinstance(part, dict) and not part.get("thought")
        ).strip()
        if not text:
            raise RuntimeError("Antigravity returned an empty response")
        return text
