# JUICR project instructions

## Purpose

Own JUICR, a locally launched Python script with a fresh browser interface for comparing model sampling settings through Lemonade and OpenRouter.

## Ownership

- `juicr_local.py`, `juicr_cli.py`, `local_ui.html`, `local_ui.js`, `local_ui.css`, `local_appearance.js`: current standalone local application and JSON CLI client; Python standard library only.
- `implmentation_plan.md`: current scope, architecture and acceptance gates. Filename spelling is intentional.
- `legacy_implementation_plan.md`, `juicr/`, existing deployment scripts and pilot evidence: retained historical platform; not the new app's runtime.
- `devlog.md`: project history and verification records.
- This repo is standalone (own git history). A parent `../AGENTS.md` in the author's local `~vibe` workspace may add shared workflow rules but is not part of this repository.

## Local Contracts

- Direction (2026-11-07): build new locally with a web interface; do not extend the old interface. The explicit local-script direction supersedes the LXC/platform architecture for this new response-comparison application.
- Novice interface: guide users through Choose a test -> Run a model -> Review results, one task per screen. Explain unfamiliar terms beside controls, provide a clearly simulated practice path, and keep advanced settings, coverage and technical evidence closed by default. Preserve full controls for users who reveal them. New tests use service sampling defaults; existing tests retain their exact values. Make the next action explicit and require saving a new version before running edited saved-test settings.
- Keep independent Theme (Default, Lemonade, Espresso, Cyberpunk; colors only) and Mode (Novice, Learned, Expert; progressively less guidance and denser layout) controls visible on every screen. Default retains the original colors and Novice the original spacing/full guidance. Remember appearance in browser-local storage with safe defaults for missing/invalid data. Theme/mode changes must preserve unsaved inputs, model parameters, test identity, results and credentials. Essential errors, costs, confirmations and state explanations remain visible in every mode; detailed setting help remains available through accessible descriptions/tooltips.
- Keep historical decisions and results in the legacy plan/devlog. Do not change CT 641, overwrite pilot evidence, publish, or run live/paid inference during development without subsequent authorization.
- Bind the interface to loopback; reject foreign Host/Origin and require a local session token for mutations. Provide API key entry in Connections: session memory by default, optional user-scoped Windows DPAPI storage, environment fallback. Never return configured keys to the browser or include them in saved comparisons/exports. Clear submitted fields after saving or closing. No plaintext disk fallback for remembered keys.
- Model selection uses endpoint-backed dropdowns with name/ID search and explicit refresh. Catalog reads and connection tests do not authorize inference. User's Lemonade endpoint is `http://10.138.6.1:13305/api/v1`; enter its key in Connections or use `LEMONADE_API_KEY`. Only send that configured key to its exact API base URL. Block connection edits during active comparisons.
- Freeze content, instructions, profiles, seeds and request order per run. Preserve raw visible output, reasoning when supplied, finish reason, usage, provider identity and errors; unavailable is not zero. Acceptance of a setting does not verify its effect.
- Default to one model configuration per test; allow optional batches of up to six. Results accumulate as individually selectable responses across runs and restarts. Compare saved results, including from different tests, without dispatching inference; flag differences in source, context, criteria, instructions or task and retain original evidence.
- Reusable tests freeze source, context, instructions, criteria and parameters in immutable revisions. Attribute coverage only to the exact saved revision and model/service identity; old unlinked runs do not imply coverage. Provide per-model histories, direct repeat comparison, explicit no-results/failed-attempt states, and Quick test that loads the definition into the indicated model without running it. Criteria are model requirements, not automatic quality scores.
- Provide confirmation-based deletion of individual/selected results and whole tests. Test deletion covers every saved version; keep linked results by default and offer an explicit option to delete them all. Deletions are undoable, persist across restart, and never renumber response IDs or affect unrelated data. Hide deleted items from normal reads/comparisons/coverage; keep retained results usable after their test is deleted. Block deletion/restoration while a test runs.
- Show the finite request count and output-token allowance before Run. No automatic retry. Stop prevents further dispatch and abandons the in-flight request promptly (recorded as an error with usage unknown; the provider may still finish and charge); in-flight cancellation/usage remain explicitly uncertain. No promise of a hard dollar cap.
- New local app compares responses only; it does not execute generated code, run tools, reload models, expose MCP, or publish reports. Historical sandbox requirements still apply to the legacy execution path.
- Automation/AI clients use `python juicr_local.py cli` (or `python juicr_cli.py`) against the running loopback app, never a second Workbench/database worker. Expose endpoint model catalogs with provider/search filters and explicit per-provider failures; discovery does not infer or imply loaded models. Support saved-test list/show/create/revise, exact-revision single-provider/model run, side-effect-free dry-run allowance, run inspection/scoped cancellation and saved-result list/comparison. Return JSON without server tokens/credentials, retain immutable settings and shared results, and never automatically retry. Waiting timeout/CLI interruption leaves submitted work running and must say so. Keys stay in Connections or the server environment; no key command-line arguments.

## Work Guidance

- Ask consequential unanswered questions one at a time with multiple-choice answers and a marked recommendation.
- Read the plan and relevant devlog before changes. Keep detailed specifications in the plan and durable operating contracts here.

## Verification

- Current local app: `python -W error::ResourceWarning -m unittest tests.test_local -v` (stdlib; simulated inference and loopback HTTP only).
- CLI: `python -W error::ResourceWarning -m unittest tests.test_cli -v` (disposable loopback servers, injected catalogs/inference and both process entry points; no provider network).
- Appearance preferences: `node tests/test_appearance.cjs` (development check only; Node is not required to run JUICR). Verify all 12 theme/mode combinations, independence, persistence and unavailable/invalid storage behavior; browser-check live form preservation and progressive guidance/density.
- Full regression: `python -m unittest discover -s tests -v`; passes on Windows (148 tests; 7 rootless-Podman sandbox tests skip there and run only in CT 641). Report any new Windows-only failure separately.
- Documentation: check policy consistency, pilot trial/request arithmetic, references and Markdown structure.
- Add executable verification procedures when implementation is authorized.

## Child DOX Index

None.
