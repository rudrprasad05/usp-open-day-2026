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
  const message = $('#canvasMessage');
  const next = $('#next');
  const undo = $('#undo');
  const clear = $('#clear');
  const done = $('#done');
  let socket, reconnectTimer, state = {}, drawing = false, last = null, pathId = null;
  let clockOffset = 0, entryForced = false, finalRevealed = true;

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
    if (state.status !== 'running') return;
    event.preventDefault();
    canvas.setPointerCapture(event.pointerId);
    drawing = true;
    last = point(event);
    pathId = `${Date.now()}-${event.pointerId}`;
  }
  function move(event) {
    if (!drawing || !last) return;
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

  function send(data) {
    if (socket?.readyState !== WebSocket.OPEN) return false;
    socket.send(JSON.stringify(data));
    return true;
  }
  function connect() {
    clearTimeout(reconnectTimer);
    const protocol = location.protocol === 'https:' ? 'wss' : 'ws';
    socket = new WebSocket(`${protocol}://${location.host}/ws/draw`);
    socket.onopen = () => {
      connection.classList.remove('offline');
      connection.querySelector('span').textContent = 'Connected';
    };
    socket.onclose = () => {
      connection.classList.add('offline');
      connection.querySelector('span').textContent = 'Reconnecting';
      reconnectTimer = setTimeout(connect, 1200);
    };
    socket.onmessage = (event) => {
      const data = JSON.parse(event.data);
      if (data.type === 'state') applyState(data);
      else if (data.type === 'error') entryError.textContent = data.message;
    };
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
    const wasActive = state.status === 'running' || state.status === 'finalizing';
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
      const running = data.status === 'running';
      [undo, clear, done].forEach(button => button.disabled = !running);
      next.classList.toggle('hidden', running || data.status === 'finalizing');
      next.innerHTML = data.status === 'session_complete'
        ? 'See final score <span aria-hidden="true">→</span>'
        : 'Next drawing <span aria-hidden="true">→</span>';
      message.classList.toggle('hidden', running);
      if (!running) {
        $('#resultKicker').textContent = data.status === 'finalizing' ? 'Scoring your drawing…' :
          data.outcome === 'timeout' ? "Time's up!" : 'Round complete';
        $('#resultTitle').textContent = data.target?.toUpperCase() || '';
        $('#resultScore').textContent = data.status === 'finalizing'
          ? '…' : `${Math.round(data.roundScore || 0)}%`;
      }
    }
    tick();
  }
  function tick() {
    let remaining = state.remaining ?? 30;
    if (state.status === 'running' && state.startedAt)
      remaining = Math.max(0, state.duration - (Date.now() / 1000 - clockOffset - state.startedAt));
    timer.textContent = Math.ceil(remaining);
    timer.classList.toggle('danger', remaining <= 5 && state.status === 'running');
  }

  entryForm.addEventListener('submit', (event) => {
    event.preventDefault();
    const name = playerName.value.trim();
    if (!name) { entryError.textContent = 'Please enter a name.'; return; }
    if (!send({type: 'start', playerName: name})) {
      entryError.textContent = 'Waiting for a connection. Please try again.';
      return;
    }
    entryError.textContent = '';
  });
  next.onclick = () => {
    if (state.status === 'session_complete') {
      finalRevealed = true;
      applyState(state);
    } else send({type: 'next'});
  };
  undo.onclick = () => send({type: 'undo'});
  clear.onclick = () => send({type: 'clear'});
  done.onclick = () => send({type: 'finish_round'});
  $('#playAgain').onclick = () => send({type: 'start', playerName: state.playerName});
  $('#newPlayer').onclick = () => {
    entryForced = true;
    playerName.value = '';
    entryError.textContent = '';
    applyState(state);
  };
  setInterval(tick, 100);
  new ResizeObserver(resize).observe(canvas);
  connect();
})();
