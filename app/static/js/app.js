(() => {
  "use strict";

  const API_URL = "/api/v1/urls";
  const REQUEST_TIMEOUT_MS = 15000;

  const $ = (id) => document.getElementById(id);

  const form = $("url-form");
  if (!form) return; // not the home page

  const els = {
    input: $("url-input"),
    submitBtn: $("submit-btn"),
    submitLabel: $("submit-label"),
    spinner: $("submit-spinner"),
    error: $("form-error"),
    empty: $("empty-state"),
    card: $("result-card"),
    link: $("result-link"),
    original: $("result-original"),
    copyBtn: $("copy-btn"),
    copyStatus: $("copy-status"),
    year: $("year"),
  };

  if (els.year) els.year.textContent = new Date().getFullYear();

  let copyResetTimer = null;

  function setLoading(isLoading) {
    els.submitBtn.disabled = isLoading;
    els.spinner.hidden = !isLoading;
    els.submitLabel.textContent = isLoading ? "Shortening…" : "Shorten";
    els.submitBtn.setAttribute("aria-busy", String(isLoading));
  }

  function showError(message) {
    els.error.textContent = message;
    els.error.hidden = false;
    els.input.setAttribute("aria-invalid", "true");
  }

  function clearError() {
    els.error.hidden = true;
    els.error.textContent = "";
    els.input.removeAttribute("aria-invalid");
  }

  function showResult(data) {
    els.link.textContent = data.short_url;
    els.link.href = data.short_url;
    els.original.textContent = data.original_url;
    resetCopyState();
    els.empty.hidden = true;
    els.card.hidden = false;
  }

  function hideResult() {
    els.card.hidden = true;
    els.empty.hidden = false;
  }

  function resetCopyState() {
    clearTimeout(copyResetTimer);
    els.copyBtn.textContent = "Copy";
    els.copyStatus.textContent = "";
  }

  async function readJson(response) {
    try {
      return await response.json();
    } catch {
      return null;
    }
  }

  async function shorten(url) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
    try {
      return await fetch(API_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({ url }),
        signal: controller.signal,
      });
    } finally {
      clearTimeout(timer);
    }
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (els.submitBtn.disabled) return;

    const url = els.input.value.trim();
    clearError();

    if (!url) {
      showError("Please enter a URL to shorten.");
      els.input.focus();
      return;
    }

    setLoading(true);
    hideResult();

    try {
      const response = await shorten(url);
      const body = await readJson(response);

      if (response.status === 201 && body && body.short_url) {
        showResult(body);
        return;
      }

      if (response.status === 400 || response.status === 422) {
        showError(body?.error?.message || "That doesn't look like a valid URL.");
        els.input.focus();
      } else if (response.status === 413) {
        showError("That URL is too long.");
      } else if (response.status === 429) {
        showError("Too many requests. Please wait a moment and try again.");
      } else {
        showError("Something went wrong on our side. Please try again shortly.");
      }
    } catch {
      showError("Couldn't reach the server. Check your connection and try again.");
    } finally {
      setLoading(false);
    }
  });

  els.input.addEventListener("input", () => {
    if (!els.error.hidden) clearError();
  });

  async function copyText(text) {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return;
    }
    // Fallback for older browsers
    const temp = document.createElement("textarea");
    temp.value = text;
    temp.setAttribute("readonly", "");
    temp.style.position = "fixed";
    temp.style.opacity = "0";
    document.body.appendChild(temp);
    temp.select();
    const ok = document.execCommand("copy");
    document.body.removeChild(temp);
    if (!ok) throw new Error("copy failed");
  }

  els.copyBtn.addEventListener("click", async () => {
    clearTimeout(copyResetTimer);
    try {
      await copyText(els.link.textContent);
      els.copyBtn.textContent = "Copied!";
      els.copyStatus.textContent = "Short link copied to clipboard.";
    } catch {
      els.copyStatus.textContent = "Couldn't copy automatically. Select the link and copy it manually.";
    }
    copyResetTimer = setTimeout(resetCopyState, 2500);
  });
})();
