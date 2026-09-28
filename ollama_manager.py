"""Fixed local API and narrowly scoped Windows process management. No shell commands."""
import asyncio
import json
import os
from pathlib import Path
import shutil
import subprocess

import httpx
import psutil

BASE_URL = "http://127.0.0.1:11434"


class OllamaError(Exception):
    pass


class OllamaManager:
    def __init__(self):
        self.lifecycle_lock = asyncio.Lock()
        self.child = None

    def client(self, streaming=False):
        return httpx.AsyncClient(base_url=BASE_URL, trust_env=False,
                                timeout=httpx.Timeout(600 if streaming else 10, connect=3))

    @staticmethod
    def check(response):
        if response.is_error:
            try:
                detail = response.json().get("error", response.text)
            except (ValueError, AttributeError):
                detail = response.text
            raise OllamaError(f"Ollama returned {response.status_code}: {str(detail)[:600]}")

    async def request(self, method, path, payload=None):
        try:
            async with self.client() as client:
                response = await client.request(method, path, json=payload)
                self.check(response)
                return response.json() if response.content else {}
        except httpx.RequestError as exc:
            raise OllamaError("Cannot reach Ollama at 127.0.0.1:11434. Start Ollama and try again.") from exc
        except ValueError as exc:
            raise OllamaError("Ollama returned an invalid response. Check that port 11434 belongs to Ollama.") from exc

    async def stream(self, path, payload):
        # Closing/cancelling the browser request closes this upstream connection.
        try:
            async with self.client(streaming=True) as client:
                async with client.stream("POST", path, json=payload) as response:
                    if response.is_error:
                        await response.aread()
                        self.check(response)
                    async for line in response.aiter_lines():
                        if line:
                            event = json.loads(line)
                            yield json.dumps(event) + "\n"
                            if event.get("error"):
                                return
        except (httpx.RequestError, OllamaError, ValueError) as exc:
            detail = str(exc) if isinstance(exc, OllamaError) else "Ollama connection interrupted or timed out. Check its status and retry."
            yield json.dumps({"error": detail}) + "\n"

    def executable(self):
        candidates = [shutil.which("ollama.exe" if os.name == "nt" else "ollama")]
        if os.name == "nt":
            candidates.append(str(Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/Ollama/ollama.exe"))
        for candidate in candidates:
            try:
                if candidate and Path(candidate).is_file():
                    return Path(candidate).resolve()
            except OSError:
                continue
        return None

    def processes(self):
        """Only this user's known Ollama executables under the resolved install directory."""
        exe = self.executable()
        if not exe:
            return []
        owner = psutil.Process().username()
        found = []
        for proc in psutil.process_iter(["pid", "name", "exe", "username"]):
            try:
                info = proc.info
                if info["username"] != owner or not info["exe"]:
                    continue
                path = Path(info["exe"]).resolve()
                if path.name.lower() not in {"ollama.exe", "ollama app.exe", "ollama_llama_server.exe", "ollama"}:
                    continue
                if path == exe or (exe.parent in path.parents):
                    found.append(proc)
            except (psutil.Error, OSError):
                continue
        return found

    async def alive(self):
        try:
            return await self.request("GET", "/api/version")
        except OllamaError:
            return None

    async def start(self):
        if await self.alive() is not None:
            return "Ollama is already running."
        exe = self.executable()
        if not exe:
            raise OllamaError("Ollama was not found. Install Ollama for Windows, then restart ollama-web.")
        env = os.environ.copy()
        env["OLLAMA_HOST"] = "127.0.0.1:11434"
        env["OLLAMA_NO_CLOUD"] = "1"
        logs = Path(__file__).parent / "data"
        try:
            logs.mkdir(exist_ok=True)
            with (logs / "ollama.log").open("ab") as log:
                self.child = subprocess.Popen([str(exe), "serve"], stdin=subprocess.DEVNULL,
                                              stdout=log, stderr=log, env=env, shell=False,
                                              creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        except OSError as exc:
            raise OllamaError("Windows could not launch Ollama. Check folder permissions and your Ollama installation.") from exc
        for _ in range(40):
            if await self.alive() is not None:
                return "Ollama started."
            if self.child.poll() is not None:
                break
            await asyncio.sleep(.5)
        raise OllamaError("Ollama did not become ready. Check data/ollama.log and whether port 11434 is occupied.")

    async def stop(self):
        if os.name != "nt":
            raise OllamaError("Lifecycle controls are supported on Windows. Stop Ollama manually on this platform.")
        processes = await asyncio.to_thread(self.processes)
        if not processes:
            if await self.alive() is None:
                return "Ollama is already stopped."
            raise OllamaError("The running server cannot be safely identified as your Ollama installation. Quit it from its tray icon or service manager.")
        # Stop the tray supervisor before the server so it cannot immediately respawn it.
        processes.sort(key=lambda p: 0 if p.name().lower() == "ollama app.exe" else 1)
        for proc in processes:
            try:
                proc.terminate()
                await asyncio.to_thread(proc.wait, timeout=5)
            except psutil.NoSuchProcess:
                pass
            except (psutil.AccessDenied, psutil.TimeoutExpired) as exc:
                raise OllamaError("Windows could not stop an Ollama process. Quit Ollama from the tray and retry.") from exc
        for _ in range(10):
            if await self.alive() is None:
                return "Ollama stopped."
            await asyncio.sleep(.3)
        raise OllamaError("Ollama is still responding. A service may be restarting it; stop that service manually.")

    async def lifecycle(self, action):
        async with self.lifecycle_lock:
            if action == "start":
                return await self.start()
            if action == "stop":
                return await self.stop()
            await self.stop()
            return await self.start()

    def system(self):
        ram = psutil.virtual_memory()
        procs = []
        for proc in self.processes():
            try:
                procs.append({"pid": proc.pid, "name": proc.name(), "ram": proc.memory_info().rss})
            except psutil.Error:
                pass
        return {"cpu_percent": psutil.cpu_percent(interval=.1), "cpu_count": psutil.cpu_count(),
                "ram_total": ram.total, "ram_used": ram.used, "ram_percent": ram.percent,
                "platform": __import__("platform").platform(), "processes": procs,
                "executable_found": self.executable() is not None, "lifecycle_supported": os.name == "nt"}
