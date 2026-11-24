/* Website adapter. All DOM-dependent selectors are isolated in this file. */
(() => {
  const cfg = window.__cgwSelectors || {};
  const selectors = Object.assign({
    messages: '[data-message-author-role]',
    content: '.markdown, [data-message-content]',
    prompt: '#prompt-textarea, textarea[data-testid="prompt-textarea"], textarea[placeholder*="Message"]',
    send: 'button[data-testid="send-button"], button[aria-label="Send prompt"], button[aria-label="Envoyer le message"]',
    stop: 'button[data-testid="stop-button"], button[aria-label="Stop generating"], button[aria-label="Arrêter la génération"]',
    upload: 'input[type="file"]',
    account: '[data-testid="profile-button"], [data-testid="accounts-profile-button"], [data-testid="account-menu-button"], [data-testid="user-menu-button"]'
  }, cfg);
  const visible = el => !!el && !el.hidden && getComputedStyle(el).display !== 'none'
    && getComputedStyle(el).visibility !== 'hidden' && el.getClientRects().length > 0;
  const text = el => (el && (el.innerText || el.textContent) || '').trim();
  const safeHref = el => el.getAttribute('href') || '';
  const refs = [];
  const reference = el => { refs.push(el); return refs.length - 1; };
  const label = el => el.getAttribute('aria-label') || el.getAttribute('placeholder')
    || (el.labels && Array.from(el.labels).map(text).join(' ')) || text(el)
    || el.name || el.type || el.tagName.toLowerCase();
  function markdown(node) {
    if (node.nodeType === 3) return node.textContent;
    if (node.nodeType !== 1) return '';
    const tag = node.tagName.toLowerCase();
    if (['script', 'style', 'button', 'svg', 'nav'].includes(tag)) return '';
    const inner = () => Array.from(node.childNodes).map(markdown).join('');
    if (tag === 'br') return '\n';
    if (tag === 'strong' || tag === 'b') return '**' + inner() + '**';
    if (tag === 'em' || tag === 'i') return '*' + inner() + '*';
    if (tag === 'code' && node.parentElement?.tagName !== 'PRE') {
      const content = node.textContent;
      const fence = '`'.repeat(Math.max(1, ...(content.match(/`+/g) || []).map(s => s.length + 1)));
      return fence + content + fence;
    }
    if (tag === 'a') return '[' + inner() + '](' + safeHref(node) + ')';
    if (tag === 'li') return '- ' + inner().trim() + '\n';
    if (/^h[1-6]$/.test(tag)) return '\n' + '#'.repeat(Number(tag[1])) + ' ' + inner() + '\n\n';
    if (tag === 'tr') return '| ' + Array.from(node.children).map(c => text(c).replace(/\|/g, '\\|')).join(' | ') + ' |\n';
    if (['p', 'div', 'section', 'ul', 'ol', 'blockquote', 'table'].includes(tag)) return inner() + '\n\n';
    return inner();
  }
  function blocks(root) {
    const result = [];
    let prose = '';
    const flush = () => {
      const value = prose.replace(/\n{3,}/g, '\n\n').trim();
      if (value) result.push({type: 'text', text: value});
      prose = '';
    };
    function visit(node) {
      if (node.nodeType === 1 && node.tagName === 'PRE') {
        flush();
        const code = node.querySelector('code') || node;
        const klass = code.getAttribute('class') || '';
        const lang = (klass.match(/(?:^|\s)language-([^\s]+)/) || [])[1]
          || code.getAttribute('data-language') || node.getAttribute('data-language') || '';
        result.push({type: 'code', language: lang, text: code.textContent});
      } else if (node.nodeType === 1 && node.querySelector('pre')) {
        for (const child of node.childNodes) visit(child);
      } else {
        prose += markdown(node);
      }
    }
    for (const child of root.childNodes) visit(child);
    flush();
    return result;
  }
  const api = {
    selectors,
    snapshot() {
      const messages = Array.from(document.querySelectorAll(selectors.messages)).map((node, i) => {
        const role = node.getAttribute('data-message-author-role') || 'assistant';
        const content = node.querySelector(selectors.content) || node;
        return {id: node.getAttribute('data-message-id') || 'turn-' + i, role, blocks: blocks(content)};
      });
      const files = [];
      refs.length = 0;
      for (const el of document.querySelectorAll('a[href]')) {
        if (!visible(el)) continue;
        const href = safeHref(el);
        if (el.hasAttribute('download') || /^(sandbox:|\/backend-api\/files\/)/.test(href)
            || /[?&]file_id=/.test(href) || /(?:oaiusercontent\.com|blob\.core\.windows\.net)\//.test(href)) {
          files.push({ref: reference(el), label: text(el) || el.getAttribute('download') || 'fichier', href});
        }
      }
      const prompt = document.querySelector(selectors.prompt);
      const send = document.querySelector(selectors.send);
      return {url: location.href, title: document.title, messages, files,
        generating: Array.from(document.querySelectorAll(selectors.stop)).some(visible),
        prompt: visible(prompt), prompt_enabled: visible(prompt) && !prompt.disabled && prompt.getAttribute('aria-disabled') !== 'true',
        send_ready: visible(send) && !send.disabled,
        challenge: api.challenge(), connected: api.snapshotConnected()};
    },
    challenge() {
      return !!document.querySelector('iframe[src*="challenges.cloudflare.com"], #challenge-running, #challenge-stage')
        || /^(just a moment|un instant|attention required)/i.test(document.title);
    },
    conversations() {
      return Array.from(document.querySelectorAll('a[href]')).map(el => ({url: el.href, title: text(el)}))
        .filter(item => /\/c\/[\w-]+(?:$|[?#])/.test(item.url) && item.title);
    },
    promptInfo() {
      const el = document.querySelector(selectors.prompt);
      if (!visible(el)) return {available: false};
      return {available: true, value: el.isContentEditable ? el.innerText : el.value || ''};
    },
    click(kind) {
      const el = document.querySelector(selectors[kind]);
      if (!visible(el) || el.disabled) return false;
      el.click(); return true;
    },
    clickFile(ref, expected) {
      const el = refs[ref];
      if (!el || !el.isConnected || safeHref(el) !== expected) return false;
      el.click(); return true;
    },
    clickDownload() {
      const candidates = Array.from(document.querySelectorAll('button, a[download], [role="menuitem"]'));
      const el = candidates.find(el => visible(el) && !el.disabled &&
        (el.hasAttribute('download') || /^(download|télécharger)(?: le fichier| file)?$/i.test(label(el).trim())));
      if (!el) return false;
      el.click(); return true;
    },
    authSnapshot() {
      refs.length = 0;
      const controls = [];
      for (const el of document.querySelectorAll('input, textarea, button, a[href], select')) {
        if (!visible(el) || el.disabled || el.type === 'hidden') continue;
        const tag = el.tagName.toLowerCase();
        const isInput = tag === 'textarea' || (tag === 'input' && !['submit', 'button', 'reset'].includes(el.type));
        controls.push({ref: reference(el), kind: isInput ? 'input' : tag === 'select' ? 'select' : 'button',
          label: label(el).slice(0, 200), input_type: el.type || '', href: tag === 'a' ? el.href : null});
        if (controls.length >= 80) break;
      }
      // Never include existing input values, hidden fields or cookies.
      return {url: location.origin + location.pathname, title: document.title,
        text: text(document.body).slice(0, 6000), controls, challenge: api.challenge(),
        connected: api.snapshotConnected()};
    },
    snapshotConnected() {
      return !!document.querySelector(selectors.account) || !!document.querySelector('a[href^="/c/"]');
    },
    authAction(ref, value, expected) {
      const el = refs[ref];
      if (!el || !el.isConnected || label(el).slice(0, 200) !== expected) return false;
      if (el.tagName === 'TEXTAREA' || (el.tagName === 'INPUT' && !['submit', 'button', 'reset'].includes(el.type))) {
        if (el.type === 'checkbox' || el.type === 'radio') {
          el.checked = value === true;
          el.dispatchEvent(new Event('change', {bubbles: true}));
        } else {
          const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
          const setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
          setter.call(el, value);
          el.dispatchEvent(new Event('input', {bubbles: true}));
          el.dispatchEvent(new Event('change', {bubbles: true}));
        }
      } else if (el.tagName === 'SELECT') {
        el.value = value;
        el.dispatchEvent(new Event('change', {bubbles: true}));
      } else el.click();
      return true;
    }
  };
  window.__cgw = api;
})();
