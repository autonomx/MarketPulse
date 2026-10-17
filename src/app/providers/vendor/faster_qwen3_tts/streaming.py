#!/usr/bin/env python3
"""
Streaming generation with CUDA graphs for both predictor and talker.

Yields codec ID chunks during generation instead of collecting all at once.
CUDA graph usage is identical to non-streaming — same per-step performance.
"""
import time
from typing import Generator, Tuple

import torch

from .predictor_graph import PredictorGraph
from .sampling import apply_repetition_penalty, sample_logits
from .talker_graph import TalkerGraph
from .termination import (
    StreamingEosDeadlines,
    StreamingEosPolicy,
    TerminationReason,
    classify_after_sample,
    eos_logit_bias,
    resolve_eos_deadlines,
    termination_metadata,
)


def _as_int(value) -> int:
    if hasattr(value, "item"):
        return int(value.item())
    return int(value)


def _sample_with_eos_policy(
    logits: torch.Tensor,
    *,
    eos_id: int,
    generation_step: int,
    deadlines: StreamingEosDeadlines,
    policy: StreamingEosPolicy,
    temperature: float,
    top_k: int,
    top_p: float,
    do_sample: bool,
    suppress_mask: torch.Tensor,
    suppress_eos: bool,
) -> tuple[torch.Tensor, float]:
    bias = eos_logit_bias(generation_step, deadlines, policy)
    if bias > 0:
        logits = logits.clone()
        logits[..., eos_id] += bias
    token = sample_logits(
        logits,
        temperature=temperature,
        top_k=top_k,
        top_p=top_p,
        do_sample=do_sample,
        suppress_mask=suppress_mask,
        suppress_tokens=[eos_id] if suppress_eos else None,
    )
    return token, bias


def _timing_payload(
    *,
    chunk_index: int,
    chunk_steps: int,
    prefill_ms: float,
    decode_ms: float,
    total_steps: int,
    is_final: bool,
    termination_reason: TerminationReason | None,
    generation_step: int,
    text_context_steps: int,
    eos_bias_applied: float,
    deadlines: StreamingEosDeadlines,
    policy: StreamingEosPolicy,
) -> dict:
    payload = {
        "chunk_index": chunk_index,
        "chunk_steps": chunk_steps,
        "prefill_ms": prefill_ms,
        "decode_ms": decode_ms,
        "total_steps_so_far": total_steps,
        "is_final": is_final,
    }
    if is_final and termination_reason is not None:
        payload.update(
            termination_metadata(
                reason=termination_reason,
                generated_steps=total_steps,
                generation_step=generation_step,
                text_context_steps=text_context_steps,
                eos_bias_applied=eos_bias_applied,
                deadlines=deadlines,
                policy=policy,
            )
        )
    return payload


def _codec_suppress_mask(vocab_size: int, eos_id: int, device: torch.device) -> torch.Tensor:
    """Build the reserved-codec mask with two CUDA writes instead of ~1,024 scalar writes."""
    suppress_mask = torch.zeros(vocab_size, dtype=torch.bool, device=device)
    suppress_start = max(0, vocab_size - 1024)
    suppress_mask[suppress_start:] = True
    if suppress_start <= eos_id < vocab_size:
        suppress_mask[eos_id] = False
    return suppress_mask


