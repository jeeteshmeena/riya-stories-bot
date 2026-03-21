import os
import requests
import asyncio
from typing import Optional

def _call_groq(prompt: str, user_text: str) -> Optional[str]:
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        return None
        
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "llama3-8b-8192",
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": user_text}
        ],
        "temperature": 0.3,
        "max_tokens": 500
    }
    
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=15)
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        return None

async def clean_description(text: str) -> str:
    prompt = "Clean the following story description by removing links, ads, extra symbols, and noise. Do NOT rewrite or summarize. Keep original meaning and wording as much as possible."
    result = await asyncio.to_thread(_call_groq, prompt, text)
    return result if result is not None else text

async def shorten_description(text: str) -> str:
    prompt = "Create a short version of this story description in 2–3 lines. Keep meaning intact. Do not add extra information."
    result = await asyncio.to_thread(_call_groq, prompt, text)
    return result if result is not None else text
