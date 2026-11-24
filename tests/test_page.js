/* Run with NODE_PATH pointing at a development installation of linkedom. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {parseHTML} = require('linkedom');
const script = fs.readFileSync(require('node:path').join(__dirname, '../chatgpt_web/page.js'), 'utf8');
function page(html) {
  const {window} = parseHTML('<!doctype html><html><head><title>Test</title></head><body>' + html + '</body></html>');
  window.HTMLElement.prototype.getClientRects = function () { return this.hidden ? [] : [{}]; };
  const context = vm.createContext({window, document: window.document,
    location: {href: 'https://chatgpt.com/c/abcdefgh', origin: 'https://chatgpt.com', pathname: '/c/abcdefgh'},
    getComputedStyle: () => ({display: 'block', visibility: 'visible'}),
    Event: window.Event, HTMLInputElement: window.HTMLInputElement, HTMLTextAreaElement: window.HTMLTextAreaElement});
  vm.runInContext(script, context);
  return window;
}
let checks = 0;
function test(name, fn) { fn(); checks++; console.log('OK', name); }
test('exact script contents, Unicode and two distinct code blocks', () => {
  const code = "printf '%s\\n' '$HOME'\n\t# été `backticks`\n";
  const window = page('<div data-message-author-role="assistant" data-message-id="id-1"><div class="markdown"><p>Bonjour <strong>toi</strong>.</p><pre><code class="language-bash"></code></pre><p>Suite.</p><pre><code class="language-diff">--- a/f\n+++ b/f\n</code></pre></div></div><textarea id="prompt-textarea"></textarea>');
  window.document.querySelector('code').textContent = code;
  const snapshot = window.__cgw.snapshot();
  const blocks = snapshot.messages[0].blocks;
  assert.equal(blocks.filter(b => b.type === 'code').length, 2);
  assert.equal(blocks[1].text, code);
  assert.equal(blocks[1].language, 'bash');
  assert.equal(blocks[0].text, 'Bonjour **toi**.');
});
test('hidden and script content is not rendered as response', () => {
  const window = page('<div data-message-author-role="assistant"><p>Texte</p><button>Copy</button><script>SECRET</script></div>');
  const text = JSON.stringify(window.__cgw.snapshot().messages);
  assert(!text.includes('Copy'));
  assert(!text.includes('SECRET'));
});
test('authentication snapshots never contain entered secrets', () => {
  const window = page('<label for="pw">Mot de passe</label><input id="pw" type="password" value="super-secret"><input type="hidden" value="csrf-secret"><button>Continuer</button>');
  const state = window.__cgw.authSnapshot();
  assert.equal(state.controls.length, 2);
  assert(!JSON.stringify(state).includes('super-secret'));
  assert(!JSON.stringify(state).includes('csrf-secret'));
});
test('submit input is an action rather than a password field', () => {
  const window = page('<input type="submit" value="Connexion">');
  assert.equal(window.__cgw.authSnapshot().controls[0].kind, 'button');
});
test('generation state and disabled empty send button remain distinct', () => {
  const window = page('<textarea id="prompt-textarea"></textarea><button data-testid="send-button" disabled>Envoyer</button><button data-testid="stop-button">Stop</button>');
  const state = window.__cgw.snapshot();
  assert(state.generating);
  assert(state.prompt_enabled);
  assert.equal(state.send_ready, false);
});
test('download entries preserve a stable href and stale refs are rejected', () => {
  const window = page('<a href="sandbox:/mnt/data/correctif.patch">correctif.patch</a>');
  const state = window.__cgw.snapshot();
  assert.equal(state.files[0].label, 'correctif.patch');
  assert.equal(window.__cgw.clickFile(state.files[0].ref, 'sandbox:/other'), false);
});
test('verification page is identified', () => {
  const window = page('<div id="challenge-stage">Vérification</div>');
  assert.equal(window.__cgw.snapshot().challenge, true);
});
console.log(checks + ' DOM checks passed');
