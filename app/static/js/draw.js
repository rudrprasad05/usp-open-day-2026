(() => {
  const canvas = document.querySelector('#canvas');
  const ctx = canvas.getContext('2d');
  const connection = document.querySelector('#connection');
  const timer = document.querySelector('#timer');
  const target = document.querySelector('#target');
  const message = document.querySelector('#canvasMessage');
  const start = document.querySelector('#start');
  const next = document.querySelector('#next');
  const undo = document.querySelector('#undo');
  const clear = document.querySelector('#clear');
  const done = document.querySelector('#done');
  let socket, reconnectTimer, state = {}, drawing = false, last = null, pathId = null;
  let clockOffset = 0;

  function resize() {
    const rect = canvas.getBoundingClientRect();
    const ratio = Math.min(devicePixelRatio || 1, 2);
    canvas.width = Math.round(rect.width * ratio); canvas.height = Math.round(rect.height * ratio);
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0); ctx.lineCap = 'round'; ctx.lineJoin = 'round';
    redraw(state.paths || []);
  }
  function line(s) {
    const r = canvas.getBoundingClientRect();
    ctx.strokeStyle = '#090b10'; ctx.lineWidth = Math.max(2, s.width * Math.min(r.width, r.height));
    ctx.beginPath(); ctx.moveTo(s.x1*r.width, s.y1*r.height); ctx.lineTo(s.x2*r.width, s.y2*r.height); ctx.stroke();
  }
  function redraw(paths) { const r=canvas.getBoundingClientRect(); ctx.clearRect(0,0,r.width,r.height); paths.forEach(p => p.forEach(line)); }
  function point(e) { const r=canvas.getBoundingClientRect(); return {x:(e.clientX-r.left)/r.width,y:(e.clientY-r.top)/r.height}; }
  function remember(seg) { state.paths ||= []; const lastPath=state.paths.at(-1); if(!lastPath||lastPath[0]?.pathId!==seg.pathId) state.paths.push([seg]); else lastPath.push(seg); }
  function begin(e) { if(state.status!=='running') return; e.preventDefault(); canvas.setPointerCapture(e.pointerId); drawing=true; last=point(e); pathId=`${Date.now()}-${e.pointerId}`; }
  function move(e) { if(!drawing||!last) return; e.preventDefault(); const p=point(e); const seg={type:'stroke',pathId,x1:last.x,y1:last.y,x2:p.x,y2:p.y,width:.014}; line(seg); remember(seg); send(seg); last=p; }
  function end(e) { if(drawing) e.preventDefault(); drawing=false; last=null; }
  canvas.addEventListener('pointerdown',begin); canvas.addEventListener('pointermove',move); canvas.addEventListener('pointerup',end); canvas.addEventListener('pointercancel',end);

  function send(data) { if(socket?.readyState===WebSocket.OPEN) socket.send(JSON.stringify(data)); }
  function connect() {
    clearTimeout(reconnectTimer); const protocol=location.protocol==='https:'?'wss':'ws'; socket=new WebSocket(`${protocol}://${location.host}/ws/draw`);
    socket.onopen=()=>{connection.classList.remove('offline');connection.querySelector('span').textContent='Connected'};
    socket.onclose=()=>{connection.classList.add('offline');connection.querySelector('span').textContent='Reconnecting';reconnectTimer=setTimeout(connect,1200)};
    socket.onmessage=e=>{const data=JSON.parse(e.data);if(data.type==='state') applyState(data)};
  }
  function applyState(data) {
    state=data; clockOffset=Date.now()/1000-data.serverNow; redraw(data.paths||[]);
    const running=data.status==='running'; target.textContent=running?`Draw: ${data.target}`:(data.status==='waiting'?'Ready?':data.target?.toUpperCase()||'Finished');
    [undo,clear,done].forEach(b=>b.disabled=!running); next.classList.toggle('hidden',running||data.status==='waiting');
    if(running){message.classList.add('hidden');start.hidden=true}
    else if(data.status==='waiting'){message.classList.remove('hidden');message.innerHTML='<strong>Ready to draw?</strong><span>The word only appears on this screen.</span>';message.append(start);start.hidden=false}
    else {message.classList.remove('hidden'); const won=data.status==='won'; message.innerHTML=`<strong>${won?'AI got it!':data.outcome==='timeout'?"Time’s up!":'Round finished'}</strong><span>${won?`${data.target.toUpperCase()} in ${data.elapsed.toFixed(1)} seconds`:`The answer was ${data.target.toUpperCase()}`}</span>`}
    tick();
  }
  function tick(){let remaining=state.remaining??30;if(state.status==='running'&&state.startedAt)remaining=Math.max(0,state.duration-(Date.now()/1000-clockOffset-state.startedAt));timer.textContent=Math.ceil(remaining);timer.classList.toggle('danger',remaining<=5&&state.status==='running')}
  setInterval(tick,100); start.onclick=()=>send({type:'start'}); next.onclick=()=>send({type:'next'}); undo.onclick=()=>send({type:'undo'}); clear.onclick=()=>send({type:'clear'}); done.onclick=()=>send({type:'done'});
  new ResizeObserver(resize).observe(canvas); connect();
})();
