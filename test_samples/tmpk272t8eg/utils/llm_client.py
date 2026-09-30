import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env", override=True)


def get_llm_key() -> str:
    return os.getenv("GROQ_API_KEY", "")


def has_llm_key() -> bool:
    key = get_llm_key()
    return bool(key and key.strip() and key not in ("", "your-groq-api-key-here"))


def call_llm(prompt: str, max_tokens: int = 2000, temperature: float = 0.2) -> str:
    from groq import Groq
    from src.config import Config

    client = Groq(api_key=get_llm_key())
    response = client.chat.completions.create(
        model=Config.GROQ_MODEL,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=max_tokens,
        temperature=temperature,
    )
    text = response.choices[0].message.content
    if not text:
        raise RuntimeError("Groq returned empty response")
    return text
