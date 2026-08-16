# Starter Kit Changelog

## Unreleased

- Use the importable `starter_kit/` name for the submission root.
- Add `__init__.py` so tests can use `from starter_kit import adapter` directly.

## 1.1.0 - 2026-07-27

- Publish the environment-only OpenAI-compatible L2 runtime contract.
- Fix the formal L2 scoring model to DeepSeek `deepseek-v4-flash`.
- Publish per-case call, token, and timeout budgets in `l2_policy.json`.
- Add a dependency-free `llm_client.py` transport helper without prompts or scoring logic.
- Clarify that the organizer provides no API endpoint, key, or credit before formal scoring.

## 1.0.1 - 2026-07-27

- Add the read-only local final-submission preflight.
- Define `starter_kit/` as the build and evaluation root in official forks.
- Document commit-SHA submission, server-side cutoff time, receipts, and resubmission rules.

## 1.0.0 - 2026-07-11

- Freeze submission contract v1.0.
- Add `submission.yaml`, version metadata, and machine-readable public reports.
- Remove mock scoring paths, prompt-specific answers, and the L3 reference solution.
- Clarify that formal scoring runs in an organizer-owned isolated environment.
