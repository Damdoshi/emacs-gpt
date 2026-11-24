"""Interact with the website through its browser UI, without private endpoints."""

import asyncio
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import time
from urllib.parse import urljoin, urlsplit

from .cdp import Chromium
from .errors import ClientError


AUTH_ORIGINS = {
    "https://chatgpt.com", "https://auth.openai.com", "https://auth0.openai.com",
    "https://accounts.google.com", "https://login.microsoftonline.com",
    "https://login.live.com", "https://appleid.apple.com", "https://account.apple.com",
}


def default_profile():
    return Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share"))) / "chatgpt-web/profile"


class Website:
    def __init__(self, profile=None, *, executable=None, headed=False,
                 selector_file=None, timeout=600, base_url="https://chatgpt.com",
                 testing=False, browser_args=()):
        self.base_url = base_url.rstrip("/")
        parts = urlsplit(self.base_url)
        if self.base_url != "https://chatgpt.com" and not (
                testing and parts.hostname in {"localhost", "127.0.0.1"}):
            raise ClientError("origin", "Le service doit utiliser https://chatgpt.com.")
        self.testing = testing
        self.browser = Chromium(profile or default_profile(), executable=executable,
                                headed=headed, extra_args=browser_args)
        self.timeout = timeout
        self.settle_seconds = 1.8
        self.session = None
        self.lockfile = None
        self.downloads = None
        self.download_events = {}
        self.auth_controls = {}
        self.stopped = False
        self.busy = False
        self.pending_attachments = False
        self.selectors = json.loads(Path(__file__).with_name("selectors.json").read_text())
        if selector_file:
            overrides = json.loads(Path(selector_file).expanduser().read_text())
            if not isinstance(overrides, dict) or any(not isinstance(v, str) for v in overrides.values()):
                raise ClientError("selectors", "Les sélecteurs doivent être un objet JSON de chaînes.")
            self.selectors.update(overrides)
        self.page_script = Path(__file__).with_name("page.js").read_text()

    async def start(self):
        if self.session:
            return
        profile = self.browser.profile
        profile.mkdir(parents=True, exist_ok=True, mode=0o700)
        if profile.is_symlink() or profile.stat().st_uid != os.getuid():
            raise ClientError("profile_permissions", "Le profil doit appartenir à votre utilisateur.")
        profile.chmod(0o700)
        fd = os.open(profile / ".chatgpt-web.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        self.lockfile = os.fdopen(fd, "w")
        try:
            fcntl.flock(self.lockfile, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.lockfile.close()
            self.lockfile = None
            raise ClientError("profile_busy", "Ce profil est déjà utilisé. Fermez l'autre compagnon ou utilisez un autre profil.") from None
        try:
            await self.browser.start()
            targets = await self.browser.command("Target.getTargets")
            pages = [t for t in targets["targetInfos"] if t["type"] == "page"]
            target = pages[0]["targetId"] if pages else (await self.browser.command("Target.createTarget", {"url": "about:blank"}))["targetId"]
            attached = await self.browser.command("Target.attachToTarget", {"targetId": target, "flatten": True})
            self.session = attached["sessionId"]
            await self.command("Page.enable")
            await self.command("Runtime.enable")
            await self.command("Network.enable")
            if not self.testing:
                await self.command("Network.setBlockedURLs", {"urls": ["http://*", "ws://*", "ftp://*"]})
            self.downloads = tempfile.TemporaryDirectory(prefix=".downloads-", dir=profile)
            self.browser.listeners.append(self._browser_event)
            await self.browser.command("Browser.setDownloadBehavior", {
                "behavior": "allowAndName", "downloadPath": self.downloads.name, "eventsEnabled": True})
        except BaseException:
            await self.close()
            raise

    async def command(self, method, params=None):
        return await self.browser.command(method, params, session=self.session)

    async def evaluate(self, expression):
        result = await self.command("Runtime.evaluate", {
            "expression": expression, "returnByValue": True, "awaitPromise": True})
        if "exceptionDetails" in result:
            raise ClientError("page_changed", "L'adaptateur ne reconnaît pas cette page. Vérifiez les sélecteurs ou actualisez.")
        return result.get("result", {}).get("value")

    async def install_adapter(self):
        await self.evaluate("if (!window.__cgw) { window.__cgwSelectors = "
                            + json.dumps(self.selectors) + ";" + self.page_script + " }")

    async def call(self, method, *args):
        await self.install_adapter()
        return await self.evaluate("window.__cgw[" + json.dumps(method) + "](..."
                                   + json.dumps(args, ensure_ascii=False) + ")")

    async def current_url(self):
        return await self.evaluate("location.href")

    def _allowed_auth(self, url):
        parts = urlsplit(url)
        return (parts.scheme + "://" + parts.netloc) in AUTH_ORIGINS or (
            self.testing and url.startswith(self.base_url + "/"))

    async def assert_origin(self, *, auth=False):
        url = await self.current_url()
        parts = urlsplit(url)
        origin = parts.scheme + "://" + parts.netloc
        if not ((auth and self._allowed_auth(url)) or origin == self.base_url):
            raise ClientError("unexpected_origin", "La page active n'est pas sur un domaine autorisé. Aucune saisie n'a été envoyée.")

    async def navigate(self, url):
        await self.start()
        previous = (await self.command("Page.getFrameTree"))["frameTree"]["frame"].get("loaderId")
        result = await self.command("Page.navigate", {"url": url})
        if result.get("errorText"):
            raise ClientError("navigation", "La page HTTPS n'a pas pu être chargée. Vérifiez le réseau et les certificats.")
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            try:
                state = await self.evaluate("document.readyState")
                current = await self.current_url()
                frame = (await self.command("Page.getFrameTree"))["frameTree"]["frame"]
                committed = frame.get("loaderId") != previous or (not result.get("loaderId") and current == url)
                if committed and state in {"interactive", "complete"} and current != "about:blank":
                    await self.assert_origin(auth=True)
                    return
            except ClientError as error:
                if error.code == "unexpected_origin":
                    raise
            await asyncio.sleep(0.2)
        raise ClientError("navigation_timeout", "Le chargement de la page a dépassé le délai prévu.")

    async def wait_prompt(self):
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            await self.assert_origin(auth=True)
            snapshot = await self.call("snapshot")
            if snapshot["challenge"]:
                raise ClientError("interactive_verification", "Le site demande une vérification interactive. Elle n'est pas contournée ; utilisez la commande de connexion.")
            if snapshot["prompt"] and snapshot["connected"]:
                await self.assert_origin()
                return snapshot
            if urlsplit(await self.current_url()).netloc != urlsplit(self.base_url).netloc:
                break
            await asyncio.sleep(0.5)
        raise ClientError("login_required", "Session absente, expirée ou page non reconnue. Lancez chatgpt-web-login dans Emacs ou chatgpt-web auth dans un terminal.")

    def conversation_url(self, value):
        if re.fullmatch(r"[A-Za-z0-9_-]{8,100}", value or ""):
            return self.base_url + "/c/" + value
        parts = urlsplit(value or "")
        if (parts.scheme + "://" + parts.netloc == self.base_url
                and re.fullmatch(r"/c/[A-Za-z0-9_-]{8,100}/?", parts.path)
                and not parts.query and not parts.fragment):
            return self.base_url + parts.path
        raise ClientError("conversation_url", "Indiquez un identifiant ou une URL https://chatgpt.com/c/… de votre compte.")

    async def open(self, url=None):
        expected = self.conversation_url(url) if url else self.base_url + "/"
        await self.start()
        if self.pending_attachments:
            if await self.current_url() != expected:
                raise ClientError("pending_attachments", "Des fichiers sont joints à un brouillon dans la conversation active. Envoyez ce message avant d'en ouvrir une autre.")
            return await self.wait_prompt()
        await self.navigate(expected)
        return await self.wait_prompt()

    async def snapshot(self, url=None):
        await self.start()
        expected = self.conversation_url(url) if url else self.base_url + "/"
        if await self.current_url() != expected:
            return await self.open(url)
        await self.assert_origin()
        return await self.wait_prompt()

    async def conversations(self):
        await self.start()
        if not (await self.current_url()).startswith(self.base_url + "/"):
            await self.open()
        await self.wait_prompt()
        entries = await self.call("conversations")
        unique = {}
        for entry in entries:
            try:
                entry["url"] = self.conversation_url(entry["url"])
                unique[entry["url"]] = entry
            except ClientError:
                continue
        return {"conversations": list(unique.values()), "partial": True,
                "note": "Conversations actuellement chargées dans la barre latérale. Une URL permet d'en ouvrir une autre."}

    async def send(self, text, emit, url=None):
        if not isinstance(text, str) or not text.strip():
            raise ClientError("empty_message", "Le message est vide.")
        if self.busy:
            raise ClientError("busy", "Une réponse est déjà en cours.")
        initial = await self.snapshot(url)
        if initial["generating"]:
            raise ClientError("busy", "Le site indique qu'une réponse est déjà en cours.")
        prompt = await self.call("promptInfo")
        if prompt.get("value", "").strip():
            raise ClientError("existing_draft", "Le navigateur contient déjà un brouillon. Il n'a pas été écrasé.")
        self.busy = True
        self.stopped = False
        try:
            document = await self.command("DOM.getDocument")
            node = await self.command("DOM.querySelector", {"nodeId": document["root"]["nodeId"], "selector": self.selectors["prompt"]})
            if not node["nodeId"]:
                raise ClientError("prompt_missing", "La zone de rédaction du site est introuvable.")
            await self.command("DOM.focus", {"nodeId": node["nodeId"]})
            await self.command("Input.insertText", {"text": text})
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                await self.assert_origin()
                current = await self.call("snapshot")
                if current["send_ready"]:
                    break
                await asyncio.sleep(0.3)
            else:
                raise ClientError("send_not_ready", "Le bouton d'envoi reste indisponible. Le brouillon est conservé dans le navigateur ; le message n'a pas été envoyé.")
            if not await self.call("click", "send"):
                raise ClientError("send_not_ready", "Le message n'a pas été envoyé.")
            self.pending_attachments = False
            baseline = sum(m["role"] == "assistant" for m in initial["messages"])
            last_hash = None
            stable_since = time.monotonic()
            deadline = time.monotonic() + self.timeout
            while time.monotonic() < deadline:
                await self.assert_origin()
                snapshot = await self.call("snapshot")
                digest = hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest()
                if digest != last_hash:
                    last_hash = digest
                    stable_since = time.monotonic()
                    await emit(snapshot)
                assistants = sum(m["role"] == "assistant" for m in snapshot["messages"])
                if not snapshot["generating"] and time.monotonic() - stable_since >= self.settle_seconds:
                    if self.stopped or (assistants > baseline and snapshot["prompt_enabled"]):
                        return snapshot
                await asyncio.sleep(0.4)
            raise ClientError("response_timeout", "Le suivi de la réponse a expiré. Actualisez la conversation avant de réessayer ; aucun second envoi automatique n'a été effectué.")
        finally:
            self.busy = False

    async def stop(self):
        self.stopped = True
        if not self.session:
            return {"stopped": False}
        await self.assert_origin()
        return {"stopped": bool(await self.call("click", "stop"))}

    async def upload(self, paths, url=None):
        if not isinstance(paths, list) or not paths:
            raise ClientError("file_missing", "Aucun fichier à envoyer.")
        files = []
        for value in paths:
            path = Path(value).expanduser().absolute()
            if not path.is_file():
                raise ClientError("file_missing", "Un des fichiers à envoyer n'est pas un fichier lisible.")
            files.append(str(path))
        await self.snapshot(url)
        document = await self.command("DOM.getDocument")
        node = await self.command("DOM.querySelector", {"nodeId": document["root"]["nodeId"], "selector": self.selectors["upload"]})
        if not node["nodeId"]:
            raise ClientError("upload_missing", "Le champ d'envoi de fichiers est introuvable. Le site a peut-être changé.")
        await self.command("DOM.setFileInputFiles", {"nodeId": node["nodeId"], "files": files})
        self.pending_attachments = True
        return {"attached": [Path(f).name for f in files], "note": "Fichiers sélectionnés dans le site. Leur traitement peut continuer avant l'envoi du message."}

    def _browser_event(self, event):
        method = event.get("method")
        data = event.get("params", {})
        if method == "Browser.downloadWillBegin":
            self.download_events[data["guid"]] = dict(data, state="inProgress")
        elif method == "Browser.downloadProgress" and data["guid"] in self.download_events:
            self.download_events[data["guid"]].update(data)

    async def download(self, ref, href, destination, url=None):
        target = Path(destination).expanduser().absolute()
        if target.exists() or target.is_symlink():
            raise ClientError("file_exists", "La destination existe déjà. Choisissez un autre nom.")
        if not target.parent.is_dir():
            raise ClientError("directory_missing", "Le dossier de destination n'existe pas.")
        # Refresh returns fresh refs; match the requested href rather than trusting an old index.
        snapshot = await self.snapshot(url)
        match = next((item for item in snapshot["files"] if item["href"] == href), None)
        if not match:
            raise ClientError("file_expired", "Ce lien de fichier n'est plus présent. Actualisez la conversation.")
        previous = set(self.download_events)
        file_url = urljoin(self.base_url + "/", match["href"])
        parts = urlsplit(file_url)
        library_view = (parts.scheme + "://" + parts.netloc == self.base_url
                        and parts.path.startswith("/library"))
        original_session = self.session
        temporary_target = None
        try:
            if library_view:
                # A dedicated file tab preserves an unsent conversation and its attachments.
                created = await self.browser.command("Target.createTarget", {"url": "about:blank"})
                temporary_target = created["targetId"]
                attached = await self.browser.command("Target.attachToTarget", {
                    "targetId": temporary_target, "flatten": True})
                self.session = attached["sessionId"]
                await self.command("Page.enable")
                await self.command("Runtime.enable")
                await self.command("Network.enable")
                if not self.testing:
                    await self.command("Network.setBlockedURLs", {"urls": ["http://*", "ws://*", "ftp://*"]})
                await self.navigate(file_url)
                deadline = time.monotonic() + 20
                while time.monotonic() < deadline:
                    await self.assert_origin()
                    if set(self.download_events) - previous or await self.call("clickDownload"):
                        break
                    await asyncio.sleep(0.3)
                else:
                    raise ClientError("download_button", "La vue du fichier ne présente aucun bouton de téléchargement reconnu.")
            elif not await self.call("clickFile", match["ref"], match["href"]):
                raise ClientError("file_expired", "Le lien a changé avant le téléchargement.")
            return await self._wait_download(previous, target)
        finally:
            self.session = original_session
            if temporary_target:
                try:
                    await self.browser.command("Target.closeTarget", {"targetId": temporary_target})
                except ClientError:
                    pass

    async def _wait_download(self, previous, target):
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            events = [event for key, event in self.download_events.items() if key not in previous]
            if events:
                event = events[0]
                if event["state"] == "canceled":
                    raise ClientError("download_canceled", "Le navigateur a annulé le téléchargement.")
                if event["state"] == "completed":
                    source = Path(self.downloads.name) / event["guid"]
                    if not source.is_file():
                        raise ClientError("download_missing", "Le téléchargement est terminé mais le fichier est indisponible.")
                    fd, stage_name = tempfile.mkstemp(prefix=".chatgpt-web-download-", dir=target.parent)
                    stage = Path(stage_name)
                    try:
                        with os.fdopen(fd, "wb") as output, source.open("rb") as input_file:
                            shutil.copyfileobj(input_file, output)
                        # Publish only a complete file; link() refuses to overwrite an existing path.
                        os.link(stage, target)
                    except FileExistsError:
                        raise ClientError("file_exists", "La destination existe déjà et n'a pas été écrasée.") from None
                    finally:
                        stage.unlink(missing_ok=True)
                    source.unlink()
                    return {"path": str(target), "bytes": target.stat().st_size}
            await asyncio.sleep(0.2)
        raise ClientError("download_timeout", "Aucun téléchargement terminé. Le lien peut avoir expiré ou ouvrir une page qui nécessite une action supplémentaire.")

    async def auth_begin(self):
        await self.navigate(self.base_url + "/auth/login")
        await asyncio.sleep(0.5)
        return await self.auth_state()

    async def auth_state(self):
        await self.start()
        await self.assert_origin(auth=True)
        snapshot = await self.call("authSnapshot")
        self.auth_controls = {control["ref"]: control for control in snapshot["controls"]}
        return snapshot

    async def auth_action(self, ref, value=None):
        await self.assert_origin(auth=True)
        control = self.auth_controls.get(ref)
        if not control:
            raise ClientError("auth_changed", "Le formulaire a changé. Actualisez la connexion.")
        if control.get("href") and not self._allowed_auth(control["href"]):
            raise ClientError("auth_link", "Ce lien sort des domaines de connexion autorisés.")
        if control["kind"] == "input" and control["input_type"] not in {"checkbox", "radio", "submit", "button"} and not isinstance(value, str):
            raise ClientError("auth_value", "Ce champ nécessite une valeur.")
        if not await self.call("authAction", ref, value, control["label"]):
            raise ClientError("auth_changed", "Le formulaire a changé. Actualisez la connexion.")
        await asyncio.sleep(0.5)
        # Navigation can destroy the old JS context while an authentication action completes.
        for attempt in range(15):
            try:
                return await self.auth_state()
            except ClientError as error:
                if error.code not in {"browser_command", "page_changed"}:
                    raise
                await asyncio.sleep(0.2)
        raise ClientError("auth_changed", "La navigation continue. Actualisez la connexion.")

    async def close(self):
        await self.browser.close()
        self.session = None
        if self.downloads:
            self.downloads.cleanup()
            self.downloads = None
        if self.lockfile:
            self.lockfile.close()
            self.lockfile = None
