/* ResumeCraft 前端核心（防御式生命周期与前端异常自动遥测） */

// 1. 全局异常监听与遥测上报 (优先捕获语法与运行时异常)
window.addEventListener('error', function(event) {
  const errorInfo = {
    level: 'error',
    message: event.message || 'Unknown JS Error',
    source: event.filename || '',
    lineno: event.lineno || 0,
    colno: event.colno || 0,
    stack: event.error ? event.error.stack : '',
    url: window.location.href,
    timestamp: new Date().toISOString()
  };
  try {
    fetch('/api/client/log', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(errorInfo)
    }).catch(() => {});
  } catch(e) {}
});

window.addEventListener('unhandledrejection', function(event) {
  const errorInfo = {
    level: 'error',
    message: 'Unhandled Promise Rejection: ' + (event.reason?.message || event.reason),
    stack: event.reason?.stack || '',
    url: window.location.href,
    timestamp: new Date().toISOString()
  };
  try {
    fetch('/api/client/log', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(errorInfo)
    }).catch(() => {});
  } catch(e) {}
});

let monacoInstance = null;

// 登录相关 DOM
const loginModal = document.getElementById('loginModal');
const loginUser = document.getElementById('loginUser');
const loginPass = document.getElementById('loginPass');
const loginErrMsg = document.getElementById('loginErrMsg');
const btnLogin = document.getElementById('btnLogin');
const btnLogout = document.getElementById('btnLogout');

// 工作台核心 DOM
const previewWrapper = document.getElementById('previewWrapper');
const previewViewport = document.getElementById('previewViewport');
const previewContainer = document.getElementById('previewContainer');
const previewFrame = document.getElementById('previewFrame');
const guidesContainer = document.getElementById('guidesContainer');
const tplSel = document.getElementById('tplSel');
const charCount = document.getElementById('charCount');
const saveStatus = document.getElementById('saveStatus');
const toastEl = document.getElementById('toast');
const pageStat = document.getElementById('pageStat');
const zoomVal = document.getElementById('zoomVal');

const sidebar = document.getElementById('sidebar');
const btnToggleSidebar = document.getElementById('btnToggleSidebar');
const tabDoc = document.getElementById('tabDoc');
const tabPages = document.getElementById('tabPages');
const tabMetrics = document.getElementById('tabMetrics');
const tabTheme = document.getElementById('tabTheme');
const panelDoc = document.getElementById('panelDoc');
const panelPages = document.getElementById('panelPages');
const panelMetrics = document.getElementById('panelMetrics');
const panelTheme = document.getElementById('panelTheme');

const docList = document.getElementById('docList');
const pagesList = document.getElementById('pagesList');
const btnUploadPage = document.getElementById('btnUploadPage');
const pageFileInput = document.getElementById('pageFileInput');
const metricsList = document.getElementById('metricsList');
const btnNewDoc = document.getElementById('btnNewDoc');
const btnSaveDoc = document.getElementById('btnSaveDoc');
const btnViewInsight = document.getElementById('btnViewInsight');
const btnRefreshMetrics = document.getElementById('btnRefreshMetrics');
const btnExportGitData = document.getElementById('btnExportGitData');
const currentDocLabel = document.getElementById('currentDocLabel');
const imageFileInput = document.getElementById('imageFileInput');
const btnUploadImage = document.getElementById('btnUploadImage');

const DRAFT_KEY = 'resumecraft_draft_md';
const CURRENT_DOC_KEY = 'resumecraft_current_filename';
let currentDoc = 'example.md';
let currentContent = '';
let templates = [];
let refreshTimer = null;
let saveTimer = null;
let isUpdatingFromSelect = false;

let currentScale = 1.0;
let isAutoFit = true;
let guideVisible = true;
let currentTab = 'doc';

/* ---------- 提示 ---------- */
function toast(msg, isError = false) {
  if (!toastEl) return;
  toastEl.textContent = msg;
  toastEl.className = 'toast show' + (isError ? ' error' : '');
  clearTimeout(toastEl._t);
  toastEl._t = setTimeout(() => toastEl.className = 'toast', 2600);
}

/* ---------- 登录逻辑 ---------- */
async function doLogin() {
  const username = loginUser ? loginUser.value.trim() : '';
  const password = loginPass ? loginPass.value : '';
  if (!username || !password) {
    if (loginErrMsg) {
      loginErrMsg.textContent = '请输入用户名和密码';
      loginErrMsg.style.display = 'block';
    }
    toast('请输入用户名和密码', true);
    return;
  }
  if (loginErrMsg) loginErrMsg.style.display = 'none';

  try {
    const res = await fetch('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password })
    });
    const data = await res.json().catch(() => ({}));
    if (res.ok && data.ok) {
      if (loginErrMsg) loginErrMsg.style.display = 'none';
      if (loginModal) loginModal.style.display = 'none';
      toast('登录成功');
      await initWorkspaceAfterAuth();
    } else {
      const err = data.detail || '用户名或密码错误';
      if (loginErrMsg) {
        loginErrMsg.textContent = err;
        loginErrMsg.style.display = 'block';
      }
      toast(err, true);
    }
  } catch (e) {
    if (loginErrMsg) {
      loginErrMsg.textContent = '登录网络异常，请重试';
      loginErrMsg.style.display = 'block';
    }
    toast('网络异常，请重试', true);
  }
}

async function doLogout() {
  try {
    await fetch('/api/auth/logout', { method: 'POST' });
    toast('已退出登录');
    if (loginModal) loginModal.style.display = 'flex';
    if (loginPass) loginPass.value = '';
  } catch (e) {
    location.reload();
  }
}

// 立即绑定登录事件
if (btnLogin) btnLogin.addEventListener('click', doLogin);
if (loginPass) loginPass.addEventListener('keydown', (e) => { if (e.key === 'Enter') doLogin(); });
if (loginUser) loginUser.addEventListener('keydown', (e) => { if (e.key === 'Enter') doLogin(); });
if (btnLogout) btnLogout.addEventListener('click', doLogout);

/* ---------- 编辑器抽象层 (Get/Set/Insert) ---------- */
function getEditorValue() {
  if (monacoInstance) return monacoInstance.getValue();
  return currentContent || '';
}

