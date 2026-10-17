"""
Base provider interface and data classes for the plugin-based provider system.

This module defines the abstract base class that all providers must implement,
along with standardized data structures for requests and responses.
"""

from abc import ABC, abstractmethod
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Iterator, List, Literal, Optional, Union, overload

from app.runtime.http_client import HttpPolicy, PooledHttpClient


# The job a provider call runs for (WP-7.2), so a cancel aimed at one job
# cannot interrupt another job's turn on a shared provider.
_TURN_OWNER: ContextVar[Optional[str]] = ContextVar("omnix_provider_turn_owner", default=None)


@contextmanager
def provider_turn_owner(owner: Optional[str]) -> Iterator[None]:
    token = _TURN_OWNER.set(owner)
    try:
        yield
    finally:
        _TURN_OWNER.reset(token)


def current_turn_owner() -> Optional[str]:
    return _TURN_OWNER.get()


class ProviderCapability(Enum):
    """Capabilities that a provider may support."""
    CHAT = "chat"
    STREAMING = "streaming"
    MODELS = "models"
    TOOL_CALLING = "tool_calling"
    FUNCTION_CALLING = "function_calling"
    IMAGE_GENERATION = "image_generation"
    EMBEDDINGS = "embeddings"


@dataclass
class ChatMessage:
    """Standardized chat message structure."""
    role: str  # 'system', 'user', 'assistant'
    content: str
    name: Optional[str] = None
    tool_calls: Optional[List[Dict[str, Any]]] = None
    tool_call_id: Optional[str] = None
    vision_images: Optional[List[Dict[str, Any]]] = None  # For vision models
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary representation."""
        result = {"role": self.role, "content": self.content}
        if self.name:
            result["name"] = self.name
        if self.tool_calls:
            result["tool_calls"] = self.tool_calls
        if self.tool_call_id:
            result["tool_call_id"] = self.tool_call_id
        
        # Handle vision images for multi-modal content
        if self.vision_images:
            # Convert to OpenAI vision format
            vision_parts = []
            for img in self.vision_images:
                vision_parts.append({
                    "type": "image_url",
                    "image_url": {
                        "url": img.get('data', '')
                    }
                })
            # Add text part if there's content
            if self.content:
                vision_parts.insert(0, {
                    "type": "text",
                    "text": self.content
                })
            result["content"] = vision_parts
        
        return result


@dataclass
class ChatResponse:
    """Standardized chat response structure."""
    content: str
    model: str
    usage: Optional[Dict[str, int]] = None
    thinking: Optional[str] = None
    reasoning: Optional[str] = None  # Alternative field for thinking (OpenRouter)
    tool_calls: Optional[List[Dict[str, Any]]] = None
    finish_reason: Optional[str] = None
    raw_response: Optional[Any] = None  # Original provider response
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary representation."""
        result = {
            "content": self.content,
            "model": self.model,
        }
        if self.usage:
            result["usage"] = self.usage
        if self.thinking:
            result["thinking"] = self.thinking
        if self.reasoning:
            result["reasoning"] = self.reasoning
        if self.tool_calls:
            result["tool_calls"] = self.tool_calls
        if self.finish_reason:
            result["finish_reason"] = self.finish_reason
        return result


@dataclass
class ModelInfo:
    """Information about an available model."""
    id: str
    name: str
    provider: str
    context_length: Optional[int] = None
    capabilities: List[ProviderCapability] = field(default_factory=list)
    description: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary representation."""
        return {
            "id": self.id,
            "name": self.name,
            "provider": self.provider,
            "context_length": self.context_length,
            "capabilities": [c.value for c in self.capabilities],
            "description": self.description,
            "metadata": self.metadata,
        }


@dataclass
class ProviderConfig:
    """Configuration for a provider instance."""
    provider_type: str
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    model: Optional[str] = None
    timeout: int = 300
    max_retries: int = 3
    extra_params: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary, excluding sensitive data."""
        result = {
            "provider_type": self.provider_type,
            "base_url": self.base_url,
            "model": self.model,
            "timeout": self.timeout,
            "max_retries": self.max_retries,
        }
        if self.api_key:
            # Mask API key: show last 4 chars or all if short
            if len(self.api_key) > 4:
                result["api_key"] = "***" + self.api_key[-4:]
            else:
                result["api_key"] = "****"
        if self.extra_params:
            result["extra_params"] = self.extra_params
        return result


class ProviderError(Exception):
    """Base exception for provider-related errors."""
    pass


class AuthenticationError(ProviderError):
    """Authentication/API key errors."""
    pass


class ConnectionError(OSError):
    """Connection/network errors."""
    pass


class ModelNotFoundError(ProviderError):
    """Model not found error."""
    pass


