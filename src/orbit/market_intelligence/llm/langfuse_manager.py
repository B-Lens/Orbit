import os
import logging
from typing import Any, Dict, Optional

from langfuse import Langfuse

logger = logging.getLogger("Orbit")


class LangfuseManager:
    """
    Thin wrapper around the Langfuse client for prompt versioning.
    Reads configuration from environment:

    - ``LANGFUSE_PUBLIC_KEY``
    - ``LANGFUSE_SECRET_KEY``
    - ``LANGFUSE_BASE_URL`` (defaults to ``https://cloud.langfuse.com``)
    """

    def __init__(self) -> None:
        public_key = os.environ.get("LANGFUSE_PUBLIC_KEY", "")
        secret_key = os.environ.get("LANGFUSE_SECRET_KEY", "")
        host = os.environ.get("LANGFUSE_BASE_URL", "https://cloud.langfuse.com")

        if not public_key or not secret_key:
            logger.warning(
                "Langfuse credentials not set — prompt versioning disabled."
            )
            self.client = None
            return

        self.client = Langfuse(
            public_key=public_key,
            secret_key=secret_key,
            host=host,
        )
        logger.info("Langfuse client initialized (host=%s)", host)

    # ------------------------------------------------------------------
    # Prompt retrieval helpers
    # ------------------------------------------------------------------

    def get_prompt_client(
        self, name: str, version: Optional[int] = None
    ):
        """Return a Langfuse prompt object (TextPromptClient or ChatPromptClient)."""
        if self.client is None:
            raise RuntimeError(
                "Langfuse client not available — check LANGFUSE_* environment variables."
            )
        return self.client.get_prompt(name, version=version)

    def compile_prompt(
        self,
        name: str,
        context: Dict[str, Any],
        version: Optional[int] = None,
    ) -> str:
        """
        Fetch a prompt by *name* and return its compiled text after filling in
        ``context`` as template variables.

        Raises ``RuntimeError`` when the client is not configured.
        """
        prompt = self.get_prompt_client(name, version=version)
        return prompt.compile(**context)
