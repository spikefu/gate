"""One module per check. Each exposes `run(ctx, ...) -> list[Finding]` and raises
`Skipped` when it cannot run. That signature is the port; adding a check means
adding a module and registering it in the runner."""
