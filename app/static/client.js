"use strict";

const DB_NAME = "pigeon-test-client";
const STORE_NAME = "device";
const IDENTITY_KEY = "identity";
const HISTORY_PREFIX = "history:";
const POLL_INTERVAL_MS = 4000;
const encoder = new TextEncoder();
const decoder = new TextDecoder("utf-8", { fatal: true });

const elements = {
  activationForm: document.querySelector("#activation-form"),
  chatList: document.querySelector("#chat-list"),
  clientPanel: document.querySelector("#client-panel"),
  connectionState: document.querySelector("#connection-state"),
  conversationKind: document.querySelector("#conversation-kind"),
  conversationTitle: document.querySelector("#conversation-title"),
  displayName: document.querySelector("#display-name"),
  error: document.querySelector("#error-message"),
  identityLabel: document.querySelector("#identity-label"),
  inviteToken: document.querySelector("#invite-token"),
  messageForm: document.querySelector("#message-form"),
  messageInput: document.querySelector("#message-input"),
  messageList: document.querySelector("#message-list"),
  participantCount: document.querySelector("#participant-count"),
  participantKeys: document.querySelector("#participant-keys"),
  refreshButton: document.querySelector("#refresh-button"),
  resetButton: document.querySelector("#reset-button"),
  sendButton: document.querySelector("#send-button"),
  setupPanel: document.querySelector("#setup-panel"),
  status: document.querySelector("#status-message"),
};

let identity = null;
let chats = [];
let selectedChatId = null;
let synchronizing = false;
let socket = null;
let reconnectTimer = null;
let heartbeatTimer = null;
let socketAuthTimer = null;
let accessDeadline = null;
let inboundQueue = Promise.resolve();
const messagesByChat = new Map();
const seenMessageIds = new Set();
const seenReceiptIds = new Set();
const processingMessageIds = new Set();
const failedMessageReasons = new Map();
const pendingReadsByChat = new Map();
const pendingReceiptIds = new Set();

function bytesToBase64(bytes) {
  let binary = "";
  for (let offset = 0; offset < bytes.length; offset += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(offset, offset + 0x8000));
  }
  return btoa(binary);
}

function base64ToBytes(value) {
  const binary = atob(value);
  return Uint8Array.from(binary, (character) => character.charCodeAt(0));
}

function openDatabase() {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, 1);
    request.onupgradeneeded = () => request.result.createObjectStore(STORE_NAME);
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

async function readIdentity() {
  const database = await openDatabase();
  return new Promise((resolve, reject) => {
    const transaction = database.transaction(STORE_NAME, "readonly");
    const request = transaction.objectStore(STORE_NAME).get(IDENTITY_KEY);
    request.onsuccess = () => resolve(request.result || null);
    request.onerror = () => reject(request.error);
    transaction.oncomplete = () => database.close();
  });
}

async function writeIdentity(value) {
  const database = await openDatabase();
  return new Promise((resolve, reject) => {
    const transaction = database.transaction(STORE_NAME, "readwrite");
    transaction.objectStore(STORE_NAME).put(value, IDENTITY_KEY);
    transaction.oncomplete = () => {
      database.close();
      resolve();
    };
    transaction.onerror = () => reject(transaction.error);
  });
}

async function clearIdentity() {
  const database = await openDatabase();
  return new Promise((resolve, reject) => {
    const transaction = database.transaction(STORE_NAME, "readwrite");
    transaction.objectStore(STORE_NAME).clear();
    transaction.oncomplete = () => {
      database.close();
      resolve();
    };
    transaction.onerror = () => reject(transaction.error);
  });
}

