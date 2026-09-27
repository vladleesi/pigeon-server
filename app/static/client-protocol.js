"use strict";

// Legacy v1 wire protocol. No DOM, storage, network, or implicit identity state.
// See docs/PROTOCOL.md before implementing another client. This is not a ratchet.
const SidewordProtocol = (() => {
  const encoder = new TextEncoder();
  const decoder = new TextDecoder("utf-8", { fatal: true });

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

  function envelopeContext(chatId, messageId, senderId, recipientId) {
    return encoder.encode(`sideword-web-v1|${chatId}|${messageId}|${senderId}|${recipientId}`);
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

  async function encryptForRecipient(identity, plaintext, chatId, messageId, recipient) {
    const ephemeral = await crypto.subtle.generateKey({ name: "X25519" }, false, ["deriveBits"]);
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

  async function decryptMessage(identity, message, sender) {
    const envelope = JSON.parse(decoder.decode(base64ToBytes(message.ciphertext)));
    if (envelope.v !== 1 || envelope.alg !== "X25519-2DH-HKDF-SHA256-AES256GCM") {
      throw new Error("Unsupported ciphertext format.");
    }
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

  async function publicKeyFingerprint(publicKey) {
    const bytes = base64ToBytes(publicKey);
    if (bytes.length !== 32) throw new Error("Invalid X25519 public key.");
    const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", bytes));
    return Array.from(digest, byte => byte.toString(16).padStart(2, "0")).join("");
  }

  return Object.freeze({ bytesToBase64, base64ToBytes, encryptForRecipient,
    decryptMessage, publicKeyFingerprint });
})();
