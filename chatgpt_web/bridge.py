"""JSON-lines protocol, private stdin/stdout only."""

import asyncio
import json
import sys

from . import __version__
from .errors import ClientError


class Bridge:
    def __init__(self, website, output=None):
        self.website = website
        self.output = output or sys.stdout
        self.lock = asyncio.Lock()
        self.tasks = set()

    def write(self, message):
        self.output.write(json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n")
        self.output.flush()

    async def dispatch(self, request):
        if not isinstance(request, dict):
            self.write({"id": None, "error": {"code": "request", "message": "Une requête doit être un objet JSON."}})
            return
        number = request.get("id")
        method = request.get("method")
        params = request.get("params", {})
        try:
            if not isinstance(number, (str, int)) or isinstance(number, bool) or not isinstance(params, dict):
                raise ClientError("request", "Identifiant ou paramètres invalides.")
            if method == "stop":
                result = await self.website.stop()
            elif method == "ping":
                result = {"version": __version__, "busy": self.website.busy}
            else:
                async with self.lock:
                    result = await self._operation(number, method, params)
            self.write({"id": number, "result": result})
        except ClientError as error:
            self.write({"id": number, "error": error.as_dict()})
        except (TypeError, KeyError, ValueError):
            self.write({"id": number, "error": {"code": "parameters", "message": "Paramètres invalides pour cette opération."}})
        except asyncio.CancelledError:
            raise
        except Exception:
            # Never echo a browser exception: it can contain a credential-bearing expression.
            self.write({"id": number, "error": {"code": "operation", "message": "L'opération a échoué. Vérifiez le dossier, les droits et la session du navigateur."}})

    async def _operation(self, number, method, params):
        website = self.website
        if method == "open":
            return await website.open(params.get("url"))
        if method == "snapshot":
            return await website.snapshot(params.get("url"))
        if method == "conversations":
            return await website.conversations()
        if method == "send":
            async def emit(snapshot):
                self.write({"id": number, "event": "snapshot", "data": snapshot})
            return await website.send(params["text"], emit, params.get("url"))
        if method == "upload":
            return await website.upload(params["paths"], params.get("url"))
        if method == "download":
            return await website.download(params["ref"], params["href"], params["destination"], params.get("url"))
        if method == "auth_begin":
            return await website.auth_begin()
        if method == "auth_state":
            return await website.auth_state()
        if method == "auth_action":
            return await website.auth_action(params["ref"], params.get("value"))
        raise ClientError("method", "Opération inconnue.")

    async def run(self, input_stream=None):
        stream = input_stream or sys.stdin.buffer
        self.write({"event": "ready", "version": __version__, "protocol": 1})
        try:
            while True:
                line = await asyncio.to_thread(stream.readline, 16 * 1024 * 1024 + 1)
                if not line:
                    break
                if len(line) > 16 * 1024 * 1024:
                    self.write({"id": None, "error": {"code": "request_size", "message": "Requête trop grande."}})
                    break
                try:
                    request = json.loads(line)
                except (ValueError, UnicodeDecodeError):
                    self.write({"id": None, "error": {"code": "json", "message": "JSON invalide."}})
                    continue
                task = asyncio.create_task(self.dispatch(request))
                self.tasks.add(task)
                task.add_done_callback(self.tasks.discard)
        finally:
            for task in tuple(self.tasks):
                task.cancel()
            if self.tasks:
                await asyncio.gather(*self.tasks, return_exceptions=True)
            await self.website.close()