async function generateIdentity() {
  const pair = await crypto.subtle.generateKey({ name: "X25519" }, true, ["deriveBits"]);
  const publicKey = new Uint8Array(await crypto.subtle.exportKey("raw", pair.publicKey));
  const privatePkcs8 = await crypto.subtle.exportKey("pkcs8", pair.privateKey);
  const privateKey = await crypto.subtle.importKey(
    "pkcs8",
    privatePkcs8,
    { name: "X25519" },
    false,
    ["deriveBits"],
  );
  const storageKey = await crypto.subtle.generateKey(
    { name: "AES-GCM", length: 256 },
    false,
    ["encrypt", "decrypt"],
  );
  new Uint8Array(privatePkcs8).fill(0);
  return {
    privateKey,
    publicKey: bytesToBase64(publicKey),
    storageKey,
    token: null,
    publicId: null,
  };
}

async function ensureStorageKey() {
  if (!identity || identity.storageKey) return;
  identity.storageKey = await crypto.subtle.generateKey(
    { name: "AES-GCM", length: 256 },
    false,
    ["encrypt", "decrypt"],
  );
  await writeIdentity(identity);
}

function historyKey(chatId, entryId) {
  return `${HISTORY_PREFIX}${identity.publicId}:${chatId}:${entryId}`;
}

async function putDatabaseValue(key, value) {
  const database = await openDatabase();
  return new Promise((resolve, reject) => {
    const transaction = database.transaction(STORE_NAME, "readwrite");
    transaction.objectStore(STORE_NAME).put(value, key);
    transaction.oncomplete = () => {
      database.close();
      resolve();
    };
    transaction.onerror = () => reject(transaction.error);
  });
}

async function readHistoryRecords() {
  if (!identity?.publicId) return [];
  const prefix = `${HISTORY_PREFIX}${identity.publicId}:`;
  const database = await openDatabase();
  return new Promise((resolve, reject) => {
    const records = [];
    const transaction = database.transaction(STORE_NAME, "readonly");
    const request = transaction.objectStore(STORE_NAME).openCursor();
    request.onsuccess = () => {
      const cursor = request.result;
      if (!cursor) return;
      if (typeof cursor.key === "string" && cursor.key.startsWith(prefix)) {
        records.push({ key: cursor.key, value: cursor.value });
      }
      cursor.continue();
    };
    request.onerror = () => reject(request.error);
    transaction.oncomplete = () => {
      database.close();
      resolve(records);
    };
  });
}

async function clearStoredHistory() {
  const database = await openDatabase();
  return new Promise((resolve, reject) => {
    const transaction = database.transaction(STORE_NAME, "readwrite");
    const store = transaction.objectStore(STORE_NAME);
    const request = store.openCursor();
    request.onsuccess = () => {
      const cursor = request.result;
      if (!cursor) return;
      if (typeof cursor.key === "string" && cursor.key.startsWith(HISTORY_PREFIX)) {
        cursor.delete();
      }
      cursor.continue();
    };
    request.onerror = () => reject(request.error);
    transaction.oncomplete = () => {
      database.close();
      resolve();
    };
  });
}

async function persistHistoryEntry(chatId, entry) {
  if (!identity?.storageKey || !identity.publicId || !entry.id) return;
  const key = historyKey(chatId, entry.id);
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const ciphertext = await crypto.subtle.encrypt(
    { name: "AES-GCM", iv, additionalData: encoder.encode(key) },
    identity.storageKey,
    encoder.encode(JSON.stringify(entry)),
  );
  await putDatabaseValue(key, { iv, ciphertext });
}

async function loadStoredHistory() {
  if (!identity?.storageKey || !identity.publicId) return;
  const records = await readHistoryRecords();
  for (const record of records) {
    try {
      const plaintext = await crypto.subtle.decrypt(
        {
          name: "AES-GCM",
          iv: record.value.iv,
          additionalData: encoder.encode(record.key),
        },
        identity.storageKey,
        record.value.ciphertext,
      );
      const parts = record.key.split(":");
      const chatId = Number(parts[2]);
      appendMessage(chatId, JSON.parse(decoder.decode(plaintext)), false);
    } catch {
      // Ignore corrupted local records; authenticated decryption prevents use.
    }
  }
  for (const entries of messagesByChat.values()) {
    entries.sort(compareHistoryEntries);
  }
}