function setEditorValue(val) {
  currentContent = val;
  if (monacoInstance) {
    if (monacoInstance.getValue() !== val) {
      monacoInstance.setValue(val);
    }
  }
}

function insertSnippet(snippet) {
  if (!monacoInstance) return;
  const selection = monacoInstance.getSelection();
  const op = {
    range: selection,
    text: "\n" + snippet + "\n",
    forceMoveMarkers: true
  };
  monacoInstance.executeEdits("snippet-insert", [op]);
  monacoInstance.focus();
  schedulePreview();
  toast('已插入内容');
}

/* ---------- 初始化 VS Code Monaco Editor ---------- */
function initMonacoEditor() {
  return new Promise((resolve) => {
    if (typeof require === 'undefined') {
      resolve(null);
      return;
    }

    require.config({ paths: { vs: 'https://cdnjs.cloudflare.com/ajax/libs/monaco-editor/0.45.0/min/vs' } });
    require(['vs/editor/editor.main'], function () {
      const container = document.getElementById('monacoEditor');
      if (!container) {
        resolve(null);
        return;
      }

      monacoInstance = monaco.editor.create(container, {
        value: currentContent || DEFAULT_FALLBACK_MD,
        language: 'markdown',
        theme: 'vs-dark',
        fontSize: 13,
        lineHeight: 22,
        fontFamily: 'ui-monospace, "SF Mono", Menlo, Consolas, monospace',
        wordWrap: 'on',
        wrappingIndent: 'same',
        minimap: { enabled: false },
        scrollBeyondLastLine: false,
        automaticLayout: true,
        tabSize: 2,
        cursorSmoothCaretAnimation: 'on',
        smoothScrolling: true,
        renderLineHighlight: 'line'
      });

      monacoInstance.onDidChangeModelContent(() => {
        currentContent = monacoInstance.getValue();
        schedulePreview();
      });

      monacoInstance.onDidChangeCursorPosition(() => {
        syncEditorCursorToPreview();
      });

      resolve(monacoInstance);
    });
  });
}

/* ---------- 鉴权检查 ---------- */
async function checkAuth() {
  try {
    const res = await fetch('/api/auth/status');
    const data = await res.json();
    if (!data.auth_enabled || data.authenticated) {
      if (loginModal) loginModal.style.display = 'none';
      return true;
    } else {
      if (loginModal) loginModal.style.display = 'flex';
      return false;
    }
  } catch (e) {
    if (loginModal) loginModal.style.display = 'flex';
    return false;
  }
}

const sidebarBackdrop = document.getElementById('sidebarBackdrop');

/* ---------- 侧边栏与活动栏 Tab 切换 ---------- */
function switchTab(tabName) {
  if (!sidebar) return;
  if (currentTab === tabName && !sidebar.classList.contains('collapsed')) {
    sidebar.classList.add('collapsed');
    if (sidebarBackdrop) sidebarBackdrop.classList.remove('show');
    [tabDoc, tabPages, tabMetrics, tabTheme].forEach(t => t && t.classList.remove('active'));
    return;
  }

  sidebar.classList.remove('collapsed');
  if (sidebarBackdrop && window.innerWidth <= 768) {
    sidebarBackdrop.classList.add('show');
  }
  currentTab = tabName;

  [tabDoc, tabPages, tabMetrics, tabTheme].forEach(t => t && t.classList.remove('active'));
  [panelDoc, panelPages, panelMetrics, panelTheme].forEach(p => p && (p.style.display = 'none'));

  if (tabName === 'doc') {
    if (tabDoc) tabDoc.classList.add('active');
    if (panelDoc) panelDoc.style.display = 'flex';
    loadDocumentList();
  } else if (tabName === 'pages') {
    if (tabPages) tabPages.classList.add('active');
    if (panelPages) panelPages.style.display = 'flex';
    loadPagesList();
  } else if (tabName === 'metrics') {
    if (tabMetrics) tabMetrics.classList.add('active');
    if (panelMetrics) panelMetrics.style.display = 'flex';
    loadMetrics();
  } else if (tabName === 'theme') {
    if (tabTheme) tabTheme.classList.add('active');
    if (panelTheme) panelTheme.style.display = 'flex';
  }
}

function toggleSidebar() {
  if (!sidebar) return;
  if (sidebar.classList.contains('collapsed')) {
    switchTab(currentTab || 'doc');
  } else {
    sidebar.classList.add('collapsed');
    if (sidebarBackdrop) sidebarBackdrop.classList.remove('show');
    [tabDoc, tabMetrics, tabTheme].forEach(t => t && t.classList.remove('active'));
  }
}

/* ---------- 模板管理 ---------- */
async function loadTemplates() {
  try {
    const res = await fetch('/api/templates');
    if (!res.ok) return;
    const data = await res.json();
    templates = data.templates || [];
    if (tplSel) {
      tplSel.innerHTML = templates.map(t => `<option value="${t}">${t}</option>`).join('');
    }
    syncTemplateFromMd();
  } catch (e) {
    toast('模板列表加载失败', true);
  }
}

function syncTemplateFromMd() {
  if (isUpdatingFromSelect || !tplSel) return;
  const md = getEditorValue();
  const tplMatch = md.match(/^---\s*\n([\s\S]*?)\n---/);
  if (tplMatch) {
    const m = tplMatch[1].match(/template\s*:\s*(\S+)/);
    if (m && tplSel.value !== m[1]) {
      tplSel.value = m[1];
    }
  }
}

function onTemplateSelectChange() {
  if (!tplSel) return;
  const chosen = tplSel.value;
  let md = getEditorValue();
  isUpdatingFromSelect = true;
  
  if (md.startsWith('---')) {
    const match = md.match(/^---\s*\n([\s\S]*?)\n---/);
    if (match) {
      let fm = match[1];
      if (/template\s*:\s*\S+/.test(fm)) {
        fm = fm.replace(/template\s*:\s*\S+/, `template: ${chosen}`);
      } else {
        fm = `template: ${chosen}\n` + fm;
      }
      md = md.replace(/^---\s*\n[\s\S]*?\n---/, `---\n${fm}\n---`);
    }
  } else {
    md = `---\ntemplate: ${chosen}\nlayout: full\n---\n\n` + md;
  }
  
  setEditorValue(md);
  isUpdatingFromSelect = false;
  toast(`已切换为「${chosen}」风格`);
  triggerAutoSave();
  doPreview();
}

