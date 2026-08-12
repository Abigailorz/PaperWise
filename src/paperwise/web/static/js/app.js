// ═══════════ PaperWise 前端逻辑 ═══════════

let sid = null, ws = null, paperDir = null, reportMd = '';
let recPapers = [];

// ═══════════ 初始化 ═══════════
async function init() {
  try {
    const r = await fetch('/api/sessions', {method:'POST'});
    const d = await r.json();
    sid = d.session_id;
  } catch(e) { toast('服务连接失败：' + e.message, 'err'); }
  // 首次加载渲染欢迎页
  const container = document.getElementById('chatMessages');
  if (!container.querySelector('.welcome') && !container.children.length) {
    container.innerHTML = welcomeHtml();
  }
  connectWS(sid);
  loadSessions();
  refreshRecommendations();
}

// ═══════════ WebSocket ═══════════
function connectWS(tid) {
  if (ws) try { ws.close(); } catch(e) {}
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  ws = new WebSocket(`${proto}//${location.host}/ws/${tid}`);
  ws.onopen = () => setConn(true);
  ws.onclose = () => setConn(false);
  ws.onmessage = e => {
    for (const line of e.data.split('\n')) {
      try {
        const d = JSON.parse(line);
        if (d.type === 'thinking') showTyping('思考中...');
        else if (d.type === 'tool_start') showTyping(`🔧 调用工具：${d.detail}`);
        else if (d.type === 'tool_end') showTyping(`  ✓ ${d.detail}`);
        else if (d.type === 'paper_loaded') {
          paperDir = true;
          document.getElementById('paperLabel').textContent = `📄 ${d.detail}`;
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
      } catch(_) {}
    }
  };
}

function setConn(on) {
  const dot = document.getElementById('connDot');
  dot.className = 'dot ' + (on ? 'on' : 'off');
}

// ═══════════ 消息发送 ═══════════
async function sendMessage() {
  const input = document.getElementById('msgInput');
  const msg = input.value.trim();
  if (!msg) return;
  input.value = ''; input.style.height = 'auto';
  const welcome = document.querySelector('.welcome');
  if (welcome) welcome.remove();
  hideBanner();
  addMessage('user', msg);
  showTyping('思考中...');
  try {
    const r = await fetch(`/api/sessions/${sid}/chat`, {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({message: msg})
    });
    const d = await r.json();
    removeTyping();
    addMessage('assistant', d.response);
  } catch(e) {
    removeTyping();
    addMessage('assistant', `抱歉，遇到了错误：${e.message}`);
  }
}

function sendHint(msg) {
  document.getElementById('msgInput').value = msg;
  sendMessage();
}

// ═══════════ 文件上传 ═══════════
async function handleFile(input) {
  const file = input.files[0];
  if (!file || !file.name.toLowerCase().endsWith('.pdf')) return;
  addMessage('user', `上传了：${file.name}`);
  showTyping('正在解析论文...');
  const fd = new FormData(); fd.append('file', file);
  try {
    const r = await fetch(`/api/sessions/${sid}/upload`, {method:'POST', body:fd});
    const d = await r.json();
    removeTyping();
    paperDir = d.paper_dir || true;
    document.getElementById('paperLabel').textContent = `📄 ${file.name}`;
    addMessage('assistant', d.response);
  } catch(e) {
    removeTyping();
    addMessage('assistant', `解析失败：${e.message}`);
  }
  input.value = '';
}

// ═══════════ 消息渲染 ═══════════
function escapeHtml(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

function renderMarkdown(text) {
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
  const container = document.getElementById('chatMessages');
  const div = document.createElement('div');
  div.className = `msg ${role}`;
  const avatarImg = role === 'user'
    ? '<img src="/static/assets/avatar-user.svg" alt="user">'
    : '<img src="/static/assets/avatar-bot.svg" alt="bot">';
  const time = new Date().toLocaleTimeString('zh-CN', {hour:'2-digit',minute:'2-digit'});
  div.innerHTML = `<div class="avatar">${avatarImg}</div>
    <div><div class="bubble">${renderMarkdown(text)}</div>
    <div class="time">${time}</div></div>`;
  container.appendChild(div);
  container.scrollTop = container.scrollHeight;
  if (role === 'assistant' && (text.includes('## ') || text.includes('### '))) {
    reportMd = text;
  }
}

function showTyping(msg) {
  removeTyping();
  const container = document.getElementById('chatMessages');
  const div = document.createElement('div');
  div.className = 'msg assistant typing';
  div.id = 'typingIndicator';
  div.innerHTML = `<div class="avatar"><img src="/static/assets/avatar-bot.svg" alt="bot"></div>
    <div><div class="bubble"><span></span><span></span><span></span></div>
    <div class="time">${escapeHtml(msg || '思考中...')}</div></div>`;
  container.appendChild(div);
  container.scrollTop = container.scrollHeight;
}

function removeTyping() {
  const el = document.getElementById('typingIndicator');
  if (el) el.remove();
}

// ═══════════ 推荐论文 ═══════════
async function refreshRecommendations() {
  try {
    const r = await fetch('/api/recommend?limit=5');
    const d = await r.json();
    if (d.papers && d.papers.length) {
      recPapers = d.papers;
      showRecommendationBanner(d.papers);
    } else if (!d.topics || !d.topics.length) {
      // 未设置研究方向：欢迎区提示
      const hint = document.getElementById('recHint');
      if (hint) hint.style.display = 'block';
    }
  } catch(_) {}
}

function showRecommendationBanner(papers) {
  const container = document.getElementById('chatMessages');
  const old = document.getElementById('recBanner');
  if (old) old.remove();
  const banner = document.createElement('div');
  banner.id = 'recBanner';
  banner.className = 'rec-banner';
  banner.innerHTML = `
    <div class="rec-head">🔥 主动推荐 · 与你研究方向相关的新论文
      <span style="margin-left:auto;display:flex;gap:6px">
        <button class="btn btn-sm btn-ghost" onclick="toggleRecommendPanel()">查看全部</button>
        <button class="btn btn-sm btn-ghost" onclick="hideBanner()">✕</button>
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
    const r = await fetch(`/api/sessions/${sid}/arxiv`, {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({url})
    });
    const d = await r.json();
    removeTyping();
    paperDir = d.paper_dir || true;
    document.getElementById('paperLabel').textContent = `📄 ${d.arxiv_id}`;
    addMessage('assistant', d.response);
    // 主动深入解读
    const msg = `请针对我的研究方向重点解读这篇论文：${d.arxiv_id}。先给出一句话总结，再展开核心方法与实验。`;
    document.getElementById('msgInput').value = msg;
    sendMessage();
  } catch(e) {
    removeTyping();
    addMessage('assistant', `获取论文失败：${e.message}`);
  }
}

async function setResearchTopics() {
  const input = document.getElementById('researchTopics');
  const topics = input.value.split(/[,，、;；]/).map(t => t.trim()).filter(Boolean);
  if (!topics.length) { toast('请输入研究方向', 'err'); return; }
  try {
    const r = await fetch('/api/profile/research', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({topics})
    });
    const d = await r.json();
    if (d.saved) {
      toast('研究方向已保存');
      hideRecommendPanel();
      refreshRecommendations();
    }
  } catch(_) { toast('保存失败', 'err'); }
}

// ═══════════ 推荐面板 ═══════════
function toggleRecommendPanel() {
  const panel = document.getElementById('recommendPanel');
  const overlay = document.getElementById('panelOverlay');
  const showing = panel.classList.toggle('show');
  overlay.classList.toggle('show', showing);
  closeOtherPanels(null);
  if (showing) renderRecommendPanel();
}

function renderRecommendPanel() {
  const body = document.getElementById('recommendBody');
  if (!recPapers.length) {
    body.innerHTML = '<p style="color:var(--dim)">暂无推荐。请先设置研究方向，或等待每日自动检索。</p>';
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
  if (!paperDir || paperDir === true) { toast('请先上传或摄入论文', 'err'); return; }
  toast('正在生成 PPT...');
  try {
    const r = await fetch(`/api/generate/pptx?paper_dir=${encodeURIComponent(paperDir)}`, {method:'POST'});
    const d = await r.json();
    window.open(`/api/download?path=${encodeURIComponent(d.path)}`, '_blank');
    toast(`PPT 已生成（${d.slides} 页）`, 'ok');
  } catch(e) { toast('PPT 生成失败', 'err'); }
}

// ═══════════ 面板控制 ═══════════
function closeOtherPanels(keep) {
  for (const id of ['reportPanel', 'memoryPanel', 'editorPanel']) {
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
  if (reportMd) {
    document.getElementById('reportContent').innerHTML = renderMarkdown(reportMd);
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
    list.innerHTML = cards.length ? '' : '<p style="color:var(--dim)">暂无记忆</p>';
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
  } catch(_) { list.innerHTML = '<p style="color:var(--dim)">加载失败</p>'; }
}

async function deleteMemoryCard(id) {
  await fetch(`/api/memory/${id}`, {method:'DELETE'});
  refreshMemory();
}

// ═══════════ 章节编辑 ═══════════
async function toggleEditor() {
  if (!paperDir || paperDir === true) { toast('请先上传并解析论文', 'err'); return; }
  const p = document.getElementById('editorPanel');
  if (p.classList.contains('show')) { p.classList.remove('show'); document.getElementById('panelOverlay').classList.remove('show'); return; }
  openPanel('editorPanel');
  await loadSections();
}

async function loadSections() {
  try {
    const r = await fetch(`/api/paper/sections?paper_dir=${encodeURIComponent(paperDir)}`);
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
      body: JSON.stringify({paper_dir: paperDir, section: sec, content})});
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
    sid = d.session_id;
  } catch(_) {}
  paperDir = null; reportMd = ''; recPapers = [];
  document.querySelectorAll('.session-item').forEach(el => el.classList.remove('active'));
  const cur = document.querySelector('.session-item[data-sid="current"]');
  if (cur) cur.classList.add('active');
  document.getElementById('chatMessages').innerHTML = welcomeHtml();
  document.getElementById('paperLabel').textContent = '';
  hideBanner();
  hideRecommendPanel();
  closeOtherPanels(null);
  connectWS(sid);
  refreshRecommendations();
}

function welcomeHtml() {
  return `<div class="welcome">
    <div class="hero-logo"><img src="/static/assets/logo.svg" alt="PaperWise"></div>
    <h2>你好，我是 PaperWise</h2>
    <p>你的 AI 学术论文研究助手 · 深度解读 · PPT 生成 · 主动推荐</p>
    <div class="hints">
      <div class="hint" onclick="sendHint('帮我分析一篇论文')">🔍 分析论文</div>
      <div class="hint" onclick="sendHint('生成一份完整的解读报告')">📝 生成报告</div>
      <div class="hint" onclick="sendHint('这篇论文的核心创新是什么？')">💡 提取创新点</div>
      <div class="hint" onclick="sendHint('实验设计有什么不足？')">🔬 批判性分析</div>
      <div class="hint" onclick="sendHint('用简单的话解释这篇论文的方法')">📖 通俗解释</div>
      <div class="hint" onclick="toggleRecommendPanel()">🔥 论文推荐</div>
    </div>
    <div id="recHint" style="display:none;margin-top:18px;color:var(--dim);font-size:13px">
      设置你的研究方向，让我主动为你推荐新论文：
      <div style="display:flex;gap:8px;justify-content:center;margin-top:8px">
        <input id="researchTopics" placeholder="如：3D Gaussian Splatting, Agent, LLM" style="width:300px;padding:9px 12px;border-radius:8px;border:1px solid var(--border);background:var(--surface);color:var(--text);outline:none">
        <button class="btn btn-primary" onclick="setResearchTopics()">保存</button>
      </div>
    </div>
    <div style="margin-top:24px">
      <label for="fileUpload" class="btn btn-primary" style="cursor:pointer;font-size:14px;padding:11px 22px">📤 上传论文 PDF</label>
      <input type="file" id="fileUpload" accept=".pdf" hidden onchange="handleFile(this)">
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
      if (s.session_id === sid) continue;
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
  sid = newSid; reportMd = '';
  paperDir = currentPaper || null;
  document.querySelectorAll('.session-item').forEach(el => el.classList.remove('active'));
  const target = document.querySelector(`.session-item[data-sid="${newSid}"]`);
  if (target) target.classList.add('active');
  document.getElementById('chatMessages').innerHTML = '';
  closeOtherPanels(null);
  hideBanner();
  document.getElementById('paperLabel').textContent = paperDir ? `📄 ${title}` : '';
  connectWS(sid);
  showTyping('恢复会话...');
  try {
    const r = await fetch(`/api/sessions/${sid}/history`);
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
