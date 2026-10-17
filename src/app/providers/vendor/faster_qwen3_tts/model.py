"""
FasterQwen3TTS: Real-time TTS using CUDA graph capture.
 
Wrapper class that provides a Qwen3-TTS API while using
CUDA graphs for 6-10x speedup.
"""
from app.config.env import env_str as _env_str
import logging
import time
from pathlib import Path
from typing import Callable, Generator, Optional, Tuple, Union

import numpy as np
import soundfile as sf
import torch

from .utils import suppress_flash_attn_warning

logger = logging.getLogger(__name__)


def _identity_decorator(*args, **kwargs):
    """
    Compatibility no-op for Hugging Face docstring decorators.

    Supports both styles:
      @decorator
      @decorator(...)
    """
    if args and len(args) == 1 and callable(args[0]) and not kwargs:
        return args[0]

    def decorator(obj):
        return obj

    return decorator


def _ensure_transformers_qwen3_compat() -> None:
    try:
        import huggingface_hub
        if not hasattr(huggingface_hub, "is_offline_mode"):
            def _fallback_is_offline_mode() -> bool:
                import os
                return (
                    _env_str("HF_HUB_OFFLINE", "").strip().lower() in {"1", "true", "yes", "on"}
                    or _env_str("TRANSFORMERS_OFFLINE", "").strip().lower() in {"1", "true", "yes", "on"}
                )
            huggingface_hub.is_offline_mode = _fallback_is_offline_mode
    except Exception:
        pass

    try:
        import huggingface_hub.utils as hf_utils
        if not hasattr(hf_utils, "is_offline_mode"):
            def _fallback_is_offline_mode_utils() -> bool:
                import os
                return (
                    _env_str("HF_HUB_OFFLINE", "").strip().lower() in {"1", "true", "yes", "on"}
                    or _env_str("TRANSFORMERS_OFFLINE", "").strip().lower() in {"1", "true", "yes", "on"}
                )
            hf_utils.is_offline_mode = _fallback_is_offline_mode_utils
    except Exception:
        pass

    import functools
    import sys
    import types

    import transformers

    # ---- 0. Patch safetensors metadata contract ----
    # Some model shards return ``None`` for ``safe_open(...).metadata()``.
    # Newer transformers codepaths assume a dict-like object and do:
    #     metadata.get("format")
    # which crashes with:
    #     AttributeError: 'NoneType' object has no attribute 'get'
    #
    # Normalize that contract here at the vendored boundary so all downstream
    # Qwen3-TTS model loads see a dict instead of None.
    try:
        import safetensors

        original_safe_open = getattr(safetensors, "safe_open", None)
        if original_safe_open is not None and not getattr(original_safe_open, "_omnix_qwen3_metadata_compat", False):
            class _SafeOpenMetadataCompatProxy:
                def __init__(self, *args, **kwargs):
                    self._inner = original_safe_open(*args, **kwargs)

                def __enter__(self):
                    entered = self._inner.__enter__()
                    # Some implementations return self, some return another handle.
                    # Keep proxy semantics stable either way.
                    if entered is not self._inner:
                        self._inner = entered
                    return self

                def __exit__(self, exc_type, exc, tb):
                    return self._inner.__exit__(exc_type, exc, tb)

                def __getattr__(self, name):
                    return getattr(self._inner, name)

                def metadata(self):
                    try:
                        metadata = self._inner.metadata()
                    except Exception:
                        raise
                    return metadata or {}

            def _patched_safe_open(*args, **kwargs):
                return _SafeOpenMetadataCompatProxy(*args, **kwargs)

            _patched_safe_open._omnix_qwen3_metadata_compat = True
            safetensors.safe_open = _patched_safe_open
            sys.modules["safetensors"].safe_open = _patched_safe_open

            # If transformers.modeling_utils already imported safe_open into its
            # module namespace, patch that live binding too.
            try:
                import transformers.modeling_utils as modeling_utils_module
                if hasattr(modeling_utils_module, "safe_open"):
                    modeling_utils_module.safe_open = _patched_safe_open
            except Exception:
                pass
    except Exception:
        pass

    # ---- 1. Ensure modeling_utils surface ----
    modeling_utils = getattr(transformers, "modeling_utils", None)
    if modeling_utils is not None and not hasattr(modeling_utils, "ALL_ATTENTION_FUNCTIONS"):
        modeling_utils.ALL_ATTENTION_FUNCTIONS = {}

    # ---- 2. Force override decorators (ALWAYS) ----
    def _noop(*args, **kwargs):
        if args and len(args) == 1 and callable(args[0]) and not kwargs:
            return args[0]
        def decorator(obj):
            return obj
        return decorator

    # Patch live module
    if hasattr(transformers, "utils"):
        transformers.utils.auto_docstring = _noop
        transformers.utils.auto_class_docstring = _noop
        if not hasattr(transformers.utils, "can_return_tuple"):
            transformers.utils.can_return_tuple = _noop

    # Patch sys.modules binding (critical)
    utils_module = sys.modules.get("transformers.utils")
    if utils_module:
        utils_module.auto_docstring = _noop
        utils_module.auto_class_docstring = _noop
        if not hasattr(utils_module, "can_return_tuple"):
            utils_module.can_return_tuple = _noop

    # ---- 3. Pre-import hook to ensure patch BEFORE vendor import ----
    # This ensures any "from transformers.utils import ..." sees patched version
    import importlib

    # Ensure module is imported before reload
    try:
        transformers_utils = importlib.import_module("transformers.utils")
    except Exception:
        transformers_utils = None

    if transformers_utils is not None and hasattr(transformers_utils, '__file__'):
        # Only reload real modules (not fake test modules)
        importlib.reload(transformers_utils)

        # Re-apply after reload (important)
        transformers.utils.auto_docstring = _noop
        transformers.utils.auto_class_docstring = _noop
        if not hasattr(transformers.utils, "can_return_tuple"):
            transformers.utils.can_return_tuple = _noop
        sys.modules["transformers.utils"].auto_docstring = _noop
        sys.modules["transformers.utils"].auto_class_docstring = _noop
        if not hasattr(sys.modules["transformers.utils"], "can_return_tuple"):
            sys.modules["transformers.utils"].can_return_tuple = _noop

    # ---- 4. Provide missing masking_utils module ----
    import importlib
    try:
        real_masking_utils = importlib.import_module("transformers.masking_utils")
        masking_utils = real_masking_utils
    except Exception:
        # Always replace broken or missing module
        masking_utils = types.ModuleType("transformers.masking_utils")
        
        # minimal stubs used by older Qwen codepaths
        def _dummy(*args, **kwargs):
            return None

        masking_utils.create_causal_mask = _dummy
        masking_utils.expand_mask = _dummy
        masking_utils.create_sliding_window_causal_mask = _dummy

        sys.modules["transformers.masking_utils"] = masking_utils

    # transformers>=4.57 generation.utils imports these symbols.
    if not hasattr(masking_utils, "create_masks_for_generate"):
        def create_masks_for_generate(*args, **kwargs):
            # Minimal compatibility shim:
            # return None so downstream generation code can fall back to
            # model-internal/default masking behavior rather than crashing
            return None

        masking_utils.create_masks_for_generate = create_masks_for_generate

    # ---- 4b. Patch safetensors metadata=None -> {"format": "pt"} ----
    try:
        import safetensors
        import transformers.modeling_utils as modeling_utils

        if not getattr(safetensors, "_omnix_metadata_patch_applied", False):
            _orig_safe_open = safetensors.safe_open

            @functools.wraps(_orig_safe_open)
            def _patched_safe_open(*args, **kwargs):
                handle = _orig_safe_open(*args, **kwargs)

                original_metadata = getattr(handle, "metadata", None)
                if callable(original_metadata):
                    def _patched_metadata():
                        value = original_metadata()
                        if value is None:
                            return {"format": "pt"}
                        if isinstance(value, dict) and "format" not in value:
                            value = dict(value)
                            value["format"] = "pt"
                        return value

                    try:
                        handle.metadata = _patched_metadata
                    except Exception:
                        class _SafeOpenHandleProxy:
                            def __init__(self, inner):
                                self._inner = inner

                            def __getattr__(self, name):
                                return getattr(self._inner, name)

                            def metadata(self):
                                value = self._inner.metadata()
                                if value is None:
                                    return {"format": "pt"}
                                if isinstance(value, dict) and "format" not in value:
                                    value = dict(value)
                                    value["format"] = "pt"
                                return value

                            def __enter__(self):
                                self._inner.__enter__()
                                return self

                            def __exit__(self, exc_type, exc, tb):
                                return self._inner.__exit__(exc_type, exc, tb)

                        handle = _SafeOpenHandleProxy(handle)

                return handle

            safetensors.safe_open = _patched_safe_open

            # Critical: Transformers may have already imported/bound safe_open.
            # Rebind its local reference too.
            if hasattr(modeling_utils, "safe_open"):
                modeling_utils.safe_open = _patched_safe_open

            safetensors._omnix_metadata_patch_applied = True
    except Exception:
        pass
        
    if not hasattr(masking_utils, "prepare_decoder_attention_mask"):
        def prepare_decoder_attention_mask(*args, **kwargs):
            return None
        masking_utils.prepare_decoder_attention_mask = prepare_decoder_attention_mask

    if not hasattr(masking_utils, "prepare_attention_mask_for_generation"):
        def prepare_attention_mask_for_generation(*args, **kwargs):
            return None
        masking_utils.prepare_attention_mask_for_generation = prepare_attention_mask_for_generation

    # ---- 5. Provide missing modeling_layers module for transformers >=4.51 ----
    if "transformers.modeling_layers" not in sys.modules:
        modeling_layers = types.ModuleType("transformers.modeling_layers")
        
        # This module was split/moved/removed in newer transformers versions
        # Add all required stubs used by Qwen3-TTS codebase
        
        try:
            from transformers.modeling_attn_mask_utils import AttentionMaskConverter
            modeling_layers.AttentionMaskConverter = AttentionMaskConverter
        except ImportError:
            pass
        
        # Dummy stubs for classes that no longer exist or were renamed
        import torch.nn as nn

        class GradientCheckpointingLayer(nn.Module):
            supports_gradient_checkpointing = True

            def __init__(self, *args, **kwargs):
                super().__init__()
        
        modeling_layers.GradientCheckpointingLayer = GradientCheckpointingLayer
        
        sys.modules["transformers.modeling_layers"] = modeling_layers

    # ---- 6. Patch missing dynamic_rope_update from modeling_rope_utils ----
    try:
        import transformers.modeling_rope_utils
        if not hasattr(transformers.modeling_rope_utils, "dynamic_rope_update"):
            logger.info("Adding missing dynamic_rope_update compatibility shim")

            def dynamic_rope_update(*args, **kwargs):
                # This function was renamed/removed in newer transformers versions
                return None, None

            transformers.modeling_rope_utils.dynamic_rope_update = dynamic_rope_update
            sys.modules["transformers.modeling_rope_utils"].dynamic_rope_update = dynamic_rope_update
    except Exception:
        pass

    # ---- 7. Patch missing layer_type_validation from configuration_utils ----
    try:
        import transformers.configuration_utils
        if not hasattr(transformers.configuration_utils, "layer_type_validation"):
            logger.info("Adding missing layer_type_validation compatibility shim")

            def layer_type_validation(*args, **kwargs):
                return None

            transformers.configuration_utils.layer_type_validation = layer_type_validation
            sys.modules["transformers.configuration_utils"].layer_type_validation = layer_type_validation
    except Exception:
        pass

    # ---- 8. Patch missing check_model_inputs from utils.generic ----
    try:
        import transformers.utils.generic
        if not hasattr(transformers.utils.generic, "check_model_inputs"):
            logger.info("Adding missing check_model_inputs compatibility shim")

            def check_model_inputs(*decorator_args, **decorator_kwargs):
                # This function was renamed/removed in newer transformers versions.
                # Mirror the decorator contract used by vendored Qwen modules:
                # support both ``@check_model_inputs`` and ``@check_model_inputs()``.
                if decorator_args and len(decorator_args) == 1 and callable(decorator_args[0]) and not decorator_kwargs:
                    return decorator_args[0]

                def decorator(fn):
                    return fn

                return decorator

            transformers.utils.generic.check_model_inputs = check_model_inputs
            sys.modules["transformers.utils.generic"].check_model_inputs = check_model_inputs
    except Exception:
        pass

    # ---- 9. Ensure we patch BEFORE the vendor module imports anything ----
    # Force clear any cached qwen_tts modules that might have already imported broken references
    for key in list(sys.modules.keys()):
        if key.startswith("app.providers.vendor.qwen"):
            del sys.modules[key]
    for key in list(sys.modules.keys()):
        if "qwen_tts" in key:
            del sys.modules[key]

    logger.info(
        "Applied Qwen3-TTS transformers compatibility shim: "
        "ALL_ATTENTION_FUNCTIONS=%s auto_docstring=%s auto_class_docstring=%s",
        hasattr(getattr(transformers, "modeling_utils", object()), "ALL_ATTENTION_FUNCTIONS"),
        hasattr(getattr(transformers, "utils", object()), "auto_docstring"),
        hasattr(getattr(transformers, "utils", object()), "auto_class_docstring"),
    )

    # ---- 5. Patch missing use_kernel_forward_from_hub for new transformers versions
    try:
        # Force import the module BEFORE patching so we patch the actual live module
        # that will be used when imports happen later
        import transformers.integrations
        
        # Also ensure sys.modules has the real module reference
        if "transformers.integrations" not in sys.modules:
            import importlib
            sys.modules["transformers.integrations"] = importlib.import_module("transformers.integrations")
            
        if not hasattr(transformers.integrations, "use_kernel_forward_from_hub"):
            # This function was removed in transformers >=4.48
            logger.info("Adding missing use_kernel_forward_from_hub compatibility shim")

            def use_kernel_forward_from_hub(*args, **kwargs):
                return lambda fn: fn

            transformers.integrations.use_kernel_forward_from_hub = use_kernel_forward_from_hub
            sys.modules["transformers.integrations"].use_kernel_forward_from_hub = use_kernel_forward_from_hub
    except Exception:
        pass

    # ---- 6. Patch missing FlashAttentionKwargs for transformers versions >=4.50
    try:
        import transformers.modeling_flash_attention_utils
        if not hasattr(transformers.modeling_flash_attention_utils, "FlashAttentionKwargs"):
            logger.info("Adding missing FlashAttentionKwargs compatibility shim")
            # This was renamed/moved/removed in newer transformers
            from typing import Optional, TypedDict

            class FlashAttentionKwargs(TypedDict, total=False):
                attention_dropout: Optional[float]
                dropout: Optional[float]
                is_causal: Optional[bool]
                use_sliding_windows: Optional[bool]
                sliding_window: Optional[int]
                softcap: Optional[float]

            transformers.modeling_flash_attention_utils.FlashAttentionKwargs = FlashAttentionKwargs
            sys.modules["transformers.modeling_flash_attention_utils"].FlashAttentionKwargs = FlashAttentionKwargs
    except Exception:
        pass


