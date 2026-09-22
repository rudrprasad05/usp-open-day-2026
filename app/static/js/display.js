(() => {
  const $ = (selector) => document.querySelector(selector);
  const canvas = $('#canvas'), ctx = canvas.getContext('2d');
  const connection = $('#connection'), timer = $('#timer');
  const empty = $('#emptyState'), result = $('#result');
  const predictions = $('#predictions'), model = $('#modelState');
  const round = $('#roundLabel'), footer = $('#footerState');
  let socket, reconnectTimer, state = {}, clockOffset = 0;

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
  function remember(segment) {
    state.paths ||= [];
    const lastPath = state.paths.at(-1);
    if (!lastPath || lastPath[0]?.pathId !== segment.pathId) state.paths.push([segment]);
    else lastPath.push(segment);
  }
  function renderPredictions(items) {
    if (!items.length) {
      predictions.innerHTML = '<div class="prediction top placeholder"><div class="rank">01</div><div class="guess"><strong>Watching…</strong><div class="bar"><i></i></div></div><div class="pct">—</div></div>';
      return;
    }
    // Labels come from the server's fixed configuration, not a player field.
    predictions.innerHTML = items.map((item, index) => `
      <div class="prediction ${index === 0 ? 'top' : ''}">
        <div class="rank">${String(index + 1).padStart(2, '0')}</div>
        <div class="guess"><strong>${item.label}</strong><div class="bar"><i style="width:${Math.max(2, item.confidence * 100)}%"></i></div></div>
        <div class="pct">${Math.round(item.confidence * 100)}%</div>
      </div>`).join('');
  }
  function renderBoard(entries) {
    const list = $('#miniScores');
    list.replaceChildren();
    if (!entries.length) {
      const emptyItem = document.createElement('li');
      emptyItem.className = 'board-empty';
      emptyItem.textContent = 'First scores coming soon';
      list.append(emptyItem);
      return;
    }
    for (const entry of entries.slice(0, 5)) {
      const item = document.createElement('li');
      const rankNumber = document.createElement('span');
      const name = document.createElement('span');
      const score = document.createElement('strong');
      rankNumber.textContent = String(entry.rank).padStart(2, '0');
      name.textContent = entry.playerName;
      score.textContent = entry.score.toFixed(1);
      item.append(rankNumber, name, score);
      list.append(item);
    }
  }
  function renderResult(data) {
    const active = data.status === 'running' || data.status === 'waiting';
    result.classList.toggle('hidden', active);
    result.classList.toggle('session-final', data.status === 'session_complete');
    if (active) return;
    if (data.status === 'finalizing') {
      $('#resultKicker').textContent = 'FINAL DRAWING SUBMITTED';
      $('#resultMain').textContent = 'SCORING…';
      $('#resultDetail').textContent = `ROUND ${data.round} / ${data.totalRounds}`;
      $('#resultTime').textContent = 'AI RECOGNITION IN PROGRESS';
    } else if (data.status === 'session_complete') {
      $('#resultKicker').textContent = 'SESSION COMPLETE';
      $('#resultMain').textContent = `${data.finalScore.toFixed(1)} / 100`;
      $('#resultDetail').textContent = data.playerName || '';
      $('#resultTime').textContent = data.leaderboardRank ? `LEADERBOARD #${data.leaderboardRank}` : 'SAVING SCORE';
    } else {
      $('#resultKicker').textContent = data.outcome === 'timeout' ? "TIME'S UP!" : 'FINAL RESULT';
      $('#resultMain').textContent = `${Math.round(data.roundScore || 0)}%`;
      $('#resultDetail').textContent = `${data.target?.toUpperCase() || ''} · ROUND ${data.round} / ${data.totalRounds}`;
      $('#resultTime').textContent = 'AI RECOGNITION SCORE';
    }
  }
  function applyState(data) {
    state = data;
    clockOffset = Date.now() / 1000 - data.serverNow;
    redraw(data.paths || []);
    round.textContent = data.round ? `ROUND ${data.round} / ${data.totalRounds} · ${data.status === 'finalizing' ? 'SCORING' : 'LIVE DRAWING'}` : 'LIVE DRAWING';
    empty.classList.toggle('hidden', data.status !== 'waiting');
    renderResult(data);
    renderPredictions(data.predictions || []);
    model.textContent = data.predictor?.error ? 'AI unavailable · check server log' :
      data.predictor?.ready ? 'AI model online' : 'Model warming up';
    model.classList.toggle('error', !!data.predictor?.error || !data.predictor?.ready);
    footer.textContent = data.status === 'running' ? 'AI IS GUESSING' :
      data.status === 'waiting' ? 'WAITING FOR PLAYER' :
      data.status === 'session_complete' ? 'FINAL SCORE RECORDED' :
      data.status === 'finalizing' ? 'SCORING DRAWING' : 'READY FOR NEXT ROUND';
    tick();
  }
  function tick() {
    let remaining = state.remaining ?? 30;
    if (state.status === 'running' && state.startedAt)
      remaining = Math.max(0, state.duration - (Date.now() / 1000 - clockOffset - state.startedAt));
    timer.querySelector('strong').textContent = String(Math.ceil(remaining)).padStart(2, '0');
    timer.classList.toggle('danger', remaining <= 5 && state.status === 'running');
  }
  function connect() {
    clearTimeout(reconnectTimer);
    const protocol = location.protocol === 'https:' ? 'wss' : 'ws';
    socket = new WebSocket(`${protocol}://${location.host}/ws/display`);
    socket.onopen = () => {
      connection.classList.remove('offline');
      connection.querySelector('span').textContent = 'Live';
    };
    socket.onclose = () => {
      connection.classList.add('offline');
      connection.querySelector('span').textContent = 'Reconnecting';
      reconnectTimer = setTimeout(connect, 1200);
    };
    socket.onmessage = (event) => {
      const data = JSON.parse(event.data);
      if (data.type === 'state') applyState(data);
      else if (data.type === 'stroke' && state.status === 'running') { line(data); remember(data); }
      else if (data.type === 'prediction') { state.predictions = data.predictions; renderPredictions(data.predictions || []); }
      else if (data.type === 'leaderboard') renderBoard(data.entries || []);
    };
  }
  setInterval(tick, 100);
  new ResizeObserver(resize).observe(canvas);
  connect();
})();
