// ═══════════ PaperWise 前端逻辑 v2 ═══════════

const state = {
  sid: null,
  ws: null,
  paperDir: null,      // 当前论文目录（绝对路径字符串）或 null
  paperTitle: '',
  reportMd: '',
  topics: [],
  profile: [],
};
let recPapers = [];
let agentStatus = { step: '', tools: [] };
let agentTrace = {
  items: [],
  total: 0,
  dropped: 0,
  view: 'key',
  active: false,
  follow: true,
  nodes: new Map(),
  thoughts: 0,
  tools: 0,
  routeText: '',
  planText: '',
  nodeText: '',
  thoughtText: '',
};

function hasPaper() {
  return !!state.paperDir && typeof state.paperDir === 'string';
}

function requirePaper() {
  if (hasPaper()) return true;
  toast('请先上传一篇论文 PDF', 'err');
  const f = document.getElementById('fileUpload2');
  if (f) f.click();
  return false;
}

function setPaper(dir, title) {
  state.paperDir = dir || null;
  state.paperTitle = title || '';
  syncPaperUI();
}

function syncPaperUI() {
  const label = document.getElementById('paperLabel');
  if (label) label.textContent = hasPaper() ? `📄 ${state.paperTitle || '已加载论文'}` : '';
}

// ═══════════ 初始化 ═══════════
async function init() {
  try {
    const r = await fetch('/api/sessions', {method:'POST'});
    const d = await r.json();
    state.sid = d.session_id;
  } catch(e) { toast('服务连接失败：' + e.message, 'err'); }
  const container = document.getElementById('chatMessages');
  if (!container.querySelector('.welcome') && !container.children.length) {
    container.innerHTML = welcomeHtml();
  }
  connectWS(state.sid);
  loadSessions();
  refreshRecommendations();
  syncPaperUI();
  const timeline = document.getElementById('traceTimeline');
  if (timeline) {
    timeline.addEventListener('scroll', () => {
      const nearBottom = timeline.scrollHeight - timeline.scrollTop - timeline.clientHeight < 48;
      agentTrace.follow = nearBottom;
      const btn = document.getElementById('traceFollowBtn');
      if (btn) btn.classList.toggle('muted', !nearBottom);
    });
  }
  document.querySelectorAll('#traceTabs button').forEach(btn => {
    btn.addEventListener('click', () => setTraceView(btn.dataset.view));
  });
}

// ═══════════ WebSocket ═══════════
function connectWS(tid) {
  if (state.ws) try { state.ws.close(); } catch(e) {}
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  state.ws = new WebSocket(`${proto}//${location.host}/ws/${tid}`);
  state.ws.onopen = () => setConn(true);
  state.ws.onclose = () => setConn(false);
  state.ws.onmessage = e => {
    for (const line of e.data.split('\n')) {
      try {
        const d = JSON.parse(line);
        if (d.type === 'step') {
          addTrace('step', d.detail, d.time);
          setAgentStatus(d.detail);
        }
        else if (d.type === 'thinking') {
          addTrace('thinking', d.detail, d.time);
          if (!agentStatus.step) setAgentStatus(d.detail);
        }
        else if (d.type === 'tool_start') {
          addTrace('tool_start', d.detail, d.time);
          appendAgentTool(d.detail, false);
        }
        else if (d.type === 'tool_end') {
          addTrace('tool_end', d.detail, d.time);
          appendAgentTool(d.detail, true);
        }
        else if (['route','plan','context','agent','orchestrator','review','result','verify','kb_hit','paper_loaded','warn','status'].includes(d.type)) {
          addTrace(d.type, d.detail, d.time);
          if (d.type === 'paper_loaded' && !agentStatus.step) setAgentStatus(d.detail);
        }
        else if (d.type === 'paper_loaded') {
          state.paperTitle = d.detail || state.paperTitle;
          syncPaperUI();
        }
        else if (d.type === 'system_event') toast(`⏰ ${d.detail && d.detail.message ? d.detail.message : '主动事件'}`);
        else if (d.type === 'paper_recommendations') {
          const payload = typeof d.detail === 'string' ? JSON.parse(d.detail) : d.detail;
          if (payload && payload.papers && payload.papers.length) {
            recPapers = payload.papers;
            showRecommendationBanner(recPapers);
            toast(`🔥 为你推荐了 ${recPapers.length} 篇新论文`);
          }
        }
        else if (d.type === 'warn') toast(d.detail, 'err');
        else if (d.type === 'status') toast(d.detail);
      } catch(_) {}
    }
  };
}

