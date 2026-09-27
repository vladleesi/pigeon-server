const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const crypto = require('node:crypto').webcrypto;

function client(shared = {}) {
  const element = { addEventListener() {} };
  const context = vm.createContext({ TextEncoder, TextDecoder, DOMException, crypto, btoa,
    document: { querySelector: () => element, addEventListener() {} },
    window: { addEventListener() {} }, ...shared });
  vm.runInContext(fs.readFileSync('app/static/client-protocol.js', 'utf8'), context);
  vm.runInContext(fs.readFileSync('app/static/client.js', 'utf8')
    .replace(/start\(\)\.catch[^\n]+/, ''), context);
  return code => vm.runInContext(code, context);
}

for (const failed of [false, true]) {
  test(`send ${failed ? 'failure' : 'success'} updates pending retries after upload and focuses without scrolling`, async () => {
    const handlers = new Map();
    const nodes = new Map();
    const run = client({ document: {
      querySelector(selector) {
        if (!nodes.has(selector)) nodes.set(selector, {
          addEventListener(event, handler) { handlers.set(`${selector}:${event}`, handler); },
          focus(options) { this.focusOptions = options; },
        });
        return nodes.get(selector);
      },
      addEventListener() {},
    } });
    run(`
      identity = { publicId: 'alice', retryWindowSeconds: 3600 };
      selectedChatId = 7;
      chats = [{ id: 7, participants: [{ public_id: 'bob' }] }];
      elements.messageInput.value = 'hello';
      globalThis.events = [];
      encryptForRecipient = async () => 'ciphertext';
      deviceLock = async (_, action) => action();
      readHistoryRecords = async () => [];
      persistOutbox = async () => events.push('saved');
      flushOutbox = async () => { events.push('upload'); ${failed ? "throw new Error('offline');" : ''} };
      renderOutbox = async () => events.push('render');
      showToast = () => events.push('error');
    `);
    await handlers.get('#message-form:submit')({ preventDefault() {} });
    assert.deepEqual(JSON.parse(run('JSON.stringify(events)')),
      failed ? ['saved', 'upload', 'render', 'error'] : ['saved', 'upload', 'render']);
    assert.equal(nodes.get('#message-input').focusOptions.preventScroll, true);
    assert.equal(nodes.get('#send-button').disabled, false);
  });
}

test('encrypted outbox survives upload response loss and history-save failure', async () => {
  const run = client();
  await run(`(async () => {
    identity = { token: 'test-token', publicId: 'alice', storageKey:
      await crypto.subtle.generateKey({ name: 'AES-GCM', length: 256 }, false, ['encrypt', 'decrypt']) };
    deviceLock = async (_, action) => action();
    globalThis.records = new Map();
    putDatabaseValue = async (key, value) => records.set(key, value);
    readHistoryRecords = async () => [...records].map(([key, value]) => ({ key, value }));
    removeOutbox = async key => records.delete(key);
    globalThis.requests = [];
    api = async (_, options) => { requests.push(options.body); throw new Error('response lost'); };
    await persistOutbox({ chatId: 7, messageId: 'stable', envelopes: [{ciphertext:'already encrypted'}],
      plaintext: 'private plaintext', expiresAt: Date.now() + 60000 });
  })()`);
  assert.equal(run('JSON.stringify([...records]).includes("private plaintext")'), false);
  await assert.rejects(run('flushOutbox()'), /response lost/);
  assert.equal(run('records.size'), 1);
  await run(`api = async (_, options) => { requests.push(options.body); return {created_at:'2026-09-27T00:00:00Z'}; };
    persistHistoryEntry = async () => { throw new Error('disk full'); };`);
  await assert.rejects(run('flushOutbox()'), /disk full/);
  assert.equal(run('records.size'), 1);
  await run('persistHistoryEntry = async () => {}; flushOutbox()');
  assert.equal(run('records.size'), 0);
  assert.equal(run('new Set(requests).size'), 1);
  assert.equal(run('messagesByChat.get(7).length'), 1);
});

test('expired outgoing records stay saved without unsafe retransmission', async () => {
  const run = client();
  await run(`(async () => {
    identity = {token:'token', publicId:'alice', storageKey:
      await crypto.subtle.generateKey({name:'AES-GCM',length:256}, false, ['encrypt','decrypt'])};
    deviceLock = async (_, action) => action();
    globalThis.record = null;
    putDatabaseValue = async (key, value) => { record = {key,value}; };
    readHistoryRecords = async () => [record];
    api = async () => { throw new Error('must not upload'); };
    await persistOutbox({chatId:7,messageId:'old',expiresAt:0});
  })()`);
  await assert.rejects(run('flushOutbox()'), /retry expired/);
  assert.equal(run('record !== null'), true);
});

test('concurrent tabs reuse persisted refresh proposals after a lost response', async () => {
  let state = { token: 'old-access', publicId: 'alice', refreshCredential: 'old-refresh', tokenExpiresAt: 0 };
  let tail = Promise.resolve();
  const requests = [];
  const shared = {
    navigator: { locks: { request: (_, action) => {
      const result = tail.then(action); tail = result.catch(() => {}); return result;
    } } },
    load: async () => structuredClone(state),
    save: async value => { state = structuredClone(value); },
    fetch: async (_, options) => {
      requests.push(JSON.parse(options.body));
      if (requests.length === 1) throw new Error('lost response');
      return { ok: true, json: async () => ({ token: 'new-access', session_id: 'session',
        access_expires_at: new Date(Date.now() + 900000).toISOString(),
        session_expires_at: new Date(Date.now() + 86400000).toISOString() }) };
    },
  };
  const a = client(shared), b = client(shared);
  for (const run of [a,b]) await run(`(async () => { identity = await load();
    readIdentity = load; writeIdentity = save; connectSocket = () => {}; })()`);
  await assert.rejects(a('ensureFreshSession()'), /lost response/);
  assert.ok(state.pendingRefreshCredential);
  await Promise.all([a('ensureFreshSession()'), b('ensureFreshSession()')]);
  assert.equal(requests.length, 2);
  assert.deepEqual(requests[0], requests[1]);
  assert.equal(state.refreshCredential, requests[1].next_credential);
  assert.equal(state.pendingRefreshCredential, undefined);
});
