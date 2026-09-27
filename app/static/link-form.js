"use strict";

const expiryInput = document.querySelector("#expires-in-hours");
const expiryError = document.querySelector("#expiry-error");
expiryInput.addEventListener("invalid", (event) => {
  event.preventDefault();
  expiryInput.setAttribute("aria-invalid", "true");
  expiryError.textContent = "Enter a whole number from 1 to 8760, or leave blank for no expiry.";
  expiryError.hidden = false;
  expiryInput.focus();
});
expiryInput.addEventListener("input", () => {
  expiryInput.setAttribute("aria-invalid", "false");
  expiryError.hidden = true;
});

const passwordMode = document.querySelector("#password-mode");
const roomPassword = document.querySelector("#room-password");
const roomPasswordError = document.querySelector("#room-password-error");
const customPasswordFields = document.querySelector("#custom-password-fields");
function updatePasswordMode() {
  const custom = passwordMode.value === "custom";
  customPasswordFields.hidden = !custom && !roomPasswordError.textContent.trim();
  roomPassword.disabled = !custom;
  roomPassword.required = custom;
  roomPassword.minLength = custom ? 8 : 0;
  if (!custom) roomPassword.value = "";
}
passwordMode.addEventListener("change", () => {
  roomPasswordError.textContent = "";
  roomPasswordError.hidden = true;
  roomPassword.setAttribute("aria-invalid", "false");
  updatePasswordMode();
});
roomPassword.addEventListener("invalid", (event) => {
  event.preventDefault();
  roomPassword.setAttribute("aria-invalid", "true");
  roomPasswordError.textContent = "Use a phrase or password of 8 to 32 characters.";
  roomPasswordError.hidden = false;
  roomPassword.focus();
});
roomPassword.addEventListener("input", () => {
  roomPassword.setAttribute("aria-invalid", "false");
  roomPasswordError.hidden = true;
});
updatePasswordMode();

const linkType = document.querySelector("#link-type");
const participantLimit = document.querySelector("#participant-limit");
const participantLimitError = document.querySelector("#participant-limit-error");
function updateParticipantLimit() {
  const group = linkType.value === "group";
  participantLimit.disabled = !group;
  document.querySelector("#participant-limit-field").hidden = !group;
  document.querySelector("#group-title-field").hidden = !group;
  document.querySelector("#group-title").disabled = !group;
}
linkType.addEventListener("change", updateParticipantLimit);
participantLimit.addEventListener("invalid", (event) => {
  event.preventDefault();
  participantLimit.setAttribute("aria-invalid", "true");
  participantLimitError.textContent = "Enter a whole number from 2 to 1000, or leave blank.";
  participantLimitError.hidden = false;
  participantLimit.focus();
});
participantLimit.addEventListener("input", () => {
  participantLimit.setAttribute("aria-invalid", "false");
  participantLimitError.hidden = true;
});
updateParticipantLimit();
