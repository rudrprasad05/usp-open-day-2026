(() => {
  const $ = (selector) => document.querySelector(selector);
  const canvas = $('#canvas');
  const ctx = canvas.getContext('2d');
  const connection = $('#connection');
  const timer = $('#timer');
  const target = $('#target');
  const roundLabel = $('#roundLabel');
  const progress = $('#progress');
  const entryScreen = $('#entryScreen');
  const playScreen = $('#playScreen');
  const finalScreen = $('#finalScreen');
  const entryForm = $('#entryForm');
  const playerName = $('#playerName');
  const entryError = $('#entryError');
  const globalStatus = $('#globalStatus');
  const reconnectOverlay = $('#reconnectOverlay');
  const startGame = $('#startGame');
  const message = $('#canvasMessage');
  const next = $('#next');
  const undo = $('#undo');
  const clear = $('#clear');
  const done = $('#done');
  const playAgain = $('#playAgain');
  let socket, reconnectTimer, state = {}, drawing = false, last = null, pathId = null;
  let clockOffset = 0, entryForced = false, finalRevealed = true;
  let connected = false, awaitingSnapshot = true, hadConnectionLoss = false, pendingAction = null;
  let navigationApproved = false;

  const isRoundActive = () => state.status === 'running' || state.status === 'finalizing';
  const canSend = () => connected && !awaitingSnapshot && socket?.readyState === WebSocket.OPEN;
  const canDraw = () => canSend() && state.status === 'running' && !pendingAction;

  function resize() {
    const rect = canvas.getBoundingClientRect();
    const ratio = Math.min(devicePixelRatio || 1, 2);
    canvas.width = Math.round(rect.width * ratio);
    canvas.height = Math.round(rect.height * ratio);
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    ctx.lineCap = 'round'; ctx.lineJoin = 'round';
    redraw(state.paths || []);
  }
  function line(segment) {
    const rect = canvas.getBoundingClientRect();
    ctx.strokeStyle = '#090b10';
    ctx.lineWidth = Math.max(2, segment.width * Math.min(rect.width, rect.height));
    ctx.beginPath();
    ctx.moveTo(segment.x1 * rect.width, segment.y1 * rect.height);
    ctx.lineTo(segment.x2 * rect.width, segment.y2 * rect.height);
    ctx.stroke();
  }
  function redraw(paths) {
    const rect = canvas.getBoundingClientRect();
    ctx.clearRect(0, 0, rect.width, rect.height);
    paths.forEach(path => path.forEach(line));
  }
  function point(event) {
    const rect = canvas.getBoundingClientRect();
    return {x: (event.clientX - rect.left) / rect.width, y: (event.clientY - rect.top) / rect.height};
  }
  function remember(segment) {
    state.paths ||= [];
    const lastPath = state.paths.at(-1);
    if (!lastPath || lastPath[0]?.pathId !== segment.pathId) state.paths.push([segment]);
    else lastPath.push(segment);
  }
  function begin(event) {
    if (!canDraw()) return;
    event.preventDefault();
    canvas.setPointerCapture(event.pointerId);
    drawing = true;
    last = point(event);
    pathId = `${Date.now()}-${event.pointerId}`;
  }
  function move(event) {
    if (!drawing || !last || !canDraw()) return;
    event.preventDefault();
    const current = point(event);
    const segment = {type: 'stroke', pathId, x1: last.x, y1: last.y,
      x2: current.x, y2: current.y, width: .014};
    line(segment); remember(segment); send(segment); last = current;
  }
  function end(event) {
    if (drawing) event.preventDefault();
    drawing = false; last = null;
  }
  canvas.addEventListener('pointerdown', begin);
  canvas.addEventListener('pointermove', move);
  canvas.addEventListener('pointerup', end);
  canvas.addEventListener('pointercancel', end);

  function setStatus(text, kind = 'status') {
    globalStatus.textContent = text;
    globalStatus.setAttribute('role', kind === 'error' ? 'alert' : 'status');
    globalStatus.classList.toggle('error', kind === 'error');
  }
  function send(data) {
    if (!canSend()) return false;
    try {
      socket.send(JSON.stringify(data));
      return true;
    } catch {
      setStatus('Connection lost. Reconnecting…', 'error');
      return false;
    }
  }
  function connect() {
    clearTimeout(reconnectTimer);
    const protocol = location.protocol === 'https:' ? 'wss' : 'ws';
    socket = new WebSocket(`${protocol}://${location.host}/ws/draw`);
    socket.onopen = () => {
      connected = true;
      awaitingSnapshot = true;
      connection.classList.remove('offline');
      connection.querySelector('span').textContent = 'Syncing';
      updateControls();
    };
    socket.onclose = () => {
      connected = false;
      awaitingSnapshot = true;
      drawing = false; last = null;
      if (isRoundActive()) hadConnectionLoss = true;
      connection.classList.add('offline');
      connection.querySelector('span').textContent = 'Reconnecting';
      if (isRoundActive()) setStatus('Connection lost. Reconnecting…', 'error');
      updateControls();
      reconnectTimer = setTimeout(connect, 1200);
    };
    socket.onerror = () => {
      if (isRoundActive()) setStatus('Connection problem. Reconnecting…', 'error');
    };
    socket.onmessage = (event) => {
      let data;
      try { data = JSON.parse(event.data); }
      catch { setStatus('Received an unreadable update. Reconnecting…', 'error'); return; }
      if (data.type === 'state') {
        const wasWaitingForSnapshot = awaitingSnapshot;
        awaitingSnapshot = false;
        if (wasWaitingForSnapshot || (pendingAction && confirmsPendingAction(data))) pendingAction = null;
        applyState(data);
        if (hadConnectionLoss && wasWaitingForSnapshot) {
          hadConnectionLoss = false;
          setStatus('Connection restored. Current game state reloaded.');
        } else {
          setStatus('');
        }
        updateControls();
      } else if (data.type === 'error') {
        pendingAction = null;
        setStatus(data.message || 'That action could not be completed. Please try again.', 'error');
        updateControls();
      }
    };
  }
  function confirmsPendingAction(data) {
    if (pendingAction === 'start' || pendingAction === 'playAgain') return data.status === 'running';
    if (pendingAction === 'done') return ['finalizing', 'round_complete', 'session_complete'].includes(data.status);
    if (pendingAction?.type === 'next') return data.status === 'running' && data.round > pendingAction.round;
    return false;
  }
  function submitAction(action, payload, pendingLabel) {
    if (pendingAction || !canSend()) {
      setStatus(canSend() ? 'Please wait for the current action to finish.' : 'Waiting for a connection. Please try again.', 'error');
      return false;
    }
    if (!send(payload)) {
      setStatus('The action was not sent. Please try again when connected.', 'error');
      return false;
    }
    pendingAction = action;
    setStatus(pendingLabel);
    updateControls();
    return true;
  }
  function updateControls() {
    const online = canSend();
    const active = state.status === 'running';
    const pending = !!pendingAction;
    startGame.disabled = !online || pending;
    undo.disabled = !online || !active || pending;
    clear.disabled = !online || !active || pending;
    done.disabled = !online || !active || pending;
    next.disabled = !online || pending || !(state.status === 'round_complete' || state.status === 'session_complete');
    playAgain.disabled = !online || pending;
    $('#newPlayer').disabled = pending;
    reconnectOverlay.classList.toggle('hidden', !(isRoundActive() && (!connected || awaitingSnapshot)));
    canvas.style.pointerEvents = canDraw() ? 'auto' : 'none';
    if (pendingAction === 'start') startGame.textContent = 'Starting…';
    else startGame.innerHTML = 'START GAME <span aria-hidden="true">→</span>';
    done.textContent = pendingAction === 'done' ? 'Submitting…' : 'Done →';
    next.textContent = pendingAction?.type === 'next' ? 'Loading next drawing…' :
      pendingAction === 'playAgain' ? 'Starting new game…' :
      state.status === 'session_complete' ? 'See final score →' : 'Next drawing →';
    playAgain.textContent = pendingAction === 'playAgain' ? 'Starting new game…' : 'Play again';
  }
  function renderProgress(data) {
    progress.replaceChildren();
    for (let number = 1; number <= data.totalRounds; number++) {
      const item = document.createElement('span');
      const completed = data.roundResults?.[number - 1];
      item.className = `progress-step ${completed ? 'complete' : number === data.round ? 'active' : ''}`;
      item.textContent = completed ? `${number} · ${Math.round(completed.score)}%` : `${number}`;
      progress.append(item);
    }
  }
  function renderFinal(data) {
    $('#finalName').textContent = data.playerName || 'artist';
    $('#finalScore').textContent = `${data.finalScore.toFixed(1)} / 100`;
    $('#finalRank').textContent = data.leaderboardRank
      ? `Leaderboard position #${data.leaderboardRank}` : 'Saving leaderboard position…';
    const list = $('#finalRounds');
    list.replaceChildren();
    for (const result of data.roundResults || []) {
      const item = document.createElement('li');
      const word = document.createElement('span');
      const score = document.createElement('strong');
      word.textContent = result.target;
      score.textContent = `${Math.round(result.score)}%`;
      item.append(word, score);
      list.append(item);
    }
  }
  function applyState(data) {
    const wasActive = isRoundActive();
    state = data;
    clockOffset = Date.now() / 1000 - data.serverNow;
    if (data.status !== 'session_complete') entryForced = false;
    if (data.status === 'session_complete' && wasActive) finalRevealed = false;
    else if (data.status !== 'session_complete') finalRevealed = true;
    const showEntry = data.status === 'waiting' || entryForced;
    const showFinal = data.status === 'session_complete' && !entryForced && finalRevealed;
    entryScreen.classList.toggle('hidden', !showEntry);
    playScreen.classList.toggle('hidden', showEntry || showFinal);
    finalScreen.classList.toggle('hidden', !showFinal);
    if (showFinal) renderFinal(data);
    if (!showEntry && !showFinal) {
      roundLabel.textContent = `Round ${data.round} of ${data.totalRounds}`;
      target.textContent = `Draw: ${data.target?.toUpperCase() || ''}`;
      renderProgress(data);
      redraw(data.paths || []);
      next.classList.toggle('hidden', data.status === 'running' || data.status === 'finalizing');
      message.classList.toggle('hidden', data.status === 'running');
      if (data.status !== 'running') {
        $('#resultKicker').textContent = data.status === 'finalizing' ? 'Scoring your drawing…' :
          data.outcome === 'timeout' ? "Time's up!" : 'Round complete';
        $('#resultTitle').textContent = data.target?.toUpperCase() || '';
        $('#resultScore').textContent = data.status === 'finalizing'
          ? '…' : `${Math.round(data.roundScore || 0)}%`;
      }
    }
    tick();
    updateControls();
  }
  function tick() {
    let remaining = state.remaining ?? 30;
    if (state.status === 'running' && state.startedAt)
      remaining = Math.max(0, state.duration - (Date.now() / 1000 - clockOffset - state.startedAt));
    timer.textContent = Math.ceil(remaining);
    timer.classList.toggle('danger', remaining <= 5 && state.status === 'running');
  }

  function confirmNavigation(event) {
    if (!isRoundActive()) return;
    event.preventDefault();
    if (window.confirm('Your round will continue and the timer will keep running if you leave this page.')) {
      navigationApproved = true;
      location.href = event.currentTarget.href;
    }
  }
  document.querySelectorAll('.phone-nav a, .topbar .brand').forEach(link => link.addEventListener('click', confirmNavigation));
  window.addEventListener('beforeunload', (event) => {
    if (!isRoundActive() || navigationApproved) return;
    event.preventDefault();
    event.returnValue = 'Your round will continue and the timer will keep running if you leave this page.';
  });

  entryForm.addEventListener('submit', (event) => {
    event.preventDefault();
    const name = playerName.value.trim();
    if (!name) { entryError.textContent = 'Please enter a name.'; return; }
    if (submitAction('start', {type: 'start', playerName: name}, 'Starting…')) entryError.textContent = '';
  });
  next.onclick = () => {
    if (state.status === 'session_complete') {
      finalRevealed = true;
      applyState(state);
      return;
    }
    submitAction({type: 'next', round: state.round}, {type: 'next'}, 'Loading next drawing…');
  };
  undo.onclick = () => send({type: 'undo'});
  clear.onclick = () => send({type: 'clear'});
  done.onclick = () => submitAction('done', {type: 'finish_round'}, 'Submitting…');
  playAgain.onclick = () => submitAction('playAgain', {type: 'start', playerName: state.playerName}, 'Starting new game…');
  $('#newPlayer').onclick = () => {
    entryForced = true;
    playerName.value = '';
    entryError.textContent = '';
    applyState(state);
    playerName.focus();
  };
  setInterval(tick, 100);
  new ResizeObserver(resize).observe(canvas);
  connect();
})();
