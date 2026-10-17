"""
Provider Registry - the LLM providers listed in ``app.providers.catalog``,
plus a factory for creating provider instances.
"""

import logging

from typing import Any, Dict, List, Optional, Type

from .base import BaseProvider, ProviderConfig
from .exceptions import ProviderRegistrationError

logger = logging.getLogger(__name__)


class ProviderRegistry:
    """
    Registry for provider plugins.
    
    Handles automatic discovery of provider classes in the providers package,
    registration, and factory-based instantiation.
    """
    
    def __init__(self):
        """Initialize the provider registry."""
        self._providers: Dict[str, Type[BaseProvider]] = {}
        self._discovered = False
        
    def discover_providers(self) -> None:
        """Load the LLM providers listed in ``app.providers.catalog`` (WP-7.2).

        A provider whose module fails to import is reported and skipped; the
        rest stay available.
        """
        if self._discovered:
            return
        from .catalog import specs

        self._providers = {}
        for spec in specs("llm"):
            try:
                provider_class = spec.load()
            except Exception as exc:
                logger.warning(f"Error loading provider {spec.id} from {spec.module}: {exc}")
                continue
            if not issubclass(provider_class, BaseProvider):
                raise ProviderRegistrationError(f"{spec.module}.{spec.attribute} is not a BaseProvider")
            self._providers[spec.id] = provider_class
        self._discovered = True
        logger.info(f"Provider discovery complete. {len(self._providers)} providers available")

    def register_provider(self, provider_class: Type[BaseProvider]) -> None:
        """
        Manually register a provider class.
        
        Args:
            provider_class: Provider class inheriting from BaseProvider
            
        Raises:
            ProviderRegistrationError: If provider name is invalid or already registered
        """
        if not issubclass(provider_class, BaseProvider):
            raise ProviderRegistrationError(f"{provider_class.__name__} must inherit from BaseProvider")
            
        provider_name = provider_class.provider_name
        if not provider_name or provider_name == "base":
            raise ProviderRegistrationError(f"Invalid provider name: {provider_name}")
            
        if provider_name in self._providers:
            raise ProviderRegistrationError(f"Provider '{provider_name}' is already registered")
            
        self._providers[provider_name] = provider_class
        logger.info(f"Manually registered provider: {provider_name}")
    
    def unregister_provider(self, provider_name: str) -> bool:
        """
        Unregister a provider.
        
        Args:
            provider_name: Name of the provider to unregister
            
        Returns:
            True if provider was unregistered, False if not found
        """
        if provider_name in self._providers:
            del self._providers[provider_name]
            logger.info(f"Unregistered provider: {provider_name}")
            return True
        return False
    
    def get_provider_class(self, provider_name: str) -> Optional[Type[BaseProvider]]:
        """
        Get the provider class for a given provider name.
        
        Args:
            provider_name: Name of the provider
            
        Returns:
            Provider class or None if not found
        """
        if not self._discovered:
            self.discover_providers()
        return self._providers.get(provider_name)
    
    def list_providers(self) -> List[Dict[str, Any]]:
        """
        Get list of all registered providers with metadata.
        
        Returns:
            List of dictionaries with provider information
        """
        if not self._discovered:
            self.discover_providers()
            
        providers_list = []
        for name, provider_class in self._providers.items():
            try:
                info = {
                    "name": name,
                    "display_name": getattr(provider_class, "provider_display_name", name),
                    "description": getattr(provider_class, "provider_description", ""),
                    "capabilities": [c.value for c in getattr(provider_class, "default_capabilities", [])],
                }
                providers_list.append(info)
            except Exception as e:
                logger.warning(f"Error getting info for provider {name}: {e}")
                
        return providers_list

    @staticmethod
    def _chatgpt_codex_config(base: ProviderConfig) -> ProviderConfig:
        """Resolve typed Codex settings for bare or legacy malformed inputs.

        New callers that already supply a ``chatgpt_codex`` ProviderConfig keep
        their explicit values. This compatibility path only repairs old factory
        callers that arrive with no Codex config or with another provider's
        configuration (historically the LM Studio fallthrough).
        """
        try:
            from app.settings.access import load_settings

            settings = load_settings()
            profile = settings.get("settings_control_center", {})
            provider_configs = profile.get("providerConfigs", {}) if isinstance(profile, dict) else {}
            codex = provider_configs.get("chatgptCodex", {}) if isinstance(provider_configs, dict) else {}
            codex = codex if isinstance(codex, dict) else {}
        except Exception:
            codex = {}
        return ProviderConfig(
            provider_type="chatgpt_codex",
            model=str(codex.get("model") or "gpt-5.6-sol"),
            timeout=base.timeout,
            max_retries=base.max_retries,
            extra_params={
                "reasoning_effort": str(codex.get("reasoningEffort") or "medium"),
                "fast_mode": bool(codex.get("fastMode", False)),
                "codex_path": str(codex.get("codexPath") or "codex"),
                "transport": str(codex.get("transport") or "app_server"),
            },
        )
    
    def create_provider(
        self,
        provider_name: str,
        config: Optional[Dict[str, Any]] = None,
        provider_config: Optional[ProviderConfig] = None
    ) -> Optional[BaseProvider]:
        """
        Factory method to create a provider instance.
        
        Args:
            provider_name: Name of the provider to instantiate
            config: Dictionary with configuration (alternative to provider_config)
            provider_config: ProviderConfig instance (preferred)
            
        Returns:
            Provider instance or None if provider not found
            
        Raises:
            ProviderRegistrationError: If provider class can't be instantiated
        """
        if not self._discovered:
            self.discover_providers()
            
        provider_class = self._providers.get(provider_name)
        if not provider_class:
            logger.debug(f"Provider '{provider_name}' not found")
            return None
            
        # Build ProviderConfig
        if provider_config:
            final_config = provider_config
        elif config:
            extra_params = config.get("extra_params", {})
            
            # Move known top-level params to extra_params if they exist in config
            for param in ["thinking_budget", "context_size"]:
                if param in config:
                    extra_params[param] = config[param]
            
            final_config = ProviderConfig(
                provider_type=provider_name,
                api_key=config.get("api_key"),
                base_url=config.get("base_url"),
                model=config.get("model"),
                timeout=config.get("timeout", 300),
                max_retries=config.get("max_retries", 3),
                extra_params=extra_params
            )
        else:
            # Use empty config, provider should provide defaults
            final_config = ProviderConfig(provider_type=provider_name)

        if provider_name == "chatgpt_codex" and (
            (provider_config is None and config is None)
            or final_config.provider_type != "chatgpt_codex"
        ):
            final_config = self._chatgpt_codex_config(final_config)
            
        try:
            return provider_class(config=final_config)
        except Exception as e:
            raise ProviderRegistrationError(
                f"Failed to instantiate provider '{provider_name}': {e}"
            ) from e
    
    def clear(self) -> None:
        """Clear all registered providers (useful for testing)."""
        self._providers.clear()
        self._discovered = False


# Global registry instance
_registry = ProviderRegistry()


def get_registry() -> ProviderRegistry:
    """
    Get the global provider registry instance.
    
    Returns:
        ProviderRegistry singleton
    """
    return _registry


# Convenience functions
def get_provider(provider_name: str, config: Optional[Dict[str, Any]] = None) -> Optional[BaseProvider]:
    """
    Convenience function to get a provider instance.
    
    Args:
        provider_name: Name of the provider
        config: Optional configuration dictionary
        
    Returns:
        Provider instance or None
    """
    return get_registry().create_provider(provider_name, config)


def list_available_providers() -> List[Dict[str, Any]]:
    """
    Get list of all available providers.
    
    Returns:
        List of provider metadata dictionaries
    """
    return get_registry().list_providers()


def register_provider(provider_class: Type[BaseProvider]) -> None:
    """
    Register a provider class with the global registry.
    
    Args:
        provider_class: Provider class to register
    """
    get_registry().register_provider(provider_class)
