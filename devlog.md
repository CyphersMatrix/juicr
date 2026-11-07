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
