from __future__ import annotations

import asyncio
import logging
from typing import Any
from urllib.parse import quote

import aiohttp


logger = logging.getLogger(__name__)

# Unofficial Google Translate endpoint. If it changes, replace this template.
TRANSLATE_URL = (
    "https://translate.googleapis.com/translate_a/single"
    "?client=gtx&sl=auto&tl=ru&dt=t&q={text}"
)

_cache: dict[str, str] = {}


async def translate_to_ru(text: str) -> str:
    clean = (text or "").strip()
    if not clean:
        return ""
    if _looks_russian(clean):
        return clean
    if clean in _cache:
        return _cache[clean]

    try:
        timeout = aiohttp.ClientTimeout(total=3)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(TRANSLATE_URL.format(text=quote(clean))) as response:
                if response.status >= 400:
                    logger.warning("Translate endpoint returned HTTP %s", response.status)
                    return clean
                data = await response.json(content_type=None)
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
        logger.warning("Translate failed: %r", exc)
        return clean

    translated = _parse_google_translate_response(data)
    if translated:
        _cache[clean] = translated
        return translated
    return clean


def _parse_google_translate_response(data: Any) -> str:
    if not isinstance(data, list) or not data:
        return ""
    chunks = data[0]
    if not isinstance(chunks, list):
        return ""
    parts = []
    for chunk in chunks:
        if isinstance(chunk, list) and chunk and isinstance(chunk[0], str):
            parts.append(chunk[0])
    return "".join(parts).strip()


def _looks_russian(text: str) -> bool:
    return any("а" <= char.lower() <= "я" or char.lower() == "ё" for char in text)
