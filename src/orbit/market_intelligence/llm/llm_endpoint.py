
# Initialize LLM with fallback

import logging
from venv import logger
from typing import Optional
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_groq import ChatGroq
import os
import logging
from orbit.utils.utils import require_env
from dotenv import load_dotenv

load_dotenv()  # Load environment variables from .env file

os.environ["GROQ_API_KEY"] = require_env("GROQ_API_KEY")
GROQ_MODEL = "openai/gpt-oss-120b"

logger = logging.getLogger("Orbit")

class LLM:
    def __init__(self) -> Optional[BaseChatModel]:
        """Initialize LLM with fallback options."""
        try:
            llm = ChatGroq(model=GROQ_MODEL, temperature=0, timeout=30)
            # Test the model
            test_response = llm.invoke("Test")
            print(f"test_response: {test_response}")
            logger.info("Groq model initialized successfully")
            self.llm = llm
        except Exception as e:
            logger.error(f"Failed to initialize Groq model: {e}")
            # Try alternative models
            alternative_models = ["llama-3.1-8b-instant", "gemma2-9b-it"]
            for alt_model in alternative_models:
                try:
                    logger.info(f"Trying alternative model: {alt_model}")
                    llm = ChatGroq(model=alt_model, temperature=0, timeout=30)
                    test_response = llm.invoke("Test")
                    logger.info(f"Alternative model {alt_model} initialized successfully")
                    return llm
                except Exception as alt_e:
                    logger.error(f"Failed to initialize {alt_model}: {alt_e}")
                    continue
            
            # If all Groq models fail, return None
            logger.error("All Groq models failed to initialize")
            return None
        
    def invoke(self, prompt: str) -> Optional[str]:
        """Invoke the LLM with a prompt."""
        if not self.llm:
            logger.error("LLM is not initialized")
            return None
        
        # Save Prompt token length for monitoring
        prompt_token_length = len(prompt.split())
        logger.info(f"Invoking LLM with prompt token length: {prompt_token_length}")
        
        try:
            response = self.llm.invoke(prompt)
            return response.content
        except Exception as e:
            logger.error(f"LLM invocation failed: {e}")
            return None