function setConn(on) {
  const dot = document.getElementById('connDot');
  dot.className = 'dot ' + (on ? 'on' : 'off');
}

// ═══════════ 消息发送 ═══════════
async function sendMessage(forceMsg) {
  const input = document.getElementById('msgInput');
  const msg = (forceMsg !== undefined ? forceMsg : input.value).trim();
  if (!msg) return;
  if (forceMsg === undefined) {
    input.value = '';
    input.style.height = 'auto';
  }
  const welcome = document.querySelector('.welcome');
  if (welcome) welcome.remove();
  hideBanner();
  addMessage('user', msg);
  showTyping('思考中...');
  resetAgentTrace();
  openPanel('agentTrailPanel');
  try {
    const r = await fetch(`/api/sessions/${state.sid}/chat`, {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({message: msg})
    });
    const d = await r.json();
    if (!r.ok || !d.response) {
      throw new Error(d.detail || '服务器响应异常');
    }
    removeTyping();
    addMessage('assistant', d.response);
    finishAgentTrace(true);
  } catch(e) {
    removeTyping();
    addMessage('assistant', `抱歉，遇到了错误：${e.message}`);
    finishAgentTrace(false);
  }
}

function sendHint(msg, needsPaper) {
  if (needsPaper && !requirePaper()) return;
  sendMessage(msg);
}

// ═══════════ 文件上传 ═══════════
async function handleFile(input) {
  const file = input.files[0];
  if (!file || !file.name.toLowerCase().endsWith('.pdf')) {
    if (file) toast('仅支持 PDF 文件', 'err');
    return;
  }
  addMessage('user', `上传了：${file.name}`);
  showTyping('正在解析论文...');
  resetAgentTrace();
  const fd = new FormData(); fd.append('file', file);
  try {
    const r = await fetch(`/api/sessions/${state.sid}/upload`, {method:'POST', body:fd});
    const d = await r.json();
    removeTyping();
    setPaper(d.paper_dir || null, file.name);
    addMessage('assistant', d.response);
    finishAgentTrace(true);
  } catch(e) {
    removeTyping();
    addMessage('assistant', `解析失败：${e.message}`);
    finishAgentTrace(false);
  }
  input.value = '';
}

