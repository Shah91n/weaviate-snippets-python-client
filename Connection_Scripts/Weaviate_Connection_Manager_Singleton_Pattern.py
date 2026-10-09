"""
Weaviate Connection Manager - Singleton Pattern
===============================================

This module provides a singleton Weaviate client manager for consistent connection
management across the entire application.

WHY SINGLETON?
Each Weaviate client opens its own connections to the cluster: an HTTP connection
pool (httpx) for REST calls and a gRPC channel for queries and batch imports.
Creating multiple clients causes unnecessary connection setup/teardown overhead.
Weaviate recommends ONE long-lived client instance per application.

HOW CONCURRENCY WORKS:
- REST calls go through httpx's connection pool (up to 100 connections, 20 kept
  alive by default; tune via AdditionalConfig(connection=ConnectionConfig(...))).
- Queries and batch imports go over a single gRPC channel, which multiplexes
  concurrent requests over HTTP/2.

Usage:
    from weaviate_connection import get_weaviate_client

    # Get the singleton client (same instance every time)
    client = get_weaviate_client()

    # Use for queries (sent over gRPC)
    result = client.collections.use("my_collection").query.fetch_objects()

    # Use for inserts (same client, same connections)
    client.collections.use("my_collection").data.insert({"name": "example"})

    # For async operations
    from weaviate_connection import get_async_weaviate_client, close_async_weaviate_connection

    async def some_async_function():
        client = await get_async_weaviate_client()
        result = await client.collections.use("my_collection").query.fetch_objects()

    # At async application shutdown (e.g. FastAPI lifespan):
    #     await close_async_weaviate_connection()

Best Practices (per Weaviate docs):
    - Initialize once at application startup
    - Reuse the same client instance for all requests
    - Only close at application shutdown
    - DO NOT create new clients per request (wasteful connection overhead)
    - DO NOT call client.close() after each operation
    - The async client is bound to the event loop it was created in; create and
      close it inside the same loop
"""
import os
import logging
import atexit
import asyncio
import threading
import weaviate
from weaviate.classes.init import Auth, AdditionalConfig, Timeout
from dotenv import load_dotenv

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class WeaviateConnectionManager:
    """
    Singleton manager that maintains ONE long-lived Weaviate client connection.

    Architecture:
    - One client instance per application lifetime
    - httpx connection pool for REST calls, one gRPC channel for queries and batch imports
    - The sync client is created on first use; the async client is created on first use

    RECOMMENDATION: Use get_weaviate_client() module-level function instead of
    directly instantiating this class.
    """

    def __init__(self):
        """Load configuration. Clients are created lazily on first use."""
        load_dotenv()

        self._cluster_url = os.getenv("CLUSTER_URL")
        self._api_key = os.getenv("API_KEY")
        self._openai_api_key = os.getenv("OPENAI_API_KEY")

        if not self._cluster_url:
            raise RuntimeError("CLUSTER_URL must be set in environment variables")

        if not self._api_key:
            raise RuntimeError("API_KEY must be set in environment variables")

        # Prepare headers
        self._headers = {}
        if self._openai_api_key:
            self._headers["X-OpenAI-Api-Key"] = self._openai_api_key

        # Configure timeouts
        self._weaviate_timeout = Timeout(init=60, query=240, insert=240)

        # Clients are created lazily so async-only apps don't open a sync client
        self._sync_client = None
        self._sync_lock = threading.Lock()
        self._async_client = None
        self._async_lock = asyncio.Lock()

        # Register cleanup of the sync client on application shutdown
        atexit.register(self.close)

    def _create_sync_client(self):
        """Create a single long-lived synchronous Weaviate client."""
        try:
            client = weaviate.connect_to_weaviate_cloud(
                cluster_url=self._cluster_url,
                auth_credentials=Auth.api_key(self._api_key),
                headers=self._headers or None,
                additional_config=AdditionalConfig(timeout=self._weaviate_timeout)
            )
            logger.info(f"Connected to Weaviate cluster: {self._cluster_url}")
            return client
        except Exception as e:
            logger.error(f"Failed to create Weaviate client: {e}")
            raise

    async def _create_async_client(self):
        """Create a single long-lived asynchronous Weaviate client."""
        try:
            client = weaviate.use_async_with_weaviate_cloud(
                cluster_url=self._cluster_url,
                auth_credentials=Auth.api_key(self._api_key),
                headers=self._headers or None,
                additional_config=AdditionalConfig(timeout=self._weaviate_timeout),
            )
            await client.connect()
            logger.info(f"Connected to Weaviate cluster (async): {self._cluster_url}")
            return client
        except Exception as e:
            logger.error(f"Failed to create async Weaviate client: {e}")
            raise

    @property
    def client(self):
        """
        Return the singleton synchronous Weaviate client.

        This is the same client instance every time - DO NOT close it after use.

        Returns:
            WeaviateClient: The long-lived client instance
        """
        if self._sync_client is None:
            with self._sync_lock:
                if self._sync_client is None:
                    self._sync_client = self._create_sync_client()
        return self._sync_client

    async def get_async_client(self):
        """
        Return the singleton asynchronous Weaviate client.

        Lazily initializes the async client on first call. The same instance
        is returned on subsequent calls.

        Returns:
            WeaviateAsyncClient: The long-lived async client instance
        """
        if self._async_client is None:
            async with self._async_lock:
                if self._async_client is None:
                    self._async_client = await self._create_async_client()
        return self._async_client

    def is_ready(self) -> bool:
        """
        Check if Weaviate is ready to accept requests.

        Returns:
            bool: True if Weaviate is ready, False otherwise
        """
        try:
            return self.client.is_ready()
        except Exception as e:
            logger.error(f"Health check failed: {e}")
            return False

    def close(self):
        """
        Close the synchronous Weaviate client.

        This should ONLY be called during application shutdown.
        DO NOT call this after individual requests.
        The async client must be closed with close_async() inside its event loop.
        """
        try:
            if self._sync_client:
                self._sync_client.close()
                logger.info("Weaviate synchronous client closed")
                self._sync_client = None
        except Exception as e:
            logger.error(f"Error closing Weaviate sync client: {e}")

    async def close_async(self):
        """
        Close the asynchronous Weaviate client.

        Call this at async application shutdown, inside the same event loop
        the client was created in.
        """
        try:
            if self._async_client:
                await self._async_client.close()
                logger.info("Weaviate asynchronous client closed")
                self._async_client = None
        except Exception as e:
            logger.error(f"Error closing Weaviate async client: {e}")

