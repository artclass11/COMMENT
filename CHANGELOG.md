# Changelog

## 1.1.0 - 2026-09-25

### Added
- Installable `vibecommit` CLI entry point through `pyproject.toml`.
- `--text` typed-intent mode for headless environments, CI, accessibility, and fast tests.
- `--timeout` input validation.
- GitHub issue templates and a terminal demo asset.

### Improved
- README positioning and quick-start instructions for faster adoption.
## 1.0.0 - 2026-09-25

### Added
- Voice-to-commit workflow using local Whisper transcription.
- OpenAI-compatible LLM endpoint support with environment-based API keys.
- Staged-diff-only Git inspection with configurable size limits.
- Conventional Commit validation and interactive commit confirmation.
- Dry-run mode and CLI version reporting.
- Automated unit/smoke tests for core paths.

### Security
- Git commands use argument arrays without shell interpolation.
- API keys are never accepted as command-line arguments.
- Temporary microphone recordings are deleted after execution.
