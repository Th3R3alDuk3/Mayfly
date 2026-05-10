(() => {
  // ---- autostart on landing page -------------------------------------------
  const autostartForm = document.querySelector('[data-autostart-form]');
  if (autostartForm) {
    autostartForm.submit();
  }

  const view = document.querySelector('[data-session-view]');
  if (!view) {
    return;
  }

  // ---- DOM ----------------------------------------------------------------
  const token = view.dataset.sessionToken;
  const frame = document.getElementById('session-frame');
  const loading = document.getElementById('session-loading');
  const message = document.getElementById('session-message');
  const spinner = document.getElementById('session-spinner');
  const statusValue = document.getElementById('session-status-value');
  const memoryValue = document.getElementById('docker-memory-value');
  const passwordValues = document.querySelectorAll('[data-session-password-value]');
  const passwordModal = document.querySelector('[data-session-password-modal]');
  const passwordCopyButton = document.querySelector('[data-session-password-copy]');
  const passwordCopyText = document.querySelector('[data-session-password-copy-text]');
  const passwordDismissButton = document.querySelector('[data-session-password-dismiss]');
  const uploadInput = document.querySelector('[data-upload-input]');
  const uploadText = document.querySelector('[data-upload-text]');

  // ---- state --------------------------------------------------------------
  let password = '';
  let socket = null;
  let statusInterval = null;
  let terminalMessageShown = false;
  let passwordModalShown = false;

  // ---- view helpers -------------------------------------------------------
  function setMessage(text) {
    message.textContent = text;
  }

  function showError(text) {
    terminalMessageShown = true;
    setMessage(text);
    spinner.classList.add('hidden');
    frame.classList.add('hidden');
    loading.classList.remove('hidden');
    if (uploadInput) uploadInput.disabled = true;
  }

  function showSession() {
    frame.src = '/mayfly/';
    loading.classList.add('hidden');
    frame.classList.remove('hidden');
    if (uploadInput) uploadInput.disabled = false;
  }

  // ---- session status badge -----------------------------------------------
  async function refreshStatus() {
    try {
      const response = await fetch(`/sessions/status?token=${encodeURIComponent(token)}`, {
        cache: 'no-store',
        headers: { Accept: 'application/json' },
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const { active, limit, memory } = await response.json();
      statusValue.textContent = `${active}/${limit}`;
      if (memoryValue) memoryValue.textContent = memory;
    } catch {
      statusValue.textContent = '-/-';
      if (memoryValue) memoryValue.textContent = '-/-';
    }
  }

  // ---- password modal -----------------------------------------------------
  function setPassword(value) {
    password = value;
    for (const element of passwordValues) {
      element.textContent = value;
    }
    if (!passwordModalShown && passwordModal) {
      passwordModalShown = true;
      passwordModal.hidden = false;
      passwordModal.classList.remove('hidden');
      passwordModal.classList.add('flex');
      passwordDismissButton?.focus();
    }
  }

  function dismissPasswordModal() {
    if (!passwordModal) return;
    passwordModal.hidden = true;
    passwordModal.classList.add('hidden');
    passwordModal.classList.remove('flex');
  }

  async function copyPassword() {
    if (!password) return;
    const original = passwordCopyText.textContent;
    try {
      await navigator.clipboard.writeText(password);
      passwordCopyText.textContent = 'Copied';
    } catch {
      passwordCopyText.textContent = 'Failed';
    }
    window.setTimeout(() => { passwordCopyText.textContent = original; }, 1500);
  }

  passwordCopyButton?.addEventListener('click', copyPassword);
  passwordDismissButton?.addEventListener('click', dismissPasswordModal);
  passwordModal?.addEventListener('click', (event) => {
    if (event.target === passwordModal) dismissPasswordModal();
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && passwordModal && !passwordModal.hidden) {
      dismissPasswordModal();
    }
  });

  // ---- workspace upload ---------------------------------------------------
  async function handleUpload(event) {
    const input = event.target;
    if (!input.files || input.files.length === 0) return;
    const file = input.files[0];
    const original = uploadText.textContent;
    input.disabled = true;
    uploadText.textContent = 'Uploading...';
    try {
      const body = new FormData();
      body.append('file', file, file.name);
      const response = await fetch(`/sessions/${encodeURIComponent(token)}/upload`, {
        method: 'POST',
        headers: { 'X-Mayfly-Password': password },
        body,
      });
      if (!response.ok) {
        let detail = `HTTP ${response.status}`;
        try {
          const payload = await response.json();
          if (typeof payload?.detail === 'string') detail = payload.detail;
        } catch {}
        throw new Error(detail);
      }
      uploadText.textContent = 'Uploaded';
      window.setTimeout(() => { uploadText.textContent = original; }, 1500);
    } catch {
      uploadText.textContent = 'Failed';
      window.setTimeout(() => { uploadText.textContent = original; }, 2000);
    } finally {
      input.value = '';
      input.disabled = false;
    }
  }

  uploadInput?.addEventListener('change', handleUpload);

  // ---- lifecycle WebSocket ------------------------------------------------
  function lifecycleUrl() {
    const url = new URL(`/sessions/${encodeURIComponent(token)}/lifecycle`, window.location.href);
    url.protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    return url;
  }

  function connectLifecycle() {
    socket = new WebSocket(lifecycleUrl());

    socket.addEventListener('message', (event) => {
      let update;
      try {
        update = JSON.parse(event.data);
      } catch {
        showError('Received an invalid session update.');
        socket.close();
        return;
      }

      if (typeof update.password === 'string' && update.password.length > 0) {
        setPassword(update.password);
      }

      if (update.state === 'starting') {
        setMessage('Starting Mayfly...');
      } else if (update.state === 'ready') {
        showSession();
      } else if (update.state === 'error') {
        showError(update.error || 'Session failed to start.');
      } else if (update.state === 'closing') {
        showError('Session is closing.');
      }
    });

    socket.addEventListener('error', () => {
      if (frame.classList.contains('hidden')) {
        showError('Lost connection to the session.');
      }
    });

    socket.addEventListener('close', (event) => {
      if (!terminalMessageShown && frame.classList.contains('hidden')) {
        showError(event.reason || 'Session ended.');
      }
    });
  }

  function closeLifecycle(reason) {
    if (socket && socket.readyState < WebSocket.CLOSING) {
      socket.close(1000, reason);
    }
  }

  // ---- multi-tab takeover detection ---------------------------------------
  // The session cookie is shared across tabs, so a second tab silently
  // hijacks this one. Every tab broadcasts its token; older tabs that see
  // a different token surrender and shut their lifecycle WS down.
  const channel = new BroadcastChannel('mayfly-session');
  channel.postMessage({ kind: 'claim', token });
  channel.onmessage = (event) => {
    if (event.data?.kind !== 'claim' || event.data.token === token) return;
    showError('Session opened in another tab — closing this one.');
    closeLifecycle('taken-over');
  };

  // ---- boot ---------------------------------------------------------------
  refreshStatus();
  statusInterval = window.setInterval(refreshStatus, 30_000);
  connectLifecycle();

  window.addEventListener('pagehide', () => {
    window.clearInterval(statusInterval);
    channel.close();
    closeLifecycle('page unloaded');
  });
})();