/* ---------- 简历文档库 ---------- */
async function loadDocumentList() {
  if (!docList) return;
  try {
    const res = await fetch('/api/documents');
    if (!res.ok) return;
    const data = await res.json();
    const docs = data.documents || [];
    
    docList.innerHTML = docs.map(d => `
      <div class="side-card ${d.filename === currentDoc ? 'active' : ''}" onclick="switchDocument('${d.filename}')">
        <div class="side-card-title">
          <span>📄 ${d.filename.replace('.md','')}</span>
          <span>
            <span class="pub-badge ${d.public ? 'on' : ''}" title="${d.public ? '已公开为个人主页' : '未公开'}">${d.public ? '🌐 公开' : '🔒 私有'}</span>
            ${d.filename === currentDoc ? '<span style="font-size:10px;color:var(--accent-green);">当前编辑</span>' : ''}
          </span>
        </div>
        <div class="side-card-meta">
          <span>${d.name || '--'} · ${d.role || '未定意向'}</span>
          <span>${d.size} 字</span>
        </div>
        <div class="side-actions" onclick="event.stopPropagation()">
          <button class="side-btn pub" onclick="togglePublic('${d.filename}', ${!d.public})" title="${d.public ? '取消公开' : '公开为个人主页'}">${d.public ? '🔓 取消公开' : '🌐 公开'}</button>
          ${d.public ? `<button class="side-btn" onclick="openPublicPage('${d.filename}')" title="打开公开个人主页">🌐 打开</button>` : ''}
          ${d.public ? `<button class="side-btn" onclick="copyPublicLink('${d.filename}')" title="复制公开链接">🔗 链接</button>` : ''}
          <button class="side-btn" onclick="copyDocument('${d.filename}')" title="创建简历副本">📋 复制</button>
          <button class="side-btn" onclick="renameDocument('${d.filename}')" title="重命名简历">✏️ 重命名</button>
          <button class="side-btn" onclick="openInsight('${d.filename}')" title="全屏独立预览">👁 全屏 ↗</button>
          <button class="side-btn del" onclick="deleteDocument('${d.filename}')" title="删除简历">🗑</button>
        </div>
      </div>
    `).join('');
  } catch (e) {
    console.error('加载文档列表失败', e);
  }
}

// 切换简历公开状态 (在 front matter 中添加/移除 public: true)
async function togglePublic(filename, makePublic) {
  try {
    const res = await fetch(`/api/documents/${encodeURIComponent(filename)}`);
    if (!res.ok) { toast('读取文档失败', true); return; }
    const { content } = await res.json();

    let newContent;
    const hasPublic = /^\s*public\s*:\s*\S+\s*$/m;
    if (makePublic) {
      newContent = content.startsWith('---')
        ? content.replace(/^---\s*\n([\s\S]*?)\n---/, (m, fm) => {
            const clean = fm.split('\n').filter(l => !/^\s*public\s*:/.test(l)).join('\n');
            return `---\n${clean.trim() ? clean + '\n' : ''}public: true\n---`;
          })
        : `---\npublic: true\n---\n\n${content}`;
    } else {
      newContent = content.replace(/^---\s*\n([\s\S]*?)\n---/, (m, fm) => {
        const clean = fm.split('\n').filter(l => !/^\s*public\s*:/.test(l)).join('\n');
        return `---\n${clean.trim() ? clean + '\n' : ''}---`;
      });
    }

    const saveRes = await fetch(`/api/documents/${encodeURIComponent(filename)}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content: newContent })
    });
    if (saveRes.ok) {
      toast(makePublic ? '已设为公开个人主页' : '已切换为私有');
      if (currentDoc === filename) {
        setEditorValue(newContent);
        doPreview();
      }
      loadDocumentList();
    } else {
      toast('切换失败', true);
    }
  } catch (e) {
    toast('网络异常', true);
  }
}

// 复制公开链接
function copyPublicLink(filename) {
  const link = window.location.origin + '/p/' + filename.replace(/\.md$/, '');
  navigator.clipboard.writeText(link).then(() => toast('公开链接已复制: ' + link));
}

// 打开公开个人主页（跳转到 /p/{slug}）
function openPublicPage(filename) {
  const slug = filename.replace(/\.md$/, '');
  window.open('/p/' + encodeURIComponent(slug), '_blank');
}

async function switchDocument(filename) {
  try {
    const res = await fetch(`/api/documents/${encodeURIComponent(filename)}`);
    if (!res.ok) {
      toast('读取文档失败', true);
      return;
    }
    const data = await res.json();
    currentDoc = filename;
    localStorage.setItem(CURRENT_DOC_KEY, currentDoc);
    if (currentDocLabel) currentDocLabel.textContent = currentDoc;
    lastLoadedVersionTag = data.version_tag || '';
    setEditorValue(data.content);
    syncTemplateFromMd();
    doPreview();
    toast(`已加载「${currentDoc.replace('.md','')}」`);
    loadDocumentList();
  } catch (e) {
    toast('切换简历失败', true);
  }
}

async function saveCurrentDocument() {
  if (!currentDoc) return;
  try {
    if (saveStatus) saveStatus.innerHTML = '<span class="dot" style="background:#e3b341;"></span> 正在同步...';
    const res = await fetch(`/api/documents/${encodeURIComponent(currentDoc)}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content: getEditorValue() })
    });
    if (res.ok) {
      if (saveStatus) saveStatus.innerHTML = '<span class="dot"></span> 云端已同步保存';
      toast(`「${currentDoc.replace('.md','')}」已保存`);
      loadDocumentList();
    } else {
      toast('保存失败', true);
    }
  } catch (e) {
    toast('保存异常', true);
  }
}

