# JUICR project instructions

## Purpose

Own the local Lemonade benchmark platform's planning and future implementation.

## Ownership

- `implmentation_plan.md`: policies, architecture, proposed pilot pack, build phases and acceptance gates. Filename spelling is intentional.
- `devlog.md`: project history and verification records.
- This repo is standalone (own git history). A parent `../AGENTS.md` in the author's local `~vibe` workspace may add shared workflow rules but is not part of this repository.

## Local Contracts

- Authorization (2026-11-07): documentation, Phase 0 (LXC 641 and its isolation battery, one Lemonade smoke load) and Phase 1 controller code against a fake backend. Further host changes, real inference beyond approved windows, GitHub publication and later phases need a subsequent instruction.
- Preserve confirmed decisions in plan section 3. Budget exhaustion cancels active work where supported; grace never expands budget.
- Restoration failure warns and continues queued experiments; verify each following experiment's required configuration.
- Initial host evidence is API-only; missing metrics and uncertain settings remain explicit.
- Nested-container isolation failure stops implementation until resolved; do not silently change the architecture or weaken isolation.
- Testing users/Hermes may configure supported settings, custom arguments and network modes within approved budgets. Owner approval protects budget increases, publication and external judging per experiment.
- Reports are read-only approved snapshots, without credentials, private evidence or control access.
- Proposed pilot defaults require review before execution. Separate reported host facts from verified observations.

## Work Guidance

- Ask consequential unanswered questions one at a time with multiple-choice answers and a marked recommendation.
- Read the plan and relevant devlog before changes. Keep detailed specifications in the plan and durable operating contracts here.

## Verification

- Code: `python3 -m unittest discover -s tests -v` (stdlib only; fake backend, no network or Lemonade).
- Documentation: check policy consistency, pilot trial/request arithmetic, references and Markdown structure.
- Add executable verification procedures when implementation is authorized.

## Child DOX Index

None.
