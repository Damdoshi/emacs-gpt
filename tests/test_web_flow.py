from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from chatgpt_web.errors import ClientError
from chatgpt_web.web import Website


class WebsiteFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_new_conversation_does_not_send_into_another_open_conversation(self):
        site = Website()
        site.start = AsyncMock()
        site.current_url = AsyncMock(return_value="https://chatgpt.com/c/other-conversation")
        site.open = AsyncMock(return_value={"messages": []})
        await site.snapshot(None)
        site.open.assert_awaited_once_with(None)

    async def test_completed_response_is_recognized_when_empty_send_button_is_disabled(self):
        site = Website()
        site.settle_seconds = 0
        initial = {"generating": False, "messages": []}
        finished = {"generating": False, "prompt_enabled": True, "send_ready": False,
                    "messages": [{"role": "assistant", "blocks": [{"type": "text", "text": "Réponse"}]}]}
        site.snapshot = AsyncMock(return_value=initial)
        site.assert_origin = AsyncMock()
        site.command = AsyncMock(side_effect=lambda method, params=None: (
            {"root": {"nodeId": 1}} if method == "DOM.getDocument" else {"nodeId": 2}))
        sent = False
        clicks = []

        async def call(method, *args):
            nonlocal sent
            if method == "promptInfo":
                return {"value": "", "available": True}
            if method == "snapshot":
                return finished if sent else {"send_ready": True}
            if method == "click":
                clicks.append(args[0])
                sent = True
                return True

        site.call = call
        emit = AsyncMock()
        self.assertEqual(await site.send("Bonjour", emit), finished)
        self.assertEqual(clicks, ["send"])
        emit.assert_awaited_once_with(finished)
        self.assertFalse(site.busy)

    async def test_existing_site_draft_is_not_overwritten(self):
        site = Website()
        site.snapshot = AsyncMock(return_value={"generating": False})
        site.call = AsyncMock(return_value={"value": "Brouillon du navigateur"})
        site.command = AsyncMock()
        with self.assertRaises(ClientError) as caught:
            await site.send("Nouveau texte", AsyncMock())
        self.assertEqual(caught.exception.code, "existing_draft")
        site.command.assert_not_awaited()

    async def test_navigation_waits_for_new_document_not_old_ready_state(self):
        site = Website()
        site.start = AsyncMock()
        frames = iter(["old", "old", "new"])

        async def command(method, params=None):
            if method == "Page.getFrameTree":
                return {"frameTree": {"frame": {"loaderId": next(frames)}}}
            return {"loaderId": "new"}

        site.command = AsyncMock(side_effect=command)
        site.evaluate = AsyncMock(return_value="complete")
        site.current_url = AsyncMock(return_value="https://chatgpt.com/c/abcdefgh")
        site.assert_origin = AsyncMock()
        with patch("chatgpt_web.web.asyncio.sleep", new=AsyncMock()):
            await site.navigate("https://chatgpt.com/c/abcdefgh")
        self.assertEqual(sum(call.args[0] == "Page.getFrameTree" for call in site.command.await_args_list), 3)


class DownloadTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.site = Website()
        self.site.downloads = SimpleNamespace(name=str(self.root / "browser"))
        Path(self.site.downloads.name).mkdir()
        self.file = {"ref": 0, "href": "sandbox:/mnt/data/correctif.patch", "label": "correctif.patch"}
        self.site.snapshot = AsyncMock(return_value={"files": [self.file]})
        self.destination = self.root / "résultat avec espaces.patch"

    def completed(self):
        guid = "11111111-2222-3333-4444-555555555555"
        (Path(self.site.downloads.name) / guid).write_bytes(b"exact patch\n")
        self.site.download_events[guid] = {"guid": guid, "state": "completed"}

    async def test_download_publishes_complete_private_file(self):
        async def click(*_args):
            self.completed()
            return True
        self.site.call = AsyncMock(side_effect=click)
        result = await self.site.download(0, self.file["href"], str(self.destination))
        self.assertEqual(result["bytes"], 12)
        self.assertEqual(self.destination.read_bytes(), b"exact patch\n")
        self.assertEqual(self.destination.stat().st_mode & 0o777, 0o600)

    async def test_file_created_during_download_is_not_overwritten(self):
        async def click(*_args):
            self.destination.write_text("existing")
            self.completed()
            return True
        self.site.call = AsyncMock(side_effect=click)
        with self.assertRaises(ClientError) as caught:
            await self.site.download(0, self.file["href"], str(self.destination))
        self.assertEqual(caught.exception.code, "file_exists")
        self.assertEqual(self.destination.read_text(), "existing")
        self.assertFalse(list(self.root.glob(".chatgpt-web-download-*")))

    async def test_library_view_uses_a_separate_tab_then_restores_conversation(self):
        self.file["href"] = "https://chatgpt.com/library?file_id=example"
        self.site.session = "conversation-session"

        async def command(method, params=None, **_kwargs):
            if method == "Target.createTarget":
                return {"targetId": "file-target"}
            if method == "Target.attachToTarget":
                return {"sessionId": "file-session"}
            return {}

        self.site.browser.command = AsyncMock(side_effect=command)
        self.site.navigate = AsyncMock()
        self.site.assert_origin = AsyncMock()

        async def click(*args):
            self.assertEqual(args[0], "clickDownload")
            self.completed()
            return True

        self.site.call = AsyncMock(side_effect=click)
        await self.site.download(0, self.file["href"], str(self.destination))
        self.assertEqual(self.site.session, "conversation-session")
        self.assertTrue(any(call.args == ("Target.closeTarget", {"targetId": "file-target"})
                            for call in self.site.browser.command.await_args_list))