function showToast(message, isError = false) {
  const target = isError ? elements.error : elements.status;
  const other = isError ? elements.status : elements.error;
  other.hidden = true;
  target.textContent = message;
  target.hidden = false;
  window.setTimeout(() => {
    target.hidden = true;
  }, isError ? 7000 : 3500);
}

function errorMessage(error) {
  if (error instanceof DOMException && error.name === "OperationError") {
    return "This message could not be authenticated or decrypted.";
  }
  return error instanceof Error ? error.message : String(error);
}

class SessionExpiredError extends Error {}

async function invalidateSession(reason = "Session expired") {
  if (!identity) return;
  if (identity.token) identity.suspendedToken = identity.token;
  identity.token = null;
  accessDeadline = null;
  updateSessionCountdown();
  await writeIdentity(identity);
  chats = [];
  selectedChatId = null;
  window.clearTimeout(reconnectTimer);
  reconnectTimer = null;
  window.clearInterval(heartbeatTimer);
  heartbeatTimer = null;
  window.clearTimeout(socketAuthTimer);
  socketAuthTimer = null;
  const activeSocket = socket;
  socket = null;
  if (activeSocket && activeSocket.readyState <= WebSocket.OPEN) activeSocket.close();
  updateIdentityUi();
  elements.inviteToken.focus();
  throw new SessionExpiredError(`${reason}. Activate a valid invite to continue.`);
}

async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  if (options.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  if (identity?.token) {
    headers.set("Authorization", `Bearer ${identity.token}`);
  }
  const response = await fetch(path, { ...options, headers, cache: "no-store" });
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      // Keep the status text when an error response is not JSON.
    }
    if (response.status === 401) {
      await invalidateSession(`Session unavailable: ${detail}`);
    }
    throw new Error(detail);
  }
  return response.json();
}

function envelopeContext(chatId, messageId, senderId, recipientId) {
  return encoder.encode(`pigeon-web-v1|${chatId}|${messageId}|${senderId}|${recipientId}`);
}

async function deriveSharedSecret(privateKey, publicKey) {
  return new Uint8Array(await crypto.subtle.deriveBits(
    { name: "X25519", public: publicKey },
    privateKey,
    256,
  ));
}

async function deriveMessageKey(sharedSecret, salt, info, usage) {
  const material = await crypto.subtle.importKey("raw", sharedSecret, "HKDF", false, ["deriveKey"]);
  sharedSecret.fill(0);
  return crypto.subtle.deriveKey(
    { name: "HKDF", hash: "SHA-256", salt, info },
    material,
    { name: "AES-GCM", length: 256 },
    false,
    [usage],
  );
}

async function encryptForRecipient(plaintext, chatId, messageId, recipient) {
  const ephemeral = await crypto.subtle.generateKey({ name: "X25519" }, true, ["deriveBits"]);
  const recipientKey = await crypto.subtle.importKey(
    "raw",
    base64ToBytes(recipient.public_key),
    { name: "X25519" },
    false,
    [],
  );
  const salt = crypto.getRandomValues(new Uint8Array(16));
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const info = envelopeContext(chatId, messageId, identity.publicId, recipient.public_id);
  const staticSecret = await deriveSharedSecret(identity.privateKey, recipientKey);
  const ephemeralSecret = await deriveSharedSecret(ephemeral.privateKey, recipientKey);
  const combinedSecret = new Uint8Array(staticSecret.length + ephemeralSecret.length);
  combinedSecret.set(staticSecret);
  combinedSecret.set(ephemeralSecret, staticSecret.length);
  staticSecret.fill(0);
  ephemeralSecret.fill(0);
  const key = await deriveMessageKey(combinedSecret, salt, info, "encrypt");
  const ciphertext = await crypto.subtle.encrypt(
    { name: "AES-GCM", iv, additionalData: info, tagLength: 128 },
    key,
    encoder.encode(plaintext),
  );
  const ephemeralPublicKey = await crypto.subtle.exportKey("raw", ephemeral.publicKey);
  const envelope = {
    v: 1,
    alg: "X25519-2DH-HKDF-SHA256-AES256GCM",
    epk: bytesToBase64(new Uint8Array(ephemeralPublicKey)),
    salt: bytesToBase64(salt),
    iv: bytesToBase64(iv),
    ct: bytesToBase64(new Uint8Array(ciphertext)),
  };
  return bytesToBase64(encoder.encode(JSON.stringify(envelope)));
}

