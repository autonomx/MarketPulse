"""
Audio Provider Plugin Architecture - Base Classes

This module defines the abstract base classes for Text-to-Speech (TTS) and 
Speech-to-Text (STT) providers, following the Plugin-Based Provider Pattern.
"""

import logging
import queue
import subprocess
import threading
from abc import abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Iterator, List, Optional

logger = logging.getLogger(__name__)


class AudioProviderError(Exception):
    """Base exception for audio provider errors."""
    pass


class AudioProviderConnectionError(AudioProviderError):
    """Connection/network errors for audio providers."""
    pass


class AudioProviderAuthenticationError(AudioProviderError):
    """Authentication/API key errors for audio providers."""
    pass


class AudioProviderNotFoundError(AudioProviderError):
    """Provider not found error."""
    pass


class AudioProviderCapability(Enum):
    """Capabilities that an audio provider may support."""
    STREAMING = "streaming"
    VOICE_CLONING = "voice_cloning"
    MULTILINGUAL = "multilingual"
    REAL_TIME = "real_time"
    BATCH_PROCESSING = "batch_processing"
    OFFLINE_BATCH = "offline_batch"
    CUSTOM_VOICE = "custom_voice"
    VOICE_DESIGN = "voice_design"
    LORA_VOICE = "lora_voice"


@dataclass
class BaseService:
    """
    Base service class that handles lifecycle management for local audio providers.
    
    This class standardizes how local audio provider processes
    are started, stopped, and health-checked.
    """
    
    config: Dict[str, Any]
    process: Optional[subprocess.Popen] = None
    log_queue: Optional[queue.Queue] = None
    log_thread: Optional[threading.Thread] = None
    
    def start(self) -> Dict[str, Any]:
        """
        Start the audio service subprocess.
        
        Returns:
            Dict with 'running' status and optional 'message'
        """
        if self.process and self.process.poll() is None:
            return {"running": True, "message": "Service already running"}
        
        try:
            # This should be implemented by concrete providers
            # to start their specific subprocess
            raise NotImplementedError("Subclasses must implement start()")
            
        except Exception as e:
            logger.error(f"Failed to start service: {e}")
            return {"running": False, "message": f"Failed to start: {str(e)}"}
    
    def stop(self) -> bool:
        """
        Stop the audio service subprocess.
        
        Returns:
            True if successfully stopped, False otherwise
        """
        if self.process and self.process.poll() is None:
            try:
                self.process.terminate()
                self.process.wait(timeout=10)
                self.process = None
                
                # Clean up log thread and queue
                if self.log_thread and self.log_thread.is_alive():
                    self.log_thread.join(timeout=5)
                self.log_queue = None
                self.log_thread = None
                
                logger.info("Service stopped successfully")
                return True
            except subprocess.TimeoutExpired:
                logger.warning("Service did not terminate gracefully, killing...")
                self.process.kill()
                self.process = None
                return False
            except Exception as e:
                logger.error(f"Error stopping service: {e}")
                return False
        
        logger.info("Service was not running")
        return True
    
    def health_check(self) -> bool:
        """
        Check if the service is healthy and responding.
        
        Returns:
            True if healthy, False otherwise
        """
        if not self.process or self.process.poll() is not None:
            return False
        
        try:
            # This should be implemented by concrete providers
            # to check their specific service health
            raise NotImplementedError("Subclasses must implement health_check()")
        except Exception as e:
            logger.error(f"Health check failed: {e}")
            return False
    
    def get_logs(self) -> List[str]:
        """
        Get recent log messages from the service.
        
        Returns:
            List of log messages
        """
        if not self.log_queue:
            return []
        
        logs = []
        try:
            while True:
                log_line = self.log_queue.get_nowait()
                logs.append(log_line)
        except queue.Empty:
            pass
        
        return logs
    
    def _start_log_thread(self):
        """Start a background thread to capture subprocess logs."""
        if self.log_queue is None:
            self.log_queue = queue.Queue()
        
        def log_reader():
            if self.process and self.process.stdout:
                try:
                    for line in iter(self.process.stdout.readline, ''):
                        if line:
                            self.log_queue.put(line.strip())
                except Exception as e:
                    logger.error(f"Error reading logs: {e}")
        
        self.log_thread = threading.Thread(target=log_reader, daemon=True)
        self.log_thread.start()


