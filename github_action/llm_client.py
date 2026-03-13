import os
import sys
from openai import OpenAI

def call_llm(prompt: str) -> str:
    client = OpenAI(
        base_url="https://models.github.ai/inference",
        api_key=os.environ["MODEL_TOKEN"],
    )

    response = client.chat.completions.create(
        model="openai/gpt-4o",
        messages=[
            {
                "role": "system",
                "content": "You are a senior software engineer assisting with GitHub automation."
            },
            {
                "role": "user",
                "content": prompt
            }
        ],
        temperature=0.2,
        max_tokens=1000,
    )

    return response.choices[0].message.content


if __name__ == "__main__":
    prompt = sys.stdin.read()
    result = call_llm(prompt)
    print(result)