async function decryptMessage(message) {
  const envelope = JSON.parse(decoder.decode(base64ToBytes(message.ciphertext)));
  if (envelope.v !== 1 || envelope.alg !== "X25519-2DH-HKDF-SHA256-AES256GCM") {
    throw new Error("Unsupported ciphertext format.");
  }
  const chat = chats.find((candidate) => candidate.id === message.chat_id);
  const sender = chat?.participants.find(
    (participant) => participant.public_id === message.sender_public_id,
  );
  if (!sender) throw new Error("Sender key is not present in this chat.");
  const senderKey = await crypto.subtle.importKey(
    "raw",
    base64ToBytes(sender.public_key),
    { name: "X25519" },
    false,
    [],
  );
  const ephemeralKey = await crypto.subtle.importKey(
    "raw",
    base64ToBytes(envelope.epk),
    { name: "X25519" },
    false,
    [],
  );
  const info = envelopeContext(
    message.chat_id,
    message.client_message_id,
    message.sender_public_id,
    identity.publicId,
  );
  const staticSecret = await deriveSharedSecret(identity.privateKey, senderKey);
  const ephemeralSecret = await deriveSharedSecret(identity.privateKey, ephemeralKey);
  const combinedSecret = new Uint8Array(staticSecret.length + ephemeralSecret.length);
  combinedSecret.set(staticSecret);
  combinedSecret.set(ephemeralSecret, staticSecret.length);
  staticSecret.fill(0);
  ephemeralSecret.fill(0);
  const key = await deriveMessageKey(combinedSecret, base64ToBytes(envelope.salt), info, "decrypt");
  const plaintext = await crypto.subtle.decrypt(
    {
      name: "AES-GCM",
      iv: base64ToBytes(envelope.iv),
      additionalData: info,
      tagLength: 128,
    },
    key,
    base64ToBytes(envelope.ct),
  );
  return decoder.decode(plaintext);
}

async function appendMessage(chatId, entry, persist = true) {
  const entries = messagesByChat.get(chatId) || [];
  if (entry.id && entries.some((candidate) => candidate.id === entry.id)) {
    return Promise.resolve();
  }
  entry.createdAt ||= Date.now();
  if (persist && entry.id) await persistHistoryEntry(chatId, entry);
  entries.push(entry);
  entries.sort(compareHistoryEntries);
  messagesByChat.set(chatId, entries);
  if (selectedChatId === chatId) renderMessages();
}

function serverTimestamp(value) {
  const utc = /(?:Z|[+-]\d{2}:\d{2})$/i.test(value) ? value : `${value}Z`;
  return Date.parse(utc) || Date.now();
}

function messageKey(message) {
  return `incoming:${JSON.stringify([message.chat_id, message.sender_public_id, message.client_message_id])}`;
}

function enqueueIncoming(action) {
  const result = inboundQueue.then(action);
  inboundQueue = result.catch(() => {});
  return result;
}

function historyTimestamp(entry) {
  if (typeof entry.createdAt === "number") return entry.createdAt;
  const parsed = Date.parse(entry.createdAt);
  return Number.isNaN(parsed) ? 0 : parsed;
}

function compareHistoryEntries(left, right) {
  const timestampDifference = historyTimestamp(left) - historyTimestamp(right);
  if (timestampDifference) return timestampDifference;
  return String(left.id || "").localeCompare(String(right.id || ""));
}

