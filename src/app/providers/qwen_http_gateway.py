"""Gateway facade for the separately managed GPU TTS service.

The service serves one local engine at a time (Qwen3-TTS or Fish S2 Pro).
Every request names the engine this facade stands for; the service switches
to it first when it is serving the other one.
"""
from __future__ import annotations

import base64
import json
import time

import numpy as np

from app.runtime.http_client import shared_http_client
from app.security.service_token import service_headers
from app.providers.tts_generation_parameters import generation_overrides


def _number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return int(number) if number.is_integer() else number


class TtsServiceSaturated(RuntimeError):
    """The shared TTS service has no realtime capacity available yet."""

    def __init__(self, retry_after: str) -> None:
        super().__init__("tts_capacity_saturated")
        self.retry_after = retry_after


class QwenHttpGatewayProvider:
    provider_name = 'faster-qwen3-tts'

    def __init__(self, base_url: str, provider_name: str = 'faster-qwen3-tts'):
        self.base_url = base_url.strip().strip('"').strip("'").replace(' ', '').rstrip('/')
        self.provider_name = provider_name
        # Shares the TTS service's connection pool with app.tts_http_client.
        self.http = shared_http_client("tts-service")

    def start(self):
        health = self._health()
        if health.get('provider') != self.provider_name:
            health = self.select_engine()
        return {'running': bool(health.get('ok')), 'message': health.get('error', '')}

    def select_engine(self):
        """Make the service serve this facade's engine (unloading the other one)."""
        response = self.http.post(self.base_url + '/api/tts/provider', json={'provider': self.provider_name},
                                  timeout=900, headers=service_headers(), retry=False)
        if response.status_code == 404:
            raise RuntimeError(
                f'The TTS service at {self.base_url} predates engine selection; restart the TTS service'
            )
        response.raise_for_status()
        return response.json()

    def _health(self):
        response = self.http.get(self.base_url + '/health', timeout=5, retry=False)
        response.raise_for_status()
        return response.json()

    def get_runtime_status(self):
        health = self._health()
        return {**health.get('details', {}), 'transport': 'http_streaming_pcm16',
                'status': health.get('status'), 'error': health.get('error', '')}

    def health_check(self):
        return bool(self._health().get('ok'))

    def stop(self):
        # This facade owns no GPU process. The service launcher owns its lifecycle.
        return True

    def get_speakers(self):
        response = self.http.get(self.base_url + '/api/tts/speakers', timeout=10, headers=service_headers())
        response.raise_for_status()
        return response.json().get('speakers', [])

    def resolve_generation_parameters(self, parameters=None):
        """The service's complete settings for these overrides: render cache identity (audiobook)."""
        overrides = generation_overrides(parameters)
        overrides.pop('instruct', None)  # A span's delivery is part of render identity on its own.
        response = self.http.post(self.base_url + '/api/tts/generation_parameters',
            json={'parameters': overrides, 'provider': self.provider_name},
            timeout=900, headers=service_headers())
        if response.status_code == 404:
            # The gateway and the TTS service restart separately; an older service lacks this route.
            raise RuntimeError(
                f'The TTS service at {self.base_url} predates audiobook rendering over HTTP; restart the TTS service'
            )
        response.raise_for_status()
        resolved = response.json().get('parameters')
        if not isinstance(resolved, dict):
            raise RuntimeError('Qwen service returned invalid generation parameters')
        return resolved

    def generate_audio(self, text, speaker=None, language=None, **kwargs):
        # The service sends the WAV bytes (WP-7.3); the provider contract's
        # base64 form is built here, in process, not on the wire.
        payload = {'text': text, 'speaker': speaker or 'default', 'language': language or 'en',
                   'provider': self.provider_name}
        overrides = generation_overrides(kwargs)
        if overrides:
            payload['parameters'] = overrides
        response = self.http.post(self.base_url + '/api/tts/generate_audio',
            json=payload, timeout=120,
            headers={**service_headers(), 'Accept': 'audio/wav, application/json;q=0.5'})
        response.raise_for_status()
        if response.headers.get('content-type', '').startswith('audio/wav'):
            result = {
                'success': True,
                'audio': base64.b64encode(response.content).decode('ascii'),
                'sample_rate': _number(response.headers.get('X-Omnix-Sample-Rate')),
                'duration': _number(response.headers.get('X-Omnix-Duration')),
                'provider': self.provider_name,
            }
            used = response.headers.get('X-Omnix-Generation-Parameters')
            if used:
                result['generation_parameters_used'] = json.loads(used)
            revision = response.headers.get('X-Omnix-Generation-Strategy-Revision')
            if revision:
                result['generation_strategy_revision'] = revision
            return result
        result = response.json()
        if not result.get('success') or result.get('is_fallback'):
            raise RuntimeError('Qwen service did not return real synthesized audio')
        return result

    def generate_audio_batch(self, requests):
        """One synthesis per request, in order: the service has no true batch endpoint."""
        return [
            self.generate_audio(
                str(request['text']), speaker=request.get('speaker'), language=request.get('language'),
                **dict(request.get('parameters') or {}),
            )
            for request in requests
        ]

    def generate_audio_stream(self, text, speaker=None, language=None, **kwargs):
        started = time.perf_counter()
        allowed = {'chunk_size', 'temperature', 'top_k', 'top_p', 'repetition_penalty', 'append_silence', 'max_new_tokens'}
        payload = {key: value for key, value in kwargs.items() if key in allowed and value is not None}
        payload.update(text=text, speaker=speaker or 'default', language=language or 'en',
                       provider=self.provider_name)
        # Realtime: a saturated service is reported at once (no retry); the
        # caller decides whether to wait for Retry-After.
        response = self.http.open_stream(
            'POST',
            self.base_url + '/api/tts/live-call/stream',
            json=payload,
            timeout=(5, 120),
            headers=service_headers(),
            retry=False,
        )
        try:
            if response.status_code == 429:
                raise TtsServiceSaturated(response.headers.get('Retry-After', '1'))
            response.raise_for_status()
            if response.headers.get('X-Omnix-Audio-Format') != 'pcm_s16le':
                raise ValueError('Qwen service must return streaming PCM16 audio')
            if response.headers.get('X-Omnix-Channels', '1') != '1':
                raise ValueError('Qwen service must return mono PCM16 audio')
            rate = int(response.headers.get('X-Omnix-Sample-Rate', '24000'))
            remainder = b''
            for chunk in response.iter_bytes(chunk_size=4800):
                if not chunk:
                    continue
                pcm = remainder + chunk
                aligned_size = len(pcm) - len(pcm) % 2
                remainder = pcm[aligned_size:]
                if aligned_size:
                    yield np.frombuffer(pcm[:aligned_size], dtype='<i2').astype(np.float32) / 32768, rate, {
                        'transport': 'http_streaming_pcm16', 'elapsed_ms': (time.perf_counter() - started) * 1000}
            if remainder:
                raise ValueError('Qwen service returned a partial PCM16 sample')
        finally:
            response.close()