class BaseProvider(ABC):
    """Abstract base class that all provider implementations must inherit from."""
    
    provider_name: str = "base"
    provider_display_name: str = "Base Provider"
    provider_description: str = "Base provider class - should not be instantiated directly"
    default_capabilities: List[ProviderCapability] = [ProviderCapability.CHAT]
    
    def __init__(self, config: ProviderConfig):
        """
        Initialize the provider with configuration.
        
        Args:
            config: ProviderConfig instance with provider-specific settings
        """
        self.config = config
        self._validate_config()

    @property
    def http(self) -> PooledHttpClient:
        """This provider's pooled HTTP client (WP-7.2), created on first use."""
        client = self.__dict__.get("_http_client")
        if client is None:
            client = PooledHttpClient(
                self.provider_name,
                HttpPolicy(
                    read_seconds=float(self.config.timeout or 300),
                    max_retries=max(0, int(self.config.max_retries)),
                ),
            )
            client = self.__dict__.setdefault("_http_client", client)
        return client

    def close(self) -> None:
        """Release the provider's pooled connections."""
        client = self.__dict__.pop("_http_client", None)
        if client is not None:
            client.close()
    
    def _validate_config(self):
        """Validate the provider configuration. Override in subclasses if needed."""
        pass
    
    # The result's shape follows ``stream``: one response, or a stream of chunks.
    @overload
    def chat_completion(
        self, messages: List[ChatMessage], model: Optional[str] = None, stream: Literal[False] = False, **kwargs: Any
    ) -> ChatResponse: ...

    @overload
    def chat_completion(
        self, messages: List[ChatMessage], model: Optional[str] = None, *, stream: Literal[True], **kwargs: Any
    ) -> Iterator[ChatResponse]: ...

    @overload
    def chat_completion(
        self, messages: List[ChatMessage], model: Optional[str] = None, stream: bool = False, **kwargs: Any
    ) -> ChatResponse | Iterator[ChatResponse]: ...

    @abstractmethod
    def chat_completion(
        self,
        messages: List[ChatMessage],
        model: Optional[str] = None,
        stream: bool = False,
        **kwargs
    ) -> Union[ChatResponse, Iterator[ChatResponse]]:
        """
        Generate a chat completion.
        
        Args:
            messages: List of chat messages
            model: Optional model override
            stream: Whether to stream the response
            **kwargs: Additional provider-specific parameters
            
        Returns:
            ChatResponse if stream=False, or Iterator[ChatResponse] if stream=True
            
        Raises:
            AuthenticationError: If authentication fails
            ConnectionError: If connection fails
            ModelNotFoundError: If model doesn't exist
            ProviderError: For other provider errors
        """
        pass
    
    @abstractmethod
    def get_models(self) -> List[ModelInfo]:
        """
        Retrieve list of available models.
        
        Returns:
            List of ModelInfo objects
            
        Raises:
            ConnectionError: If unable to fetch models
        """
        pass
    
    @abstractmethod
    def test_connection(self) -> bool:
        """
        Test if the provider connection is working.
        
        Returns:
            True if connection successful, False otherwise
        """
        pass
    
    def get_config_schema(self) -> Dict[str, Any]:
        """
        Get the configuration schema for this provider.
        Used by frontend to render appropriate settings UI.
        
        Returns:
            Dictionary with configuration fields and metadata
        """
        return {
            "provider_type": self.provider_name,
            "display_name": self.provider_display_name,
            "description": self.provider_description,
            "fields": []
        }
    
    def get_capabilities(self) -> List[ProviderCapability]:
        """
        Get list of capabilities supported by this provider.
        
        Returns:
            List of ProviderCapability enums
        """
        return self.default_capabilities.copy()
    
    def supports_streaming(self) -> bool:
        """Check if provider supports streaming."""
        return ProviderCapability.STREAMING in self.get_capabilities()

    def cancel_active_request(self, owner: Optional[str] = None) -> bool:
        """Best-effort interruption of an in-flight request; ``owner`` names the job's turn."""
        return False

    def requires_api_key(self) -> bool:
        """Check if provider requires an API key."""
        return True
    
    def to_shared_format(self, response: ChatResponse) -> Dict[str, Any]:
        """
        Convert standardized response to the format expected by shared.py functions.
        Maintains backward compatibility with existing code.
        
        Args:
            response: Standardized ChatResponse
            
        Returns:
            Dictionary in legacy format
        """
        result = response.to_dict()
        # Ensure both 'thinking' and 'reasoning' fields for compatibility
        if response.reasoning and not response.thinking:
            result["thinking"] = response.reasoning
        elif response.thinking and not response.reasoning:
            result["reasoning"] = response.thinking
        return result
    
    def from_shared_format(self, data: Dict[str, Any]) -> ChatResponse:
        """
        Convert from shared.py format to standardized ChatResponse.
        
        Args:
            data: Dictionary in legacy shared format
            
        Returns:
            ChatResponse object
        """
        return ChatResponse(
            content=data.get("content", ""),
            model=data.get("model", ""),
            usage=data.get("usage"),
            thinking=data.get("thinking") or data.get("reasoning"),
            reasoning=data.get("reasoning") or data.get("thinking"),
            raw_response=data.get("raw_response")
        )