// ═══════════ 消息渲染 ═══════════
function escapeHtml(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

function renderMarkdown(text) {
  text = text || '';
  let html = escapeHtml(text)
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/\*([^*]+)\*/g, '<em>$1</em>')
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>')
    .replace(/\n\n/g, '</p><p>')
    .replace(/\n/g, '<br>')
    .replace(/\$\$(.+?)\$\$/g, '<code>$1</code>')
    .replace(/^### (.+)$/gm, '<h3>$1</h3>')
    .replace(/^## (.+)$/gm, '<h2>$1</h2>')
    .replace(/^\- (.+)/gm, '<br>• $1')
    .replace(/^• (.+)/gm, '<br>• $1');
  return '<p>' + html + '</p>';
}

function addMessage(role, text) {
  text = text || '';
  const container = document.getElementById('chatMessages');
  const div = document.createElement('div');
  div.className = `msg ${role}`;
  const avatarImg = role === 'user'
    ? '<img src="/static/assets/avatar-user.svg" alt="user">'
    : '<img src="/static/assets/avatar-bot.svg" alt="bot">';
  const time = new Date().toLocaleTimeString('zh-CN', {hour:'2-digit',minute:'2-digit'});
  div.innerHTML = `<div class="avatar">${avatarImg}</div>
    <div class="msg-main"><div class="bubble">${renderMarkdown(text)}</div>
    <div class="time">${time}</div></div>`;
  container.appendChild(div);
  container.scrollTop = container.scrollHeight;
  if (role === 'assistant' && (text.includes('## ') || text.includes('### '))) {
    state.reportMd = text;
  }
}

function showTyping(msg) {
  removeTyping();
  agentStatus = { step: '', tools: [] };
  const container = document.getElementById('chatMessages');
  const div = document.createElement('div');
  div.className = 'msg assistant typing';
  div.id = 'typingIndicator';
  div.innerHTML = `<div class="avatar"><img src="/static/assets/avatar-bot.svg" alt="bot"></div>
    <div class="msg-main"><div class="bubble"><span></span><span></span><span></span>
    <div class="agent-status"></div></div>
    <div class="time">${escapeHtml(msg || '思考中...')}</div></div>`;
  container.appendChild(div);
  container.scrollTop = container.scrollHeight;
}

function removeTyping() {
  const el = document.getElementById('typingIndicator');
  if (el) el.remove();
}

function setAgentStatus(text) {
  agentStatus.step = text || '';
  renderAgentStatus();
}

function appendAgentTool(name, done) {
  const clean = String(name || '').replace(/\s*完成\s*$/, '').trim();
  if (!clean) return;
  const existing = agentStatus.tools.find(t => t.name === clean);
  if (existing) existing.done = done;
  else agentStatus.tools.push({ name: clean, done: !!done });
  if (agentStatus.tools.length > 5) agentStatus.tools = agentStatus.tools.slice(-5);
  renderAgentStatus();
}

function renderAgentStatus() {
  const el = document.getElementById('typingIndicator');
  if (!el) return;
  const box = el.querySelector('.agent-status');
  if (!box) return;
  let html = '';
  if (agentStatus.step) html += `<div class="as-step">${escapeHtml(agentStatus.step)}</div>`;
  agentStatus.tools.forEach(t => {
    html += `<div class="as-tool${t.done ? ' done' : ''}">${t.done ? '✓' : '·'} ${escapeHtml(t.name)}</div>`;
  });
  box.innerHTML = html;
  const c = document.getElementById('chatMessages');
  if (c) c.scrollTop = c.scrollHeight;
}

// ═══════════ Agent 编排轨迹 ═══════════
const TRACE_META = {
  route: { label:'路由', icon:'alt_route' },
  plan: { label:'规划', icon:'account_tree' },
  context: { label:'上下文', icon:'layers' },
  agent: { label:'子代理', icon:'smart_toy' },
  orchestrator: { label:'编排', icon:'schema' },
  step: { label:'节点', icon:'check_circle' },
  thinking: { label:'思考', icon:'psychology' },
  tool_start: { label:'工具', icon:'construction' },
  tool_end: { label:'工具完成', icon:'task_alt' },
  verify: { label:'校验', icon:'verified' },
  review: { label:'评审', icon:'rate_review' },
  result: { label:'结果', icon:'flag' },
  kb_hit: { label:'索引', icon:'storage' },
  paper_loaded: { label:'论文', icon:'description' },
  warn: { label:'警告', icon:'warning' },
  status: { label:'状态', icon:'info' },
};

function clipTraceText(text, length = 620) {
  const value = String(text || '').replace(/\s+/g, ' ').trim();
  return value.length > length ? `${value.slice(0, length)}...` : value;
}

function resetAgentTrace() {
  agentTrace = {
    items: [], total: 0, dropped: 0, view: agentTrace.view || 'key',
    active: true, follow: true, nodes: new Map(), thoughts: 0, tools: 0,
    routeText: '', planText: '', nodeText: '', thoughtText: '',
  };
  renderAgentRibbon();
  setTraceLive('运行中', 'running');
  renderTrace();
}

function finishAgentTrace(ok = true) {
  if (!agentTrace.active) return;
  agentTrace.active = false;
  setTraceLive(ok ? '已完成' : '有异常', ok ? 'done' : 'error');
  renderAgentRibbon();
  renderTrace();
}

function setTraceLive(text, state = 'running') {
  const el = document.getElementById('traceLive');
  if (el) { el.textContent = text; el.dataset.state = state; }
}

function upsertTraceNode(name, status) {
  if (!name || agentTrace.nodes.has(name) && ['done','failed'].includes(agentTrace.nodes.get(name))) return;
  agentTrace.nodes.set(name, status);
}

function addTrace(type, detail, time) {
  const text = String(detail || '').trim();
  if (!text) return;

  if (type === 'step') {
    const match = text.match(/^([A-Za-z0-9_]+):\s*(started|done|failed|replan|retry.*)$/i);
    if (match) upsertTraceNode(match[1], match[2].toLowerCase());
  }
  if (type === 'plan') {
    const names = text.split('→').map(s => s.trim()).filter(Boolean);
    names.forEach(name => upsertTraceNode(name, 'planned'));
  }
  if (type === 'thinking') agentTrace.thoughts += 1;
  if (type.startsWith('tool_')) agentTrace.tools += 1;
  if (type === 'route') agentTrace.routeText = text;
  if (type === 'orchestrator') agentTrace.routeText = text;
  if (type === 'plan') agentTrace.planText = text;
  if (type === 'step') agentTrace.nodeText = text;
  if (type === 'thinking' && text.length > 12) agentTrace.thoughtText = clipTraceText(text, 180);

  const last = agentTrace.items[agentTrace.items.length - 1];
  if (type === 'thinking' && last && last.type === 'thinking' && Date.now() - last.ms < 1500) {
    last.text = clipTraceText(`${last.text} ${text}`, 720);
    last.time = time || last.time;
    last.segments = (last.segments || 1) + 1;
  } else {
    agentTrace.items.push({
      type, text: clipTraceText(text, type === 'thinking' ? 720 : 360),
      time: time || new Date().toLocaleTimeString('zh-CN', {hour12:false}),
      ms: Date.now(), segments: 1,
    });
  }

  agentTrace.total += 1;
  if (agentTrace.items.length > 180) {
    agentTrace.dropped += agentTrace.items.length - 180;
    agentTrace.items = agentTrace.items.slice(-180);
  }
  renderTrace();
  renderAgentRibbon();
}

function isKeyTrace(item) {
  return ['route','plan','agent','orchestrator','step','thinking','tool_end','verify','review','result','warn'].includes(item.type);
}

function setTraceView(view) {
  agentTrace.view = view;
  document.querySelectorAll('#traceTabs button').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.view === view);
  });
  renderTrace();
}