async function copyDocument(filename) {
  try {
    toast(`正在创建「${filename.replace('.md','')}」的副本...`);
    const res = await fetch(`/api/documents/${encodeURIComponent(filename)}/copy`, {
      method: 'POST'
    });
    const data = await res.json();
    if (res.ok && data.ok) {
      toast(`已成功创建副本「${data.new_filename.replace('.md','')}」`);
      await loadDocumentList();
      await switchDocument(data.new_filename);
    } else {
      toast(data.detail || '复制失败', true);
    }
  } catch (e) {
    toast('网络异常，复制失败', true);
  }
}

async function renameDocument(filename) {
  const currentBase = filename.replace(/\.md$/, '');
  const newName = prompt(`请输入「${currentBase}」的新名称:`, currentBase);
  if (!newName || !newName.trim() || newName.trim() === currentBase) return;
  const newFilename = newName.trim().endsWith('.md') ? newName.trim() : `${newName.trim()}.md`;

  try {
    const res = await fetch(`/api/documents/${encodeURIComponent(filename)}/rename`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ new_filename: newFilename })
    });
    const data = await res.json();
    if (res.ok && data.ok) {
      toast(`已重命名为「${newFilename.replace('.md','')}」`);
      if (currentDoc === filename) {
        currentDoc = newFilename;
        localStorage.setItem(CURRENT_DOC_KEY, currentDoc);
        if (currentDocLabel) currentDocLabel.textContent = currentDoc;
      }
      loadDocumentList();
    } else {
      toast(data.detail || '重命名失败', true);
    }
  } catch (e) {
    toast('网络异常，重命名失败', true);
  }
}

async function createNewDocument() {
  const name = prompt('请输入新简历文件名（如：张三_前端开发工程师）:');
  if (!name || !name.trim()) return;
  const filename = name.trim().endsWith('.md') ? name.trim() : `${name.trim()}.md`;
  
  try {
    const res = await fetch('/api/documents_new', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ filename })
    });
    const data = await res.json();
    if (res.ok) {
      toast('新简历创建成功');
      await switchDocument(data.filename);
    } else {
      toast(data.detail || '创建失败', true);
    }
  } catch (e) {
    toast('网络异常，创建失败', true);
  }
}

async function deleteDocument(filename) {
  if (!confirm(`确定要永久删除「${filename}」吗？`)) return;
  try {
    const res = await fetch(`/api/documents/${encodeURIComponent(filename)}`, { method: 'DELETE' });
    if (res.ok) {
      toast('简历已删除');
      if (currentDoc === filename) {
        currentDoc = 'example.md';
        await switchDocument(currentDoc);
      } else {
        loadDocumentList();
      }
    } else {
      toast('删除失败', true);
    }
  } catch (e) {
    toast('删除异常', true);
  }
}

function openInsight(filename) {
  window.open(`/view?doc=${encodeURIComponent(filename || currentDoc)}`, '_blank');
}

/* ---------- HTML 页面托管 (data/pages/) ---------- */
async function loadPagesList() {
  if (!pagesList) return;
  try {
    const res = await fetch('/api/pages');
    if (!res.ok) return;
    const data = await res.json();
    const pages = data.pages || [];

    if (!pages.length) {
      pagesList.innerHTML = `
        <div style="font-size:12px;color:var(--text-muted);text-align:center;padding:24px 8px;border:1px dashed var(--border-color);border-radius:8px;">
          暂无 HTML 页面<br>点击上方「上传」添加你的炫技个人主页 / 项目展示页
        </div>`;
      return;
    }

    pagesList.innerHTML = pages.map(p => `
      <div class="side-card">
        <div class="side-card-title">
          <span>🖥️ ${p.filename}</span>
          <span style="font-size:10px;color:var(--muted, #8b949e);">${(p.size/1024).toFixed(1)} KB</span>
        </div>
        <div class="side-card-meta" title="服务器绝对路径（本机 / AI 可定位编辑）">
          <span style="max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-family:monospace;font-size:10px;">${p.path}</span>
        </div>
        <div class="side-actions" onclick="event.stopPropagation()">
          <button class="side-btn" onclick="previewPage('${p.filename}')">🔍 在线预览</button>
          <button class="side-btn" onclick="window.open('${p.url}','_blank')">🔗 外网访问</button>
          <button class="side-btn" onclick="copyPublicLinkFromUrl('${p.url}')">📋 复制</button>
          <button class="side-btn del" onclick="deletePage('${p.filename}')">🗑</button>
        </div>
      </div>
    `).join('');
  } catch (e) {
    console.error('加载页面列表失败', e);
  }
}

function copyPublicLinkFromUrl(url) {
  const link = window.location.origin + url;
  navigator.clipboard.writeText(link).then(() => toast('外网链接已复制: ' + link));
}

function previewPage(filename) {
  const url = `/api/pages/preview?file=${encodeURIComponent(filename)}`;
  window.open(url, '_blank');
}

async function deletePage(filename) {
  if (!confirm(`确定删除页面「${filename}」吗？`)) return;
  try {
    const res = await fetch(`/api/pages/${encodeURIComponent(filename)}`, { method: 'DELETE' });
    if (res.ok) {
      toast('页面已删除');
      loadPagesList();
    } else {
      toast('删除失败', true);
    }
  } catch (e) {
    toast('网络异常', true);
  }
}

async function handleUploadPage(file) {
  if (!file.name.toLowerCase().endsWith('.html')) {
    toast('仅支持 .html 文件', true);
    return;
  }
  toast('正在上传页面...');
  const formData = new FormData();
  formData.append('file', file);
  try {
    const res = await fetch('/api/pages/upload', { method: 'POST', body: formData });
    const data = await res.json();
    if (res.ok && data.ok) {
      toast('页面上传成功');
      loadPagesList();
    } else {
      toast(data.detail || '上传失败', true);
    }
  } catch (e) {
    toast('网络异常，上传失败', true);
  }
}

