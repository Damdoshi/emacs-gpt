"""Small CDP pipe transport; standard Python library only.

Browser output and page console messages are deliberately not logged. The
protocol stream can contain passwords, cookies and private conversations.
"""

import asyncio
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading

from .errors import ClientError


class Chromium:
    def __init__(self, profile, *, executable=None, headed=False, extra_args=()):
        self.profile = Path(profile).expanduser().absolute()
        self.executable = executable or shutil.which("chromium") or shutil.which("google-chrome")
        self.headed = headed
        self.extra_args = list(extra_args)
        self.process = None
        self.pending = {}
        self.next_id = 0
        self.listeners = []
        self.write_lock = threading.Lock()
        self.writer = None
        self.reader = None
        self.closed = False

    async def start(self):
        if self.process and self.process.poll() is None:
            return
        self.closed = False
        self.process = None
        if not self.executable:
            raise ClientError("browser_missing", "Chromium est absent. Installez le paquet chromium.")
        self.executable = shutil.which(self.executable) or self.executable
        self.profile.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.profile.is_symlink() or self.profile.stat().st_uid != os.getuid():
            raise ClientError("profile_permissions", "Le profil doit appartenir à votre utilisateur et ne pas être un lien symbolique.")
        self.profile.chmod(0o700)
        self.loop = asyncio.get_running_loop()
        child_read, parent_write = os.pipe()
        parent_read, child_write = os.pipe()
        args = [self.executable, "--remote-debugging-pipe",
                "--user-data-dir=" + str(self.profile), "--no-first-run",
                "--no-default-browser-check", "--disable-background-networking",
                "--disable-sync", "--disable-dev-shm-usage"]
        if not self.headed:
            args.append("--headless=new")
        args.extend(self.extra_args)
        args.append("about:blank")
        try:
            self.process = subprocess.Popen(
                [sys.executable, str(Path(__file__).with_name("browser_child.py")),
                 str(child_read), str(child_write), *args],
                pass_fds=(child_read, child_write), stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True)
        except OSError:
            os.close(parent_read)
            os.close(parent_write)
            raise ClientError("browser_start", "Impossible de démarrer Chromium.") from None
        finally:
            os.close(child_read)
            os.close(child_write)
        self.writer = os.fdopen(parent_write, "wb", buffering=0)
        self.reader = os.fdopen(parent_read, "rb", buffering=0)
        self.thread = threading.Thread(target=self._read, daemon=True)
        self.thread.start()
        try:
            await self.command("Browser.getVersion", timeout=15)
        except ClientError:
            await self.close()
            raise ClientError("browser_start", "Chromium n'a pas démarré. Vérifiez le profil et le fonctionnement du sandbox Chromium sur cette machine.") from None

    def _read(self):
        buffer = bytearray()
        try:
            while True:
                chunk = self.reader.read(65536)
                if not chunk:
                    break
                buffer.extend(chunk)
                if len(buffer) > 64 * 1024 * 1024:
                    break
                while b"\0" in buffer:
                    raw, _, remainder = buffer.partition(b"\0")
                    buffer = bytearray(remainder)
                    if raw:
                        try:
                            self.loop.call_soon_threadsafe(self._receive, json.loads(raw))
                        except (ValueError, RuntimeError):
                            pass
        except (OSError, ValueError):
            pass
        try:
            self.loop.call_soon_threadsafe(self._disconnected)
        except RuntimeError:
            pass

    def _receive(self, message):
        if "id" in message:
            future = self.pending.pop(message["id"], None)
            if future and not future.done():
                if "error" in message:
                    future.set_exception(ClientError("browser_command", "L'opération demandée au navigateur a échoué."))
                else:
                    future.set_result(message.get("result", {}))
        else:
            for listener in tuple(self.listeners):
                listener(message)

    def _disconnected(self):
        for future in self.pending.values():
            if not future.done():
                future.set_exception(ClientError("browser_closed", "Le navigateur s'est arrêté."))
        self.pending.clear()

    def _write(self, data):
        with self.write_lock:
            if self.closed or self.writer is None:
                raise OSError("closed")
            view = memoryview(data)
            while view:
                count = self.writer.write(view)
                view = view[count:]

    async def command(self, method, params=None, *, session=None, timeout=30):
        self.next_id += 1
        number = self.next_id
        message = {"id": number, "method": method, "params": params or {}}
        if session:
            message["sessionId"] = session
        future = self.loop.create_future()
        self.pending[number] = future
        try:
            await asyncio.to_thread(self._write, json.dumps(message, ensure_ascii=False).encode("utf-8") + b"\0")
            return await asyncio.wait_for(future, timeout)
        except asyncio.TimeoutError:
            raise ClientError("browser_timeout", "Le navigateur n'a pas répondu dans le délai prévu.") from None
        except OSError:
            raise ClientError("browser_closed", "La liaison avec Chromium est fermée.") from None
        finally:
            self.pending.pop(number, None)

    async def close(self):
        if self.closed:
            return
        if self.process and self.process.poll() is None:
            try:
                await self.command("Browser.close", timeout=3)
            except ClientError:
                pass
        self.closed = True
        if self.writer:
            self.writer.close()
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                await asyncio.to_thread(self.process.wait, 5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                await asyncio.to_thread(self.process.wait)
        if self.reader:
            self.reader.close()
        if hasattr(self, "thread"):
            await asyncio.to_thread(self.thread.join, 1)
        self._disconnected()