function resetTraceFollow() {
  agentTrace.follow = true;
  renderTrace();
}

function showAgentTrailPanel() {
  openPanel('agentTrailPanel');
  renderTrace();
}

function closeAgentTrailPanel() {
  document.getElementById('agentTrailPanel').classList.remove('show');
  document.getElementById('panelOverlay').classList.remove('show');
}

function renderTraceMetrics() {
  const total = agentTrace.nodes.size;
  let done = 0, failed = 0;
  for (const status of agentTrace.nodes.values()) {
    if (status === 'done') done += 1;
    if (status === 'failed') failed += 1;
  }
  return [
    { label:'编排节点', value:`${done}/${total}`, tone: failed ? 'error' : 'ok' },
    { label:'思考片段', value: agentTrace.thoughts, tone:'info' },
    { label:'工具', value: agentTrace.tools, tone:'info' },
    { label:'缓冲', value:`${agentTrace.items.length}/${agentTrace.total}`, tone:'muted' },
  ];
}

function renderTraceChain() {
  if (!agentTrace.nodes.size) return '<span class="trace-empty">等待编排计划...</span>';
  return [...agentTrace.nodes.entries()].map(([name, status]) =>
    `<span class="trace-node ${status}">${escapeHtml(name)}</span>`
  ).join('<span class="trace-arrow">→</span>');
}

function renderAgentRibbon() {
  const ribbon = document.getElementById('agentRibbon');
  if (!ribbon) return;
  ribbon.hidden = false;
  const route = document.getElementById('ribbonRoute');
  const node = document.getElementById('ribbonNode');
  const thought = document.getElementById('ribbonThought');
  route.textContent = agentTrace.routeText || '等待路由判定...';
  node.textContent = agentTrace.planText
    ? `计划：${agentTrace.planText}${agentTrace.nodeText ? ` · 当前：${agentTrace.nodeText}` : ''}`
    : (agentTrace.nodeText || '等待计划...');
  thought.textContent = agentTrace.thoughtText ? `思考：${agentTrace.thoughtText}` : '';
  thought.hidden = !agentTrace.thoughtText;
}

function renderTrace() {
  const metricsEl = document.getElementById('traceMetrics');
  const chainEl = document.getElementById('traceChain');
  const timeline = document.getElementById('traceTimeline');
  if (!metricsEl || !chainEl || !timeline) return;

  metricsEl.innerHTML = renderTraceMetrics().map(m =>
    `<span class="trace-metric ${m.tone}"><i>${m.label}</i><b>${escapeHtml(String(m.value))}</b></span>`
  ).join('');
  chainEl.innerHTML = renderTraceChain();

  const shown = agentTrace.items.filter(item => agentTrace.view === 'all' || isKeyTrace(item));
  if (!shown.length) {
    timeline.innerHTML = '<div class="trace-empty">暂无轨迹事件。开始一次报告或 PPT 生成后，这里会显示路由、节点、子代理与思考片段。</div>';
    return;
  }

  timeline.innerHTML = shown.map(item => {
    const meta = TRACE_META[item.type] || { label:item.type, icon:'circle' };
    const segment = item.segments > 1 ? `<i>· ${item.segments} 段</i>` : '';
    return `<div class="trace-item ${item.type}">
      <div class="trace-row">
        <span class="material-symbols-outlined">${meta.icon}</span>
        <b>${meta.label}</b>
        <span class="trace-time">${escapeHtml(item.time)} ${segment}</span>
      </div>
      <div class="trace-text">${escapeHtml(item.text).replace(/\n/g, '<br>')}</div>
    </div>`;
  }).join('');

  const note = document.createElement('div');
  note.className = 'trace-buffer-note';
  note.textContent = agentTrace.dropped ? `为保持流畅，已折叠最早 ${agentTrace.dropped} 条事件` : '滚动缓冲区：最多保留 180 条事件';
  timeline.appendChild(note);

  if (agentTrace.follow) timeline.scrollTop = timeline.scrollHeight;
}