/* ---------- 多平台量化指标 ---------- */
async function loadMetrics(refresh = false) {
  if (!metricsList) return;
  try {
    if (refresh) toast('正在拉取多平台最新指标...');
    const res = await fetch(`/api/metrics?refresh=${refresh}`);
    if (!res.ok) return;
    const data = await res.json();
    const m = data.metrics || {};
    const ds = m.deepseek || {};
    const sf = m.siliconflow || {};
    const mai = m.maiapi || {};
    const git = m.git || {};
    const sec = m.security || {};
    const cem = m.cem || {};

    metricsList.innerHTML = `
      <!-- LLM 中转网关 -->
      <div class="side-card" onclick="insertSnippet('- **多模型 LLM API 中转网关**：自建多模型 API 智能代理网关，累计承载 **${mai.total_requests_str || '—'}** 请求，稳定分发 **${mai.total_tokens_str || '—'}** Tokens。')">
        <div class="side-card-title" style="color:#d2a8ff;">⚡ LLM API 中转网关</div>
        <div class="side-card-meta"><span>累计调用请求</span><b>${mai.total_requests_str || '—'}</b></div>
        <div class="side-card-meta"><span>Token 吞吐</span><b>${mai.total_tokens_str || '—'}</b></div>
        <button class="side-btn" style="margin-top:6px;background:#8957e5;color:#fff;font-weight:700;">+ 插入 Token 成果</button>
      </div>

      <!-- 云推理算力 -->
      <div class="side-card" onclick="insertSnippet('- **云端推理算力接入**：接入矩阵推理算力，通过多个业务 API Key 累计分发 **${sf.total_tokens_str || '—'}** Tokens（消费 **${sf.total_cost_str || '—'}** 元）。')">
        <div class="side-card-title" style="color:#79c0ff;">🌊 云端推理算力</div>
        <div class="side-card-meta"><span>累计 Token 吞吐</span><b>${sf.total_tokens_str || '—'}</b></div>
        <div class="side-card-meta"><span>聚合消费账单</span><b>${sf.total_cost_str || '—'}</b></div>
        <button class="side-btn" style="margin-top:6px;background:#1f6feb;color:#fff;font-weight:700;">+ 插入 Token 数据</button>
      </div>

      <!-- 官方大模型 -->
      <div class="side-card" onclick="insertSnippet('- **大模型官方用量治理**：官方累计调度 **${ds.total_tokens_str || '—'}** Tokens（消费 **${ds.total_cost || '—'}**，余额 **${ds.balance || '—'}**）。')">
        <div class="side-card-title" style="color:var(--accent-blue);">🤖 官方大模型用量</div>
        <div class="side-card-meta"><span>累计 Token 吞吐</span><b>${ds.total_tokens_str || '—'}</b></div>
        <div class="side-card-meta"><span>官方账单消费</span><b>${ds.total_cost || '—'}</b></div>
        <button class="side-btn" style="margin-top:6px;background:var(--accent-blue);color:#fff;">+ 插入官方用量</button>
      </div>

      <!-- Git / 开源 -->
      <div class="side-card" onclick="insertSnippet('![Git 真实贡献日历](/api/git/chart.svg)\n- **真实工程沉淀**：本地与 GitHub 累计提交 **${git.total_commits_str || '—'}** 次 Commit。')">
        <div class="side-card-title" style="color:var(--accent-green);">🐙 Git 真实代码提交资产</div>
        <div class="side-card-meta"><span>提交 Commit</span><b>${git.total_commits_str || '—'} 次</b></div>
        <div class="side-card-meta"><span>纳管仓库</span><b>${git.local_repos_count || '—'} 个</b></div>
        <button class="side-btn" style="margin-top:6px;background:var(--accent-green);color:#0f1117;font-weight:700;">+ 插入 Git 日历与数据</button>
      </div>

      <!-- 服务器安全 -->
      <div class="side-card" onclick="insertSnippet('- **服务器安全防御**：打通 Fail2ban 与 iptables 自动封禁链，累计拦截 **${sec.blocked_ips_str || '—'}**，防御公网扫描 **${sec.ssh_defense_str || '—'}**。')">
        <div class="side-card-title" style="color:#f85149;">🛡️ 服务器安全防御</div>
        <div class="side-card-meta"><span>封禁恶意 IP</span><b>${sec.blocked_ips_str || '—'}</b></div>
        <div class="side-card-meta"><span>抵御扫描</span><b>${sec.ssh_defense_str || '—'}</b></div>
        <button class="side-btn" style="margin-top:6px;">+ 插入安全防御数据</button>
      </div>

      <!-- 工业调度 -->
      <div class="side-card" onclick="insertSnippet('- **工业调度高可用**：自建任务与设备调度系统，累计调度 **${cem.scheduled_tasks_str || '—'}**，成功率 **${cem.dispatch_success_rate || '—'}**。')">
        <div class="side-card-title" style="color:#e3b341;">🏭 工业任务调度</div>
        <div class="side-card-meta"><span>累计调度任务</span><b>${cem.scheduled_tasks_str || '—'}</b></div>
        <div class="side-card-meta"><span>调度成功率</span><b>${cem.dispatch_success_rate || '—'}</b></div>
        <button class="side-btn" style="margin-top:6px;">+ 插入工业调度指标</button>
      </div>
    `;
    if (refresh) toast('多平台数据已刷新');
  } catch (e) {
    console.error('加载指标失败', e);
  }
}

/* ---------- 缩放控制引擎 ---------- */
function applyScale(scale, isAuto = false) {
  if (!previewViewport || !previewContainer) return;
  currentScale = Math.max(0.4, Math.min(1.8, scale));
  isAutoFit = isAuto;
  previewViewport.style.transform = `scale(${currentScale})`;
  if (zoomVal) {
    zoomVal.textContent = isAuto ? `自适应 (${Math.round(currentScale * 100)}%)` : `${Math.round(currentScale * 100)}%`;
  }
  
  const actualHeight = previewContainer.offsetHeight * currentScale;
  previewViewport.style.height = `${actualHeight}px`;
  previewViewport.style.marginBottom = '40px';
}

function autoFitScale() {
  if (!previewWrapper) return;
  const a4WidthPx = 794;
  const availableWidth = previewWrapper.clientWidth - 32;
  if (availableWidth > 0) {
    const targetScale = Math.min(1.15, Math.max(0.45, availableWidth / a4WidthPx));
    applyScale(targetScale, true);
  }
}

