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
