const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const { webcrypto } = require('node:crypto');

// Async IndexedDB adapter that structured-clones records, including real CryptoKeys.
function storage() {
  const records = new Map();
  const session = new Map();
  let now = Date.now();
  const db = {
    close() {},
    transaction() {
      const transaction = {};
      const complete = () => setImmediate(() => transaction.oncomplete());
      transaction.objectStore = () => ({
        put(value, key) {
          records.set(key, structuredClone(value));
          complete();
          return { result: key };
        },
        get(key) { complete(); return { result: structuredClone(records.get(key)) }; },
        openCursor() {
          const request = {};
          const entries = [...records.entries()];
          let index = 0;
          function next() {
            setImmediate(() => {
              const entry = entries[index++];
              request.result = entry ? {
                value: entry[1], delete() { records.delete(entry[0]); }, continue: next,
              } : null;
              request.onsuccess();
              if (!entry) complete();
            });
          }
          next();
          return request;
        },
      });
      return transaction;
    },
  };
  const indexedDB = { open() {
    const request = { result: db };
    setImmediate(() => request.onsuccess());
    return request;
  } };
  const sessionStorage = {
    getItem: key => session.get(key) || null,
    setItem: (key, value) => session.set(key, value),
    removeItem: key => session.delete(key),
  };
  function reload() {
    const context = vm.createContext({
      window: {}, indexedDB, sessionStorage, crypto: webcrypto,
      TextEncoder, TextDecoder, Uint8Array, Date: { now: () => now },
    });
    vm.runInContext(fs.readFileSync('app/static/invite-vault.js', 'utf8'), context);
    return context.window.inviteVault;
  }
  return { records, session, reload, expire() { now += 25 * 60 * 60 * 1000; } };
}

test('refresh recovers generated or custom details with no plaintext stored and a non-exportable key', async () => {
  const backend = storage();
  const details = { url: 'https://example.invalid/invite', password: 'custom12' };
  await backend.reload().save(details);
  const record = [...backend.records.values()][0];
  assert.equal(record.key.extractable, false);
  assert.equal(record.key.algorithm.name, 'AES-GCM');
  await assert.rejects(webcrypto.subtle.exportKey('raw', record.key));
  assert.equal(Object.hasOwn(record, 'password'), false);
  assert.equal(Object.hasOwn(record, 'url'), false);
  assert.equal(JSON.stringify([...backend.session]).includes(details.password), false);
  assert.equal(Buffer.from(record.ciphertext).includes(Buffer.from(details.password)), false);
  assert.equal(JSON.stringify(await backend.reload().read()), JSON.stringify(details));
});

test('new creation replaces the last receipt and expired receipts are removed', async () => {
  const backend = storage();
  const vault = backend.reload();
  await vault.save({ url: 'https://example.invalid/first', password: 'first123' });
  await vault.save({ url: 'https://example.invalid/second', password: 'second12' });
  assert.equal(backend.records.size, 1);
  assert.equal((await backend.reload().read()).password, 'second12');
  backend.expire();
  assert.equal(await backend.reload().read(), null);
  assert.equal(backend.records.size, 0);
  assert.equal(backend.session.size, 0);
});

test('tampering with encrypted details is rejected', async () => {
  const backend = storage();
  await backend.reload().save({ url: 'https://example.invalid/invite', password: 'custom12' });
  const record = [...backend.records.values()][0];
  new Uint8Array(record.ciphertext)[0] ^= 1;
  await assert.rejects(backend.reload().read());
});
