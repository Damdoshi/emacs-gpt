import asyncio
from pathlib import Path
import tempfile
import unittest

from chatgpt_web.cdp import Chromium
from chatgpt_web.errors import ClientError


EMULATOR = '''#!/usr/bin/python3
import json,os
reader=os.fdopen(3,'rb',buffering=0)
writer=os.fdopen(4,'wb',buffering=0)
buffer=b''
while True:
 data=reader.read(4096)
 if not data: break
 buffer+=data
 while b'\\0' in buffer:
  raw,buffer=buffer.split(b'\\0',1)
  message=json.loads(raw)
  if message['method']=='Never': continue
  if message['method']=='Reject':
   result={'id':message['id'],'error':{'message':'password=NEVER_PRINT'}}
  else:
   result={'id':message['id'],'result':message.get('params',{})}
  encoded=json.dumps(result,ensure_ascii=False).encode()+b'\\0'
  for start in range(0,len(encoded),3): writer.write(encoded[start:start+3])
  if message['method']=='Browser.close': raise SystemExit(0)
'''


class CDPTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        executable = self.root / "fake-chromium"
        executable.write_text(EMULATOR)
        executable.chmod(0o700)
        self.browser = Chromium(self.root / "profile", executable=str(executable))
        await self.browser.start()
        self.addAsyncCleanup(self.browser.close)

    async def test_split_utf8_frames_and_concurrent_commands(self):
        results = await asyncio.gather(*(self.browser.command("Echo", {"text": f"Réponse été {i}"}) for i in range(8)))
        self.assertEqual(results[-1]["text"], "Réponse été 7")

    async def test_browser_error_does_not_leak_request(self):
        with self.assertRaises(ClientError) as caught:
            await self.browser.command("Reject")
        self.assertNotIn("NEVER_PRINT", str(caught.exception))

    async def test_timeout_releases_pending_request(self):
        with self.assertRaises(ClientError) as caught:
            await self.browser.command("Never", timeout=0.03)
        self.assertEqual(caught.exception.code, "browser_timeout")
        self.assertFalse(self.browser.pending)

    async def test_private_profile(self):
        self.assertEqual(self.browser.profile.stat().st_mode & 0o777, 0o700)
