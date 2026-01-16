"""
Singleton model loaders for production pipeline.

Models are loaded once and reused across all pipeline steps.
"""

import os
from functools import lru_cache

import httpx
from loguru import logger

# =============================================================================
# CONFIGURATION
# =============================================================================

# GLiNER2 for entity and relation extraction
GLINER2_MODEL = "fastino/gliner2-base-v1"

# Sentence Transformer for embeddings
EMBEDDING_MODEL = "BAAI/bge-base-en-v1.5"

# OpenRouter LLM configuration
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# Request settings
LLM_TIMEOUT = 60.0
LLM_MAX_RETRIES = 3


# =============================================================================
# MODEL LOADERS (Singleton via lru_cache)
# =============================================================================


@lru_cache(maxsize=1)
def get_gliner():
    """
    Load GLiNER2 model (singleton).

    Used for:
    - Entity extraction (03_1)
    - Relation extraction (03_4)

    Returns:
        GLiNER2 model instance
    """
    from gliner2 import GLiNER2

    logger.info(f"Loading GLiNER2 model: {GLINER2_MODEL}")
    model = GLiNER2.from_pretrained(GLINER2_MODEL)
    logger.info("GLiNER2 model loaded")
    return model


@lru_cache(maxsize=1)
def get_embedder():
    """
    Load Sentence Transformer model (singleton).

    Used for:
    - Event embedding (04_2)
    - Similarity search for blocking (05_1)

    Returns:
        SentenceTransformer model instance
    """
    from sentence_transformers import SentenceTransformer

    logger.info(f"Loading embedding model: {EMBEDDING_MODEL}")
    model = SentenceTransformer(EMBEDDING_MODEL)
    logger.info("Embedding model loaded")
    return model


# =============================================================================
# LLM CLIENT
# =============================================================================


class LLMClient:
    """
    Client for OpenRouter API.

    Used for:
    - Entity normalization
    - Semantic extraction
    - Causal classification
    """

    def __init__(
        self,
        model: str,
        api_key: str | None = None,
        timeout: float = LLM_TIMEOUT,
    ):
        self.api_key = api_key or os.getenv("OPENROUTER_API_KEY")
        if not self.api_key:
            raise ValueError("OPENROUTER_API_KEY not set")

        self.model = model
        self.timeout = timeout
        self.base_url = OPENROUTER_BASE_URL

        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create async client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=self.timeout,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
            )
        return self._client

    async def complete(
        self,
        messages: list[dict],
        temperature: float = 0.1,
        max_tokens: int | None = None,
    ) -> str:
        """
        Send a chat completion request.

        Args:
            messages: List of message dicts with 'role' and 'content'
            temperature: Sampling temperature (0.0-1.0)
            max_tokens: Maximum tokens in response

        Returns:
            The assistant's response text
        """
        client = await self._get_client()

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
        }
        if max_tokens:
            payload["max_tokens"] = max_tokens

        for attempt in range(LLM_MAX_RETRIES):
            try:
                response = await client.post(
                    f"{self.base_url}/chat/completions",
                    json=payload,
                )
                response.raise_for_status()
                data = response.json()
                return data["choices"][0]["message"]["content"]

            except httpx.HTTPStatusError as e:
                if e.response.status_code == 429:
                    # Rate limited, wait and retry
                    import asyncio

                    wait_time = 2**attempt
                    logger.warning(f"Rate limited, waiting {wait_time}s...")
                    await asyncio.sleep(wait_time)
                    continue
                raise

            except httpx.RequestError:
                if attempt < LLM_MAX_RETRIES - 1:
                    import asyncio

                    await asyncio.sleep(1)
                    continue
                raise

        raise RuntimeError(f"Failed after {LLM_MAX_RETRIES} attempts")

    async def complete_batch(
        self,
        prompts: list[list[dict]],
        temperature: float = 0.1,
        max_concurrency: int = 5,
    ) -> list[str]:
        """
        Send multiple completion requests with controlled concurrency.

        Args:
            prompts: List of message lists
            temperature: Sampling temperature
            max_concurrency: Max concurrent requests

        Returns:
            List of response texts
        """
        import asyncio

        semaphore = asyncio.Semaphore(max_concurrency)

        async def limited_complete(messages: list[dict]) -> str:
            async with semaphore:
                return await self.complete(messages, temperature)

        tasks = [limited_complete(messages) for messages in prompts]
        return await asyncio.gather(*tasks)

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> "LLMClient":
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.close()


# Singleton instances (keyed by model)
_llm_clients: dict[str, LLMClient] = {}


def get_llm_client(model: str) -> LLMClient:
    """
    Get LLM client for the specified model.

    Args:
        model: Model identifier from OpenRouter (see https://openrouter.ai/models)

    Returns:
        LLMClient instance for the specified model
    """
    global _llm_clients

    if model not in _llm_clients:
        _llm_clients[model] = LLMClient(model=model)
        logger.debug(f"Created LLM client for model: {model}")

    return _llm_clients[model]


async def close_all_llm_clients() -> None:
    """Close all LLM client connections."""
    global _llm_clients
    for client in _llm_clients.values():
        await client.close()
    _llm_clients = {}


# =============================================================================
# UTILITY FUNCTIONS
# =============================================================================


def preload_models() -> None:
    """
    Preload all models at pipeline start.

    Call this at the beginning of the pipeline to load
    all models upfront rather than lazily.
    """
    logger.info("Preloading models...")
    get_gliner()
    get_embedder()
    # LLM client doesn't need preloading (no local model)
    logger.info("All models preloaded")


def clear_model_cache() -> None:
    """Clear model caches (for testing)."""
    get_gliner.cache_clear()
    get_embedder.cache_clear()
    global _llm_clients
    _llm_clients = {}
