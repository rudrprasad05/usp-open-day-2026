(() => {
  const list = document.querySelector('#leaderboardEntries');
  const connection = document.querySelector('#connection');
  const updated = document.querySelector('#updated');
  let socket, reconnectTimer;

  function render(entries) {
    list.replaceChildren();
    if (!entries.length) {
      const item = document.createElement('li');
      item.className = 'empty';
      item.textContent = 'First scores coming soon';
      list.append(item);
      return;
    }
    for (const entry of entries) {
      const item = document.createElement('li');
      item.className = `entry rank-${entry.rank}`;
      const rank = document.createElement('span');
      const name = document.createElement('span');
      const score = document.createElement('strong');
      rank.textContent = String(entry.rank).padStart(2, '0');
      name.textContent = entry.playerName;
      score.textContent = entry.score.toFixed(1);
      item.append(rank, name, score);
      list.append(item);
    }
    updated.textContent = `${entries.length} TOP SCORES · LIVE`;
  }
  async function loadFallback() {
    try {
      const response = await fetch('/api/leaderboard?limit=20');
      if (response.ok) render((await response.json()).entries);
    } catch { /* WebSocket reconnection continues below. */ }
  }
  function connect() {
    clearTimeout(reconnectTimer);
    const protocol = location.protocol === 'https:' ? 'wss' : 'ws';
    socket = new WebSocket(`${protocol}://${location.host}/ws/leaderboard`);
    socket.onopen = () => {
      connection.classList.remove('offline');
      connection.querySelector('span').textContent = 'Live';
    };
    socket.onclose = () => {
      connection.classList.add('offline');
      connection.querySelector('span').textContent = 'Reconnecting';
      loadFallback();
      reconnectTimer = setTimeout(connect, 1200);
    };
    socket.onmessage = (event) => {
      const message = JSON.parse(event.data);
      if (message.type === 'leaderboard') render(message.entries || []);
    };
  }
  loadFallback();
  connect();
})();