@dataclass
class BaseTTSProvider(BaseService):
    """
    Abstract base class for Text-to-Speech providers.
    
    All TTS providers must inherit from this class and implement the required methods.
    """
    
    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Return the unique name of this provider."""
        pass
    
    @abstractmethod
    def get_speakers(self) -> List[Dict[str, Any]]:
        """
        Get list of available speakers/voices.
        
        Returns:
            List of speaker dictionaries with 'id', 'name', 'language' keys
        """
        pass
    
    @abstractmethod
    def generate_audio(self, text: str, speaker: Optional[str] = None, 
                      language: Optional[str] = None, **kwargs) -> Dict[str, Any]:
        """
        Generate audio from text.
        
        Args:
            text: The text to synthesize
            speaker: Optional speaker/voice to use
            language: Optional language code
            **kwargs: Provider-specific parameters
            
        Returns:
            Dict with 'success', 'audio' (base64), 'sample_rate', 'format' keys
        """
        pass

    def generate_audio_stream(self, text: str, speaker: Optional[str] = None, 
                             language: Optional[str] = None, **kwargs) -> Iterator[bytes]:
        """
        Stream audio generation for real-time playback.
        
        Args:
            text: The text to synthesize
            speaker: Optional speaker/voice to use
            language: Optional language code
            **kwargs: Provider-specific parameters
            
        Yields:
            PCM audio chunks as bytes
        """
        # Default implementation falls back to batch generation
        # Providers should override this for true streaming
        result = self.generate_audio(text, speaker, language, **kwargs)
        if result.get('success') and result.get('audio'):
            import base64
            audio_bytes = base64.b64decode(result['audio'])
            yield audio_bytes

    def resolve_generation_parameters(self, parameters: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Return the complete generation-affecting parameter map used for cache identity.

        Providers with configurable defaults should override this method so callers
        never hash only sparse user overrides while synthesis silently fills in
        different defaults later.
        """
        return dict(parameters or {})

    def generate_audio_batch(self, requests: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Ordered fallback; providers advertise OFFLINE_BATCH only for true batching."""
        return [
            self.generate_audio(
                str(request["text"]),
                speaker=request.get("speaker"),
                language=request.get("language"),
                **dict(request.get("parameters") or {}),
            )
            for request in requests
        ]
    
    @abstractmethod
    def voice_clone(self, voice_id: str, audio_data: bytes, 
                   ref_text: Optional[str] = None) -> Dict[str, Any]:
        """
        Create a voice clone from audio data.
        
        Args:
            voice_id: Unique identifier for the voice clone
            audio_data: Reference audio data
            ref_text: Optional reference text for the voice
            
        Returns:
            Dict with 'success' status and optional 'message'
        """
        pass
    
    def get_capabilities(self) -> List[AudioProviderCapability]:
        """
        Get list of capabilities supported by this provider.
        
        Returns:
            List of AudioProviderCapability enums
        """
        return []
    
    def supports_streaming(self) -> bool:
        """Check if provider supports streaming TTS."""
        return AudioProviderCapability.STREAMING in self.get_capabilities()
    
    def supports_voice_cloning(self) -> bool:
        """Check if provider supports voice cloning."""
        return AudioProviderCapability.VOICE_CLONING in self.get_capabilities()


@dataclass
class BaseSTTProvider(BaseService):
    """
    Abstract base class for Speech-to-Text providers.
    
    All STT providers must inherit from this class and implement the required methods.
    """
    
    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Return the unique name of this provider."""
        pass
    
    @abstractmethod
    def transcribe(self, audio_file_path: str, language: Optional[str] = None, 
                  **kwargs) -> Dict[str, Any]:
        """
        Transcribe audio file to text.
        
        Args:
            audio_file_path: Path to the audio file
            language: Optional language code
            **kwargs: Provider-specific parameters
            
        Returns:
            Dict with 'success', 'text', 'segments', 'duration' keys
        """
        pass
    
    @abstractmethod
    def transcribe_raw(self, audio_data: bytes, sample_rate: int = 16000, 
                      language: Optional[str] = None, **kwargs) -> Dict[str, Any]:
        """
        Transcribe raw audio data to text.
        
        Args:
            audio_data: Raw audio bytes
            sample_rate: Audio sample rate
            language: Optional language code
            **kwargs: Provider-specific parameters
            
        Returns:
            Dict with 'success', 'text', 'segments', 'duration' keys
        """
        pass

    def transcribe_stream(self, audio_chunks: Iterator[bytes]) -> Iterator[Dict[str, Any]]:
        """
        Stream STT transcription partials.
        
        Args:
            audio_chunks: Iterator of audio chunks (bytes)
            
        Yields:
            Dict with 'partial' or 'final' keys containing text
        """
        pass
    
    def get_capabilities(self) -> List[AudioProviderCapability]:
        """
        Get list of capabilities supported by this provider.
        
        Returns:
            List of AudioProviderCapability enums
        """
        return []
    
    def supports_streaming(self) -> bool:
        """Check if provider supports streaming STT."""
        return AudioProviderCapability.STREAMING in self.get_capabilities()
    
    def supports_batch_processing(self) -> bool:
        """Check if provider supports batch processing."""
        return AudioProviderCapability.BATCH_PROCESSING in self.get_capabilities()


@dataclass
class AudioProviderConfig:
    """Configuration for an audio provider instance."""
    
    provider_type: str
    base_url: Optional[str] = None
    timeout: int = 300
    max_retries: int = 3
    extra_params: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary representation."""
        return {
            "provider_type": self.provider_type,
            "base_url": self.base_url,
            "timeout": self.timeout,
            "max_retries": self.max_retries,
            "extra_params": self.extra_params
        }


@dataclass
class TTSAudioResponse:
    """Standardized TTS response structure."""
    
    audio_data: bytes
    sample_rate: int
    format: str = "audio/wav"
    duration: Optional[float] = None
    speaker: Optional[str] = None
    language: Optional[str] = None
    raw_response: Optional[Any] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary representation."""
        import base64
        return {
            "audio": base64.b64encode(self.audio_data).decode('utf-8'),
            "sample_rate": self.sample_rate,
            "format": self.format,
            "duration": self.duration,
            "speaker": self.speaker,
            "language": self.language
        }


@dataclass
class STTTranscriptionResponse:
    """Standardized STT response structure."""
    
    text: str
    segments: List[Dict[str, Any]]
    duration: Optional[float] = None
    language: Optional[str] = None
    confidence: Optional[float] = None
    raw_response: Optional[Any] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary representation."""
        return {
            "text": self.text,
            "segments": self.segments,
            "duration": self.duration,
            "language": self.language,
            "confidence": self.confidence
        }