class FasterQwen3TTS:
    """
    Qwen3-TTS model with CUDA graphs for real-time inference.
    
    Compatible API with Qwen3TTSModel, but uses CUDA graph
    capture for 6-10x speedup on NVIDIA GPUs.
    """
    
    def __init__(
        self,
        base_model,
        predictor_graph,
        talker_graph,
        device: str = "cuda",
        dtype: torch.dtype = torch.bfloat16,
        max_seq_len: int = 2048,
    ):
        self.model = base_model  # The qwen-tts Qwen3TTSModel instance
        self.predictor_graph = predictor_graph
        self.talker_graph = talker_graph
        self.device = device
        self.dtype = dtype
        self.max_seq_len = max_seq_len
        self.sample_rate = 12000  # Qwen3-TTS uses 12kHz
        self._warmed_up = False
        self._voice_prompt_cache = {}  # Cache (ref_audio, ref_text) -> (vcp, ref_ids)
        self._cuda_graphs_enabled = (
            predictor_graph is not None and talker_graph is not None and str(device).startswith("cuda")
        )
        
    @classmethod
    def from_pretrained(
        cls,
        model_name: str,
        device: str = "cuda",
        dtype: Union[str, torch.dtype] = torch.bfloat16,
        attn_implementation: str = "eager",
        max_seq_len: int = 2048,
        **kwargs,
    ):
        """
        Load Qwen3-TTS model and prepare CUDA graphs.
        
        Args:
            model_name: Model path or HuggingFace Hub ID
            device: Device to use ("cuda" or "cpu")
            dtype: Data type for inference
            attn_implementation: Attention implementation (use "eager" on Jetson)
            max_seq_len: Maximum sequence length for static cache
            
        Returns:
            FasterQwen3TTS instance
        """
        if isinstance(dtype, str):
            dtype = getattr(torch, dtype)

        normalized_device = str(device or "cpu").strip().lower()
        cuda_enabled = normalized_device.startswith("cuda") and torch.cuda.is_available()

        logger.info(f"Loading Qwen3-TTS model: {model_name}")

        # Import here to avoid dependency issues (and suppress flash-attn warning)
        with suppress_flash_attn_warning():
            _ensure_transformers_qwen3_compat()
            from app.providers.vendor.qwen_tts import Qwen3TTSModel

        # Load base model using qwen-tts library
        normalized_device = (device or "cpu").strip().lower()
        use_cuda = normalized_device.startswith("cuda")

        model_kwargs = dict(kwargs or {})
        model_kwargs["dtype"] = dtype or (torch.float16 if use_cuda else torch.float32)
        model_kwargs["attn_implementation"] = attn_implementation

        if use_cuda:
            model_kwargs["device_map"] = normalized_device
        else:
            model_kwargs.pop("device_map", None)

        base_model = Qwen3TTSModel.from_pretrained(
            model_name,
            **model_kwargs,
        )

        predictor_graph = None
        talker_graph = None

        if cuda_enabled:
            from .predictor_graph import PredictorGraph
            from .talker_graph import TalkerGraph

            talker = base_model.model.talker
            talker_config = base_model.model.config.talker_config

            # Extract predictor config from loaded model
            predictor = talker.code_predictor
            pred_config = predictor.model.config
            talker_hidden = talker_config.hidden_size

            # Build CUDA graphs
            logger.info("Building CUDA graphs...")
            predictor_graph = PredictorGraph(
                predictor,
                pred_config,
                talker_hidden,
                device=normalized_device,
                dtype=dtype,
                do_sample=True,
                top_k=50,
                temperature=0.9,
            )
            
            talker_graph = TalkerGraph(
                talker.model,
                talker_config,
                device=normalized_device,
                dtype=dtype,
                max_seq_len=max_seq_len,
            )
            
            logger.info("CUDA graphs initialized (will capture on first run)")
        else:
            logger.info("CUDA unavailable or non-CUDA device requested; using non-CUDA Qwen3-TTS path without CUDA graphs")

        return cls(
            base_model=base_model,
            predictor_graph=predictor_graph,
            talker_graph=talker_graph,
            device=normalized_device,
            dtype=dtype,
            max_seq_len=max_seq_len,
        )
    
    def _warmup(self, prefill_len: int):
        """Warm up and capture CUDA graphs with given prefill length."""
        if self._warmed_up:
            return

        if not self._cuda_graphs_enabled:
            self._warmed_up = True
            return

        logger.info("Warming up CUDA graphs...")
        self.predictor_graph.capture(num_warmup=3)
        self.talker_graph.capture(prefill_len=prefill_len, num_warmup=3)
        self._warmed_up = True
        logger.info("CUDA graphs captured and ready")
    
    def generate(
        self,
        text: str,
        language: str = "English",
        max_new_tokens: int = 2048,
        temperature: float = 0.9,
        top_k: int = 50,
        do_sample: bool = True,
        repetition_penalty: float = 1.05,
    ) -> Tuple[list, int]:
        """
        Generate speech from text using default voice.
        
        Not yet implemented - use generate_voice_clone() instead.
        """
        raise NotImplementedError(
            "Default voice generation not yet implemented. "
            "Use generate_voice_clone() with reference audio."
        )
    
    def _load_ref_audio_with_silence(self, ref_audio: Union[str, Path], silence_secs: float = 0.5) -> Tuple[np.ndarray, int]:
        """Load reference audio and optionally append trailing silence.

        The ICL voice-cloning prompt ends with the last codec token of the reference
        audio, so the model's first generated token is conditioned on whatever phoneme
        the reference ends with. Appending a short silence makes the last tokens
        encode silence instead, preventing that phoneme from bleeding into the start
        of the generated speech. Set silence_secs=0 to disable this behavior.
        """
        audio, sr = sf.read(str(ref_audio), dtype="float32", always_2d=False)
        if audio.ndim > 1:
            audio = audio.mean(axis=1)  # convert to mono
        if silence_secs > 0:
            silence = np.zeros(int(silence_secs * sr), dtype=np.float32)
            audio = np.concatenate([audio, silence])
        return audio, sr

    def _prepare_generation(
        self,
        text: str,
        ref_audio: Union[str, Path],
        ref_text: str,
        language: str,
        xvec_only: bool = True,
        non_streaming_mode: bool = False,
        append_silence: bool = True,
        skip_warmup: bool = False,
        timing: Optional[dict] = None,
    ):
        """Prepare inputs for generation (shared by streaming and non-streaming).

        Args:
            xvec_only: When True (default), use only the speaker embedding (x-vector) for voice
                cloning instead of the full ICL acoustic prompt. This prevents the model from
                continuing the reference audio's last phoneme and allows natural language switching.
                When False, the full reference audio codec tokens are included in context (ICL mode).
        """
        prepare_started_at = time.perf_counter()
        tokenization_started_at = prepare_started_at
        input_texts = [self.model._build_assistant_text(text)]
        input_ids = self.model._tokenize_texts(input_texts)
        tokenization_completed_at = time.perf_counter()

        cache_key = (str(ref_audio), ref_text, xvec_only, append_silence)
        voice_prompt_started_at = tokenization_completed_at
        voice_prompt_cache_hit = cache_key in self._voice_prompt_cache
        if cache_key in self._voice_prompt_cache:
            vcp, ref_ids = self._voice_prompt_cache[cache_key]
        elif xvec_only:
            prompt_items = self.model.create_voice_clone_prompt(
                ref_audio=str(ref_audio),
                ref_text="",
                x_vector_only_mode=True,
            )
            spk_emb = prompt_items[0].ref_spk_embedding
            vcp = dict(
                ref_code=[None],
                ref_spk_embedding=[spk_emb],
                x_vector_only_mode=[True],
                icl_mode=[False],
            )
            ref_ids = [None] * len(input_ids)
            self._voice_prompt_cache[cache_key] = (vcp, ref_ids)
        else:
            silence_secs = 0.5 if append_silence else 0.0
            ref_audio_input = self._load_ref_audio_with_silence(ref_audio, silence_secs=silence_secs)
            prompt_items = self.model.create_voice_clone_prompt(
                ref_audio=ref_audio_input,
                ref_text=ref_text
            )
            vcp = self.model._prompt_items_to_voice_clone_prompt(prompt_items)

            ref_ids = []
            rt = prompt_items[0].ref_text
            if rt:
                ref_texts = [self.model._build_ref_text(rt)]
                ref_ids.append(self.model._tokenize_texts(ref_texts)[0])
            else:
                ref_ids.append(None)

            self._voice_prompt_cache[cache_key] = (vcp, ref_ids)
        voice_prompt_completed_at = time.perf_counter()

        m = self.model.model

        talker_inputs_started_at = voice_prompt_completed_at
        tie, tam, tth, tpe = self._build_talker_inputs_local(
            m=m,
            input_ids=input_ids,
            ref_ids=ref_ids,
            voice_clone_prompt=vcp,
            languages=[language] if language is not None else ["Auto"],
            speakers=None,
            non_streaming_mode=non_streaming_mode,
        )
        talker_inputs_completed_at = time.perf_counter()

        warmup_started_at = talker_inputs_completed_at
        if not skip_warmup and not self._warmed_up:
            self._warmup(tie.shape[1])
        warmup_completed_at = time.perf_counter()

        talker = m.talker
        config = m.config.talker_config
        talker.rope_deltas = None

        # For ICL mode: return ref_codes so the decoder can use them as acoustic context
        ref_codes = None
        if not xvec_only and vcp.get("ref_code") and vcp["ref_code"][0] is not None:
            ref_codes = vcp["ref_code"][0]

        if timing is not None:
            timing.update({
                "prepare_total_ms": (warmup_completed_at - prepare_started_at) * 1000.0,
                "input_tokenization_ms": (
                    tokenization_completed_at - tokenization_started_at
                ) * 1000.0,
                "voice_prompt_prepare_ms": (
                    voice_prompt_completed_at - voice_prompt_started_at
                ) * 1000.0,
                "voice_prompt_cache_hit": voice_prompt_cache_hit,
                "talker_input_construction_ms": (
                    talker_inputs_completed_at - talker_inputs_started_at
                ) * 1000.0,
                "graph_warmup_ms": (
                    warmup_completed_at - warmup_started_at
                ) * 1000.0,
                "prompt_tokens": int(tie.shape[1]),
            })

        return m, talker, config, tie, tam, tth, tpe, ref_codes

    def _prepare_generation_custom(
        self,
        text: str,
        language: str,
        speaker: Optional[str],
        instruct: Optional[str] = None,
    ):
        input_texts = [self.model._build_assistant_text(text)]
        input_ids = self.model._tokenize_texts(input_texts)

        instruct_ids = []
        if instruct is None or instruct == "":
            instruct_ids.append(None)
        else:
            instruct_ids.append(self.model._tokenize_texts([self.model._build_instruct_text(instruct)])[0])

        m = self.model.model
        tie, tam, tth, tpe = self._build_talker_inputs_local(
            m=m,
            input_ids=input_ids,
            ref_ids=[None],
            voice_clone_prompt=None,
            languages=[language] if language is not None else ["Auto"],
            speakers=[speaker],
            non_streaming_mode=False,
            instruct_ids=instruct_ids,
        )

        if not self._warmed_up:
            self._warmup(tie.shape[1])

        talker = m.talker
        config = m.config.talker_config
        talker.rope_deltas = None

        return m, talker, config, tie, tam, tth, tpe

    def _build_talker_inputs_local(
        self,
        m,
        input_ids,
        ref_ids,
        voice_clone_prompt,
        languages,
        speakers,
        non_streaming_mode: bool,
        instruct_ids=None,
    ):
        """Local copy of upstream talker input building for qwen-tts main repo."""
        talker_input_embeds = [[] for _ in range(len(input_ids))]

        voice_clone_spk_embeds = None
        if voice_clone_prompt is not None:
            voice_clone_spk_embeds = m.generate_speaker_prompt(voice_clone_prompt)

        if instruct_ids is not None:
            for index, instruct_id in enumerate(instruct_ids):
                if instruct_id is not None:
                    talker_input_embeds[index].append(
                        m.talker.text_projection(m.talker.get_text_embeddings()(instruct_id))
                    )

        if speakers is None:
            speakers = [None] * len(input_ids)

        trailing_text_hiddens = []
        tts_pad_embed = None

        for index, (input_id, language, speaker) in enumerate(zip(input_ids, languages, speakers)):
            if voice_clone_spk_embeds is None:
                if speaker == "" or speaker is None:
                    speaker_embed = None
                else:
                    if speaker.lower() not in m.config.talker_config.spk_id:
                        raise NotImplementedError(f"Speaker {speaker} not implemented")
                    spk_id = m.config.talker_config.spk_id[speaker.lower()]
                    speaker_embed = m.talker.get_input_embeddings()(
                        torch.tensor(spk_id, device=m.talker.device, dtype=input_id.dtype)
                    )
            else:
                if voice_clone_prompt["x_vector_only_mode"][index] or voice_clone_prompt["icl_mode"][index]:
                    speaker_embed = voice_clone_spk_embeds[index]
                else:
                    speaker_embed = None

            assert language is not None
            if language.lower() == "auto":
                language_id = None
            else:
                if language.lower() not in m.config.talker_config.codec_language_id:
                    raise NotImplementedError(f"Language {language} not implemented")
                language_id = m.config.talker_config.codec_language_id[language.lower()]

            if (
                language.lower() in ["chinese", "auto"]
                and speaker not in ("", None)
                and m.config.talker_config.spk_is_dialect[speaker.lower()]
            ):
                dialect = m.config.talker_config.spk_is_dialect[speaker.lower()]
                language_id = m.config.talker_config.codec_language_id[dialect]

            tts_bos_embed, tts_eos_embed, tts_pad_embed = m.talker.text_projection(
                m.talker.get_text_embeddings()(
                    torch.tensor(
                        [[m.config.tts_bos_token_id, m.config.tts_eos_token_id, m.config.tts_pad_token_id]],
                        device=m.talker.device,
                        dtype=input_id.dtype,
                    )
                )
            ).chunk(3, dim=1)

            if language_id is None:
                codec_prefill_list = [[
                    m.config.talker_config.codec_nothink_id,
                    m.config.talker_config.codec_think_bos_id,
                    m.config.talker_config.codec_think_eos_id,
                ]]
            else:
                codec_prefill_list = [[
                    m.config.talker_config.codec_think_id,
                    m.config.talker_config.codec_think_bos_id,
                    language_id,
                    m.config.talker_config.codec_think_eos_id,
                ]]

            codec_input_emebdding_0 = m.talker.get_input_embeddings()(
                torch.tensor(codec_prefill_list, device=m.talker.device, dtype=input_id.dtype)
            )
            codec_input_emebdding_1 = m.talker.get_input_embeddings()(
                torch.tensor(
                    [[m.config.talker_config.codec_pad_id, m.config.talker_config.codec_bos_id]],
                    device=m.talker.device,
                    dtype=input_id.dtype,
                )
            )
            if speaker_embed is None:
                codec_input_emebdding = torch.cat([codec_input_emebdding_0, codec_input_emebdding_1], dim=1)
            else:
                codec_input_emebdding = torch.cat([codec_input_emebdding_0, speaker_embed.view(1, 1, -1), codec_input_emebdding_1], dim=1)

            _talker_input_embed_role = m.talker.text_projection(
                m.talker.get_text_embeddings()(input_id[:, :3])
            )
            _talker_input_embed = torch.cat(
                (
                    tts_pad_embed.expand(-1, codec_input_emebdding.shape[1] - 2, -1),
                    tts_bos_embed,
                ),
                dim=1,
            ) + codec_input_emebdding[:, :-1]

            talker_input_embed = torch.cat((_talker_input_embed_role, _talker_input_embed), dim=1)

            if (
                voice_clone_prompt is not None
                and voice_clone_prompt.get("ref_code", None) is not None
                and voice_clone_prompt["icl_mode"][index]
            ):
                icl_input_embed, trailing_text_hidden = m.generate_icl_prompt(
                    text_id=input_id[:, 3:-5],
                    ref_id=ref_ids[index][:, 3:-2],
                    ref_code=voice_clone_prompt["ref_code"][index].to(m.talker.device),
                    tts_pad_embed=tts_pad_embed,
                    tts_eos_embed=tts_eos_embed,
                    non_streaming_mode=non_streaming_mode,
                )
                talker_input_embed = torch.cat([talker_input_embed, icl_input_embed], dim=1)
            else:
                talker_input_embed = torch.cat(
                    [
                        talker_input_embed,
                        m.talker.text_projection(
                            m.talker.get_text_embeddings()(input_id[:, 3:4])
                        )
                        + codec_input_emebdding[:, -1:],
                    ],
                    dim=1,
                )
                if non_streaming_mode:
                    talker_input_embed = talker_input_embed[:, :-1]
                    talker_input_embed = torch.cat(
                        [
                            talker_input_embed,
                            torch.cat(
                                (
                                    m.talker.text_projection(
                                        m.talker.get_text_embeddings()(input_id[:, 3:-5])
                                    ),
                                    tts_eos_embed,
                                ),
                                dim=1,
                            )
                            + m.talker.get_input_embeddings()(
                                torch.tensor(
                                    [[m.config.talker_config.codec_pad_id] * (input_id[:, 3:-5].shape[1] + 1)],
                                    device=m.talker.device,
                                    dtype=input_id.dtype,
                                )
                            ),
                            tts_pad_embed
                            + m.talker.get_input_embeddings()(
                                torch.tensor(
                                    [[m.config.talker_config.codec_bos_id]],
                                    device=m.talker.device,
                                    dtype=input_id.dtype,
                                )
                            ),
                        ],
                        dim=1,
                    )
                    trailing_text_hidden = tts_pad_embed
                else:
                    trailing_text_hidden = torch.cat(
                        (
                            m.talker.text_projection(
                                m.talker.get_text_embeddings()(input_id[:, 4:-5])
                            ),
                            tts_eos_embed,
                        ),
                        dim=1,
                    )

            talker_input_embeds[index].append(talker_input_embed)
            trailing_text_hiddens.append(trailing_text_hidden)

        for index, talker_input_embed in enumerate(talker_input_embeds):
            talker_input_embeds[index] = torch.cat([item for item in talker_input_embed if item is not None], dim=1)

        original_lengths = torch.tensor([t.shape[1] for t in talker_input_embeds])
        sequences = [t.squeeze(0) for t in talker_input_embeds]
        sequences_reversed = [t.flip(dims=[0]) for t in sequences]
        padded_reversed = torch.nn.utils.rnn.pad_sequence(
            sequences_reversed,
            batch_first=True,
            padding_value=0.0,
        )
        talker_input_embeds = padded_reversed.flip(dims=[1])

        batch_size, max_len = talker_input_embeds.shape[0], talker_input_embeds.shape[1]
        indices = torch.arange(max_len).expand(batch_size, -1)
        num_pads = max_len - original_lengths
        talker_attention_mask = (indices >= num_pads.unsqueeze(1)).long().to(talker_input_embeds.device)

        pad_embedding_vector = tts_pad_embed.squeeze()
        sequences_to_pad = [t.squeeze(0) for t in trailing_text_hiddens]
        trailing_text_original_lengths = [s.shape[0] for s in sequences_to_pad]
        padded_hiddens = torch.nn.utils.rnn.pad_sequence(
            sequences_to_pad,
            batch_first=True,
            padding_value=0.0,
        )
        arange_tensor = torch.arange(max(trailing_text_original_lengths), device=padded_hiddens.device).expand(
            len(trailing_text_original_lengths), -1
        )
        lengths_tensor = torch.tensor(trailing_text_original_lengths, device=padded_hiddens.device).unsqueeze(1)
        padding_mask = arange_tensor >= lengths_tensor
        padded_hiddens[padding_mask] = pad_embedding_vector
        trailing_text_hiddens = padded_hiddens

        return talker_input_embeds, talker_attention_mask, trailing_text_hiddens, tts_pad_embed

    @torch.inference_mode()
    def generate_voice_clone(
        self,
        text: str,
        language: str,
        ref_audio: Union[str, Path],
        ref_text: str,
        max_new_tokens: int = 2048,
        min_new_tokens: int = 2,
        temperature: float = 0.9,
        top_k: int = 50,
        top_p: float = 1.0,
        do_sample: bool = True,
        repetition_penalty: float = 1.05,
        xvec_only: bool = True,
        non_streaming_mode: bool = True,
        append_silence: bool = True,
        parity_mode: bool = False,
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> Tuple[list, int]:
        """
        Generate speech with voice cloning using reference audio.

        Args:
            text: Text to synthesize
            language: Target language
            ref_audio: Path to reference audio file
            ref_text: Transcription of reference audio
            max_new_tokens: Maximum tokens to generate
            min_new_tokens: Minimum tokens before EOS is allowed
            temperature: Sampling temperature
            top_k: Top-k sampling
            top_p: Top-p (nucleus) sampling
            do_sample: Whether to sample
            repetition_penalty: Repetition penalty
            xvec_only: When True (default), use only the speaker embedding for voice cloning.
                This prevents phoneme bleed-through from the reference and allows clean
                language switching. Set to False for full ICL mode (reference audio in context).
            non_streaming_mode: Match upstream non-streaming prompt layout. Default True for better non-streaming quality.
            parity_mode: When True, disables CUDA graph decode and uses the dynamic generation path.
            progress_callback: Optional callback receiving generated and maximum codec steps.

        Returns:
            Tuple of ([audio_waveform], sample_rate)
        """
        from .generate import fast_generate

        m, talker, config, tie, tam, tth, tpe, ref_codes = self._prepare_generation(
            text,
            ref_audio,
            ref_text,
            language=language,
            xvec_only=xvec_only,
            non_streaming_mode=non_streaming_mode,
            append_silence=append_silence,
            skip_warmup=parity_mode,
        )

        codec_ids, timing = fast_generate(
            talker=talker,
            talker_input_embeds=tie,
            attention_mask=tam,
            trailing_text_hiddens=tth,
            tts_pad_embed=tpe,
            config=config,
            predictor_graph=self.predictor_graph,
            talker_graph=self.talker_graph,
            max_new_tokens=max_new_tokens,
            min_new_tokens=min_new_tokens,
            temperature=temperature,
            top_k=top_k,
            top_p=top_p,
            do_sample=do_sample,
            repetition_penalty=repetition_penalty,
            parity_mode=parity_mode,
            progress_callback=progress_callback,
        )

        if codec_ids is None:
            logger.warning("Generation returned no tokens")
            return [np.zeros(1, dtype=np.float32)], self.sample_rate

        # In ICL mode: prepend reference codes before decoding so the codec decoder
        # has acoustic context from the reference audio (matches official implementation).
        speech_tokenizer = m.speech_tokenizer
        if ref_codes is not None:
            ref_codes_dev = ref_codes.to(codec_ids.device)
            codes_for_decode = torch.cat([ref_codes_dev, codec_ids], dim=0)
        else:
            codes_for_decode = codec_ids
        audio_list, sr = speech_tokenizer.decode({"audio_codes": codes_for_decode.unsqueeze(0)})

        # Convert to numpy and trim off the reference audio portion
        ref_len = ref_codes.shape[0] if ref_codes is not None else 0
        total_len = codes_for_decode.shape[0]
        audio_arrays = []
        for a in audio_list:
            if hasattr(a, 'cpu'):  # torch tensor
                a = a.flatten().cpu().numpy()
            else:  # already numpy
                a = a.flatten() if hasattr(a, 'flatten') else a
            if ref_len > 0:
                cut = int(ref_len / max(total_len, 1) * len(a))
                a = a[cut:]
            audio_arrays.append(a)
        
        n_steps = timing['steps']
        audio_duration = n_steps / 12.0  # 12 Hz codec
        total_time = timing['prefill_ms']/1000 + timing['decode_s']
        rtf = audio_duration / total_time if total_time > 0 else 0
        
        logger.info(
            f"Generated {audio_duration:.2f}s audio in {total_time:.2f}s "
            f"({timing['ms_per_step']:.1f}ms/step, RTF: {rtf:.2f})"
        )
        
        return audio_arrays, sr

    @torch.inference_mode()
    def generate_voice_clone_streaming(
        self,
        text: str,
        language: str,
        ref_audio: Union[str, Path],
        ref_text: str,
        max_new_tokens: int = 2048,
        min_new_tokens: int = 2,
        temperature: float = 0.9,
        top_k: int = 50,
        top_p: float = 1.0,
        do_sample: bool = True,
        repetition_penalty: float = 1.05,
        chunk_size: int = 12,
        xvec_only: bool = True,
        non_streaming_mode: bool = True,
        append_silence: bool = True,
        parity_mode: bool = False,
    ) -> Generator[Tuple[np.ndarray, int, dict], None, None]:
        """
        Stream voice-cloned speech generation, yielding audio chunks.

        Same as generate_voice_clone() but yields (audio_chunk, sample_rate, timing)
        tuples every chunk_size codec steps (~chunk_size/12 seconds of audio).

        Args:
            text: Text to synthesize
            language: Target language
            ref_audio: Path to reference audio file
            ref_text: Transcription of reference audio
            max_new_tokens: Maximum tokens to generate
            min_new_tokens: Minimum tokens before EOS is allowed
            temperature: Sampling temperature
            top_k: Top-k sampling
            top_p: Top-p (nucleus) sampling
            do_sample: Whether to sample
            repetition_penalty: Repetition penalty
            chunk_size: Codec steps per chunk (12 = ~1 second)
            xvec_only: When True (default), use only the speaker embedding for voice cloning.
                This prevents phoneme bleed-through from the reference and allows clean
                language switching. Set to False for full ICL mode (reference audio in context).
            non_streaming_mode: When True (default), prefill the full target text before
                streaming decode. Set to False to feed text token-by-token during decode.
            parity_mode: When True, disables CUDA graphs and uses dynamic cache streaming.

        Yields:
            Tuple of (audio_chunk_numpy, sample_rate, timing_dict)
        """
        from .streaming import fast_generate_streaming, parity_generate_streaming

        model_started_at = time.perf_counter()
        preparation_timing = {}
        m, talker, config, tie, tam, tth, tpe, ref_codes = self._prepare_generation(
            text,
            ref_audio,
            ref_text,
            language=language,
            xvec_only=xvec_only,
            non_streaming_mode=non_streaming_mode,
            append_silence=append_silence,
            skip_warmup=parity_mode,
            timing=preparation_timing,
        )

        speech_tokenizer = m.speech_tokenizer

        # Hybrid decode strategy:
        # 1. Accumulated decode for early chunks (correct, calibrates samples_per_frame)
        # 2. Sliding window with 25-frame left context once calibrated (constant cost)
        # This avoids boundary artifacts (pops) while keeping decode cost bounded.
        context_frames = 25
        min_calibration_frames = max(context_frames, chunk_size)
        all_codes = []
        prev_gen_audio_len = 0  # tracks position within the generated (non-ref) audio
        samples_per_frame = None

        stream_fn = parity_generate_streaming if parity_mode else fast_generate_streaming
        stream_kwargs = dict(
            talker=talker,
            talker_input_embeds=tie,
            attention_mask=tam,
            trailing_text_hiddens=tth,
            tts_pad_embed=tpe,
            config=config,
            max_new_tokens=max_new_tokens,
            min_new_tokens=min_new_tokens,
            temperature=temperature,
            top_k=top_k,
            top_p=top_p,
            do_sample=do_sample,
            repetition_penalty=repetition_penalty,
            chunk_size=chunk_size,
        )
        if not parity_mode:
            stream_kwargs["predictor_graph"] = self.predictor_graph
            stream_kwargs["talker_graph"] = self.talker_graph

        for codec_chunk, timing in stream_fn(**stream_kwargs):
            codec_ready_at = time.perf_counter()
            is_first_codec_chunk = not all_codes
            timing = dict(timing)
            if is_first_codec_chunk:
                timing.update(preparation_timing)
                timing["model_to_first_codec_ms"] = (
                    codec_ready_at - model_started_at
                ) * 1000.0
            all_codes.append(codec_chunk)
            n_new = codec_chunk.shape[0]
            all_flat = torch.cat(all_codes, dim=0)
            n_total = all_flat.shape[0]

            if samples_per_frame is None:
                # Phase 1: accumulated decode until we can calibrate.
                # In ICL mode prepend reference codes so the codec decoder has acoustic
                # context from the reference audio (matches official implementation).
                if ref_codes is not None:
                    codes_input = torch.cat([ref_codes.to(all_flat.device), all_flat], dim=0)
                else:
                    codes_input = all_flat
                speech_decode_started_at = time.perf_counter()
                audio_list, sr = speech_tokenizer.decode(
                    {"audio_codes": codes_input.unsqueeze(0)}
                )
                speech_decode_returned_at = time.perf_counter()
                audio = audio_list[0]
                tensor_conversion_started_at = time.perf_counter()
                if hasattr(audio, 'cpu'):
                    audio = audio.flatten().cpu().numpy()
                else:
                    audio = audio.flatten() if hasattr(audio, 'flatten') else audio
                tensor_conversion_completed_at = time.perf_counter()

                # Separate out reference audio portion; track position in generated audio only
                if ref_codes is not None:
                    ref_len = ref_codes.shape[0]
                    total_len = codes_input.shape[0]
                    ref_audio_cut = int(ref_len / max(total_len, 1) * len(audio))
                    gen_audio = audio[ref_audio_cut:]
                else:
                    gen_audio = audio

                new_audio = gen_audio[prev_gen_audio_len:]
                prev_gen_audio_len = len(gen_audio)

                if n_total >= min_calibration_frames:
                    samples_per_frame = len(gen_audio) / n_total
            else:
                # Phase 2: sliding window with left context
                ctx_start = max(0, n_total - n_new - context_frames)
                window = all_flat[ctx_start:]
                n_ctx = window.shape[0] - n_new

                speech_decode_started_at = time.perf_counter()
                audio_list, sr = speech_tokenizer.decode(
                    {"audio_codes": window.unsqueeze(0)}
                )
                speech_decode_returned_at = time.perf_counter()
                audio = audio_list[0]
                tensor_conversion_started_at = time.perf_counter()
                if hasattr(audio, 'cpu'):
                    audio = audio.flatten().cpu().numpy()
                else:
                    audio = audio.flatten() if hasattr(audio, 'flatten') else audio
                tensor_conversion_completed_at = time.perf_counter()

                if n_ctx > 0:
                    ctx_samples = int(round(n_ctx * samples_per_frame))
                    new_audio = audio[ctx_samples:]
                else:
                    new_audio = audio

            if is_first_codec_chunk:
                timing.update({
                    "speech_tokenizer_decode_call_ms": (
                        speech_decode_returned_at - speech_decode_started_at
                    ) * 1000.0,
                    "tensor_to_numpy_ms": (
                        tensor_conversion_completed_at - tensor_conversion_started_at
                    ) * 1000.0,
                    "speech_tokenizer_decode_ms": (
                        tensor_conversion_completed_at - speech_decode_started_at
                    ) * 1000.0,
                    "codec_to_audio_ms": (
                        tensor_conversion_completed_at - codec_ready_at
                    ) * 1000.0,
                    "model_to_first_audio_ms": (
                        tensor_conversion_completed_at - model_started_at
                    ) * 1000.0,
                    "first_audio_samples": int(len(new_audio)),
                })

            yield new_audio, sr, timing

    @torch.inference_mode()
    def generate_custom_voice(
        self,
        text: str,
        speaker: str,
        language: str,
        instruct: Optional[str] = None,
        max_new_tokens: int = 2048,
        min_new_tokens: int = 2,
        temperature: float = 0.9,
        top_k: int = 50,
        top_p: float = 1.0,
        do_sample: bool = True,
        repetition_penalty: float = 1.05,
    ) -> Tuple[list, int]:
        if self.model.model.tts_model_type != "custom_voice":
            raise ValueError("Loaded model does not support custom voice generation")

        self.model._validate_languages([language])
        self.model._validate_speakers([speaker])

        if self.model.model.tts_model_size in "0b6":
            instruct = None

        from .generate import fast_generate

        m, talker, config, tie, tam, tth, tpe = self._prepare_generation_custom(
            text=text,
            language=language,
            speaker=speaker,
            instruct=instruct,
        )

        codec_ids, timing = fast_generate(
            talker=talker,
            talker_input_embeds=tie,
            attention_mask=tam,
            trailing_text_hiddens=tth,
            tts_pad_embed=tpe,
            config=config,
            predictor_graph=self.predictor_graph,
            talker_graph=self.talker_graph,
            max_new_tokens=max_new_tokens,
            min_new_tokens=min_new_tokens,
            temperature=temperature,
            top_k=top_k,
            top_p=top_p,
            do_sample=do_sample,
            repetition_penalty=repetition_penalty,
        )

        if codec_ids is None:
            logger.warning("Generation returned no tokens")
            return [np.zeros(1, dtype=np.float32)], self.sample_rate

        speech_tokenizer = m.speech_tokenizer
        audio_list, sr = speech_tokenizer.decode({"audio_codes": codec_ids.unsqueeze(0)})

        audio_arrays = []
        for a in audio_list:
            if hasattr(a, "cpu"):
                audio_arrays.append(a.flatten().cpu().numpy())
            else:
                audio_arrays.append(a.flatten() if hasattr(a, "flatten") else a)

        n_steps = timing["steps"]
        audio_duration = n_steps / 12.0
        total_time = timing["prefill_ms"] / 1000 + timing["decode_s"]
        rtf = audio_duration / total_time if total_time > 0 else 0

        logger.info(
            f"Generated {audio_duration:.2f}s audio in {total_time:.2f}s "
            f"({timing['ms_per_step']:.1f}ms/step, RTF: {rtf:.2f})"
        )

        return audio_arrays, sr

    @torch.inference_mode()
    def generate_custom_voice_streaming(
        self,
        text: str,
        speaker: str,
        language: str,
        instruct: Optional[str] = None,
        max_new_tokens: int = 2048,
        min_new_tokens: int = 2,
        temperature: float = 0.9,
        top_k: int = 50,
        top_p: float = 1.0,
        do_sample: bool = True,
        repetition_penalty: float = 1.05,
        chunk_size: int = 12,
    ) -> Generator[Tuple[np.ndarray, int, dict], None, None]:
        if self.model.model.tts_model_type != "custom_voice":
            raise ValueError("Loaded model does not support custom voice generation")

        self.model._validate_languages([language])
        self.model._validate_speakers([speaker])

        if self.model.model.tts_model_size in "0b6":
            instruct = None

        from .streaming import fast_generate_streaming

        m, talker, config, tie, tam, tth, tpe = self._prepare_generation_custom(
            text=text,
            language=language,
            speaker=speaker,
            instruct=instruct,
        )

        speech_tokenizer = m.speech_tokenizer

        context_frames = 25
        min_calibration_frames = max(context_frames, chunk_size)
        all_codes = []
        prev_audio_len = 0
        samples_per_frame = None

        for codec_chunk, timing in fast_generate_streaming(
            talker=talker,
            talker_input_embeds=tie,
            attention_mask=tam,
            trailing_text_hiddens=tth,
            tts_pad_embed=tpe,
            config=config,
            predictor_graph=self.predictor_graph,
            talker_graph=self.talker_graph,
            max_new_tokens=max_new_tokens,
            min_new_tokens=min_new_tokens,
            temperature=temperature,
            top_k=top_k,
            top_p=top_p,
            do_sample=do_sample,
            repetition_penalty=repetition_penalty,
            chunk_size=chunk_size,
        ):
            all_codes.append(codec_chunk)
            n_new = codec_chunk.shape[0]
            all_flat = torch.cat(all_codes, dim=0)
            n_total = all_flat.shape[0]

            if samples_per_frame is None:
                audio_list, sr = speech_tokenizer.decode({"audio_codes": all_flat.unsqueeze(0)})
                audio = audio_list[0]
                if hasattr(audio, "cpu"):
                    audio = audio.flatten().cpu().numpy()
                else:
                    audio = audio.flatten() if hasattr(audio, "flatten") else audio

                new_audio = audio[prev_audio_len:]
                prev_audio_len = len(audio)

                if n_total >= min_calibration_frames:
                    samples_per_frame = len(audio) / n_total
            else:
                ctx_start = max(0, n_total - n_new - context_frames)
                window = all_flat[ctx_start:]
                n_ctx = window.shape[0] - n_new

                audio_list, sr = speech_tokenizer.decode({"audio_codes": window.unsqueeze(0)})
                audio = audio_list[0]
                if hasattr(audio, "cpu"):
                    audio = audio.flatten().cpu().numpy()
                else:
                    audio = audio.flatten() if hasattr(audio, "flatten") else audio

                if n_ctx > 0:
                    ctx_samples = int(round(n_ctx * samples_per_frame))
                    new_audio = audio[ctx_samples:]
                else:
                    new_audio = audio

            yield new_audio, sr, timing

    @torch.inference_mode()
    def generate_voice_design(
        self,
        text: str,
        instruct: str,
        language: str,
        max_new_tokens: int = 2048,
        min_new_tokens: int = 2,
        temperature: float = 0.9,
        top_k: int = 50,
        top_p: float = 1.0,
        do_sample: bool = True,
        repetition_penalty: float = 1.05,
    ) -> Tuple[list, int]:
        if self.model.model.tts_model_type != "voice_design":
            raise ValueError("Loaded model does not support voice design generation")

        self.model._validate_languages([language])

        from .generate import fast_generate

        m, talker, config, tie, tam, tth, tpe = self._prepare_generation_custom(
            text=text,
            language=language,
            speaker=None,
            instruct=instruct,
        )

        codec_ids, timing = fast_generate(
            talker=talker,
            talker_input_embeds=tie,
            attention_mask=tam,
            trailing_text_hiddens=tth,
            tts_pad_embed=tpe,
            config=config,
            predictor_graph=self.predictor_graph,
            talker_graph=self.talker_graph,
            max_new_tokens=max_new_tokens,
            min_new_tokens=min_new_tokens,
            temperature=temperature,
            top_k=top_k,
            top_p=top_p,
            do_sample=do_sample,
            repetition_penalty=repetition_penalty,
        )

        if codec_ids is None:
            logger.warning("Generation returned no tokens")
            return [np.zeros(1, dtype=np.float32)], self.sample_rate

        speech_tokenizer = m.speech_tokenizer
        audio_list, sr = speech_tokenizer.decode({"audio_codes": codec_ids.unsqueeze(0)})

        audio_arrays = []
        for a in audio_list:
            if hasattr(a, "cpu"):
                audio_arrays.append(a.flatten().cpu().numpy())
            else:
                audio_arrays.append(a.flatten() if hasattr(a, "flatten") else a)

        n_steps = timing["steps"]
        audio_duration = n_steps / 12.0
        total_time = timing["prefill_ms"] / 1000 + timing["decode_s"]
        rtf = audio_duration / total_time if total_time > 0 else 0

        logger.info(
            f"Generated {audio_duration:.2f}s audio in {total_time:.2f}s "
            f"({timing['ms_per_step']:.1f}ms/step, RTF: {rtf:.2f})"
        )

        return audio_arrays, sr

    @torch.inference_mode()
    def generate_voice_design_streaming(
        self,
        text: str,
        instruct: str,
        language: str,
        max_new_tokens: int = 2048,
        min_new_tokens: int = 2,
        temperature: float = 0.9,
        top_k: int = 50,
        top_p: float = 1.0,
        do_sample: bool = True,
        repetition_penalty: float = 1.05,
        chunk_size: int = 12,
    ) -> Generator[Tuple[np.ndarray, int, dict], None, None]:
        if self.model.model.tts_model_type != "voice_design":
            raise ValueError("Loaded model does not support voice design generation")

        self.model._validate_languages([language])

        from .streaming import fast_generate_streaming

        m, talker, config, tie, tam, tth, tpe = self._prepare_generation_custom(
            text=text,
            language=language,
            speaker=None,
            instruct=instruct,
        )

        speech_tokenizer = m.speech_tokenizer

        context_frames = 25
        min_calibration_frames = max(context_frames, chunk_size)
        all_codes = []
        prev_audio_len = 0
        samples_per_frame = None

        for codec_chunk, timing in fast_generate_streaming(
            talker=talker,
            talker_input_embeds=tie,
            attention_mask=tam,
            trailing_text_hiddens=tth,
            tts_pad_embed=tpe,
            config=config,
            predictor_graph=self.predictor_graph,
            talker_graph=self.talker_graph,
            max_new_tokens=max_new_tokens,
            min_new_tokens=min_new_tokens,
            temperature=temperature,
            top_k=top_k,
            top_p=top_p,
            do_sample=do_sample,
            repetition_penalty=repetition_penalty,
            chunk_size=chunk_size,
        ):
            all_codes.append(codec_chunk)
            n_new = codec_chunk.shape[0]
            all_flat = torch.cat(all_codes, dim=0)
            n_total = all_flat.shape[0]

            if samples_per_frame is None:
                audio_list, sr = speech_tokenizer.decode({"audio_codes": all_flat.unsqueeze(0)})
                audio = audio_list[0]
                if hasattr(audio, "cpu"):
                    audio = audio.flatten().cpu().numpy()
                else:
                    audio = audio.flatten() if hasattr(audio, "flatten") else audio

                new_audio = audio[prev_audio_len:]
                prev_audio_len = len(audio)

                if n_total >= min_calibration_frames:
                    samples_per_frame = len(audio) / n_total
            else:
                ctx_start = max(0, n_total - n_new - context_frames)
                window = all_flat[ctx_start:]
                n_ctx = window.shape[0] - n_new

                audio_list, sr = speech_tokenizer.decode({"audio_codes": window.unsqueeze(0)})
                audio = audio_list[0]
                if hasattr(audio, "cpu"):
                    audio = audio.flatten().cpu().numpy()
                else:
                    audio = audio.flatten() if hasattr(audio, "flatten") else audio

                if n_ctx > 0:
                    ctx_samples = int(round(n_ctx * samples_per_frame))
                    new_audio = audio[ctx_samples:]
                else:
                    new_audio = audio

            yield new_audio, sr, timing
