import asyncio
import io
import json
import unittest

from chatgpt_web.bridge import Bridge
from chatgpt_web.errors import ClientError
from chatgpt_web.web import Website


class FakeWebsite:
    busy = False

    def __init__(self):
        self.started = asyncio.Event()
        self.stopped = asyncio.Event()

    async def send(self, text, emit, url):
        self.busy = True
        self.started.set()
        await emit({"messages": [{"role": "assistant", "blocks": [{"type": "text", "text": "Début…"}]}]})
        await self.stopped.wait()
        self.busy = False
        return {"complete": True}

    async def stop(self):
        self.stopped.set()
        return {"stopped": True}

    async def open(self, url):
        if url == "secret":
            raise RuntimeError("password=SENSITIVE")
        raise ClientError("login_required", "Connexion requise.")


class BridgeTests(unittest.IsolatedAsyncioTestCase):
    async def test_stop_while_streaming_and_request_correlation(self):
        site = FakeWebsite()
        output = io.StringIO()
        bridge = Bridge(site, output)
        send = asyncio.create_task(bridge.dispatch({"id": 7, "method": "send", "params": {"text": "Bonjour"}}))
        await site.started.wait()
        await bridge.dispatch({"id": 8, "method": "stop"})
        await send
        messages = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(messages[0]["id"], 7)
        self.assertEqual(messages[0]["event"], "snapshot")
        self.assertEqual(messages[1], {"id": 8, "result": {"stopped": True}})
        self.assertEqual(messages[2]["id"], 7)

    async def test_errors_never_echo_exception_or_parameters(self):
        output = io.StringIO()
        bridge = Bridge(FakeWebsite(), output)
        await bridge.dispatch({"id": 1, "method": "open", "params": {"url": "secret"}})
        self.assertNotIn("SENSITIVE", output.getvalue())
        self.assertNotIn("password", output.getvalue())
        self.assertEqual(json.loads(output.getvalue())["error"]["code"], "operation")

    async def test_public_errors_are_preserved(self):
        output = io.StringIO()
        await Bridge(FakeWebsite(), output).dispatch({"id": 3, "method": "open"})
        self.assertEqual(json.loads(output.getvalue())["error"]["code"], "login_required")

    async def test_invalid_requests_are_errors(self):
        output = io.StringIO()
        bridge = Bridge(FakeWebsite(), output)
        for request in [None, [], {"id": 2, "method": "unknown"}, {"id": 3, "method": "send"}]:
            await bridge.dispatch(request)
        self.assertTrue(all("error" in json.loads(line) for line in output.getvalue().splitlines()))


class OriginTests(unittest.TestCase):
    def test_conversation_restrictions(self):
        site = Website()
        self.assertEqual(site.conversation_url("abcdefgh-123"), "https://chatgpt.com/c/abcdefgh-123")
        for value in ["http://chatgpt.com/c/abcdefgh", "https://evil.test/c/abcdefgh",
                      "https://chatgpt.com.evil/c/abcdefgh", "https://chatgpt.com/c/abcdefgh?x=1",
                      "https://chatgpt.com/c/../../foo"]:
            with self.subTest(value=value), self.assertRaises(ClientError):
                site.conversation_url(value)

    def test_production_origin_requires_https_chatgpt(self):
        for origin in ["http://chatgpt.com", "https://evil.test", "http://127.0.0.1:1234"]:
            with self.subTest(origin=origin), self.assertRaises(ClientError):
                Website(base_url=origin)

    def test_authentication_origin_is_not_a_suffix_check(self):
        site = Website()
        self.assertTrue(site._allowed_auth("https://auth.openai.com/log-in/password"))
        self.assertFalse(site._allowed_auth("https://auth.openai.com.evil.test/"))
        self.assertFalse(site._allowed_auth("http://auth.openai.com/"))

    def test_browser_uses_pipes_and_keeps_sandbox_enabled(self):
        site = Website()
        self.assertNotIn("--no-sandbox", site.browser.extra_args)