/* ---------- 动态渲染多页 A4 截断线 ---------- */
function updatePageBreakGuides() {
  if (!guidesContainer || !previewFrame || !previewContainer) return;
  guidesContainer.innerHTML = '';
  
  try {
    const iframeDoc = previewFrame.contentDocument || previewFrame.contentWindow?.document;
    if (!iframeDoc || !iframeDoc.body) return;
    
    const pageEl = iframeDoc.querySelector('.page') || iframeDoc.body;
    const computedBg = iframeDoc.defaultView.getComputedStyle(pageEl).backgroundColor;
    if (computedBg && computedBg !== 'transparent') {
      previewContainer.style.background = computedBg;
    }
    
    const bodyHeight = Math.max(pageEl.scrollHeight, iframeDoc.body.scrollHeight);
    const a4HeightPx = 1122.5; // 297mm @ 96DPI
    const realHeight = Math.max(bodyHeight, a4HeightPx);
    
    previewFrame.style.height = `${realHeight}px`;
    previewContainer.style.height = `${realHeight}px`;
    
    const totalPages = Math.ceil(bodyHeight / a4HeightPx);
    if (pageStat) {
      pageStat.textContent = totalPages <= 1 ? '共 1 页 (单页达标)' : `共 ${totalPages} 页`;
      pageStat.style.color = totalPages === 1 ? 'var(--accent-green)' : '#e3b341';
    }
    
    if (guideVisible) {
      for (let p = 1; p < totalPages; p++) {
        const topPx = p * a4HeightPx;
        const line = document.createElement('div');
        line.className = 'page-guide-line';
        line.style.top = `${topPx}px`;
        line.innerHTML = `
          <span class="page-guide-tag">第 ${p} 页 / 第 ${p + 1} 页 A4 截断线 (${p * 297}mm)</span>
          <span class="page-guide-warn">注意避免卡片跨页腰斩</span>
        `;
        guidesContainer.appendChild(line);
      }
    }
    
    if (isAutoFit) {
      autoFitScale();
    } else {
      applyScale(currentScale, false);
    }
  } catch (e) {
    console.error('更新分页标线失败', e);
  }
}

/* ---------- 编辑光标跟随与右侧高亮联动 (Sync Scroll & Focus) ---------- */
function syncEditorCursorToPreview() {
  if (!previewFrame) return;
  
  try {
    const iframeDoc = previewFrame.contentDocument || previewFrame.contentWindow?.document;
    if (!iframeDoc) return;

    let textBefore = '';
    if (monacoInstance) {
      const pos = monacoInstance.getPosition();
      const model = monacoInstance.getModel();
      if (pos && model) {
        textBefore = model.getValueInRange({ startLineNumber: 1, startColumn: 1, endLineNumber: pos.lineNumber, endColumn: pos.column });
      }
    } else {
      textBefore = currentContent;
    }

    const lines = textBefore.split('\n');
    let activeSecName = 'hero';
    for (let i = lines.length - 1; i >= 0; i--) {
      const line = lines[i].trim();
      if (line.startsWith('## ')) {
        activeSecName = line.replace('##', '').trim();
        break;
      } else if (line.startsWith('# ')) {
        activeSecName = 'hero';
        break;
      }
    }

    const cards = iframeDoc.querySelectorAll('.card');
    cards.forEach(card => card.classList.remove('active-focus'));

    let targetCard = null;
    if (activeSecName === 'hero') {
      targetCard = iframeDoc.querySelector('#sec-hero');
    } else {
      cards.forEach(card => {
        const sec = card.getAttribute('data-sec');
        if (sec && (sec === activeSecName || activeSecName.includes(sec) || sec.includes(activeSecName))) {
          targetCard = card;
        }
      });
    }

    if (targetCard) {
      targetCard.classList.add('active-focus');
      if (previewWrapper && previewContainer) {
        const targetTop = targetCard.offsetTop;
        const targetScroll = (targetTop * currentScale) - 80;
        previewWrapper.scrollTo({
          top: Math.max(0, targetScroll),
          behavior: 'smooth'
        });
      }
    }
  } catch (e) {}
}

/* ---------- 预览与暂存 ---------- */
function triggerAutoSave() {
  markUserTyping();
  if (saveStatus) saveStatus.innerHTML = '<span class="dot" style="background:#e3b341;"></span> 正在暂存...';
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => {
    try {
      localStorage.setItem(DRAFT_KEY, getEditorValue());
      if (saveStatus) saveStatus.innerHTML = '<span class="dot"></span> 自动已暂存';
    } catch (e) {}
  }, 300);
}

function schedulePreview() {
  syncTemplateFromMd();
  triggerAutoSave();
  clearTimeout(refreshTimer);
  refreshTimer = setTimeout(doPreview, 350);
}

async function doPreview() {
  if (!previewFrame) return;
  const md = getEditorValue();
  if (charCount) charCount.textContent = md.length + ' 字';

  try {
    const res = await fetch('/api/preview', {
      method: 'POST',
      headers: { 'Content-Type': 'text/plain; charset=utf-8' },
      body: md,
    });
    if (res.status === 401) {
      if (loginModal) loginModal.style.display = 'flex';
      return;
    }
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      toast('渲染失败：' + (err.detail || res.status), true);
      return;
    }
    const html = await res.text();
    const baseHref = window.location.origin;
    const injectedHtml = html.replace('<head>', `<head><base href="${baseHref}/">`);
    previewFrame.srcdoc = injectedHtml;

    previewFrame.onload = () => {
      setTimeout(() => {
        updatePageBreakGuides();
        syncEditorCursorToPreview();
      }, 60);
    };
  } catch (e) {
    toast('预览请求失败', true);
  }
}

/* ---------- 导出 PDF ---------- */
async function exportPdf() {
  toast('正在生成高保真 PDF…');
  try {
    const res = await fetch('/api/export', {
      method: 'POST',
      headers: { 'Content-Type': 'text/plain; charset=utf-8' },
      body: getEditorValue(),
    });
    if (res.status === 401) {
      if (loginModal) loginModal.style.display = 'flex';
      toast('请先登录', true);
      return;
    }
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      toast('导出失败：' + (err.detail || res.status), true);
      return;
    }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `resume_${encodeURIComponent(currentDoc.replace('.md',''))}.pdf`;
    a.click();
    URL.revokeObjectURL(url);
    toast('PDF 已成功生成并下载');
  } catch (e) {
    toast('导出请求失败', true);
  }
}

