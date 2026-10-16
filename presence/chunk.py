"""Frame budget -> iGPU prefill chunk, and the gateway's prefix-fed chunk plan.

OVMS fixes `max_num_batched_tokens` per graph (changing it reloads the graph).
The gateway instead splits a long prompt into prefixes and sends each with
`max_tokens=1` to a graph started with `enable_prefix_caching: true`, so every
request prefills only the tokens past the cached prefix. Chunk size and the gap
between chunks are then live knobs.
"""


def frame_budget_ms(refresh_hz, compositor_p99_ms, margin_ms=1.0):
    """GPU time a non-preemptible slice may take and still let the compositor
    meet the next vblank: frame period - compositor work - margin."""
    return 1000.0 / refresh_hz - compositor_p99_ms - margin_ms


def budget_chunk(rate_tok_per_ms, refresh_hz, compositor_p99_ms, block=16,
                 margin_ms=1.0, slice_frac=1.0):
    """Largest block-aligned chunk whose longest non-preemptible slice fits the
    frame budget. `slice_frac` = longest kernel / whole chunk time (1.0 = the
    chunk is one non-preemptible unit)."""
    budget = frame_budget_ms(refresh_hz, compositor_p99_ms, margin_ms)
    tokens = int(rate_tok_per_ms * budget / slice_frac) // block * block
    return max(block, tokens)


def prefix_plan(n_tokens, chunk, block=16):
    """Prefix lengths the gateway sends, each a block multiple except the last."""
    if chunk % block:
        raise ValueError("chunk must be a multiple of the KV block size")
    ends = list(range(chunk, n_tokens, chunk)) + [n_tokens]
    return ends


def requests(text, token_char_ends, chunk, block=16):
    """`/v3/completions` bodies for prefix feeding. OVMS completions accepts only
    a single string prompt (docs: model_server_rest_api_completions.md), so each
    prefix is the text cut at the char offset where token `e` ends
    (`token_char_ends[i]` = end offset of token i, from the model tokenizer).
    Re-tokenising a cut prefix can merge differently at its tail; that costs at
    most the last KV block per chunk, reported back in
    `usage.prompt_tokens_details.cached_tokens`. The last body is the real request."""
    ends = prefix_plan(len(token_char_ends), chunk, block)
    bodies = [{"prompt": text[:token_char_ends[e - 1]], "max_tokens": 1}
              for e in ends[:-1]]
    bodies.append({"prompt": text, "stream": True})
    return bodies