@torch.inference_mode()
def fast_generate_streaming(
    talker,
    talker_input_embeds: torch.Tensor,
    attention_mask: torch.Tensor,
    trailing_text_hiddens: torch.Tensor,
    tts_pad_embed: torch.Tensor,
    config,
    predictor_graph: PredictorGraph,
    talker_graph: TalkerGraph,
    max_new_tokens: int = 2048,
    min_new_tokens: int = 2,
    temperature: float = 0.9,
    top_k: int = 50,
    top_p: float = 1.0,
    do_sample: bool = True,
    repetition_penalty: float = 1.05,
    chunk_size: int = 12,
    eos_policy: StreamingEosPolicy | None = None,
) -> Generator[Tuple[torch.Tensor, dict], None, None]:
    """
    Streaming autoregressive generation with CUDA-graphed predictor and talker.

    Natural EOS remains the preferred stop. Near the phrase's text-relative
    token budget, EOS is progressively encouraged and then generation is
    deterministically stopped before the catastrophic hard ceiling.
    """
    eos_id = config.codec_eos_token_id
    vocab_size = config.vocab_size
    device = talker_input_embeds.device
    text_context_steps = int(trailing_text_hiddens.shape[1])
    policy = eos_policy or StreamingEosPolicy()
    deadlines = resolve_eos_deadlines(
        max_new_tokens=max_new_tokens,
        text_context_steps=text_context_steps,
        policy=policy,
    )

    suppress_mask = _codec_suppress_mask(vocab_size, eos_id, device)

    predictor = talker.code_predictor
    talker_codec_embed = talker.get_input_embeddings()
    talker_codec_head = talker.codec_head
    predictor_codec_embeds = predictor.get_input_embeddings()
    num_code_groups = config.num_code_groups

    t_start = time.time()
    out = talker.forward(
        inputs_embeds=talker_input_embeds,
        attention_mask=attention_mask,
        use_cache=True,
        output_hidden_states=True,
        return_dict=True,
        trailing_text_hidden=trailing_text_hiddens,
        tts_pad_embed=tts_pad_embed,
        generation_step=None,
        past_hidden=None,
        past_key_values=None,
    )

    talker_past_kv = out.past_key_values
    past_hidden = out.past_hidden
    generation_step = _as_int(out.generation_step)

    logits = out.logits[:, -1, :]
    token, last_eos_bias = _sample_with_eos_policy(
        logits,
        eos_id=eos_id,
        generation_step=generation_step,
        deadlines=deadlines,
        policy=policy,
        temperature=temperature,
        top_k=top_k,
        top_p=top_p,
        do_sample=do_sample,
        suppress_mask=suppress_mask,
        suppress_eos=min_new_tokens > 0,
    )

    prefill_len = talker_graph.prefill_kv(talker_past_kv)
    rope_deltas = getattr(talker, "rope_deltas", None)
    talker_graph.set_generation_state(attention_mask, rope_deltas)

    torch.cuda.synchronize()
    t_prefill = time.time() - t_start

    chunk_buffer = []
    all_first_tokens = []
    total_steps = 0
    chunk_count = 0
    chunk_start = time.time()
    termination_reason: TerminationReason | None = None

    for step_idx in range(max_new_tokens):
        if token.item() == eos_id:
            termination_reason = "natural_eos"
            break

        current_pos = prefill_len + step_idx
        if current_pos >= talker_graph.max_seq_len - 1:
            termination_reason = "sequence_limit"
            break

        last_id_hidden = talker_codec_embed(token.unsqueeze(1))
        pred_input = torch.cat((past_hidden, last_id_hidden), dim=1)
        codebook_token_ids = predictor_graph.run(pred_input)

        all_cb = torch.cat([token.view(1), codebook_token_ids])
        chunk_buffer.append(all_cb.detach())
        all_first_tokens.append(token.detach())

        codec_hiddens = [last_id_hidden]
        for i in range(num_code_groups - 1):
            codec_hiddens.append(
                predictor_codec_embeds[i](
                    codebook_token_ids[i].unsqueeze(0).unsqueeze(0)
                )
            )
        inputs_embeds = torch.cat(codec_hiddens, dim=1).sum(1, keepdim=True)

        if generation_step < text_context_steps:
            inputs_embeds = (
                inputs_embeds
                + trailing_text_hiddens[:, generation_step].unsqueeze(1)
            )
        else:
            inputs_embeds = inputs_embeds + tts_pad_embed

        hidden_states = talker_graph.run(inputs_embeds, position=current_pos)
        logits = talker_codec_head(hidden_states[:, -1, :]).unsqueeze(0)

        if repetition_penalty != 1.0 and all_first_tokens:
            history = torch.stack(all_first_tokens)
            logits = apply_repetition_penalty(logits, history, repetition_penalty)

        token, last_eos_bias = _sample_with_eos_policy(
            logits.squeeze(0),
            eos_id=eos_id,
            generation_step=generation_step,
            deadlines=deadlines,
            policy=policy,
            temperature=temperature,
            top_k=top_k,
            top_p=top_p,
            do_sample=do_sample,
            suppress_mask=suppress_mask,
            suppress_eos=len(all_first_tokens) < min_new_tokens,
        )
        past_hidden = hidden_states[:, -1:, :].clone()
        generation_step += 1

        termination_reason = classify_after_sample(
            sampled_token_id=_as_int(token),
            eos_token_id=eos_id,
            generation_step=generation_step,
            deadlines=deadlines,
        )
        if termination_reason is None and current_pos + 1 >= talker_graph.max_seq_len - 1:
            termination_reason = "sequence_limit"
        if termination_reason is None and step_idx + 1 >= max_new_tokens:
            termination_reason = "token_limit"

        if len(chunk_buffer) >= chunk_size:
            torch.cuda.synchronize()
            chunk_decode_time = time.time() - chunk_start
            total_steps += len(chunk_buffer)
            is_final = termination_reason is not None

            yield torch.stack(chunk_buffer), _timing_payload(
                chunk_index=chunk_count,
                chunk_steps=len(chunk_buffer),
                prefill_ms=t_prefill * 1000 if chunk_count == 0 else 0,
                decode_ms=chunk_decode_time * 1000,
                total_steps=total_steps,
                is_final=is_final,
                termination_reason=termination_reason,
                generation_step=generation_step,
                text_context_steps=text_context_steps,
                eos_bias_applied=last_eos_bias,
                deadlines=deadlines,
                policy=policy,
            )

            chunk_buffer = []
            chunk_count += 1
            chunk_start = time.time()
            if is_final:
                return

        if termination_reason is not None:
            break

    if termination_reason is None:
        termination_reason = "token_limit"

    if chunk_buffer:
        torch.cuda.synchronize()
        chunk_decode_time = time.time() - chunk_start
        total_steps += len(chunk_buffer)

        yield torch.stack(chunk_buffer), _timing_payload(
            chunk_index=chunk_count,
            chunk_steps=len(chunk_buffer),
            prefill_ms=t_prefill * 1000 if chunk_count == 0 else 0,
            decode_ms=chunk_decode_time * 1000,
            total_steps=total_steps,
            is_final=True,
            termination_reason=termination_reason,
            generation_step=generation_step,
            text_context_steps=text_context_steps,
            eos_bias_applied=last_eos_bias,
            deadlines=deadlines,
            policy=policy,
        )