function renderMessages() {
  elements.messageList.replaceChildren();
  const entries = [...(messagesByChat.get(selectedChatId) || [])].sort(compareHistoryEntries);
  if (!entries.length) {
    const empty = document.createElement("p");
    empty.className = "empty-state";
    empty.textContent = "No messages yet.";
    elements.messageList.append(empty);
    return;
  }
  for (const entry of entries) {
    const message = document.createElement("div");
    message.className = `message ${entry.kind}`;
    const text = document.createElement("span");
    text.textContent = entry.text;
    message.append(text);
    if (entry.meta) {
      const meta = document.createElement("span");
      meta.className = "message-meta";
      meta.textContent = entry.meta;
      message.append(meta);
    }
    elements.messageList.append(message);
  }
  elements.messageList.scrollTop = elements.messageList.scrollHeight;
}

function chatName(chat) {
  if (chat.title) return chat.title;
  const peers = chat.participants.filter((participant) => participant.public_id !== identity.publicId);
  return peers.map((peer) => peer.display_name || peer.public_id).join(", ") || "Waiting for peer";
}

function renderChats() {
  elements.chatList.replaceChildren();
  if (!chats.length) {
    const empty = document.createElement("p");
    empty.className = "empty-state";
    empty.textContent = "No chats yet. Activate an invite.";
    elements.chatList.append(empty);
    return;
  }
  for (const chat of chats) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `chat-button${chat.id === selectedChatId ? " active" : ""}`;
    button.setAttribute("role", "listitem");
    const title = document.createElement("strong");
    title.textContent = chatName(chat);
    const detail = document.createElement("span");
    detail.textContent = `${chat.chat_type} · ${chat.participants.length} participant${chat.participants.length === 1 ? "" : "s"}`;
    button.append(title, detail);
    button.addEventListener("click", () => selectChat(chat.id));
    elements.chatList.append(button);
  }
}

function selectChat(chatId) {
  selectedChatId = chatId;
  const chat = chats.find((candidate) => candidate.id === chatId);
  elements.conversationKind.textContent = chat?.chat_type || "Conversation";
  elements.conversationTitle.textContent = chat ? chatName(chat) : "No conversation selected";
  elements.participantCount.textContent = chat ? `${chat.participants.length} participants` : "";
  elements.participantKeys.textContent = chat
    ? chat.participants
        .filter((item) => item.public_id !== identity.publicId)
        .map((item) => `${item.public_id}: ${item.key_fingerprint}`)
        .join(" · ")
    : "";
  const canSend = Boolean(chat && chat.participants.some((item) => item.public_id !== identity.publicId));
  elements.messageInput.disabled = !canSend;
  elements.sendButton.disabled = !canSend;
  renderChats();
  renderMessages();
  if (canSend) elements.messageInput.focus();
}

function updateIdentityUi() {
  const active = Boolean(identity?.token && identity?.publicId);
  elements.setupPanel.hidden = active;
  elements.clientPanel.hidden = !active;
  document.querySelector("#reconnect-session").hidden = active || !identity?.suspendedToken;
  updateConnectionState(false);
}

function updateConnectionState(connected) {
  const active = Boolean(identity?.token && identity?.publicId);
  elements.connectionState.classList.toggle("online", active && connected);
  elements.identityLabel.textContent = active
    ? `Device ${identity.publicId} · ${connected ? "Live" : "Polling"}`
    : "No local identity";
}

async function loadChats() {
  if (!identity?.token) return;
  const data = await api("/api/v1/me");
  accessDeadline = data.access_expires_at
    ? performance.now() + serverTimestamp(data.access_expires_at) - serverTimestamp(data.server_time)
    : null;
  updateSessionCountdown();
  if (data.user.public_key !== identity.publicKey) {
    throw new Error("Server identity does not match this device key. Reset this device.");
  }
  chats = data.chats;
  identity.publicId = data.user.public_id;
  await writeIdentity(identity);
  if (selectedChatId && !chats.some((chat) => chat.id === selectedChatId)) selectedChatId = null;
  if (!selectedChatId && chats.length) selectedChatId = chats[0].id;
  renderChats();
  if (selectedChatId) selectChat(selectedChatId);
}

function senderKeyIsLoaded(message) {
  const chat = chats.find((candidate) => candidate.id === message.chat_id);
  return Boolean(
    chat?.participants.some(
      (participant) => participant.public_id === message.sender_public_id,
    ),
  );
}

