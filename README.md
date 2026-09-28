# ollama-web

A calm, local browser workspace for Ollama on Windows. FastAPI backend, plain HTML/CSS/JavaScript frontend, no build tools or cloud account required.

## Quick start on Windows

1. Install **Python 3.11 or newer** from https://www.python.org/downloads/windows/ (include the Python launcher). Install **Ollama for Windows** from https://ollama.com/download/windows if it is not already installed.
2. Extract the ZIP completely into a folder you can write to, such as `Documents\ollama-web`. Do not run it from inside the ZIP.
3. Double-click **start.bat**. On first run it creates a private `.venv` and downloads Python dependencies. This step needs internet access; later runs reuse the environment.
4. Your browser opens at **http://127.0.0.1:8080**. Keep the terminal window open.
5. If Ollama is stopped, open **System → Start**. Select an installed model and send a message. Existing models are discovered automatically; there is nothing to import or hardcode.

To close the web server, press **Ctrl+C** in its terminal. This does not stop Ollama. Use **System → Stop** if you want to stop Ollama too. No administrator session is normally required.

## Features

- Stream chat responses, stop generation, copy responses, and see tokens/second when Ollama provides timings.
- Separate expandable reasoning text for models that return `message.thinking`.
- Discover models dynamically; switch models, download with layer progress, delete with confirmation, and unload from memory.
- View loaded models, RAM/VRAM use and expiry through Ollama's `/api/ps` endpoint.
- Start, stop and restart Ollama on Windows; view version, basic system CPU/RAM and identified Ollama processes.
- Local browser conversation history with individual deletion and JSON export.
- Responsive light interface, keyboard support, safe basic Markdown/code-block rendering, all frontend assets included locally.

## History and privacy

History is saved in **this browser's local storage**, scoped to the web address. Always use `http://127.0.0.1:8080` to see the same history: `localhost:8080`, a different browser, or a private window has separate storage. Clearing browser data removes history. It is not encrypted. Use one tab to avoid conflicting changes and export JSON backups with the arrow beside **Your conversations**. Export is an archive; the UI does not currently import it. Browser quota errors are shown visibly.

Chats go to the fixed local Ollama endpoint `http://127.0.0.1:11434`. Models marked as remote/cloud are disabled. When this app starts Ollama it also sets `OLLAMA_NO_CLOUD=1`. An already-running Ollama retains its own configuration; for strictly local operation use local models and disable cloud in Ollama itself. Model downloads and first-time dependency installation use the internet. There are no remote fonts, CDNs, telemetry, or analytics in this app.

## Lifecycle behavior

- Start runs the resolved `ollama.exe serve` with a fixed argument list, no shell, hidden window, localhost binding and logging to `data\ollama.log`. Ollama is found on PATH or in `%LOCALAPPDATA%\Programs\Ollama`.
- Stop targets only recognized Ollama executable names under that resolved installation folder, owned by the current Windows user. It closes the tray supervisor before the server to prevent the tray from respawning it. This affects other chats, downloads or apps using those same processes; the UI asks for confirmation.
- Stop does not delete models. Unload frees memory; Delete removes an installed model and may require downloading it again.
- Services, installations belonging to other users, elevated processes, custom installs outside the resolved folder, and remote/Docker/WSL Ollama instances may require manual management. There is no privilege elevation or arbitrary process/PID input in the browser.
- Start/restart uses the environment inherited by ollama-web, including `OLLAMA_MODELS` when set. Relaunch the web app after changing Windows environment variables.
- If a service or supervisor outside this installation keeps restarting Ollama, stop it manually. Non-Windows users can run the UI but should manage the Ollama service themselves.

## Troubleshooting

**Python not found:** Install Python 3.11+ with the launcher, then reopen `start.bat`. Microsoft Store aliases may need disabling if they intercept `python`. Manual setup below also works.

**Installation interrupted:** Run `start.bat` again; missing dependencies will be installed. If the virtual environment itself is damaged, remove only this project's `.venv` folder and rerun.

**Ollama executable not found:** Install Ollama, reopen the web app, or add the correct Ollama install folder to PATH. An existing API may still work even if its executable cannot be identified; lifecycle operations will then be restricted.

**Port 8080 occupied:** Close any other ollama-web instance or application using 8080, then try again. The launcher binds only to `127.0.0.1:8080`; changing the port also requires updating Host validation in `app.py`.

**Cannot reach Ollama:** Open System and Start. Check whether Ollama uses the standard port 11434. The app deliberately does not accept arbitrary server URLs. Inspect `data\ollama.log` if starting fails, or `%LOCALAPPDATA%\Ollama` for Ollama's own logs.

**No models:** Start Ollama, open Model library and download a local model by name, such as `llama3.2:latest`. Download size and hardware needs vary by model. All installed names are discovered, including custom names.

**Slow first response:** Loading a model can take time. Stop generation aborts this request and closes the upstream connection; Ollama may take a moment to release its work. Stopping a reply does not unload the model.

**Download stopped:** Retry the same name. Ollama normally reuses downloaded layers. Progress is per current layer, not an inaccurate total across the whole model. Cancelling closes this pull request; shared downloads/partial files may remain in Ollama.

**Long conversation:** The app limits requests to 200 messages and 500,000 content characters. A model's context window may be smaller; start a new chat if it forgets context or runs out of memory. Images, attachments and tool calls are not supported.

**Model size vs disk usage:** The System page shows the sum of logical installed model sizes from Ollama. Shared layers can be counted more than once, and partial downloads are not included. This is not an exact filesystem usage measurement. GPU percentages on loaded models derive from API memory sizes, not live GPU utilization.

**Session expired:** Refresh after restarting the web server. Its request token changes each time.

## Manual setup

Open a terminal in the extracted project folder:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe launch.py
```

The launcher binds only to the loopback address, checks port availability, and opens your browser. Do not expose this service on a network or through a reverse proxy. Its same-origin token and Host checks protect local browser access; it is not a multi-user authenticated server.

## Project layout

```text
app.py                 FastAPI routes, validation, local-only request protections
ollama_manager.py      Fixed Ollama API client and Windows process management
launch.py              Local server and automatic browser opening
start.bat              Windows setup and launch
requirements.txt       Application dependencies
templates/index.html   Interface shell
static/                Local CSS, JavaScript and icon
tests/test_app.py      Backend regression tests with mocked Ollama
TESTING.md             Test results and known verification limits
data/ollama.log         Created when this app starts Ollama
```

## Development tests

```powershell
.\.venv\Scripts\python.exe -m pip install pytest
.\.venv\Scripts\python.exe -m pytest tests -q
```

Tests use simulated API responses and process objects and do not download/delete models or stop real Ollama processes. See `TESTING.md` for the tests run for this package.

API references: https://docs.ollama.com/api/chat, https://docs.ollama.com/api/pull, https://docs.ollama.com/api/ps, https://docs.ollama.com/windows.
