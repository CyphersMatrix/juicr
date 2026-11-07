# JUICR devlog

## 2026-11-07 — Initial implementation plan

- Created `implmentation_plan.md` from the supplied proposal and subsequent user decisions; documentation only was authorized.
- Added concrete synthetic pilot fixtures, phased implementation gates and explicit evidence/accounting contracts.
- Pilot arithmetic: 30 top-level trials, up to 48 measured inference requests before separately budgeted probes/warmups/retries.
- Added project AGENTS.md. Parent child index and root devlog must register this new boundary; shared workflow rules remain unchanged.
- Initial large shell write failed before execution because Windows command length exceeded the process limit; used focused file patches instead.
- No application or existing executable tests were present. No deployment, host probes or model inference were performed. Documentation verification is recorded at closeout.

- Closeout: confirmed all six reviewed policies, five pilot fixtures, 30-trial/48-request arithmetic, 14 numbered plan sections and balanced Markdown fences. Parent child index contains exactly one JUICR entry; parent shared rules were left unchanged. No executable tests apply to this documentation-only change.

## 2026-11-07 — Repository initialised (local)

- Made AGENTS.md standalone: the parent `../AGENTS.md` reference now notes it lives outside this repo. Earlier devlog entries left unchanged as history.
- Added .gitignore (secrets, local state, caches); `git init` on `main` and first commit. No GitHub remote yet; none created or pushed.
- Documentation only; no host changes, inference or deployment.

## 2026-11-07 — Stack and pilot defaults decided

- Owner decisions: Python/FastAPI/SQLite; rootless Podman (isolation still unproven until Phase 0); htmx server-rendered testing UI with static published reports; 10 min finite grace and 2 restoration retries.
- Recorded as plan section 3 items 14-15; the old "proposals" item is now 16. Section 13 trimmed to what remains open: API paths/auth, model identity, cache controls, sampling values, resource/budget limits.
- Documentation only; no host changes, inference or deployment.

## 2026-11-07 — Pilot budget ceiling decided; Phase 0 started (read-only inventory)

- Owner accepted: 60-minute pilot window, 150-request ceiling. Plan section 3 item 16 (pack proposals now item 17).
- Phase 0 authorized to begin. Scope this step: read-only Lemonade API inventory only. LXC creation/isolation proof awaits separate confirmation.

- Phase 0 read-only inventory (GET only, non-admin key from Hermes env; key never printed or stored): Lemonade 2026.45.3, auth required (401 without key). Host: Ryzen AI 9 HX 370, Radeon 890M gfx1150 (vram 0.5 GB, virtual 46.7 GB), XDNA2 NPU, 93.42 GB RAM, kernel 7.0.14-25-pve. max_models llm=2.
- Currently loaded: `gemma4-it-e4b-FLM` (flm v1.0.7 on NPU, ctx 131072) — NOT the pilot target. Pilot target `Gemma-4-E4B-it-GGUF` (unsloth/gemma-4-E4B-it-GGUF:Q4_K_M, 5.56 GB) is downloaded; llamacpp vulkan b11846 installed (devices amd_gpu, cpu); rocm and cpu installable only.
- Interference risk: Hermes uses the loaded e4b model on this Lemonade; loading the GGUF for runs may evict/contend with it. Needs coordination (plan decision 7). Model-storage free 79.8 GB of 654.7 GB.
- Not yet verified: effective settings, cache reset API, sampling recommendations, per-request timing fields, tool-call behavior. Isolation proof (LXC) not started.
