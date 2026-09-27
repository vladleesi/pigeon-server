const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function client() {
  const element = { addEventListener() {} };
  const context = vm.createContext({
    TextEncoder, TextDecoder, DOMException,
    crypto: require('node:crypto').webcrypto, btoa,
    document: { querySelector: () => element, addEventListener() {} },
    window: { addEventListener() {} },
  });
  const source = fs.readFileSync('app/static/client.js', 'utf8');
  vm.runInContext(fs.readFileSync('app/static/client-protocol.js', 'utf8'), context);
  vm.runInContext(source.replace(/start\(\)\.catch[^\n]+/, ''), context);
  vm.runInContext(`
    senderKeyIsLoaded = () => true;
    decryptMessage = async (message) => message.ciphertext;
    persistHistoryEntry = async () => {};
  `, context);
  return (code) => vm.runInContext(code, context);
}

function renderingClient() {
  const run = client();
  run(`
    class TestNode {
      constructor() { this.children = []; this.scrollTop = 0; this.clientHeight = 100; }
      get firstChild() { return this.children[0]; }
      get scrollHeight() { return this.children.length * 100; }
      replaceChildren() { this.children = []; }
      append(...nodes) { for (const node of nodes) this.insertBefore(node, null); }
      insertBefore(node, reference) {
        node.remove();
        const index = reference ? this.children.indexOf(reference) : this.children.length;
        this.children.splice(index, 0, node);
        node.parent = this;
      }
      remove() {
        if (this.parent) this.parent.children.splice(this.parent.children.indexOf(this), 1);
        this.parent = null;
      }
    }
    document.createElement = () => new TestNode();
    elements.messageList = new TestNode();
    selectedChatId = 7;
    messagesByChat.set(7, [
      { id: 'a', text: 'first', createdAt: 1 },
      { id: 'c', text: 'third', createdAt: 3 },
      { id: 'd', text: 'fourth', createdAt: 4 },
    ]);
    renderMessages();
    globalThis.originalNodes = [...elements.messageList.children];
  `);
  return run;
}

test('incoming messages preserve existing nodes, chronological order, and reading position', async () => {
  const run = renderingClient();
  await run(`elements.messageList.scrollTop = 50;
    appendMessage(7, { id: 'b', text: '<b>plain text</b>', createdAt: 2 })`);
  assert.equal(run('elements.messageList.scrollTop'), 50);
  assert.equal(run('elements.messageList.children[0] === originalNodes[0]'), true);
  assert.equal(run('elements.messageList.children[2] === originalNodes[1]'), true);
  assert.equal(run('elements.messageList.children[3] === originalNodes[2]'), true);
  assert.equal(run('elements.messageList.children[1].children[0].textContent'), '<b>plain text</b>');
  run('renderMessages()');
  assert.equal(run('elements.messageList.scrollTop'), 50);
  assert.equal(run('elements.messageList.children[0] === originalNodes[0]'), true);
});

test('new messages follow the bottom and switching chats clears previous messages', async () => {
  const run = renderingClient();
  await run(`elements.messageList.scrollTop = 200;
    appendMessage(7, { id: 'e', text: 'latest', createdAt: 5 })`);
  assert.equal(run('elements.messageList.scrollTop'), 400);
  run(`selectedChatId = 8; renderMessages();`);
  assert.equal(run('elements.messageList.children.length'), 1);
  assert.equal(run('elements.messageList.firstChild.textContent'), 'No messages yet.');
  await run(`appendMessage(8, { id: 'a', text: 'different chat', createdAt: 1 })`);
  assert.equal(run('elements.messageList.children.length'), 1);
  assert.equal(run('elements.messageList.firstChild.children[0].textContent'), 'different chat');
  assert.equal(run('elements.messageList.firstChild === originalNodes[0]'), false);
});

test('reused SQLite row IDs do not drop new messages or receipts', async () => {
  const run = client();
  await run(`(async () => {
    for (let i = 0; i < 10; i++) {
      const message = { delivery_id: 'a'.repeat(32), id: 1, chat_id: 7, sender_public_id: 'alice',
        client_message_id: 'uuid-' + i, ciphertext: 'text-' + i,
        created_at: '2026-09-26T02:00:' + String(i).padStart(2, '0') };
      await processMessages([message]);
      await processMessages([message]);
      await processReceipts([{ ...message, reader_public_id: 'bob' }]);
    }
  })()`);
  assert.equal(run('messagesByChat.get(7).filter(e => e.kind === "theirs").length'), 10);
  assert.equal(run('messagesByChat.get(7).filter(e => e.kind === "system").length'), 10);
});

test('invite retry credentials persist before admission and stay stable after a failed save', async () => {
  const run = client();
  await run(`(async () => {
    identity = { publicKey: 'device-public-key' };
    writeIdentity = async () => { throw new Error('storage unavailable'); };
    try {
      await prepareInviteResume('example-invite');
      throw new Error('must not continue to admission');
    } catch (error) {
      if (error.message !== 'storage unavailable') throw error;
    }
  })()`);
  const credential = run('identity.inviteCredentials["example-invite"]');
  assert.match(credential, /^[A-Za-z0-9_-]{43}$/);
  await run(`(async () => {
    writeIdentity = async (value) => { globalThis.savedIdentity = structuredCloneForTest(value); };
    globalThis.structuredCloneForTest = (value) => JSON.parse(JSON.stringify(value));
    await prepareInviteResume('example-invite');
  })()`);
  assert.equal(run('savedIdentity.inviteCredentials["example-invite"]'), credential);
  assert.equal(await run('prepareInviteResume("example-invite")'), credential);
  assert.notEqual(await run('prepareInviteResume("another-invite")'), credential);
  assert.equal(run('Object.hasOwn(savedIdentity, "password")'), false);
});