function queueRead(message) {
  const ids = pendingReadsByChat.get(message.chat_id) || new Set();
  ids.add(message.client_message_id);
  pendingReadsByChat.set(message.chat_id, ids);
}

async function flushAcknowledgements() {
  for (const [chatId, ids] of pendingReadsByChat) {
    await api(`/api/v1/chats/${chatId}/read`, {
      method: "POST",
      body: JSON.stringify({ client_message_ids: [...ids] }),
    });
    pendingReadsByChat.delete(chatId);
  }
  if (pendingReceiptIds.size) {
    await api("/api/v1/ack", {
      method: "POST",
      body: JSON.stringify({ read_ids: [...pendingReceiptIds] }),
    });
    pendingReceiptIds.clear();
  }
}

async function processMessages(messages) {
  const needsFreshChat = messages.some(
    (message) => !seenMessageIds.has(messageKey(message)) && !senderKeyIsLoaded(message),
  );
  if (needsFreshChat) await loadChats();

  for (const message of messages) {
    const key = messageKey(message);
    if (seenMessageIds.has(key)) {
      queueRead(message);
      continue;
    }
    if (processingMessageIds.has(key)) continue;
    processingMessageIds.add(key);
    try {
      const plaintext = await decryptMessage(message);
      await appendMessage(message.chat_id, {
        id: key,
        kind: "theirs",
        text: plaintext,
        meta: `From ${message.sender_public_id}`,
        createdAt: serverTimestamp(message.created_at),
      });
      seenMessageIds.add(key);
      failedMessageReasons.delete(key);
      queueRead(message);
    } catch (error) {
      const reason = errorMessage(error);
      if (failedMessageReasons.get(key) !== reason) {
        failedMessageReasons.set(key, reason);
        appendMessage(message.chat_id, {
          kind: "system",
          text: `Message retained for retry: ${reason}`,
        }, false);
      }
    } finally {
      processingMessageIds.delete(key);
    }
  }
}

async function processReceipts(receipts) {
  for (const receipt of receipts) {
    const key = `receipt:${JSON.stringify([receipt.chat_id, receipt.reader_public_id, receipt.client_message_id])}`;
    if (seenReceiptIds.has(key)) {
      pendingReceiptIds.add(receipt.id);
      continue;
    }
    await appendMessage(receipt.chat_id, {
      id: key,
      kind: "system",
      text: `Message ${receipt.client_message_id} was read by ${receipt.reader_public_id}.`,
      createdAt: serverTimestamp(receipt.created_at),
    });
    seenReceiptIds.add(key);
    pendingReceiptIds.add(receipt.id);
  }
}

async function processIncoming(data) {
  await processMessages(data.messages || []);
  await processReceipts(data.read_receipts || []);
  await flushAcknowledgements();
}

async function synchronize() {
  if (!identity?.token || synchronizing) return;
  synchronizing = true;
  try {
    await loadChats();
    const data = await api(`/api/v1/poll?_=${Date.now()}`);
    await enqueueIncoming(() => processIncoming(data));
  } catch (error) {
    showToast(errorMessage(error), true);
  } finally {
    synchronizing = false;
  }
}

function scheduleReconnect() {
  if (reconnectTimer || !identity?.token) return;
  reconnectTimer = window.setTimeout(() => {
    reconnectTimer = null;
    connectSocket();
  }, 2000);
}

async function handleSocketPayload(payload) {
  if (payload.type === "auth_error") {
    await invalidateSession("The saved session is no longer valid");
  } else if (payload.type === "hello") {
    updateConnectionState(true);
    await loadChats();
    await processIncoming(payload.backlog || {});
  } else if (payload.type === "message") {
    await processMessages([payload.message]);
    await flushAcknowledgements();
  } else if (payload.type === "read") {
    await processReceipts([payload.read]);
    await flushAcknowledgements();
  }
}