# Singleton instance (guarded by a lock so concurrent threads create only one)
_manager_instance = None
_manager_lock = threading.Lock()

def get_weaviate_manager() -> WeaviateConnectionManager:
    """
    Get the singleton WeaviateConnectionManager instance.

    Use this if you need access to manager methods like is_ready() or close().
    For normal operations, use get_weaviate_client() instead.

    Returns:
        WeaviateConnectionManager: The singleton manager instance
    """
    global _manager_instance
    if _manager_instance is None:
        with _manager_lock:
            if _manager_instance is None:
                _manager_instance = WeaviateConnectionManager()
    return _manager_instance

def get_weaviate_client():
    """
    Get the singleton Weaviate client instance.

    This returns the SAME client instance every time. Use this client directly
    for all operations - do not close it after use.

    Example:
        client = get_weaviate_client()
        result = client.collections.use("products").query.fetch_objects()
        # Client remains open for next request

    Returns:
        WeaviateClient: The singleton client instance
    """
    return get_weaviate_manager().client

async def get_async_weaviate_client():
    """
    Get the singleton async Weaviate client instance.

    This returns the SAME async client every time. Use this client directly
    for all async operations - do not close it after use.

    Lazy initialization: The async client is only created on first call.
    Subsequent calls return the same instance.

    Example:
        client = await get_async_weaviate_client()
        result = await client.collections.use("products").query.fetch_objects()
        # Client remains open for next request

    Returns:
        WeaviateAsyncClient: The singleton async client instance
    """
    return await get_weaviate_manager().get_async_client()

def close_weaviate_connection():
    """
    Close the synchronous Weaviate connection.

    This should ONLY be called during application shutdown.
    The sync client is automatically closed via atexit, so manual
    calls are typically unnecessary.
    """
    if _manager_instance:
        _manager_instance.close()

async def close_async_weaviate_connection():
    """
    Close the asynchronous Weaviate connection.

    Call this at async application shutdown (e.g. FastAPI lifespan),
    inside the same event loop the client was created in.
    """
    if _manager_instance:
        await _manager_instance.close_async()

# Example usage
if __name__ == "__main__":
    # Example 1: Simple synchronous usage
    print("Example 1: Basic synchronous usage")
    client = get_weaviate_client()
    try:
        print("Client obtained successfully")
        # result = client.collections.use("YourCollection").query.fetch_objects()
    except Exception as e:
        print(f"Error: {e}")

    # Example 2: Health check via manager
    print("\nExample 2: Health check via manager")
    manager = get_weaviate_manager()
    is_ready = manager.is_ready()
    print(f"Weaviate is ready: {is_ready}")

    # Example 3: Multiple requests (reuses same client)
    print("\nExample 3: Multiple requests with same client")
    client1 = get_weaviate_client()
    client2 = get_weaviate_client()
    print(f"client1 is client2: {client1 is client2}")  # True - same instance
    print("Multiple calls return the SAME client instance - no connection overhead")

    # Example 4: Async usage (create and close inside the same event loop)
    print("\nExample 4: Async usage")
    async def async_example():
        client = await get_async_weaviate_client()
        try:
            print("Async client obtained successfully")
            # result = await client.collections.use("YourCollection").query.fetch_objects()
        except Exception as e:
            print(f"Error: {e}")
        finally:
            await close_async_weaviate_connection()

    asyncio.run(async_example())

    # Cleanup (automatic via atexit, but shown here for demonstration)
    print("\nClosing connections...")
    close_weaviate_connection()