// ═══════════ 推荐论文 ═══════════
async function refreshRecommendations() {
  await refreshInterests();
  try {
    const r = await fetch('/api/recommend?limit=5');
    const d = await r.json();
    if (d.papers && d.papers.length) {
      recPapers = d.papers;
      showRecommendationBanner(d.papers);
    }
  } catch(_) {}
}

async function refreshInterests() {
  try {
    const r = await fetch('/api/interests');
    const d = await r.json();
    state.profile = d.profile || [];
    state.topics = state.profile.map(p => p.topic);
  } catch(_) {
    state.profile = [];
    state.topics = [];
  }
}

function showRecommendationBanner(papers) {
  const container = document.getElementById('chatMessages');
  const old = document.getElementById('recBanner');
  if (old) old.remove();
  const banner = document.createElement('div');
  banner.id = 'recBanner';
  banner.className = 'rec-banner';
  banner.innerHTML = `
    <div class="rec-head"><span class="material-symbols-outlined">auto_awesome</span> 主动推荐 · 基于你的研究兴趣自动推荐
      <span style="margin-left:auto;display:flex;gap:6px">
        <button class="btn btn-sm btn-ghost" onclick="toggleRecommendPanel()">查看全部</button>
        <button class="btn btn-sm btn-ghost" onclick="hideBanner()"><span class="material-symbols-outlined">close</span></button>
      </span></div>
    <div class="rec-list">${papers.slice(0, 5).map(recCardHtml).join('')}</div>`;
  container.insertBefore(banner, container.firstChild);
  container.scrollTop = container.scrollHeight;
}

function recCardHtml(p) {
  const score = Math.round((p.score || 0) * 100);
  return `<div class="rec-card">
    <div class="rc-title">${escapeHtml(p.title || '')}</div>
    <div class="rc-meta">${escapeHtml((p.authors || []).slice(0, 3).join(', '))} · ${p.published || ''}</div>
    <div class="rc-score">匹配度 ${score}% · ${escapeHtml((p.matched || []).join('、') || '相关')}</div>
    <div class="rc-actions">
      <button class="btn btn-sm btn-primary" onclick="ingestRecommended('${escapeHtml(p.url || '')}')">解读这篇</button>
      <a class="btn btn-sm" href="${escapeHtml(p.url || '#')}" target="_blank" rel="noopener">原文</a>
    </div>
  </div>`;
}

function hideBanner() {
  const old = document.getElementById('recBanner');
  if (old) old.remove();
}

async function ingestRecommended(url) {
  if (!url) return;
  showTyping('正在获取并解析 arXiv 论文...');
  try {
    const r = await fetch(`/api/sessions/${state.sid}/arxiv`, {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({url})
    });
    const d = await r.json();
    if (!r.ok || !d.response) {
      throw new Error(d.detail || 'arXiv 论文获取失败，请稍后重试');
    }
    removeTyping();
    setPaper(d.paper_dir || null, d.arxiv_id || 'arXiv 论文');
    addMessage('assistant', d.response);
    const msg = `请针对我的研究方向重点解读这篇论文：${d.arxiv_id}。先给出一句话总结，再展开核心方法与实验。`;
    sendMessage(msg);
  } catch(e) {
    removeTyping();
    addMessage('assistant', `获取论文失败：${e.message}`);
  }
}

// ═══════════ 推荐面板 ═══════════
function toggleRecommendPanel() {
  const panel = document.getElementById('recommendPanel');
  if (panel.classList.contains('show')) {
    hideRecommendPanel();
    return;
  }
  openPanel('recommendPanel');
  renderRecommendPanel();
}

