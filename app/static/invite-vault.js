"use strict";

// One encrypted receipt per browser tab, retained for at most 24 hours.
// Only a random record ID goes into sessionStorage. AES keys are non-exportable.
window.inviteVault = (() => {
  const pointer = "sideword-admin-invite";
  const lifetime = 24 * 60 * 60 * 1000;
  async function database() {
    return new Promise((resolve, reject) => {
      const request = indexedDB.open("sideword-admin-receipts", 1);
      request.onupgradeneeded = () => request.result.createObjectStore("receipts");
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
  }
  async function access(mode, operation) {
    const db = await database();
    try {
      return await new Promise((resolve, reject) => {
        const transaction = db.transaction("receipts", mode);
        const request = operation(transaction.objectStore("receipts"));
        transaction.oncomplete = () => resolve(request?.result);
        transaction.onerror = () => reject(transaction.error);
        transaction.onabort = () => reject(transaction.error);
      });
    } finally { db.close(); }
  }
  async function purge() {
    await access("readwrite", store => {
      const request = store.openCursor();
      request.onsuccess = () => {
        const cursor = request.result;
        if (!cursor) return;
        if (cursor.value.expires <= Date.now()) cursor.delete();
        cursor.continue();
      };
    });
  }
  return {
    async save(details) {
      const id = sessionStorage.getItem(pointer) || crypto.randomUUID();
      const key = await crypto.subtle.generateKey({ name: "AES-GCM", length: 256 }, false, ["encrypt", "decrypt"]);
      const iv = crypto.getRandomValues(new Uint8Array(12));
      const plaintext = new TextEncoder().encode(JSON.stringify(details));
      let ciphertext;
      try { ciphertext = await crypto.subtle.encrypt({ name: "AES-GCM", iv }, key, plaintext); }
      finally { plaintext.fill(0); }
      sessionStorage.setItem(pointer, id);
      await access("readwrite", store => store.put({ key, iv, ciphertext, expires: Date.now() + lifetime }, id));
      await purge();
    },
    async read() {
      await purge();
      const id = sessionStorage.getItem(pointer);
      if (!id) return null;
      const record = await access("readonly", store => store.get(id));
      if (!record) { sessionStorage.removeItem(pointer); return null; }
      const plaintext = new Uint8Array(await crypto.subtle.decrypt({ name: "AES-GCM", iv: record.iv }, record.key, record.ciphertext));
      try { return JSON.parse(new TextDecoder().decode(plaintext)); }
      finally { plaintext.fill(0); }
    },
  };
})();
