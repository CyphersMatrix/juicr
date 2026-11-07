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

## 2026-11-07 — Phase 0: LXC created (isolation not yet proven)

- Owner approved creation (ID changed to 641). Created unprivileged LXC 641 `juicr` at 10.138.6.41 on proxispace: Debian 13, nesting+keyctl, 4 vCPU / 8 GiB / 32 GiB on `library`, onboot off, not in the nightly backup job. Undo: `pct stop 641 && pct destroy 641`.
- Installed podman 5.4.2, uidmap, passt, python3; user `juicr`. Rootless Podman initially failed (`newuidmap` EPERM) because default subuid 100000+ exceeds the 65536-ID map of an unprivileged LXC; fixed with `juicr:10000:50000` in /etc/subuid and /etc/subgid.
- Verified: rootless overlay/crun; a debian:13 container runs with `--network=none` and maps container root to the unprivileged `juicr` user.
- Finding: networked rootless containers (pasta) fail — `/dev/net/tun` is not available in the LXC. Isolation default is network-disabled so this does not block the plan, but allowlisted/unrestricted network case modes would need a tun device passthrough (an LXC config change) — awaiting owner decision.
- NOT yet done: the isolation test battery (filesystem/socket, contamination, DNS/IPv6, resource ceilings, timeout/orphan cleanup). Vault thread and State.md updated.

## 2026-11-07 — Phase 0: isolation battery run

- Added `scripts/phase0-isolation.sh`; evidence in `evidence/phase0-isolation-20261107.txt`. Executed only inside LXC 641 as unprivileged `juicr` (rootless Podman, `--network=none`, `--cap-drop=all`, no-new-privileges, read-only rootfs). No Lemonade inference.
- PASS: capabilities dropped; read-only rootfs; LXC home/files not visible; no management or other sockets; no cross-task contamination (only declared volumes persist); loopback only — no IPv6 interfaces, DNS, Lemonade, LAN or internet connectivity; memory ceiling (64 MB) kills allocation; PID ceiling refuses forks; tmpfs disk ceiling gives ENOSPC; CPU cap slows work (0.25 CPU ≈ 5.7x vs uncapped); `--timeout` kills the task; `podman stop` leaves nothing; `rm -f -a` sweeps records after a simulated conmon crash.
- Findings: (1) First run showed memory/PID/CPU limits NOT enforced — rootless Podman had no cgroup delegation. Fixed with `loginctl enable-linger juicr` + `dbus-user-session`; delegated controllers cpu, memory, pids (no io). The runner must start under a real user session and verify delegation before each run. (2) After a simulated conmon crash the process died but a container record stayed until an explicit sweep; the runner needs a startup/shutdown sweep. (3) Two first-run FAILs were test bugs and are explained in the evidence file.
- NOT tested: networked modes (no /dev/net/tun; kept off by owner decision), artifact-collection path (traversal/symlink/size), resource ceilings under concurrency, I/O bandwidth limits, kernel-escape-class attacks. Phase 0 isolation gate is met for the default no-network sandbox only; broader claims are unverified.
- Next: Phase 0 remaining item — inventory gaps (effective settings, cache-reset API, sampling values, timing fields), which need more read-only Lemonade calls; then Phase 1.

## 2026-11-07 — Phase 0: Lemonade smoke load

- Owner approved one short smoke load on shared Lemonade. Checked Hermes idle first; loaded Gemma-4-E4B-it-GGUF (ctx 8192, not saved), ran 4 streamed requests, unloaded only that model. Then I mistakenly re-ran the script, which auto-loaded the model at default ctx 65536 and made 4 more requests; unloaded again (see evidence incident note). Final state matches the pre-test state (FLM e4b still loaded). Evidence: `evidence/phase0-lemonade-smoke-20261107.md`, `scripts/phase0-smoke.py`.
- Verified: model identity/quant/snapshot; backend llamacpp vulkan b11846; per-request backend timings and usage are in the final stream event; load-time `llamacpp_args` defaults are `--temp 1.0 --top-p 0.95 --top-k 64 --parallel 1`; a second LLM slot is available without evicting Hermes' model.
- Findings: prefix cache is active between requests (19/24 prompt tokens cached) and no cache-reset API was found — pilot timings must record cache condition and treat the first request per case separately. `/slots` answers 200 without a key (unexplored). Cache-reset and effective-flag verification remain unverified.
- Still open before pilot execution: freeze sampling values (candidate: top_p 0.95, top_k 64 from the model defaults), agent tool-call compatibility check, budget confirmation in a real window.

## 2026-11-07 — Phase 1: controller core (fake backend)