function connectSocket() {
  if (!identity?.token || (socket && socket.readyState <= WebSocket.OPEN)) return;
  const scheme = window.location.protocol === "https:" ? "wss:" : "ws:";
  const ws = new WebSocket(`${scheme}//${window.location.host}/ws`, ["pigeon.v1"]);
  socket = ws;
  ws.addEventListener("open", () => {
    if (socket !== ws || !identity?.token) {
      ws.close();
      return;
    }
    ws.send(JSON.stringify({ type: "auth", token: identity.token }));
    window.clearTimeout(socketAuthTimer);
    socketAuthTimer = window.setTimeout(() => {
      if (socket === ws) ws.close();
    }, 7000);
    let lastResponse = Date.now();
    ws.addEventListener("message", () => { lastResponse = Date.now(); });
    window.clearInterval(heartbeatTimer);
    heartbeatTimer = window.setInterval(() => {
      if (Date.now() - lastResponse > 60000) {
        ws.close();
        return;
      }
      if (socket === ws && ws.readyState === WebSocket.OPEN) ws.send("ping");
    }, 25000);
  });
  ws.addEventListener("message", (event) => {
    if (socket !== ws) return;
    if (event.data === "pong") return;
    enqueueIncoming(async () => {
        if (socket !== ws) return;
        const payload = JSON.parse(event.data);
        if (payload.type === "hello") {
          window.clearTimeout(socketAuthTimer);
          socketAuthTimer = null;
        }
        await handleSocketPayload(payload);
      })
      .catch((error) => showToast(errorMessage(error), true));
  });
  ws.addEventListener("close", () => {
    if (socket !== ws) return;
    window.clearTimeout(socketAuthTimer);
    socketAuthTimer = null;
    window.clearInterval(heartbeatTimer);
    heartbeatTimer = null;
    socket = null;
    updateConnectionState(false);
    scheduleReconnect();
  });
  ws.addEventListener("error", () => ws.close());
}

const refresh = synchronize;

function showNameError() {
  const error = document.querySelector("#display-name-error");
  error.textContent = "Enter your name (1–64 characters).";
  error.style.color = "var(--danger)";
  error.hidden = false;
  elements.displayName.setAttribute("aria-invalid", "true");
  elements.displayName.focus();
}

elements.displayName.addEventListener("invalid", (event) => {
  event.preventDefault();
  showNameError();
});
elements.displayName.addEventListener("input", () => {
  document.querySelector("#display-name-error").hidden = true;
  elements.displayName.setAttribute("aria-invalid", "false");
});

elements.activationForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const displayName = elements.displayName.value.trim();
  if (!displayName || displayName.length > 64) {
    showNameError();
    return;
  }
  const token = elements.inviteToken.value.trim();
  if (!/^[A-Za-z0-9_-]{20,128}$/.test(token)) {
    showToast("Enter a valid URL-safe invite token.", true);
    return;
  }
  const submitButton = elements.activationForm.querySelector("button[type='submit']");
  submitButton.disabled = true;
  try {
    if (!identity) identity = await generateIdentity();
    await ensureStorageKey();
    const previousPublicId = identity.publicId;
    const result = await api(`/api/v1/links/${encodeURIComponent(token)}/activate`, {
      method: "POST",
      body: JSON.stringify({
        public_key: identity.publicKey,
        display_name: displayName,
      }),
    });
    if (previousPublicId && previousPublicId !== result.user.public_id) {
      await clearStoredHistory();
      messagesByChat.clear();
      seenMessageIds.clear();
      seenReceiptIds.clear();
    }
    identity.token = result.token;
    identity.suspendedToken = null;
    identity.publicId = result.user.public_id;
    await writeIdentity(identity);
    history.replaceState(null, "", "/client");
    updateIdentityUi();
    await refresh();
    connectSocket();
    selectChat(result.chat.id);
    showToast("Invite activated. This device key is stored locally.");
  } catch (error) {
    showToast(errorMessage(error), true);
  } finally {
    submitButton.disabled = false;
  }
});