test('naive server UTC and explicit UTC have identical ordering', async () => {
  const run = client();
  assert.equal(run('serverTimestamp("2026-09-26T02:00:00")'), Date.parse('2026-09-26T02:00:00Z'));
  await run(`(async () => {
    await appendMessage(7, { id: 'new', createdAt: serverTimestamp('2026-09-26T02:00:02Z') });
    await appendMessage(7, { id: 'old', createdAt: serverTimestamp('2026-09-26T02:00:01') });
  })()`);
  assert.equal(run('messagesByChat.get(7).map(e => e.id).join(",")'), 'old,new');
});

test('session timer formats days, rounds seconds and clamps expiry', () => {
  const run = client();
  assert.equal(run('formatTimeRemaining(1001)'), '00:00:02');
  assert.equal(run('formatTimeRemaining(90061000)'), '1d 01:01:01');
  assert.equal(run('formatTimeRemaining(-1000)'), '00:00:00');
});

test('failed local persistence can retry without acknowledging an unsaved message', async () => {
  const run = client();
  await run(`(async () => {
    persistHistoryEntry = async () => { throw new Error('storage unavailable'); };
    const message = { delivery_id: 'a'.repeat(32), id: 1, chat_id: 7, sender_public_id: 'alice',
      client_message_id: 'uuid', ciphertext: 'hello', created_at: '2026-09-26T02:00:00Z' };
    await processMessages([message]);
  })()`);
  assert.equal(run('pendingReadsByChat.size'), 0);
  assert.equal(run('seenMessageIds.size'), 0);
  await run(`(async () => {
    persistHistoryEntry = async () => {};
    await processMessages([{ delivery_id: 'a'.repeat(32), id: 1, chat_id: 7, sender_public_id: 'alice',
      client_message_id: 'uuid', ciphertext: 'hello', created_at: '2026-09-26T02:00:00Z' }]);
  })()`);
  assert.equal(run('pendingReadsByChat.get(7).size'), 1);
  assert.equal(run('messagesByChat.get(7).filter(e => e.kind === "theirs").length'), 1);
});

test('exact acknowledgements retain colliding group identities and retry lost responses', async () => {
  const run = client();
  await run(`(async () => {
    globalThis.calls = [];
    api = async (path, options) => {
      calls.push({ path, body: JSON.parse(options.body) });
      throw new Error('response lost');
    };
    for (const [sender, delivery] of [['alice', 'a'], ['bob', 'b']]) {
      await processMessages([{ id: 1, delivery_id: delivery.repeat(32), chat_id: 7,
        sender_public_id: sender, client_message_id: 'collision', ciphertext: sender }]);
    }
    await processReceipts([{ id: 1, delivery_id: 'c'.repeat(32), chat_id: 7,
      reader_public_id: 'bob', client_message_id: 'outbound' }]);
  })()`);
  await assert.rejects(run('flushAcknowledgements()'), /response lost/);
  assert.equal(run('pendingReadsByChat.get(7).size'), 2);
  await run(`api = async (path, options) => calls.push({ path, body: JSON.parse(options.body) });
    flushAcknowledgements()`);
  const calls = JSON.parse(run('JSON.stringify(calls)'));
  assert.equal(calls[0].path, '/api/v1/chats/7/read/exact');
  assert.deepEqual(calls[1], calls[0]);
  assert.deepEqual(calls[1].body.messages.map(m => m.sender_public_id), ['alice', 'bob']);
  assert.equal(calls[2].path, '/api/v1/ack/exact');
  assert.deepEqual(calls[2].body, { receipts: [{ delivery_id: 'c'.repeat(32),
    chat_id: 7, client_message_id: 'outbound', reader_public_id: 'bob' }] });
  assert.equal(run('pendingReadsByChat.size + pendingReceiptIds.size'), 0);
});

test('batches are bounded and receipt failures keep only unfinished acknowledgements', async () => {
  const run = client();
  await run(`(async () => {
    for (let i = 0; i < 205; i++) {
      await processReceipts([{ id: i, delivery_id: i.toString(16).padStart(32, '0'),
        chat_id: 7, reader_public_id: 'bob', client_message_id: 'm-' + i }]);
    }
    globalThis.calls = [];
    api = async (path, options) => {
      calls.push(JSON.parse(options.body));
      if (calls.length === 2) throw new Error('offline');
    };
  })()`);
  await assert.rejects(run('flushAcknowledgements()'), /offline/);
  assert.equal(run('pendingReceiptIds.size'), 105);
  await run('flushAcknowledgements()');
  assert.deepEqual(JSON.parse(run('JSON.stringify(calls.map(c => c.receipts.length))')), [100, 100, 100, 5]);
  assert.equal(run('pendingReceiptIds.size'), 0);
});

test('older servers cannot trigger fallback to ambiguous deletion endpoints', async () => {
  const run = client();
  await run(`processMessages([{ id: 1, chat_id: 7, sender_public_id: 'alice',
    client_message_id: 'legacy', ciphertext: 'stored' }])`);
  assert.equal(run('pendingReadsByChat.size'), 0);
  await run(`queueRead({ delivery_id: 'a'.repeat(32), chat_id: 7,
    sender_public_id: 'alice', client_message_id: 'm' });
    globalThis.paths = [];
    api = async (path) => { paths.push(path); throw new Error('404'); };`);
  await assert.rejects(run('flushAcknowledgements()'), /404/);
  assert.deepEqual(JSON.parse(run('JSON.stringify(paths)')), ['/api/v1/chats/7/read/exact']);
  assert.equal(run('pendingReadsByChat.get(7).size'), 1);
});