- Added `juicr/controller.py` (stdlib: sqlite3, WAL, `BEGIN IMMEDIATE` transactions), `juicr/fake_backend.py`, `tests/test_controller.py`. Owner approval via bootstrap token hash; protected actions (budget increase, publication, external judging) need separate per-experiment owner grants; transactional reserve-then-settle budgets; exhaustion cancels active work and blocks new dispatch; unknown usage is flagged rather than zero; overrun recorded; windows with finite/unlimited grace that never admit new work or expand budgets; cancellation overrides grace.
- Verification: 16 tests pass. Mutation checks: removing the budget check fails 4 tests, weakening the owner-forgery check fails 2; code restored and all 16 pass again. Concurrent admission test (40 threads, limit 10) dispatched exactly 10.
- Limits: the in-process `Principal` token stops accidental or naive forgery only; the real boundary (separate processes, authenticated API for UI/MCP, owner session) is not built. No FastAPI layer, queue scheduler loop, wall-clock `seconds` accounting, restart reconciliation of open reservations, or per-dimension in-flight overrun handling yet. Running experiment states beyond queued/running/cancelling are not transitioned (completed/partial not implemented).
- Phase 1 acceptance is partly met: the fake-backend checks above pass; the API/auth boundary is not yet exercised. Next: FastAPI layer in a venv with owner session, a scheduler loop, restart reconciliation, then Phase 2 runner.

## 2026-11-07 — Staged in LXC 641

- Owner chose to build/test in the LXC (no sudo on labyrispace). Installed python3-venv and python3-pip inside CT 641 only; created `/home/juicr/venv` and pinned deps (`requirements.lock`, 19 packages: fastapi 0.115.6, uvicorn 0.34.0, httpx, jinja2, python-multipart, itsdangerous). Repo copied (no .git) to `/home/juicr/app`.
- Verified: all 23 unit tests pass inside the CT under the unprivileged `juicr` user. No web service started; nothing listening. Dev stays in the labyrispace checkout; sync is a tarball push via `pct push` (no git on the CT).

## 2026-11-07 — Phase 1: web layer and LAN deployment (LXC 641)

- Added `juicr/web.py` (FastAPI), templates, vendored htmx 2.0.4 (`juicr/static/htmx.min.js`, sha256 e209dda5c823…fb447), `juicr/__main__.py`, `scripts/sync-to-ct641.sh`. Owner login with the owner token (signed HttpOnly SameSite=Strict session cookie, 8 h), login lockout (5 failures/5 min per IP), per-session CSRF token on every cookie-authenticated POST, strict CSP (no inline script/style), no-store, DENY framing. Non-owner API callers use bearer tokens (roles user/mcp from `~/.juicr/api_tokens.json`, none configured); bearer tokens can admit trials, cancel and ask about protected actions but never approve, grant, or create. A wrong bearer never falls back to the cookie.
- Tests: 35 pass in CT 641 (23 controller + 12 web). Mutation checks: CSRF check disabled -> 2 fail; owner gate loosened -> 1 error; bad-bearer fallback -> 1 fail. A test also caught a real bug (sign-out lacked the CSRF field); fixed.
- Deployed as user systemd unit `juicr.service` in CT 641, bound to 10.138.6.41:8810 (owner approved LAN exposure), UMask 0077, NoNewPrivileges, ProtectSystem=strict, data in `/home/juicr/.juicr` (0700; owner token and session secret 0600). Verified from the labyrispace VM over the LAN: /healthz 200, CSP/DENY headers, and an end-to-end flow with the real token (bad login 401, login 303, create without CSRF 403, create 200, approve 200). Test data removed.
- Limits: HTTP only, so the session cookie and owner token cross the LAN unencrypted (no Secure flag); anyone on the LAN can attempt logins (lockout is per-IP, in-memory). Backend is the fake one: scheduler runs but no real inference is possible; trials never finish on their own. UI was not visually reviewed in a browser, no accessibility pass, no multi-user model, no MCP server yet. PVE firewall on the CT was not inspected (reachable from the labyrispace VM only; other LAN hosts untested).
- Phase 1 acceptance: fake-backend checks, approval separation and API/auth boundary now tested; remaining gaps above are carried to Phase 4 (UI/MCP) and a TLS decision.

## 2026-11-07 — UI redesign (polished dashboard)