function renderRecommendPanel() {
  const body = document.getElementById('recommendBody');
  const topicsEl = document.getElementById('recoTopics');
  const srcLabel = {declared:'声明', paper:'论文', conversation:'对话'};
  if (topicsEl) {
    topicsEl.innerHTML = state.profile.length
      ? `<div class="topics-label">从记忆自动学习的兴趣 · 无需手动填写</div>
         <div class="topics-chips">${state.profile.map(p => {
           const src = (p.sources || []).map(s => srcLabel[s] || s).join('+');
           return `<span class="tag" title="来源：${escapeHtml(src)}">${escapeHtml(p.topic)}<i>·${escapeHtml(src)}</i></span>`;
         }).join('')}</div>`
      : '<div class="topics-label">我会从你上传的论文和对话中自动学习研究兴趣，然后主动为你推荐。</div>';
  }
  if (!recPapers.length) {
    body.innerHTML = '<div class="empty-state"><span class="material-symbols-outlined">auto_awesome</span><p>暂无推荐</p><span>上传论文或和我聊聊你的关注点，我会自动学习兴趣并主动推荐。</span></div>';
    return;
  }
  body.innerHTML = recPapers.map(p => `
    <div class="mem-card">
      <div class="cat">匹配度 ${Math.round((p.score||0)*100)}% <span class="tag">${escapeHtml((p.matched||[]).join('、') || '相关')}</span></div>
      <div class="data"><strong>${escapeHtml(p.title || '')}</strong></div>
      <div class="back">${escapeHtml((p.summary || '').slice(0, 180))}...</div>
      <div style="display:flex;gap:6px;margin-top:8px">
        <button class="btn btn-sm btn-primary" onclick="ingestRecommended('${escapeHtml(p.url || '')}')">解读这篇</button>
        <a class="btn btn-sm" href="${escapeHtml(p.url || '#')}" target="_blank" rel="noopener">arXiv 原文</a>
      </div>
    </div>`).join('');
}

function hideRecommendPanel() {
  document.getElementById('recommendPanel').classList.remove('show');
  document.getElementById('panelOverlay').classList.remove('show');
}

// ═══════════ PPT 生成 ═══════════
async function generatePPTX() {
  if (!requirePaper()) return;
  const target = `${state.paperDir}\\presentation\\slides.pptx`;
  const msg = '请加载 nature-paper2ppt 技能，严格按其完整流程为当前论文生成学术汇报 PPT。'
    + '最终用 code_interpreter 生成 .pptx，并保存到绝对路径：' + target + '。'
    + '完成后告诉我文件是否保存成功。';
  await sendMessage(msg);
  const ok = await fileExists(target);
  if (ok) {
    triggerDownload(target);
    toast('PPT 已生成并开始下载', 'ok');
  } else {
    toast('PPT 尚未生成完成，请看对话里的说明', 'err');
  }
}

async function fileExists(path) {
  try {
    const r = await fetch(`/api/exists?path=${encodeURIComponent(path)}`);
    const d = await r.json();
    return !!d.exists;
  } catch(e) { return false; }
}

function triggerDownload(path) {
  const a = document.createElement('a');
  a.href = `/api/download?path=${encodeURIComponent(path)}`;
  a.download = '';
  document.body.appendChild(a);
  a.click();
  a.remove();
}

// ═══════════ 面板控制 ═══════════
function closeOtherPanels(keep) {
  for (const id of ['reportPanel', 'memoryPanel', 'editorPanel', 'agentTrailPanel']) {
    if (id !== keep) document.getElementById(id).classList.remove('show');
  }
  if (keep !== 'recommendPanel') document.getElementById('recommendPanel').classList.remove('show');
  document.getElementById('panelOverlay').classList.remove('show');
}

function openPanel(id) {
  closeOtherPanels(id);
  const panel = document.getElementById(id);
  panel.classList.add('show');
  document.getElementById('panelOverlay').classList.add('show');
}

function toggleReport() {
  const p = document.getElementById('reportPanel');
  if (p.classList.contains('show')) { p.classList.remove('show'); document.getElementById('panelOverlay').classList.remove('show'); return; }
  openPanel('reportPanel');
  const content = document.getElementById('reportContent');
  if (state.reportMd) {
    content.innerHTML = renderMarkdown(state.reportMd);
  } else {
    content.innerHTML = '<div class="empty-state"><span class="material-symbols-outlined">description</span><p>还没有生成报告</p><span>上传论文后，在对话中让我「生成报告」即可。</span></div>';
  }
}

