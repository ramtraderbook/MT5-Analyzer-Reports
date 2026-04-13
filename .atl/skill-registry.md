# Skill Registry

**Delegator use only.** Any agent that launches sub-agents reads this registry to resolve compact rules, then injects them directly into sub-agent prompts. Sub-agents do NOT read this registry or individual SKILL.md files.

See `_shared/skill-resolver.md` for the full resolution protocol.

## User Skills

| Trigger | Skill | Path |
|---------|-------|------|
| When creating a pull request, opening a PR, or preparing changes for review. | branch-pr | `C:\Users\ramir\.config\opencode\skills\branch-pr\SKILL.md` |
| When writing Go tests, using teatest, or adding test coverage. | go-testing | `C:\Users\ramir\.config\opencode\skills\go-testing\SKILL.md` |
| When creating a GitHub issue, reporting a bug, or requesting a feature. | issue-creation | `C:\Users\ramir\.config\opencode\skills\issue-creation\SKILL.md` |
| When user says "judgment day", "judgment-day", "review adversarial", "dual review", "doble review", "juzgar", "que lo juzguen". | judgment-day | `C:\Users\ramir\.config\opencode\skills\judgment-day\SKILL.md` |
| When user asks to create a new skill, add agent instructions, or document patterns for AI. | skill-creator | `C:\Users\ramir\.config\opencode\skills\skill-creator\SKILL.md` |

## Compact Rules

Pre-digested rules per skill. Delegators copy matching blocks into sub-agent prompts as `## Project Standards (auto-resolved)`.

### branch-pr
- Every PR MUST link an approved issue; PRs without issue linkage are blocked.
- Every PR MUST have exactly one `type:*` label that matches the change type.
- Use branch names as `type/description` with lowercase `a-z0-9._-` only.
- Use conventional commits only; never add `Co-Authored-By` trailers.
- PR body must include issue reference, summary bullets, changes table, and test plan.
- Run `shellcheck` on modified shell scripts before opening the PR.
- Do not merge until automated PR validation and CI checks pass.

### go-testing
- Prefer table-driven tests for pure functions and multi-case behavior.
- For Bubbletea models, test `Model.Update()` state transitions directly before heavier integration tests.
- Use `teatest.NewTestModel()` for full interactive TUI flows.
- Use golden files for stable rendered output snapshots.
- Use `t.TempDir()` for file-system tests and interfaces/mocks for `os/exec` or side effects.
- Test both success and error paths explicitly.

### issue-creation
- Always search for duplicates before creating a new issue.
- Blank issues are disabled; use the correct GitHub template every time.
- Bug reports and feature requests auto-receive `status:needs-review`; no PR until a maintainer adds `status:approved`.
- Questions belong in Discussions, not Issues.
- Fill all required template fields, including pre-flight checks and reproduction/problem details.
- Use conventional issue titles like `fix(scope): ...` or `feat(scope): ...`.

### judgment-day
- Before launching judges, resolve project standards from the skill registry and inject the same compact rules into every judge/fix prompt.
- Launch exactly TWO blind judges in parallel; they must not know about each other.
- The orchestrator synthesizes verdicts into confirmed, suspect, contradiction, and info outcomes.
- Classify warnings as `WARNING (real)` only if a normal user can trigger them; otherwise report as `WARNING (theoretical)` / INFO.
- Round 1: present confirmed issues and ask the user before fixing.
- Round 2+: re-judge only for confirmed CRITICALs; fix real warnings inline without another judge round.
- After two fix iterations, escalate to the user before continuing.

### skill-creator
- Create a skill only for reusable, non-trivial patterns; do not create skills for one-off tasks.
- Use the standard structure: `skills/{skill-name}/SKILL.md` plus optional `assets/` and `references/`.
- Frontmatter must include `name`, `description` with `Trigger:`, `license: Apache-2.0`, and metadata fields.
- Put the most critical actionable patterns first; keep examples minimal and focused.
- Prefer local files in `references/`; do not use web URLs there.
- After creating a skill, register it in `AGENTS.md`.

## Project Conventions

| File | Path | Notes |
|------|------|-------|
| `CLAUDE.md` | `C:\Users\ramir\Desktop\MT5-Analyzer-Reports\CLAUDE.md` | Standalone project conventions |

Read the convention files listed above for project-specific patterns and rules. All referenced paths have been extracted — no need to read index files to discover more.