/* ---------- 导出 Git 贡献日历数据 (JSON) ---------- */
async function exportGitData() {
  toast('正在拉取 Git 贡献数据…');
  try {
    const res = await fetch('/api/git/data');
    if (!res.ok) { toast('拉取失败：' + res.status, true); return; }
    const data = await res.json();
    const json = JSON.stringify(data, null, 2);
    const blob = new Blob([json], { type: 'application/json;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    const d = new Date();
    const stamp = `${d.getFullYear()}${String(d.getMonth()+1).padStart(2,'0')}${String(d.getDate()).padStart(2,'0')}`;
    a.href = url;
    a.download = `git_calendar_${stamp}.json`;
    a.click();
    URL.revokeObjectURL(url);
    toast(`Git 数据已导出（GitHub ${data.github_total} / Gitea ${data.gitea_total} / 共 ${data.total}）`);
  } catch (e) {
    toast('导出 Git 数据失败', true);
  }
}
if (btnExportGitData) btnExportGitData.addEventListener('click', exportGitData);

/* ---------- 图片上传 ---------- */
if (btnUploadImage && imageFileInput) {
  btnUploadImage.addEventListener('click', () => imageFileInput.click());
  imageFileInput.addEventListener('change', async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    await handleUploadFile(file);
  });
}

async function handleUploadFile(file) {
  if (!file.type.startsWith('image/')) {
    toast('仅支持图片格式 (PNG/JPG/WEBP)', true);
    return;
  }
  toast('正在上传图片...');
  const formData = new FormData();
  formData.append('file', file);
  try {
    const res = await fetch('/api/upload/image', { method: 'POST', body: formData });
    const data = await res.json();
    if (res.ok && data.ok) {
      toast('图片上传成功！已更新头像');
      let md = getEditorValue();
      if (md.startsWith('---')) {
        if (/avatar\s*:\s*\S+/.test(md)) {
          md = md.replace(/avatar\s*:\s*\S+/, `avatar: "${data.url}"`);
        } else {
          md = md.replace(/^---\s*\n/, `---\navatar: "${data.url}"\n`);
        }
      } else {
        md = `---\navatar: "${data.url}"\n---\n\n` + md;
      }
      setEditorValue(md);
      schedulePreview();
    } else {
      toast(data.detail || '上传失败', true);
    }
  } catch (err) {
    toast('网络异常，上传失败', true);
  }
}

/* ---------- 事件监听与快捷键绑定 ---------- */
if (tabDoc) tabDoc.addEventListener('click', () => switchTab('doc'));
if (tabPages) tabPages.addEventListener('click', () => switchTab('pages'));
if (tabMetrics) tabMetrics.addEventListener('click', () => switchTab('metrics'));
if (tabTheme) tabTheme.addEventListener('click', () => switchTab('theme'));
if (btnToggleSidebar) btnToggleSidebar.addEventListener('click', toggleSidebar);
if (sidebarBackdrop) sidebarBackdrop.addEventListener('click', () => toggleSidebar());

if (btnUploadPage) btnUploadPage.addEventListener('click', () => pageFileInput && pageFileInput.click());
if (pageFileInput) pageFileInput.addEventListener('change', (e) => {
  const file = e.target.files[0];
  if (file) handleUploadPage(file);
});

window.addEventListener('keydown', (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'b') {
    e.preventDefault();
    toggleSidebar();
  }
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') {
    e.preventDefault();
    saveCurrentDocument();
  }
});

if (tplSel) tplSel.addEventListener('change', onTemplateSelectChange);

if (btnSaveDoc) btnSaveDoc.addEventListener('click', saveCurrentDocument);
if (btnViewInsight) btnViewInsight.addEventListener('click', () => openInsight(currentDoc));
if (btnExport) btnExport.addEventListener('click', exportPdf);
if (btnNewDoc) btnNewDoc.addEventListener('click', createNewDocument);
if (btnRefreshMetrics) btnRefreshMetrics.addEventListener('click', () => loadMetrics(true));

const btnRefresh = document.getElementById('btnRefresh');
if (btnRefresh) btnRefresh.addEventListener('click', doPreview);

const btnInsertJob = document.getElementById('btnInsertJob');
if (btnInsertJob) {
  btnInsertJob.addEventListener('click', () => {
    insertSnippet(`### 示例科技有限公司 · 2023.07 - 至今\n**前端工程师**\n\n- **工程化提效**：搭建 Vite + TypeScript 脚手架，构建时间缩短 70%\n- **性能优化**：核心页面首屏耗时从 3.2s 降至 1.1s`);
  });
}

const btnInsertProj = document.getElementById('btnInsertProj');
if (btnInsertProj) {
  btnInsertProj.addEventListener('click', () => {
    insertSnippet(`### 组件库建设 \`核心负责人\`\n基于设计系统沉淀 30+ 可复用组件，统一多项目视觉与交互规范。`);
  });
}

const btnInsertSkill = document.getElementById('btnInsertSkill');
if (btnInsertSkill) {
  btnInsertSkill.addEventListener('click', () => {
    insertSnippet(`- **前端**：TypeScript / React / Vue\n- **工程化**：Vite / Webpack / CI-CD\n- **性能**：SSR / 首屏优化 / 内存排查`);
  });
}

const btnZoomIn = document.getElementById('btnZoomIn');
if (btnZoomIn) btnZoomIn.addEventListener('click', () => applyScale(currentScale + 0.1, false));

const btnZoomOut = document.getElementById('btnZoomOut');
if (btnZoomOut) btnZoomOut.addEventListener('click', () => applyScale(currentScale - 0.1, false));

const btnZoomAuto = document.getElementById('btnZoomAuto');
if (btnZoomAuto) {
  btnZoomAuto.addEventListener('click', () => {
    autoFitScale();
    toast('已开启自适应缩放');
  });
}

