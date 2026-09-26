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
