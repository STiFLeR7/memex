import logging
from typing import Any

logger = logging.getLogger(__name__)

try:
    import time
    import threading
    import asyncio
    import functools
    import re
    from google.genai import models

    _last_request_time: float = 0.0
    _lock: threading.Lock = threading.Lock()
    COOLDOWN_DELAY: float = 4.5  # 15 RPM safe limit

    def _sync_cooldown() -> None:
        global _last_request_time
        with _lock:
            now = time.time()
            elapsed = now - _last_request_time
            if elapsed < COOLDOWN_DELAY:
                sleep_time = COOLDOWN_DELAY - elapsed
                time.sleep(sleep_time)
            _last_request_time = time.time()

    async def _async_cooldown() -> None:
        global _last_request_time
        with _lock:
            now = time.time()
            elapsed = now - _last_request_time
            if elapsed < COOLDOWN_DELAY:
                sleep_time = COOLDOWN_DELAY - elapsed
                _last_request_time = now + sleep_time
            else:
                sleep_time = 0.0
                _last_request_time = now
                
        if sleep_time > 0:
            await asyncio.sleep(sleep_time)

    # Monkey-patch Models.generate_content
    orig_generate = models.Models.generate_content
    @functools.wraps(orig_generate)
    def wrapped_generate(*args: Any, **kwargs: Any) -> Any:
        for attempt in range(5):
            _sync_cooldown()
            try:
                return orig_generate(*args, **kwargs)
            except Exception as e:
                err_str = str(e).lower()
                if "429" in err_str or "rate limit" in err_str or "resource_exhausted" in err_str:
                    sleep_time = 60.0
                    match = re.search(r"retry in (\d+\.?\d*)s", err_str)
                    if match:
                        sleep_time = float(match.group(1)) + 1.0
                    logger.warning("Gemini API rate-limited (429). Retrying in %.2fs...", sleep_time)
                    time.sleep(sleep_time)
                    continue
                raise
        return orig_generate(*args, **kwargs)
    models.Models.generate_content = wrapped_generate

    # Monkey-patch Models.embed_content
    orig_embed = models.Models.embed_content
    @functools.wraps(orig_embed)
    def wrapped_embed(*args: Any, **kwargs: Any) -> Any:
        for attempt in range(5):
            _sync_cooldown()
            try:
                return orig_embed(*args, **kwargs)
            except Exception as e:
                err_str = str(e).lower()
                if "429" in err_str or "rate limit" in err_str or "resource_exhausted" in err_str:
                    sleep_time = 60.0
                    match = re.search(r"retry in (\d+\.?\d*)s", err_str)
                    if match:
                        sleep_time = float(match.group(1)) + 1.0
                    logger.warning("Gemini API rate-limited (429). Retrying in %.2fs...", sleep_time)
                    time.sleep(sleep_time)
                    continue
                raise
        return orig_embed(*args, **kwargs)
    models.Models.embed_content = wrapped_embed

    # Monkey-patch AsyncModels.generate_content
    orig_async_generate = models.AsyncModels.generate_content
    @functools.wraps(orig_async_generate)
    async def wrapped_async_generate(*args: Any, **kwargs: Any) -> Any:
        for attempt in range(5):
            await _async_cooldown()
            try:
                return await orig_async_generate(*args, **kwargs)
            except Exception as e:
                err_str = str(e).lower()
                if "429" in err_str or "rate limit" in err_str or "resource_exhausted" in err_str:
                    sleep_time = 60.0
                    match = re.search(r"retry in (\d+\.?\d*)s", err_str)
                    if match:
                        sleep_time = float(match.group(1)) + 1.0
                    logger.warning("Gemini API rate-limited (429). Retrying in %.2fs...", sleep_time)
                    await asyncio.sleep(sleep_time)
                    continue
                raise
        return await orig_async_generate(*args, **kwargs)
    models.AsyncModels.generate_content = wrapped_async_generate

    # Monkey-patch AsyncModels.embed_content
    orig_async_embed = models.AsyncModels.embed_content
    @functools.wraps(orig_async_embed)
    async def wrapped_async_embed(*args: Any, **kwargs: Any) -> Any:
        for attempt in range(5):
            await _async_cooldown()
            try:
                return await orig_async_embed(*args, **kwargs)
            except Exception as e:
                err_str = str(e).lower()
                if "429" in err_str or "rate limit" in err_str or "resource_exhausted" in err_str:
                    sleep_time = 60.0
                    match = re.search(r"retry in (\d+\.?\d*)s", err_str)
                    if match:
                        sleep_time = float(match.group(1)) + 1.0
                    logger.warning("Gemini API rate-limited (429). Retrying in %.2fs...", sleep_time)
                    await asyncio.sleep(sleep_time)
                    continue
                raise
        return await orig_async_embed(*args, **kwargs)
    models.AsyncModels.embed_content = wrapped_async_embed

    logger.info("Successfully applied local Gemini API rate-limiting and auto-retry wrapper.")

except Exception as e:
    logger.warning("Could not apply local Gemini API rate limiter monkey-patch: %s", e)
