"use strict";

(() => {
  const form = document.querySelector("#create-link-form");
  let creating = false;
  let refreshing = false;
  let revision = 0;

  async function savePanel() {
    if (!window.inviteVault) return;
    const hint = document.querySelector("#invite-save-status");
    try {
      await window.inviteVault.save({
        url: document.querySelector("#created-invite-url").value,
        password: document.querySelector("#created-invite-password").value,
      });
      hint.textContent = "Saved encrypted in this browser tab for 24 hours, including across refreshes. Share the password separately.";
    } catch {
      hint.textContent = "Browser saving is unavailable. Copy the password before refreshing or leaving this page.";
    }
  }

  async function restorePanel() {
    if (!window.inviteVault) return;
    const initialRevision = revision;
    if (document.querySelector("#created-invite-password").value) { await savePanel(); return; }
    try {
      const details = await window.inviteVault.read();
      if (!details || revision !== initialRevision) return;
      document.querySelector("#created-invite-url").value = details.url;
      document.querySelector("#created-invite-password").value = details.password;
      document.querySelector("#created-invite-panel").hidden = false;
      document.querySelector("#created-invite-heading").textContent = "Previously saved invite";
      const context = document.querySelector("#created-invite-context");
      context.textContent = "Saved password-protected invite from an earlier creation. New links appear in Existing links below.";
      context.hidden = false;
      document.querySelector("#created-invite-details").hidden = true;
      const toggle = document.querySelector("[data-toggle-invite]");
      toggle.textContent = "Show details";
      toggle.setAttribute("aria-expanded", "false");
      document.querySelector("#invite-save-status").textContent = "Restored from encrypted storage in this browser tab. Share the password separately.";
    } catch {
      // Never block the admin UI when browser storage is unavailable.
    }
  }

  function syncLinks(fresh, force = false) {
    const current = document.querySelector("#existing-links");
    const rows = [...current.querySelectorAll("[data-link-id]")];
    const nextRows = [...fresh.querySelectorAll("[data-link-id]")];
    const sameRows = rows.length === nextRows.length && rows.every(
      (row, index) => row.dataset.linkId === nextRows[index].dataset.linkId,
    );
    if (sameRows) {
      rows.forEach((row, index) => {
        for (const cell of row.querySelectorAll("[data-live-field]")) {
          const next = nextRows[index].querySelector(`[data-live-field="${cell.dataset.liveField}"]`);
          if (next && cell.innerHTML !== next.innerHTML && !cell.contains(document.activeElement)) {
            cell.replaceChildren(...next.childNodes);
          }
        }
      });
      return;
    }
    // Avoid disturbing an action the user is about to take during background refresh.
    if (!force && current.contains(document.activeElement)) return;
    const selected = new Set([...current.querySelectorAll('input[name="ids"]:checked')]
      .map(input => input.value));
    const replacement = document.importNode(fresh, true);
    current.replaceWith(replacement);
    for (const input of replacement.querySelectorAll('input[name="ids"]')) {
      input.checked = selected.has(input.value);
    }
    const selectionForm = replacement.querySelector("[data-selection-form]");
    if (selectionForm && typeof updateSelection === "function") updateSelection(selectionForm);
  }

  async function refreshLinks(manual = false) {
    if (refreshing || creating || document.hidden || document.querySelector("#confirm-dialog")?.open) return;
    refreshing = true;
    const startedAtRevision = revision;
    const button = document.querySelector("#refresh-links");
    if (manual) {
      button.disabled = true;
      button.textContent = "Refreshing…";
      button.setAttribute("aria-busy", "true");
    }
    try {
      const response = await fetch("/admin/links", { cache: "no-store", credentials: "same-origin" });
      const page = new DOMParser().parseFromString(await response.text(), "text/html");
      const section = page.querySelector("#existing-links");
      if (!response.ok || !section) throw new Error("List unavailable");
      if (creating || revision !== startedAtRevision) return;
      syncLinks(section, manual);
      const status = document.querySelector("#links-refresh-status");
      status.textContent = "";
      status.hidden = true;
    } catch {
      const status = document.querySelector("#links-refresh-status");
      status.textContent = "Could not refresh the list. Try again or sign in if your session expired.";
      status.hidden = false;
    } finally {
      refreshing = false;
      if (manual) {
        const currentButton = document.querySelector("#refresh-links");
        currentButton.disabled = false;
        currentButton.textContent = "Refresh list";
        currentButton.setAttribute("aria-busy", "false");
      }
    }
  }

  // Keep creation out of navigation history. Reload then performs a fresh GET,
  // rather than resubmitting the POST and creating another empty room.
  form.addEventListener("submit", async (event) => {
    if (document.querySelector("#password-mode").value === "none") return;
    event.preventDefault();
    if (creating) return;
    creating = true;
    revision += 1;
    const submit = form.querySelector('button[type="submit"]');
    const error = document.querySelector("#create-link-error");
    submit.disabled = true;
    error.hidden = true;
    try {
      const response = await fetch(form.action, {
        method: "POST", body: new FormData(form), cache: "no-store", credentials: "same-origin",
      });
      const page = new DOMParser().parseFromString(await response.text(), "text/html");
      const panel = page.querySelector("#created-invite-panel");
      const section = page.querySelector("#existing-links");
      if (!response.ok || !panel || panel.hidden || !section) {
        const message = [...page.querySelectorAll('[role="alert"]:not([hidden])')]
          .map(item => item.textContent.trim()).filter(Boolean).join(" ");
        throw new Error(message || "Creation could not be confirmed. Refresh the list before trying again.");
      }
      const replacement = document.importNode(panel, true);
      const previous = document.querySelector("#created-invite-panel");
      if (previous) previous.replaceWith(replacement);
      else document.querySelector(".create-link-card").before(replacement);
      syncLinks(section, true);
      document.querySelector("#room-password").value = "";
      await savePanel();
      document.querySelector("#created-invite-heading").focus();
    } catch (failure) {
      error.textContent = failure.message || "Could not create invite. Refresh the list before trying again.";
      error.hidden = false;
    } finally {
      creating = false;
      submit.disabled = false;
    }
  });

  // Delegation also covers newly rendered creation panels and refreshed lists.
  document.addEventListener("click", async (event) => {
    const toggle = event.target.closest("[data-toggle-invite]");
    if (toggle) {
      const details = document.querySelector("#created-invite-details");
      details.hidden = !details.hidden;
      toggle.setAttribute("aria-expanded", String(!details.hidden));
      toggle.textContent = details.hidden ? "Show details" : "Hide details";
      return;
    }
    if (event.target.closest("#refresh-links")) {
      await refreshLinks(true);
      return;
    }
    const button = event.target.closest("[data-copy-invite]");
    if (!button) return;
    const input = document.getElementById(button.dataset.copyInvite);
    const status = document.querySelector("#invite-copy-status");
    button.disabled = true;
    try {
      await navigator.clipboard.writeText(input.value);
      button.textContent = "Copied";
      status.textContent = "";
      status.hidden = true;
      button.setAttribute("aria-label", "Copied");
      window.setTimeout(() => {
        button.textContent = "Copy";
        button.setAttribute("aria-label", button.dataset.copyInvite === "created-invite-url" ? "Copy invite link" : "Copy room password");
      }, 1800);
    } catch {
      input.focus();
      input.select();
      status.textContent = "Clipboard unavailable. The text is selected; copy it using your keyboard.";
      status.hidden = false;
    } finally {
      button.disabled = false;
    }
  });

  window.addEventListener("focus", () => refreshLinks());
  window.addEventListener("pageshow", () => refreshLinks());
  document.addEventListener("visibilitychange", () => refreshLinks());
  window.setInterval(() => refreshLinks(), 5000);
  restorePanel();
})();