async function toggleMemory() {
  const p = document.getElementById('memoryPanel');
  if (p.classList.contains('show')) { p.classList.remove('show'); document.getElementById('panelOverlay').classList.remove('show'); return; }
  openPanel('memoryPanel');
  await refreshMemory();
}

async function refreshMemory() {
  const list = document.getElementById('memoryList');
  try {
    const r = await fetch('/api/memory');
    const d = await r.json();
    const cards = d.cards || [];
    if (!cards.length) {
      list.innerHTML = '<div class="empty-state"><span class="material-symbols-outlined">psychology</span><p>暂无记忆</p></div>';
      return;
    }
    list.innerHTML = '';
    for (const c of cards) {
      const div = document.createElement('div');
      div.className = 'mem-card';
      const dataStr = Object.entries(c.data || {}).map(([k, v]) => `${k}: ${v}`).join('<br>');
      div.innerHTML = `<div class="cat">${c.category} · ${Math.round((c.confidence || 0) * 100)}% 置信度
        <button class="btn btn-sm danger" onclick="deleteMemoryCard('${c.card_id}')">删除</button></div>
        <div class="data">${escapeHtml(dataStr).replace(/\n/g, '<br>')}</div>
        <div class="back">${escapeHtml(c.backstory || '')}</div>`;
      list.appendChild(div);
    }
  } catch(_) { list.innerHTML = '<div class="empty-state"><p>加载失败</p></div>'; }
}

async function deleteMemoryCard(id) {
  await fetch(`/api/memory/${id}`, {method:'DELETE'});
  refreshMemory();
}

// ═══════════ 章节编辑 ═══════════
async function toggleEditor() {
  const p = document.getElementById('editorPanel');
  if (p.classList.contains('show')) { p.classList.remove('show'); document.getElementById('panelOverlay').classList.remove('show'); return; }
  if (!requirePaper()) return;
  openPanel('editorPanel');
  await loadSections();
}

async function loadSections() {
  try {
    const r = await fetch(`/api/paper/sections?paper_dir=${encodeURIComponent(state.paperDir)}`);
    const d = await r.json();
    window.__sections = d.sections || {};
    const sel = document.getElementById('sectionSelect');
    sel.innerHTML = '';
    const names = {overview:'概览', motivation:'动机', methodology:'方法', experiments:'实验',
                   critical_analysis:'批判分析', related_work:'相关工作', conclusion:'结论'};
    for (const sec of Object.keys(window.__sections)) {
      const opt = document.createElement('option');
      opt.value = sec; opt.textContent = names[sec] || sec;
      sel.appendChild(opt);
    }
    if (sel.options.length) selectSection();
    else document.getElementById('sectionText').value = '（暂无章节内容，请先生成报告）';
  } catch(_) { toast('加载章节失败', 'err'); }
}

function selectSection() {
  const sec = document.getElementById('sectionSelect').value;
  document.getElementById('sectionText').value =
    (window.__sections && window.__sections[sec]) || '';
}

async function saveSection(regenerate) {
  const sec = document.getElementById('sectionSelect').value;
  const content = document.getElementById('sectionText').value;
  if (!sec) { toast('请先选择章节', 'err'); return; }
  try {
    const r = await fetch('/api/paper/sections', {method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({paper_dir: state.paperDir, section: sec, content})});
    if (!r.ok) { toast('保存失败', 'err'); return; }
    toast('章节已保存', 'ok');
    if (regenerate) generatePPTX();
  } catch(_) { toast('保存失败', 'err'); }
}

// ═══════════ 会话管理 ═══════════
async function newSession() {
  try {
    const r = await fetch('/api/sessions', {method:'POST'});
    const d = await r.json();
    state.sid = d.session_id;
  } catch(_) {}
  state.paperDir = null; state.paperTitle = ''; state.reportMd = ''; recPapers = [];
  document.querySelectorAll('.session-item').forEach(el => el.classList.remove('active'));
  const cur = document.querySelector('.session-item[data-sid="current"]');
  if (cur) cur.classList.add('active');
  document.getElementById('chatMessages').innerHTML = welcomeHtml();
  document.getElementById('paperLabel').textContent = '';
  hideBanner();
  hideRecommendPanel();
  closeOtherPanels(null);
  connectWS(state.sid);
  refreshRecommendations();
  syncPaperUI();
}

