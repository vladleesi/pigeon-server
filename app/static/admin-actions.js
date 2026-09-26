"use strict";

const dialog = document.querySelector("#confirm-dialog");
const description = document.querySelector("#confirm-description");
const accept = document.querySelector("#confirm-accept");
let pending = null;
const approved = new WeakSet();

function selectedInputs(form) {
  return Array.from(form.elements).filter(input => input.name === "ids" && input.type === "checkbox");
}

function updateSelection(form) {
  const inputs = selectedInputs(form);
  const count = inputs.filter(input => input.checked).length;
  form.querySelector("[data-selection-count]").textContent = `${count} selected`;
  form.querySelector("button[type=submit]").disabled = count === 0 || count > 500;
  const all = document.querySelector(`[data-select-all="${form.id}"]`);
  all.checked = count > 0 && count === inputs.length;
  all.indeterminate = count > 0 && count < inputs.length;
  for (const input of inputs) input.closest("tr").classList.toggle("selected", input.checked);
}

document.addEventListener("change", (event) => {
  const input = event.target;
  if (input.dataset.selectAll) {
    const form = document.getElementById(input.dataset.selectAll);
    for (const checkbox of selectedInputs(form)) checkbox.checked = input.checked;
    updateSelection(form);
  } else if (input.name === "ids" && input.form?.hasAttribute("data-selection-form")) {
    updateSelection(input.form);
  }
});

document.addEventListener("submit", (event) => {
  const form = event.target;
  if (approved.delete(form)) return;
  const prompt = form.dataset.confirm;
  if (!prompt) return;
  event.preventDefault();
  const count = form.hasAttribute("data-selection-form")
    ? selectedInputs(form).filter(input => input.checked).length : null;
  if (count !== null && (count === 0 || count > 500)) return;
  pending = { form, submitter: event.submitter };
  description.textContent = count === null ? prompt : `${count} selected. ${prompt}`;
  accept.textContent = form.dataset.confirmLabel || "Delete";
  dialog.showModal();
});

document.querySelector("#confirm-cancel").addEventListener("click", () => dialog.close());
dialog.addEventListener("close", () => { pending = null; });
accept.addEventListener("click", () => {
  if (!pending) return;
  const { form, submitter } = pending;
  pending = null;
  approved.add(form);
  dialog.close();
  form.requestSubmit(submitter || undefined);
});

for (const form of document.querySelectorAll("[data-selection-form]")) updateSelection(form);
