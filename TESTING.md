# Verification

Tested on Windows with Python 3.13 and headless Microsoft Edge.

## Automated backend tests

24 tests passed using `python -m pytest tests -q`:

- Homepage/assets and security headers.
- Host/DNS-rebinding protection, cross-origin rejection, mutation session token.
- Required destructive-action confirmation and rejection of shell-like model names or arbitrary lifecycle commands.
- Dynamic model discovery and remote/cloud model exclusion from chat.
- Fixed delete/unload REST payloads; actionable offline and malformed-response errors.
- Chat content/reasoning/completion streaming; model download progress.
- Upstream connection closure when a response stream is closed.
- Windows process matching excludes unrelated paths and other users.
- Start uses fixed executable arguments, no shell, and loopback-only environment.
- Inaccessible Ollama executable detection fails gracefully.

One upstream Starlette test-client deprecation warning was emitted; it does not affect application operation.

## Browser integration checks

Used the real FastAPI server and frontend with simulated Ollama responses. Passed:

- Four dynamically returned model names populate the selector and library.
- Multi-chunk responses, reasoning, fenced code, and token timing render.
- Model-provided HTML/script text is displayed without executing.
- Conversation survives reload and reopens from the sidebar.
- Stop generation aborts the browser request, closes the backend stream and preserves partial text.
- Model pull completes and updates the library.
- Cancelling deletion keeps a model; confirming deletion removes it.
- Unload updates loaded-model state.
- Start/stop controls update status with destructive confirmation.
- Mobile viewport has no horizontal page overflow.
- No JavaScript page errors occurred.

Desktop and mobile screenshots were visually inspected. The delivered preview uses simulated installed models; it is not evidence of a live Ollama connection.

## Other checks

- Python source compilation and JavaScript syntax check passed.
- ZIP contents verified: source, local assets, documentation and tests included; no virtual environment, caches, logs, conversation history or credentials.

## Not verified against live Ollama

Real model inference, network model downloads, GPU statistics, real Ollama process termination/restart and a fresh end-user double-click installation were not exercised. The Windows process API and subprocess arguments were tested with mocks to avoid altering an existing Ollama installation. Ollama API payloads follow its official documentation.
