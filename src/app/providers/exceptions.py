"""
Custom exceptions for the provider plugin system.
"""

from .base import ProviderError


class ProviderRegistrationError(ProviderError):
    """Error during provider registration or discovery."""
    pass


class ProviderConfigurationError(ProviderError):
    """Error in provider configuration."""
    pass


class ProviderNotAvailableError(ProviderError):
    """Provider is not available or not installed."""
    pass


class StreamInterruptedError(ProviderError):
    """Stream was interrupted or cancelled."""
    pass


class RateLimitError(ProviderError):
    """Rate limit exceeded."""
    pass


class InsufficientQuotaError(ProviderError):
    """Insufficient quota or credits."""
    pass