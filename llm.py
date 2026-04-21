"""
Azure OpenAI chat completion client.
"""

import json
import os
import time

import requests
from dotenv import load_dotenv

load_dotenv()

AZURE_ENDPOINT = os.getenv("AZURE_ENDPOINT")
AZURE_API_KEY = os.getenv("AZURE_API_KEY")
AZURE_DEPLOYMENT = os.getenv("AZURE_DEPLOYMENT", "gpt-4.1")
AZURE_API_VERSION = os.getenv("AZURE_API_VERSION", "2024-12-01-preview")

_headers = {
    "api-key": AZURE_API_KEY,
    "Content-Type": "application/json",
}

_config = {
    "temperature": float(os.getenv("LLM_TEMPERATURE", "0")),
    "top_p": float(os.getenv("LLM_TOP_P", "1.0")),
    "presence_penalty": float(os.getenv("LLM_PRESENCE_PENALTY", "0.2")),
    "max_tokens": int(os.getenv("LLM_MAX_TOKENS", "2000")),
}


def call_llm(
    prompt: str,
    system: str = None,
    max_retries: int = 3,
    wait: int = 10,
    backoff_factor: int = 2,
) -> str:
    """
    Call Azure OpenAI chat completions with retry logic.
    Returns the model's response content or raises on persistent failure.
    """
    url = (
        f"{AZURE_ENDPOINT.rstrip('/')}/openai/deployments/{AZURE_DEPLOYMENT}"
        f"/chat/completions?api-version={AZURE_API_VERSION}"
    )

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    payload = {
        "messages": messages,
        **_config,
    }

    time.sleep(1)  # throttle
    current_wait = wait

    for attempt in range(max_retries):
        try:
            response = requests.post(url, headers=_headers, data=json.dumps(payload), timeout=120)
            if response.status_code == 200:
                content = response.json()["choices"][0]["message"].get("content", "")
                return content.strip()
            elif response.status_code == 400:
                print(f"Bad request (not retrying): {response.text[:200]}")
                return "error"
            else:
                print(f"Request failed ({response.status_code}): {response.text[:200]}")
        except Exception as e:
            print(f"Exception: {e}")

        if attempt < max_retries - 1:
            print(f"Retrying in {current_wait}s... ({attempt + 2}/{max_retries})")
            time.sleep(current_wait)
            current_wait *= backoff_factor

    raise RuntimeError("LLM call failed after max retries.")
