const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function client() {
  const element = { addEventListener() {} };
  const context = vm.createContext({
    TextEncoder, TextDecoder, DOMException,
    document: { querySelector: () => element, addEventListener() {} },
    window: { addEventListener() {} },
  });
  const source = fs.readFileSync('app/static/client.js', 'utf8');
  vm.runInContext(source.replace(/start\(\)\.catch[^\n]+/, ''), context);
  vm.runInContext(`
    senderKeyIsLoaded = () => true;
    decryptMessage = async (message) => message.ciphertext;
    persistHistoryEntry = async () => {};
  `, context);
  return (code) => vm.runInContext(code, context);
}

test('reused SQLite row IDs do not drop new messages or receipts', async () => {
  const run = client();
  await run(`(async () => {
    for (let i = 0; i < 10; i++) {
      const message = { id: 1, chat_id: 7, sender_public_id: 'alice',
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

test('naive server UTC and explicit UTC have identical ordering', async () => {
  const run = client();
  assert.equal(run('serverTimestamp("2026-09-26T02:00:00")'), Date.parse('2026-09-26T02:00:00Z'));
  await run(`(async () => {
    await appendMessage(7, { id: 'new', createdAt: serverTimestamp('2026-09-26T02:00:02Z') });
    await appendMessage(7, { id: 'old', createdAt: serverTimestamp('2026-09-26T02:00:01') });
  })()`);
  assert.equal(run('messagesByChat.get(7).map(e => e.id).join(",")'), 'old,new');
});

test('failed local persistence can retry without acknowledging an unsaved message', async () => {
  const run = client();
  await run(`(async () => {
    persistHistoryEntry = async () => { throw new Error('storage unavailable'); };
    const message = { id: 1, chat_id: 7, sender_public_id: 'alice',
      client_message_id: 'uuid', ciphertext: 'hello', created_at: '2026-09-26T02:00:00Z' };
    await processMessages([message]);
  })()`);
  assert.equal(run('pendingReadsByChat.size'), 0);
  assert.equal(run('seenMessageIds.size'), 0);
  await run(`(async () => {
    persistHistoryEntry = async () => {};
    await processMessages([{ id: 1, chat_id: 7, sender_public_id: 'alice',
      client_message_id: 'uuid', ciphertext: 'hello', created_at: '2026-09-26T02:00:00Z' }]);
  })()`);
  assert.equal(run('pendingReadsByChat.get(7).size'), 1);
  assert.equal(run('messagesByChat.get(7).filter(e => e.kind === "theirs").length'), 1);
});
