(() => {
  const autostartForm = document.querySelector('[data-autostart-form]');
  if (autostartForm instanceof HTMLFormElement) {
    autostartForm.submit();
  }

  const view = document.querySelector('[data-session-view]');
  if (!(view instanceof HTMLElement)) {
    return;
  }

  const token = view.dataset.sessionToken;
  const frame = document.getElementById('session-frame');
  const loading = document.getElementById('session-loading');
  const message = document.getElementById('session-message');
  const spinner = document.getElementById('session-spinner');
  const statusValue = document.getElementById('session-status-value');
  const passwordPanels = Array.from(document.querySelectorAll('[data-session-password-panel]'));
  const passwordValues = Array.from(document.querySelectorAll('[data-session-password-value]'));
  const copyPasswordButtons = Array.from(document.querySelectorAll('[data-copy-password]'));

  if (
    !token ||
    !(frame instanceof HTMLIFrameElement) ||
    !(loading instanceof HTMLElement) ||
    !(message instanceof HTMLElement) ||
    !(spinner instanceof HTMLElement) ||
    !(statusValue instanceof HTMLElement)
  ) {
    return;
  }

  let statusInterval;
  let terminalMessageShown = false;
  let password = '';
  let socket;

  async function refreshStatus() {
    try {
      const response = await fetch('/sessions/status', {
        cache: 'no-store',
        headers: { Accept: 'application/json' },
      });
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }
      const { active, limit } = await response.json();
      statusValue.textContent = `${active}/${limit}`;
    } catch {
      statusValue.textContent = '-/-';
    }
  }

  function setMessage(text) {
    message.textContent = text;
  }

  function showError(text) {
    terminalMessageShown = true;
    setMessage(text);
    spinner.classList.add('hidden');
  }

  function showSession(url) {
    if (!url) {
      showError('Session is missing a container URL.');
      return;
    }
    frame.src = url;
    loading.classList.add('hidden');
    frame.classList.remove('hidden');
  }

  function setPassword(value) {
    password = value;
    for (const element of passwordValues) {
      element.textContent = value;
    }
    for (const panel of passwordPanels) {
      if (panel instanceof HTMLElement) {
        panel.hidden = false;
      }
    }
  }

  function lifecycleUrl() {
    const url = new URL(`/sessions/${encodeURIComponent(token)}/lifecycle`, window.location.href);
    url.protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    return url;
  }

  function setupPasswordCopy() {
    for (const button of copyPasswordButtons) {
      if (!(button instanceof HTMLButtonElement)) {
        continue;
      }
      button.addEventListener('click', async () => {
        if (!password) {
          return;
        }
        const originalText = button.textContent || 'Copy';
        try {
          await navigator.clipboard.writeText(password);
          button.textContent = 'Copied';
        } catch {
          button.textContent = 'Copy failed';
        } finally {
          window.setTimeout(() => {
            button.textContent = originalText;
          }, 1500);
        }
      });
    }
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
        showSession(update.url);
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

  refreshStatus();
  statusInterval = window.setInterval(refreshStatus, 60_000);
  setupPasswordCopy();
  connectLifecycle();

  window.addEventListener('pagehide', () => {
    window.clearInterval(statusInterval);
    if (socket instanceof WebSocket && socket.readyState === WebSocket.OPEN) {
      socket.close(1000, 'page unloaded');
    }
  });
})();
