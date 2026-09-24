# Contributing

1. Fork the repository and create a focused branch.
2. Keep the CLI dependency-light and preserve the single-file application design.
3. Avoid shell interpolation when invoking external commands.
4. Do not add telemetry or send raw audio anywhere without explicit configuration.
5. Run `python -m py_compile VibeCommit.py` before opening a pull request.
6. Document behavior changes in `README.md`.

Pull requests should explain the user-facing behavior, security implications, and how the change was tested.