@torch.inference_mode()
def parity_generate_streaming(
    talker,
    talker_input_embeds: torch.Tensor,
    attention_mask: torch.Tensor,
    trailing_text_hiddens: torch.Tensor,
    tts_pad_embed: torch.Tensor,
    config,
    max_new_tokens: int = 2048,
    min_new_tokens: int = 2,
    temperature: float = 0.9,
    top_k: int = 50,
    top_p: float = 1.0,
    do_sample: bool = True,
    repetition_penalty: float = 1.05,
    chunk_size: int = 12,
    eos_policy: StreamingEosPolicy | None = None,
) -> Generator[Tuple[torch.Tensor, dict], None, None]:
    """
    Streaming generation without CUDA graphs (dynamic cache).

    The EOS policy intentionally matches ``fast_generate_streaming`` so parity
    retries preserve both stopping behavior and diagnostics.
    """
    eos_id = config.codec_eos_token_id
    vocab_size = config.vocab_size
    device = talker_input_embeds.device
    text_context_steps = int(trailing_text_hiddens.shape[1])
    policy = eos_policy or StreamingEosPolicy()
    deadlines = resolve_eos_deadlines(
        max_new_tokens=max_new_tokens,
        text_context_steps=text_context_steps,
        policy=policy,
    )

    suppress_mask = _codec_suppress_mask(vocab_size, eos_id, device)

    t_start = time.time()
    out = talker.forward(
        inputs_embeds=talker_input_embeds,
        attention_mask=attention_mask,
        use_cache=True,
        output_hidden_states=True,
        return_dict=True,
        trailing_text_hidden=trailing_text_hiddens,
        tts_pad_embed=tts_pad_embed,
        generation_step=None,
        past_hidden=None,
        past_key_values=None,
    )

    talker_past_kv = out.past_key_values
    past_hidden = out.past_hidden
    generation_step = _as_int(out.generation_step)

    logits = out.logits[:, -1, :]
    token, last_eos_bias = _sample_with_eos_policy(
        logits,
        eos_id=eos_id,
        generation_step=generation_step,
        deadlines=deadlines,
        policy=policy,
        temperature=temperature,
        top_k=top_k,
        top_p=top_p,
        do_sample=do_sample,
        suppress_mask=suppress_mask,
        suppress_eos=min_new_tokens > 0,
    )

    if attention_mask is not None:
        attention_mask = attention_mask.clone()

    torch.cuda.synchronize()
    t_prefill = time.time() - t_start

    chunk_buffer = []
    all_first_tokens = []
    total_steps = 0
    chunk_count = 0
    chunk_start = time.time()
    termination_reason: TerminationReason | None = None

    for step_idx in range(max_new_tokens):
        if token.item() == eos_id:
            termination_reason = "natural_eos"
            break

        cache_position = None
        if attention_mask is not None:
            attention_mask = torch.cat(
                [
                    attention_mask,
                    attention_mask.new_ones((attention_mask.shape[0], 1)),
                ],
                dim=1,
            )
            cache_position = torch.tensor(
                [attention_mask.shape[1] - 1],
                device=attention_mask.device,
            )

        current_generation_step = generation_step
        out = talker.forward(
            input_ids=token.view(1, 1),
            attention_mask=attention_mask,
            use_cache=True,
            output_hidden_states=True,
            return_dict=True,
            trailing_text_hidden=trailing_text_hiddens,
            tts_pad_embed=tts_pad_embed,
            generation_step=generation_step,
            past_hidden=past_hidden,
            past_key_values=talker_past_kv,
            subtalker_dosample=do_sample,
            subtalker_top_k=top_k,
            subtalker_top_p=top_p,
            subtalker_temperature=temperature,
            cache_position=cache_position,
        )

        codec_ids = out.hidden_states[1]
        if codec_ids is None:
            termination_reason = "model_stopped"
            break

        chunk_buffer.append(codec_ids.squeeze(0).detach())
        all_first_tokens.append(token.detach())

        logits = out.logits[:, -1, :]
        if repetition_penalty != 1.0 and all_first_tokens:
            history = torch.stack(all_first_tokens)
            logits = apply_repetition_penalty(logits, history, repetition_penalty)

        token, last_eos_bias = _sample_with_eos_policy(
            logits,
            eos_id=eos_id,
            generation_step=current_generation_step,
            deadlines=deadlines,
            policy=policy,
            temperature=temperature,
            top_k=top_k,
            top_p=top_p,
            do_sample=do_sample,
            suppress_mask=suppress_mask,
            suppress_eos=len(all_first_tokens) < min_new_tokens,
        )

        talker_past_kv = out.past_key_values
        past_hidden = out.past_hidden
        generation_step = _as_int(out.generation_step)

        termination_reason = classify_after_sample(
            sampled_token_id=_as_int(token),
            eos_token_id=eos_id,
            generation_step=generation_step,
            deadlines=deadlines,
        )
        if termination_reason is None and step_idx + 1 >= max_new_tokens:
            termination_reason = "token_limit"

        if len(chunk_buffer) >= chunk_size:
            torch.cuda.synchronize()
            chunk_decode_time = time.time() - chunk_start
            total_steps += len(chunk_buffer)
            is_final = termination_reason is not None

            yield torch.stack(chunk_buffer), _timing_payload(
                chunk_index=chunk_count,
                chunk_steps=len(chunk_buffer),
                prefill_ms=t_prefill * 1000 if chunk_count == 0 else 0,
                decode_ms=chunk_decode_time * 1000,
                total_steps=total_steps,
                is_final=is_final,
                termination_reason=termination_reason,
                generation_step=generation_step,
                text_context_steps=text_context_steps,
                eos_bias_applied=last_eos_bias,
                deadlines=deadlines,
                policy=policy,
            )

            chunk_buffer = []
            chunk_count += 1
            chunk_start = time.time()
            if is_final:
                return

        if termination_reason is not None:
            break

    if termination_reason is None:
        termination_reason = "token_limit"

    if chunk_buffer:
        torch.cuda.synchronize()
        chunk_decode_time = time.time() - chunk_start
        total_steps += len(chunk_buffer)

        yield torch.stack(chunk_buffer), _timing_payload(
            chunk_index=chunk_count,
            chunk_steps=len(chunk_buffer),
            prefill_ms=t_prefill * 1000 if chunk_count == 0 else 0,
            decode_ms=chunk_decode_time * 1000,
            total_steps=total_steps,
            is_final=True,
            termination_reason=termination_reason,
            generation_step=generation_step,
            text_context_steps=text_context_steps,
            eos_bias_applied=last_eos_bias,
            deadlines=deadlines,
            policy=policy,
        )