- Owner found the first UI "basic, almost gross"; chose the polished-dashboard direction. Rebuilt templates and `app.css`: sidebar shell (Experiments live; Evidence/Reports shown as "soon"), stat cards (total, awaiting approval, active, finished), "Awaiting your approval" section with the approve form, run cards with state badges, window/grace line, per-dimension budget bars (used/reserved/overrun, "?" for unknown usage), segmented trial-state bar with legend, grant chips and actions, empty state, centred login card with brand mark, light and dark themes, phone layout (no horizontal overflow at 390 px, checked by script).
- CSP kept strict (no inline styles/scripts): bars are SVG, interactivity is `<details>` and htmx. The 4 s refresh only swaps the run list (`/experiments/live`), so approve forms keep typed values.
- Visual review: rendered with seeded demo data (`scripts/demo_preview.py`, temp DB, never real data) and screenshotted at desktop/phone, dark/light. Not yet reviewed on the live service after sign-in by me (token entry is the owner's), and no accessibility audit beyond labels, focus rings and aria labels on bars.
- The preview exposed a controller bug: cancelling (or exhausting) an experiment with nothing running left it "cancelling" forever. Fixed (`_maybe_complete` after cancel and exhaustion) with 2 new tests; 37 tests pass in CT 641. Also fixed a demo-clock mismatch and an overflowing approve button.
- Deployed to CT 641 and restarted `juicr.service`; verified /login, static assets, and an authenticated dashboard fetch over the LAN. A test experiment `delete-this-test` created by the owner exists in the live DB; left untouched.

## 2026-11-07 — Phase 2: runner and immutable evidence

- Owner chose to reuse Hermes' non-admin Lemonade key; copied (not printed) to CT 641 `/home/juicr/.juicr/lemonade_key` (0600). Traffic from juicr is therefore not separable from Hermes' in Lemonade's view. CT authenticates to Lemonade (health ok).
- Added `juicr/lemonade.py` (stdlib adapter: health, per-load non-persistent `load` with `save_options:false`, `unload`, SSE `chat_stream` with deadline/cancel and client timing; errors redacted), `juicr/evidence.py` (content-addressed artifacts verified by hash on read; append-only fsynced JSONL attempt events; write-once read-only manifests with hash; corrupt/tampered manifests raise `EvidenceError`), `juicr/runner.py` (state snapshot, `establish` with per-setting verified/unverified/mismatch from API-visible state, pre-dispatch re-verification before every request, bounded restoration that warns and continues, retries as separate accounted attempts, idempotent `execute`, segment manifests), `tests/fake_lemonade.py` and `tests/test_runner.py`.
- Verification: 53 tests pass locally and in CT 641 (12 web tests run only in the CT). Mutation checks: removing pre-dispatch re-verification fails 3, hiding restoration failure fails 1, allowing resume re-send fails 1, allowing manifest overwrite fails 1; all restored. Tests cover: accepted-but-unverified vs mismatch, autoload-guard (never served on default 65536), no request sent on an unestablished config, failed attempts surviving retries and counted, unknown usage not zero, timeout, cancellation marked server-side-unverified, restore success/failure, resume without re-sending or budget reset (also from a fresh process), evidence immutability, and the key never appearing in evidence or errors.
- Bugs found by the tests and fixed: controller `settle` crashed on usage in an unapproved dimension (now recorded as an `unbudgeted_usage` event); a stream-close edge case I introduced; corrupt manifest JSON raised a raw decode error (now `EvidenceError`).
- Read-only check: the real adapter from CT 641 snapshotted live Lemonade (only gemma4-it-e4b-FLM on NPU, ctx 131072, v2026.45.3). No model load, unload or inference was done through the runner.
- Not done / unverified: real inference through the runner; scheduler integration (runner is not yet called by the web service's tick loop, which still uses the fake backend); cache-reset (none found); effective-flag verification beyond `ctx_size` (only settings exposed in `recipe_options` or `launch_command` can be verified); concurrent clients (single active prediction only); model-load timeout/eviction interference with Hermes beyond the earlier smoke observations; the sandbox/agent path (Phase 3). Phase 2 acceptance is met against the fake backend.

## 2026-11-07 — Phase 3: pilot fixtures, oracles, sandbox, review

- Added `juicr/cases.py` (five frozen fixtures, limits, E1 schema), `oracles.py` (C1 harness with 21 independent checks, D1/W1/E1 rules, A1 trace scorer, minimal schema validator), `sandbox.py` (rootless Podman: no network, caps dropped, read-only rootfs, memory/PID/CPU ceilings, timeout, output cap; reuses the CT's python3.13 read-only in the existing debian:13 image — no new image), `agent.py` (A1 loop with simulated tools, caps, trace; no send/save tool), `review.py` (append-only evaluations, blind packets, human scores, failure-class `classify`), `pilot.py` (30 trials, matched pairs, recorded RNG seed, accounting), `scripts/pilot_review.py` -> `PILOT_REVIEW.md` for the owner gate.
- Verification: 82 tests pass in CT 641 (including the sandbox ones; 7 sandbox + 12 web run only there); 63 run on the labyrispace VM. Reference outputs pass every oracle; 10 wrong C1 solutions, 5 D1, 5 W1, 7 E1 and 6 A1 scenarios fail the intended checks. Plan arithmetic confirmed in code: 30 trials, 24 + up to 24 agent requests, 48 measured maximum. Mutation checks (all caught, all restored): D1 accepting an invented owner, unreviewed treated as zero, pair order not randomized, failed tool calls free, guarantee not flagged, sandbox network allowed, harness adjacency check neutered.
- Bugs found by the tests and fixed: (1) W1 rollout-date check missed "is planned for June 2027" — now sentence-based with negation handling; (2) a Podman failure inside the sandbox (rc 1, "Failed to obtain podman configuration") was indistinguishable from model code producing no output, i.e. an infrastructure fault could have been scored as a task failure — now raised as `SandboxUnavailable` (infrastructure) and tested; (3) the caller's shell handed down root's XDG_RUNTIME_DIR via `runuser` — the sandbox now sets its own HOME/runtime dir; (4) output-cap deadlock (stopped reading the pipe, so podman never exited; a container hung 8 minutes) — now drains and discards; (5) podman `--timeout` exits 255, not recognised as a timeout.
- Limits: the D1/W1 critical-failure rules are heuristics and the W1 audience/tone and D1 citation-support judgments need blind human review (reviews are stored, none collected). The C1 harness proves the 21 listed behaviours, not general correctness. A1 has only been exercised against scripted fake models; tool-call compatibility with Gemma-4-E4B on the installed build is unverified, so A1 admission is blocked. The sandbox was tested for default no-network operation only. Sampling values (top_p 0.95, top_k 64), limits and the fixtures are PROPOSED until the owner approves `PILOT_REVIEW.md`. Nothing here ran real inference.
- Next: owner review of `PILOT_REVIEW.md`; then Phase 4 (UI/MCP) and Phase 5 (measured pilot in an approved window), plus the A1 tool-call compatibility check (needs a short model load, owner OK).

## 2026-11-07 — Pilot pack approved as-is

- Owner reviewed `PILOT_REVIEW.md` and approved the fixtures, sampling values (top_p 0.95, top_k 64; temperature 0.0/0.7; seeds 17/43/101), limits, order seed and A1 admission rule as written. Recorded as plan section 3 item 17 and in `cases.PACK_STATUS` (now `approved-fixtures-limits-20261107; run-approval-pending; A1-blocked`); a test asserts that approving the pack does not mean run approval.
- Not approved: running the pilot (needs an owner window + budget in the UI), publication, external judging. A1 remains blocked until Gemma-4-E4B tool-call compatibility is verified on the installed build (needs a short model load, owner OK).
- Next: Phase 4 (testing UI/MCP wired to the runner) or the A1 compatibility check, owner's choice.

## 2026-11-07 — A1 tool-call compatibility check (real Lemonade, owner-approved)

- Idle checks first (request count unchanged 15 s; no model busy twice). Ran `scripts/a1_compat_check.py` from CT 641: snapshot, `Runner.establish` (Gemma-4-E4B-it-GGUF, ctx 8192, verified), one A1 run at temp 0.0 seed 17, `Runner.restore`. Evidence `evidence/a1-compat-20261107.json` (no key in it; grep-checked). This was the first use of the runner and adapter against real Lemonade.
- Attempt 1: the model returned a structured tool call (`finish_reason: tool_calls`, valid JSON arguments), then Lemonade answered HTTP 500 on the next turn. Cause was my bug: the loop echoed tool calls back in an internal shape rather than OpenAI wire shape; also the tool reply's `tool_call_id` could mismatch when a model gives no id. Fixed both with a test; attempt 1 restored state cleanly (2 requests).
- Attempt 2 (after the fix): full round trip works — structured tool call, valid arguments, tool error result accepted, model replied; 2 requests, 462 generated tokens, 20 s; restoration ok (attempts 2), final state only gemma4-it-e4b-FLM, same as before. Total real requests for this check: 4.
- Compatibility verdict: **verified** — the installed build and this model emit valid structured tool calls and accept tool results. A1 admitted under the approved admission rule (pack status `A1-admitted-compat-verified`; fixtures, sampling and limits unchanged).
- Observation, not a pilot result: in this single sample the model did not retry after the transient search error; it said "I will try the search again" and stopped, so A1 scored 0 on recovery and supported answer. Expect low A1 scores; that is a model behavior finding for the pilot, not an infrastructure problem. One sample at one seed says nothing about rates.
- Still true: nothing is approved to run the pilot or to publish; the real request count so far in Phase 0-A1 checks is outside any formal window.
