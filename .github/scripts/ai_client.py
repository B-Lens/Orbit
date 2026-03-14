import os
from openai import OpenAI


class AIClient:
    def __init__(self, model="o3-mini"):
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY not set")

        self.client = OpenAI(api_key=api_key)
        self.model = model

    def chat(self, system_prompt: str, user_prompt: str) -> str:
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )

        return response.choices[0].message.content.strip()


class GitHubModelClient:
    def __init__(self, model="openai/gpt-5"):
        api_key = os.environ.get("MODEL_TOKEN")
        if not api_key:
            raise RuntimeError("GITHUB MODEL_TOKEN not set")

        self.client = OpenAI(
            base_url="https://models.github.ai/inference",
            api_key=api_key,
        )

        self.model = model

    def chat(self, system_prompt: str, user_prompt: str) -> str:
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=1.0,
            max_tokens=1000,
            top_p=1.0,
        )

        return response.choices[0].message.content.strip()