function welcomeHtml() {
  return `<div class="welcome">
    <div class="hero-logo"><img src="/static/assets/logo.svg" alt="PaperWise"></div>
    <h2>你好，我是 PaperWise</h2>
    <p>你的 AI 学术论文研究助手 · 深度解读 · PPT 生成 · 主动推荐</p>
    <div class="hints">
      <div class="hint" onclick="sendHint('帮我分析一篇论文', true)"><span class="material-symbols-outlined">search</span>分析论文</div>
      <div class="hint" onclick="sendHint('生成一份完整的解读报告', true)"><span class="material-symbols-outlined">description</span>生成报告</div>
      <div class="hint" onclick="sendHint('这篇论文的核心创新是什么？', true)"><span class="material-symbols-outlined">lightbulb</span>提取创新点</div>
      <div class="hint" onclick="sendHint('实验设计有什么不足？', true)"><span class="material-symbols-outlined">troubleshoot</span>批判性分析</div>
      <div class="hint" onclick="sendHint('用简单的话解释这篇论文的方法', true)"><span class="material-symbols-outlined">translate</span>通俗解释</div>
      <div class="hint" onclick="toggleRecommendPanel()"><span class="material-symbols-outlined">auto_awesome</span>论文推荐</div>
    </div>
    <div class="reco-passive">💡 无需填写研究方向，我会从你上传的论文和对话中自动学习兴趣，并主动为你推荐新论文</div>
    <div class="upload-cta">
      <label for="fileUpload" class="btn btn-primary btn-lg" style="cursor:pointer">📤 上传论文 PDF</label>
      <input type="file" id="fileUpload" accept=".pdf" hidden onchange="handleFile(this)">
      <div class="upload-hint">支持 PDF，自动解析并进入对话</div>
    </div>
  </div>`;
}

function switchSession(s) {
  if (s === 'current') {
    document.querySelectorAll('.session-item').forEach(el => el.classList.remove('active'));
    const cur = document.querySelector('.session-item[data-sid="current"]');
    if (cur) cur.classList.add('active');
  }
}

async function loadSessions() {
  try {
    const r = await fetch('/api/sessions');
    const d = await r.json();
    const list = document.getElementById('sessionList');
    for (const s of (d.sessions || [])) {
      if (s.session_id === state.sid) continue;
      const div = document.createElement('div');
      div.className = 'session-item';
      div.dataset.sid = s.session_id;
      const title = (s.current_paper || s.topic || s.session_id).slice(0, 16);
      const meta = (s.last_active || '').slice(0, 16);
      div.innerHTML = `<span class="icon">💬</span><div class="meta"><div class="title">${escapeHtml(title)}</div><div class="sub">${meta}</div></div>`;
      div.onclick = () => resumeSession(s.session_id, title, s.current_paper);
      list.insertBefore(div, list.lastElementChild);
    }
  } catch(_) {}
}

async function resumeSession(newSid, title, currentPaper) {
  state.sid = newSid;
  state.reportMd = '';
  setPaper(currentPaper || null, title);
  document.querySelectorAll('.session-item').forEach(el => el.classList.remove('active'));
  const target = document.querySelector(`.session-item[data-sid="${newSid}"]`);
  if (target) target.classList.add('active');
  document.getElementById('chatMessages').innerHTML = '';
  closeOtherPanels(null);
  hideBanner();
  connectWS(state.sid);
  showTyping('恢复会话...');
  try {
    const r = await fetch(`/api/sessions/${state.sid}/history`);
    const d = await r.json();
    removeTyping();
    const msgs = d.messages || [];
    for (const m of msgs) addMessage(m.role, m.content);
    if (!msgs.length) {
      document.getElementById('chatMessages').innerHTML = welcomeHtml();
    }
  } catch(e) { removeTyping(); }
}

// ═══════════ Toast ═══════════
function toast(msg, type) {
  const el = document.createElement('div');
  el.className = 'toast ' + (type || '');
  el.textContent = msg;
  document.getElementById('toasts').appendChild(el);
  setTimeout(() => el.remove(), 3600);
}

// 输入框自适应高度
document.addEventListener('DOMContentLoaded', () => {
  const input = document.getElementById('msgInput');
  if (input) {
    input.addEventListener('input', function() {
      this.style.height = 'auto';
      this.style.height = Math.min(this.scrollHeight, 150) + 'px';
    });
  }
});

init();