const btnPublicPage = document.getElementById('btnPublicPage');
if (btnPublicPage) btnPublicPage.addEventListener('click', async () => {
  // 若当前编辑的文档已公开，精确跳转到它的公开页；否则跳默认公开主页
  try {
    const res = await fetch(`/api/documents/${encodeURIComponent(currentDoc)}`);
    if (res.ok) {
      const d = await res.json();
      const m = d.content && d.content.match(/^\s*public\s*:\s*(true|false)\s*$/m);
      if (m && m[1] === 'true') {
        window.open('/p/' + encodeURIComponent(currentDoc.replace(/\.md$/, '')), '_blank');
        return;
      }
    }
  } catch (e) {}
  window.open('/p', '_blank');
});

const btnToggleGuide = document.getElementById('btnToggleGuide');
if (btnToggleGuide) {
  btnToggleGuide.addEventListener('click', (e) => {
    guideVisible = !guideVisible;
    if (guidesContainer) guidesContainer.style.display = guideVisible ? 'block' : 'none';
    e.target.textContent = guideVisible ? '📏 截断线: 开' : '📏 截断线: 关';
  });
}

const resizeObserver = new ResizeObserver(() => {
  if (isAutoFit) autoFitScale();
});
if (previewWrapper) resizeObserver.observe(previewWrapper);

/* ---------- 极简兜底 Markdown 模板 ---------- */
const DEFAULT_FALLBACK_MD = `
---
template: dark
layout: full
---

# 陈亦辰

**求职方向**：前端工程师 / Web 开发
**所在地**：深圳
**电话**：136-XXXX-XXXX
**邮箱**：fe@example.com
**GitHub**：[github.com/example](https://github.com/example)

> 四年 Web 前端开发经验，专注性能优化与工程化提效。熟悉现代前端框架与构建体系，能独立完成从设计稿到可维护组件的完整落地。

## 工作经历

### 某互联网公司 · 2020.5 - 至今
**前端工程师**

- **前端工程化（核心项目）**：搭建基于 Vite + TypeScript 的脚手架与组件库，统一多项目构建与代码规范，构建时间缩短 70%。
- **性能优化**：主导核心页面性能优化，通过懒加载、SSR 与缓存策略，首屏耗时从 3.2s 降至 1.1s。
- **组件研发**：基于设计系统沉淀 30+ 可复用组件，覆盖图表、表单、虚拟列表等高频场景。

## 专业技能

- **框架**：Vue / React / TypeScript
- **工程化**：Vite / Webpack / CI-CD / 组件库
- **性能**：首屏优化 / SSR / 内存泄漏排查
- **协作**：Git 工作流 / Code Review / 技术文档

## 教育背景

### 某理工大学 · 2016.9-2020.6
软件工程 · 本科
`;
;

let lastLoadedVersionTag = '';
let isUserTyping = false;
let userTypingTimer = null;
let fileSyncPollTimer = null;

/* 标记用户正在键盘输入（防止输入时被后端覆盖） */
function markUserTyping() {
  isUserTyping = true;
  clearTimeout(userTypingTimer);
  userTypingTimer = setTimeout(() => {
    isUserTyping = false;
  }, 2500); // 停顿 2.5 秒后视为处于空闲状态
}

/* 核心：检查后端文件是否有外部修改并执行智能热更新 */
async function checkDocumentHotReload(manual = false) {
  if (!currentDoc || (isUserTyping && !manual)) return;

  try {
    const res = await fetch(`/api/documents/${encodeURIComponent(currentDoc)}/status`);
    if (!res.ok) return;
    const data = await res.json();
    if (!data.ok || !data.version_tag) return;

    // 首次记录基准版本
    if (!lastLoadedVersionTag) {
      lastLoadedVersionTag = data.version_tag;
      return;
    }

    // 检测到后端文件版本发生变动
    if (data.version_tag !== lastLoadedVersionTag) {
      lastLoadedVersionTag = data.version_tag;

      // 拉取最新文件内容
      const docRes = await fetch(`/api/documents/${encodeURIComponent(currentDoc)}`);
      if (!docRes.ok) return;
      const docData = await docRes.json();

      const currentEditorText = getEditorValue();
      if (currentEditorText === docData.content) {
        return; // 内容实质相同则无需刷新
      }

      // 如果当前没有正在编辑冲突，直接无感热替换并重新渲染预览
      setEditorValue(docData.content);
      syncTemplateFromMd();
      doPreview();
      toast(`🔄 检测到「${currentDoc.replace('.md','')}」已被外部修改，已自动热更新`);
      if (saveStatus) {
        saveStatus.innerHTML = '<span class="dot" style="background:var(--accent-blue);"></span> 外部变动已热同步';
      }
    }
  } catch (e) {
    // 静默容错
  }
}

/* 启动后台空闲轻量轮询 (每 2.5 秒比对一次状态) */
function startFileWatcher() {
  clearInterval(fileSyncPollTimer);
  fileSyncPollTimer = setInterval(() => {
    // 页面可见且未输入时执行检测
    if (!document.hidden && !isUserTyping) {
      checkDocumentHotReload(false);
    }
  }, 2500);
}

/* 窗口焦点恢复触发（从终端/其他编辑器切回浏览器时立即检测） */
window.addEventListener('focus', () => {
  checkDocumentHotReload(true);
});
document.addEventListener('visibilitychange', () => {
  if (!document.hidden) {
    checkDocumentHotReload(true);
  }
});
async function initWorkspaceAfterAuth() {
  await loadTemplates();
  await loadDocumentList();
  try {
    const res = await fetch(`/api/documents/${encodeURIComponent(currentDoc)}`);
    if (res.ok) {
      const data = await res.json();
      currentContent = data.content;
      lastLoadedVersionTag = data.version_tag || '';
    }
  } catch(e) {}

  await initMonacoEditor();
  doPreview();
  startFileWatcher();
}

/* 页面启动入口 */
(async () => {
  const urlParams = new URLSearchParams(window.location.search);
  const paramDoc = urlParams.get('doc');
  if (paramDoc) currentDoc = paramDoc;

  if (currentDocLabel) currentDocLabel.textContent = currentDoc;

  const authed = await checkAuth();
  if (authed) {
    await initWorkspaceAfterAuth();
  }
})();