elements.messageInput.addEventListener("keydown", (event) => {
  if (event.key !== "Enter" || !event.shiftKey || event.isComposing
      || event.ctrlKey || event.altKey || event.metaKey) return;
  event.preventDefault();
  if (event.repeat || elements.messageInput.disabled || elements.sendButton.disabled) return;
  elements.messageForm.requestSubmit(elements.sendButton);
});

elements.messageForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const plaintext = elements.messageInput.value.trim();
  const chat = chats.find((candidate) => candidate.id === selectedChatId);
  if (!plaintext || !chat) return;
  elements.sendButton.disabled = true;
  try {
    const messageId = crypto.randomUUID();
    const recipients = chat.participants.filter((item) => item.public_id !== identity.publicId);
    const envelopes = await Promise.all(
      recipients.map(async (recipient) => ({
        recipient_public_id: recipient.public_id,
        ciphertext: await encryptForRecipient(plaintext, chat.id, messageId, recipient),
      })),
    );
    const result = await api(`/api/v1/chats/${chat.id}/messages`, {
      method: "POST",
      body: JSON.stringify({ client_message_id: messageId, envelopes }),
    });
    await appendMessage(chat.id, {
      id: `outgoing:${messageId}`,
      kind: "mine",
      text: plaintext,
      meta: `Sent · ${messageId.slice(0, 8)}`,
      createdAt: serverTimestamp(result.created_at),
    });
    elements.messageInput.value = "";
  } catch (error) {
    showToast(errorMessage(error), true);
  } finally {
    elements.sendButton.disabled = false;
    elements.messageInput.focus();
  }
});

elements.refreshButton.addEventListener("click", refresh);
document.querySelector("#reconnect-session").addEventListener("click", async (event) => {
  if (!identity?.suspendedToken) return;
  event.target.disabled = true;
  identity.token = identity.suspendedToken;
  try {
    await loadChats();
    identity.suspendedToken = null;
    await writeIdentity(identity);
    updateIdentityUi();
    connectSocket();
    await synchronize();
  } catch (error) {
    identity.token = null;
    await writeIdentity(identity);
    updateIdentityUi();
    showToast(errorMessage(error), true);
  } finally {
    event.target.disabled = false;
  }
});
elements.resetButton.addEventListener("click", async () => {
  const confirmed = window.confirm(
    "Remove this browser's private key and session? Existing chats cannot be recovered on this device.",
  );
  if (!confirmed) return;
  await clearIdentity();
  window.location.assign("/client");
});

async function start() {
  if (!window.isSecureContext || !window.crypto?.subtle || !window.indexedDB) {
    throw new Error("This client requires a secure browser context (HTTPS or localhost) with Web Crypto.");
  }
  identity = await readIdentity();
  await ensureStorageKey();
  await loadStoredHistory();
  updateIdentityUi();
  if (identity?.token) {
    await refresh();
    connectSocket();
  }
  window.setInterval(synchronize, POLL_INTERVAL_MS);
  window.setInterval(updateSessionCountdown, 1000);
}

function formatTimeRemaining(milliseconds) {
  const total = Math.max(0, Math.ceil(milliseconds / 1000));
  const days = Math.floor(total / 86400);
  const hours = Math.floor(total / 3600) % 24;
  const minutes = Math.floor(total / 60) % 60;
  const seconds = total % 60;
  const clock = [hours, minutes, seconds].map(value => String(value).padStart(2, "0")).join(":");
  return `${days ? `${days}d ` : ""}${clock}`;
}

function updateSessionCountdown() {
  const element = document.querySelector("#session-countdown");
  if (!element) return;
  element.hidden = !identity?.token || accessDeadline === null;
  if (element.hidden) return;
  const remaining = accessDeadline - performance.now();
  element.textContent = `Session expires in ${formatTimeRemaining(remaining)}`;
  if (remaining <= 0) {
    accessDeadline = null;
    void invalidateSession("Session expired").catch(error => showToast(errorMessage(error), true));
  }
}

window.addEventListener("online", () => {
  connectSocket();
  void synchronize();
});
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") {
    connectSocket();
    void synchronize();
  }
});

start().catch((error) => showToast(errorMessage(error), true));
