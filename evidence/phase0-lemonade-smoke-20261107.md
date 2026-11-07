# Phase 0 Lemonade smoke (2026-11-07)

Pre-state: only `gemma4-it-e4b-FLM` loaded (NPU, ctx 131072), idle (0 active requests over 20 s). Non-admin key from Hermes env; never printed/stored.
- POST /api/v1/load {Gemma-4-E4B-it-GGUF, ctx_size 8192, save_options false}: 7.5 s, success. Second LLM slot used; FLM model was NOT evicted (max_models llm=2).
- Effective load (from /api/v1/health): gpu, llamacpp **vulkan**, `llamacpp_args` default `--temp 1.0 --top-p 0.95 --top-k 64 --parallel 1`, launch command includes `--ctx-size 8192 --jinja --metrics --mmproj ... --parallel 1`; checkpoint unsloth/gemma-4-E4B-it-GGUF:Q4_K_M, snapshot bfc15c382204943c3a8fff0c750b94ae2364d7a3. Model catalog `recipe_options` stayed `{}` (nothing persisted).
- 4 streamed chat requests (temp 0.0 and 0.7 x seeds 17, 43; prompt 24 tokens; 9 output tokens): client TTFT 0.10-0.23 s; backend `timings` per request (prompt_n, prompt_ms, predicted_n, predicted_per_second ~23.4-23.9 tok/s, cache_n) returned in the final stream event alongside `usage`; request-level `cached_tokens` present.
- **Prefix cache is active:** first request cached_tokens 0, later requests 19 of 24 cached. Cache condition must be recorded; no verified cache-reset API found (probed unauthenticated paths /api/v1/{slots,props,cache} -> 401). `/slots` (no /api/v1 prefix) answered 200 without a key — unexplored, noted as a possible unauthenticated llama.cpp passthrough; not queried further.
- Temp 0.0 gave identical text across seeds 17/43 (as the plan predicted). Temp 0.7 varied by seed.
- POST /api/v1/unload {Gemma-4-E4B-it-GGUF}: success. Post-state: only gemma4-it-e4b-FLM loaded, recipe_options unchanged (ctx 131072). Matches pre-state.
- Requests used: 4 inference requests (budget notional; no formal window was open).
