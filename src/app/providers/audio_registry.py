"""
Audio Provider Registry - Factory for Audio Providers

This module implements a registry that automatically discovers audio provider plugins
and provides a factory for creating TTS and STT provider instances.
"""

import logging

from typing import Any, Dict, List, Optional, Type

from .audio_base import AudioProviderConfig, BaseSTTProvider, BaseTTSProvider
from .exceptions import ProviderRegistrationError

logger = logging.getLogger(__name__)


class AudioProviderRegistry:
    """
    Registry for audio provider plugins.
    
    Handles automatic discovery of TTS and STT provider classes,
    registration, and factory-based instantiation.
    """
    
    def __init__(self):
        """Initialize the audio provider registry."""
        self._tts_providers: Dict[str, Type[BaseTTSProvider]] = {}
        self._stt_providers: Dict[str, Type[BaseSTTProvider]] = {}
        self._discovered = False
        
    def discover_providers(self) -> None:
        """Load the TTS and STT providers listed in ``app.providers.catalog`` (WP-7.2)."""
        if self._discovered:
            return
        from .catalog import specs

        self._tts_providers = {}
        self._stt_providers = {}
        for kind, base, target in (("tts", BaseTTSProvider, self._tts_providers), ("stt", BaseSTTProvider, self._stt_providers)):
            for spec in specs(kind):
                try:
                    provider_class = spec.load()
                except Exception as exc:
                    logger.warning(f"Error loading {kind.upper()} provider {spec.id} from {spec.module}: {exc}")
                    continue
                if not issubclass(provider_class, base):
                    raise ProviderRegistrationError(f"{spec.module}.{spec.attribute} is not a {base.__name__}")
                target[spec.id] = provider_class
        self._discovered = True
        logger.info(f"[INFO] Audio provider discovery complete. {len(self._tts_providers)} TTS and {len(self._stt_providers)} STT providers available")

    def register_tts_provider(self, provider_class: Type[BaseTTSProvider]) -> None:
        """
        Manually register a TTS provider class.
        
        Args:
            provider_class: TTS provider class inheriting from BaseTTSProvider
            
        Raises:
            ProviderRegistrationError: If provider name is invalid or already registered
        """
        if not issubclass(provider_class, BaseTTSProvider):
            raise ProviderRegistrationError(f"{provider_class.__name__} must inherit from BaseTTSProvider")
            
        provider_name = provider_class.provider_name
        if not provider_name or provider_name == "base":
            raise ProviderRegistrationError(f"Invalid TTS provider name: {provider_name}")
            
        if provider_name in self._tts_providers:
            raise ProviderRegistrationError(f"TTS Provider '{provider_name}' is already registered")
            
        self._tts_providers[provider_name] = provider_class
        logger.info(f"[INFO] Manually registered TTS provider: {provider_name}")
    
    def register_stt_provider(self, provider_class: Type[BaseSTTProvider]) -> None:
        """
        Manually register an STT provider class.
        
        Args:
            provider_class: STT provider class inheriting from BaseSTTProvider
            
        Raises:
            ProviderRegistrationError: If provider name is invalid or already registered
        """
        if not issubclass(provider_class, BaseSTTProvider):
            raise ProviderRegistrationError(f"{provider_class.__name__} must inherit from BaseSTTProvider")
            
        provider_name = provider_class.provider_name
        if not provider_name or provider_name == "base":
            raise ProviderRegistrationError(f"Invalid STT provider name: {provider_name}")
            
        if provider_name in self._stt_providers:
            raise ProviderRegistrationError(f"STT Provider '{provider_name}' is already registered")
            
        self._stt_providers[provider_name] = provider_class
        logger.info(f"[INFO] Manually registered STT provider: {provider_name}")
    
    def unregister_tts_provider(self, provider_name: str) -> bool:
        """
        Unregister a TTS provider.
        
        Args:
            provider_name: Name of the TTS provider to unregister
            
        Returns:
            True if provider was unregistered, False if not found
        """
        if provider_name in self._tts_providers:
            del self._tts_providers[provider_name]
            logger.info(f"[INFO] Unregistered TTS provider: {provider_name}")
            return True
        return False
    
    def unregister_stt_provider(self, provider_name: str) -> bool:
        """
        Unregister an STT provider.
        
        Args:
            provider_name: Name of the STT provider to unregister
            
        Returns:
            True if provider was unregistered, False if not found
        """
        if provider_name in self._stt_providers:
            del self._stt_providers[provider_name]
            logger.info(f"[INFO] Unregistered STT provider: {provider_name}")
            return True
        return False
    
    def get_tts_provider_class(self, provider_name: str) -> Optional[Type[BaseTTSProvider]]:
        """
        Get the TTS provider class for a given provider name.
        
        Args:
            provider_name: Name of the TTS provider
            
        Returns:
            TTS provider class or None if not found
        """
        if not self._discovered:
            self.discover_providers()
        return self._tts_providers.get(provider_name)
    
    def get_stt_provider_class(self, provider_name: str) -> Optional[Type[BaseSTTProvider]]:
        """
        Get the STT provider class for a given provider name.
        
        Args:
            provider_name: Name of the STT provider
            
        Returns:
            STT provider class or None if not found
        """
        if not self._discovered:
            self.discover_providers()
        return self._stt_providers.get(provider_name)
    
    def list_tts_providers(self) -> List[Dict[str, Any]]:
        """
        Get list of all registered TTS providers with metadata.
        
        Returns:
            List of dictionaries with TTS provider information
        """
        if not self._discovered:
            self.discover_providers()
            
        providers_list = []
        for name, provider_class in self._tts_providers.items():
            try:
                # Get class-level attributes
                info = {
                    "name": name,
                    "display_name": getattr(provider_class, "provider_display_name", name),
                    "description": getattr(provider_class, "provider_description", ""),
                    "capabilities": [c.value for c in getattr(provider_class, "default_capabilities", [])],
                }
                providers_list.append(info)
            except Exception as e:
                logger.warning(f"Error getting info for TTS provider {name}: {e}")
                
        return providers_list
    
    def list_stt_providers(self) -> List[Dict[str, Any]]:
        """
        Get list of all registered STT providers with metadata.
        
        Returns:
            List of dictionaries with STT provider information
        """
        if not self._discovered:
            self.discover_providers()
            
        providers_list = []
        for name, provider_class in self._stt_providers.items():
            try:
                # Get class-level attributes
                info = {
                    "name": name,
                    "display_name": getattr(provider_class, "provider_display_name", name),
                    "description": getattr(provider_class, "provider_description", ""),
                    "capabilities": [c.value for c in getattr(provider_class, "default_capabilities", [])],
                }
                providers_list.append(info)
            except Exception as e:
                logger.warning(f"Error getting info for STT provider {name}: {e}")
                
        return providers_list
    
    def create_tts_provider(
        self,
        provider_name: str,
        config: Optional[Dict[str, Any]] = None,
        provider_config: Optional[AudioProviderConfig] = None
    ) -> Optional[BaseTTSProvider]:
        """
        Factory method to create a TTS provider instance.
        
        Args:
            provider_name: Name of the TTS provider to instantiate
            config: Dictionary with configuration (alternative to provider_config)
            provider_config: AudioProviderConfig instance (preferred)
            
        Returns:
            TTS provider instance or None if provider not found
            
        Raises:
            ProviderRegistrationError: If provider class can't be instantiated
        """
        if not self._discovered:
            self.discover_providers()
            
        provider_class = self._tts_providers.get(provider_name)
        if not provider_class:
            logger.debug(f"TTS Provider '{provider_name}' not found")
            return None
            
        # Build AudioProviderConfig
        if provider_config:
            final_config = provider_config
        elif config:
            final_config = AudioProviderConfig(
                provider_type=provider_name,
                base_url=config.get("base_url"),
                timeout=config.get("timeout", 300),
                max_retries=config.get("max_retries", 3),
                extra_params=config.get("extra_params", {})
            )
        else:
            # Use empty config, provider should provide defaults
            final_config = AudioProviderConfig(provider_type=provider_name)
            
        try:
            # Create provider instance with config dict
            provider_instance = provider_class(config=final_config.to_dict())
            return provider_instance
        except Exception as e:
            raise ProviderRegistrationError(
                f"Failed to instantiate TTS provider '{provider_name}': {e}"
            ) from e
    
    def create_stt_provider(
        self,
        provider_name: str,
        config: Optional[Dict[str, Any]] = None,
        provider_config: Optional[AudioProviderConfig] = None
    ) -> Optional[BaseSTTProvider]:
        """
        Factory method to create an STT provider instance.
        
        Args:
            provider_name: Name of the STT provider to instantiate
            config: Dictionary with configuration (alternative to provider_config)
            provider_config: AudioProviderConfig instance (preferred)
            
        Returns:
            STT provider instance or None if provider not found
            
        Raises:
            ProviderRegistrationError: If provider class can't be instantiated
        """
        if not self._discovered:
            self.discover_providers()
            
        provider_class = self._stt_providers.get(provider_name)
        if not provider_class:
            logger.debug(f"STT Provider '{provider_name}' not found")
            return None
            
        # Build AudioProviderConfig
        if provider_config:
            final_config = provider_config
        elif config:
            final_config = AudioProviderConfig(
                provider_type=provider_name,
                base_url=config.get("base_url"),
                timeout=config.get("timeout", 300),
                max_retries=config.get("max_retries", 3),
                extra_params=config.get("extra_params", {})
            )
        else:
            # Use empty config, provider should provide defaults
            final_config = AudioProviderConfig(provider_type=provider_name)
            
        try:
            # Create provider instance with config dict
            provider_instance = provider_class(config=final_config.to_dict())
            return provider_instance
        except Exception as e:
            raise ProviderRegistrationError(
                f"Failed to instantiate STT provider '{provider_name}': {e}"
            ) from e
    
    def clear(self) -> None:
        """Clear all registered providers (useful for testing)."""
        self._tts_providers.clear()
        self._stt_providers.clear()
        self._discovered = False



