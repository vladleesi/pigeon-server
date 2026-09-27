const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function row(uses) {
  const cell = {
    dataset: { liveField: 'uses' }, innerHTML: uses, childNodes: [uses],
    contains: () => false,
    replaceChildren(...nodes) { this.innerHTML = nodes.join(''); },
  };
  return {
    dataset: { linkId: '1' }, cell,
    querySelectorAll: () => [cell], querySelector: () => cell,
  };
}

function section(uses) {
  const item = row(uses);
  return { item, querySelectorAll: selector => selector === '[data-link-id]' ? [item] : [] };
}

function fixture(vault = null) {
  const elements = new Map();
  const documentEvents = {};
  const windowEvents = {};
  const requests = [];
  const replies = [];
  let interval;
  const plain = () => ({ hidden: true, textContent: '', disabled: false, focus() {}, setAttribute(name, value) { this[name] = value; } });
  for (const id of ['password-mode', 'create-link-error', 'refresh-links',
    'links-refresh-status', 'created-invite-heading', 'invite-copy-status', 'room-password']) {
    elements.set('#' + id, plain());
  }
  elements.get('#password-mode').value = 'generated';
  elements.get('#refresh-links').textContent = 'Refresh list';
  if (vault) {
    for (const id of ['created-invite-password', 'created-invite-url', 'created-invite-panel',
      'created-invite-details', 'created-invite-context', 'invite-save-status']) {
      elements.set('#' + id, { ...plain(), value: '' });
    }
    elements.set('[data-toggle-invite]', plain());
  }
  elements.set('#existing-links', section('0 / 2'));
  const submit = plain();
  const formEvents = {};
  const form = {
    action: '/admin/links',
    addEventListener(name, handler) { formEvents[name] = handler; },
    querySelector: () => submit,
  };
  elements.set('#create-link-form', form);
  elements.set('.create-link-card', {
    before(panel) { elements.set('#created-invite-panel', panel); },
  });
  const document = {
    hidden: false,
    querySelector: selector => elements.get(selector) || null,
    getElementById: id => elements.get('#' + id),
    addEventListener(name, handler) { documentEvents[name] = handler; },
    importNode: node => node,
  };
  const clipboard = { async writeText(value) { clipboard.value = value; } };
  const context = vm.createContext({
    document, navigator: { clipboard },
    window: {
      inviteVault: vault,
      addEventListener(name, handler) { windowEvents[name] = handler; },
      setInterval(handler) { interval = handler; }, setTimeout() {},
    },
    FormData: class { constructor(value) { this.form = value; } },
    DOMParser: class { parseFromString(value) { return value; } },
    fetch: async (url, options) => {
      requests.push({ url, ...options });
      assert.ok(replies.length, 'Unexpected request');
      return replies.shift()();
    },
  });
  vm.runInContext(fs.readFileSync('app/static/admin-links.js', 'utf8'), context);
  function respond({ uses = '0 / 2', panel = null, ok = true, error = '' } = {}) {
    replies.push(async () => ({ ok, text: async () => ({
      querySelector: selector => selector === '#existing-links' ? section(uses) : panel,
      querySelectorAll: () => error ? [{ textContent: error }] : [],
    }) }));
  }
  async function create() {
    const event = { prevented: false, preventDefault() { this.prevented = true; } };
    await formEvents.submit(event);
    return event;
  }
  return { elements, document, requests, replies, respond, create, submit, clipboard,
    tick: () => interval(),
    click: (selector, target) => documentEvents.click({
      target: { closest: candidate => candidate === selector ? target : null },
    }),
  };
}

test('generated creation is intercepted; refresh reads new counts without losing the password panel', async () => {
  const app = fixture();
  const panel = { marker: 'one-time panel' };
  const existingRow = app.elements.get('#existing-links').item;
  app.respond({ panel });
  assert.equal((await app.create()).prevented, true);
  assert.equal(app.requests.length, 1);
  assert.equal(app.requests[0].method, 'POST');
  assert.equal(app.elements.get('#created-invite-panel'), panel);
  assert.equal(app.elements.get('#links-refresh-status').hidden, true);
  app.respond({ uses: '2 / 2' });
  await app.tick();
  assert.equal(app.requests.length, 2);
  assert.equal(app.requests[1].url, '/admin/links');
  assert.equal(app.requests[1].method, undefined);
  assert.equal(app.requests[1].cache, 'no-store');
  assert.equal(existingRow.cell.innerHTML, '2 / 2');
  assert.equal(app.elements.get('#existing-links').item, existingRow);
  assert.equal(app.elements.get('#created-invite-panel'), panel);
});