# Global registry instance
_registry = AudioProviderRegistry()


def get_audio_registry() -> AudioProviderRegistry:
    """
    Get the global audio provider registry instance.
    
    Returns:
        AudioProviderRegistry singleton
    """
    return _registry


# Convenience functions
def get_tts_provider(provider_name: str, config: Optional[Dict[str, Any]] = None) -> Optional[BaseTTSProvider]:
    """
    Convenience function to get a TTS provider instance.
    
    Args:
        provider_name: Name of the TTS provider
        config: Optional configuration dictionary
        
    Returns:
        TTS provider instance or None
    """
    return get_audio_registry().create_tts_provider(provider_name, config)


def get_stt_provider(provider_name: str, config: Optional[Dict[str, Any]] = None) -> Optional[BaseSTTProvider]:
    """
    Convenience function to get an STT provider instance.
    
    Args:
        provider_name: Name of the STT provider
        config: Optional configuration dictionary
        
    Returns:
        STT provider instance or None
    """
    return get_audio_registry().create_stt_provider(provider_name, config)


def list_available_tts_providers() -> List[Dict[str, Any]]:
    """
    Get list of all available TTS providers.
    
    Returns:
        List of TTS provider metadata dictionaries
    """
    return get_audio_registry().list_tts_providers()


def list_available_stt_providers() -> List[Dict[str, Any]]:
    """
    Get list of all available STT providers.
    
    Returns:
        List of STT provider metadata dictionaries
    """
    return get_audio_registry().list_stt_providers()


def register_tts_provider(provider_class: Type[BaseTTSProvider]) -> None:
    """
    Register a TTS provider class with the global registry.
    
    Args:
        provider_class: TTS provider class to register
    """
    get_audio_registry().register_tts_provider(provider_class)


def register_stt_provider(provider_class: Type[BaseSTTProvider]) -> None:
    """
    Register an STT provider class with the global registry.
    
    Args:
        provider_class: STT provider class to register
    """
    get_audio_registry().register_stt_provider(provider_class)