test('background refresh started before creation cannot replace newer results', async () => {
  const app = fixture();
  let release;
  app.replies.push(() => new Promise(resolve => { release = resolve; }));
  const pendingRefresh = app.tick();
  assert.equal(app.elements.get('#refresh-links').textContent, 'Refresh list');
  assert.equal(app.elements.get('#refresh-links')['aria-busy'], undefined);
  app.respond({ uses: '1 / 2', panel: {} });
  await app.create();
  release({ ok: true, text: async () => ({ querySelector: () => section('0 / 2') }) });
  await pendingRefresh;
  assert.equal(app.elements.get('#refresh-links').textContent, 'Refresh list');
  assert.equal(app.elements.get('#refresh-links')['aria-busy'], undefined);
  assert.equal(app.elements.get('#existing-links').item.cell.innerHTML, '1 / 2');
});

test('hide and show retain the same one-time details without network requests', async () => {
  const app = fixture();
  const details = { hidden: false, marker: 'keep existing details' };
  app.elements.set('#created-invite-details', details);
  const button = { setAttribute(name, value) { this[name] = value; } };
  await app.click('[data-toggle-invite]', button);
  assert.equal(details.hidden, true);
  assert.equal(button['aria-expanded'], 'false');
  await app.click('[data-toggle-invite]', button);
  assert.equal(details.hidden, false);
  assert.equal(app.elements.get('#created-invite-details'), details);
  assert.equal(app.requests.length, 0);
});

test('creation failure stays inline and never automatically retries POST', async () => {
  const app = fixture();
  app.respond({ ok: false, error: 'Check expiry.' });
  await app.create();
  assert.equal(app.requests.length, 1);
  assert.equal(app.elements.get('#create-link-error').textContent, 'Check expiry.');
  assert.equal(app.elements.get('#create-link-error').hidden, false);
  assert.equal(app.submit.disabled, false);
});

test('copy works on dynamically inserted panels and offers selection when clipboard is denied', async () => {
  const app = fixture();
  const input = { value: 'synthetic-test-value', focus() {}, select() { this.selected = true; } };
  app.elements.set('#created-invite-password', input);
  const button = { dataset: { copyInvite: 'created-invite-password' }, setAttribute(name, value) { this[name] = value; } };
  await app.click('[data-copy-invite]', button);
  assert.equal(app.clipboard.value, input.value);
  assert.equal(button.textContent, 'Copied');
  assert.equal(app.elements.get('#invite-copy-status').hidden, true);
  app.clipboard.writeText = async () => { throw new Error('Denied'); };
  await app.click('[data-copy-invite]', button);
  assert.equal(input.selected, true);
  assert.equal(button.disabled, false);
  assert.match(app.elements.get('#invite-copy-status').textContent, /copy it using your keyboard/);
});

test('hidden pages do not poll', async () => {
  const app = fixture();
  app.document.hidden = true;
  await app.tick();
  assert.equal(app.requests.length, 0);
});

test('custom phrase creation uses the same non-navigating receipt flow', async () => {
  const app = fixture();
  app.elements.get('#password-mode').value = 'custom';
  const panel = { marker: 'custom receipt' };
  app.respond({ panel });
  assert.equal((await app.create()).prevented, true);
  assert.equal(app.requests[0].method, 'POST');
  assert.equal(app.elements.get('#created-invite-panel'), panel);
});

test('manual refresh alone displays a loading button and clears it after completion', async () => {
  const app = fixture();
  let release;
  app.replies.push(() => new Promise(resolve => { release = resolve; }));
  const pending = app.click('#refresh-links', {});
  const button = app.elements.get('#refresh-links');
  assert.equal(button.textContent, 'Refreshing…');
  assert.equal(button.disabled, true);
  release({ ok: true, text: async () => ({ querySelector: () => section('2 / 2') }) });
  await pending;
  assert.equal(button.textContent, 'Refresh list');
  assert.equal(button.disabled, false);
  assert.equal(app.elements.get('#links-refresh-status').hidden, true);
});

test('restored password invite is labeled as previous and collapsed, including after a plain invite', async () => {
  const app = fixture({ read: async () => ({ url: 'https://example.invalid/old', password: 'example123' }) });
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(app.elements.get('#created-invite-heading').textContent, 'Previously saved invite');
  assert.equal(app.elements.get('#created-invite-details').hidden, true);
  assert.equal(app.elements.get('#created-invite-panel').hidden, false);
  assert.match(app.elements.get('#created-invite-context').textContent, /earlier creation/);
  app.elements.get('#password-mode').value = 'none';
  assert.equal((await app.create()).prevented, false);
  assert.equal(app.elements.get('#created-invite-password').value, 'example123');
});
