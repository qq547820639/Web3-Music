const API = '';
const state = {
  token: '',
  workspace: localStorage.getItem('resonance_workspace') || '',
  user: null,
  memberships: [],
  bootstrap: null,
  projects: [],
  projectId: null,
  detail: null,
  quote: null,
  assets: [],
  activeAsset: null,
  candidates: [],
  blindMode: false,
  view: 'creation',
  lists: {
    projects: { offset: 0, limit: 20, q: '', status: '', total: 0 },
    assets: { offset: 0, limit: 20, q: '', status: '', total: 0 },
    orders: { offset: 0, limit: 20, q: '', status: '', total: 0 },
    licenses: { offset: 0, limit: 20, q: '', status: '', total: 0 },
    tickets: { offset: 0, limit: 20, q: '', status: '', total: 0 }
  }
};
const $ = s => document.querySelector(s);
const $$ = s => [...document.querySelectorAll(s)];
const sleep = ms => new Promise(r => setTimeout(r, ms));
localStorage.removeItem('resonance_token');
function cookie(name) {
  return document.cookie.split('; ').find(x => x.startsWith(name + '='))?.split('=').slice(1).join('=') || '';
}
function escapeHtml(v) {
  return String(v ?? '').replace(/[&<>"']/g, c => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#39;'
  })[c]);
}
function fmtMoney(amount, currency = 'USD') {
  return new Intl.NumberFormat('zh-CN', {
    style: 'currency',
    currency
  }).format((Number(amount) || 0) / 100);
}
function fmtDate(v) {
  if (!v) return '—';
  try {
    return new Intl.DateTimeFormat('zh-CN', {
      dateStyle: 'medium',
      timeStyle: 'short'
    }).format(new Date(v));
  } catch {
    return v;
  }
}
function toast(message, type = 'info') {
  const el = $('#toast');
  el.textContent = message;
  el.hidden = false;
  el.style.borderColor = type === 'error' ? 'var(--danger)' : type === 'ok' ? 'var(--ok)' : 'var(--line)';
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => el.hidden = true, 4200);
}
let lastDialogTrigger = null;
function rememberDialogTrigger() {
  lastDialogTrigger = document.activeElement;
}
function restoreDialogFocus() {
  if (lastDialogTrigger && typeof lastDialogTrigger.focus === 'function') {
    lastDialogTrigger.focus();
  }
  lastDialogTrigger = null;
}
function firstFocusable(root) {
  return root.querySelector('button, [href], input, select, textarea, audio, [tabindex]:not([tabindex="-1"])');
}
function showDialog(html) {
  rememberDialogTrigger();
  $('#dialogContent').innerHTML = html;
  const dialog = $('#dialog');
  dialog.showModal();
  const target = firstFocusable($('#dialogContent')) || dialog.querySelector('.dialog-close');
  if (target) target.focus();
}
function closeDialog() {
  if ($('#dialog').open) $('#dialog').close();
}
// 原生 <dialog>.showModal() 自带焦点陷阱与 Escape 关闭；这里补“关闭后焦点还原到触发按钮”。
$('#dialog').addEventListener('close', restoreDialogFocus);
const ERROR_MESSAGES = {
  revision_conflict: '版本已被其他人更新，请刷新后重试',
  locked_or_invalid_patch: '修改触及锁定字段或补丁无效',
  idempotency_conflict: '请求冲突，请勿重复提交',
  quote_already_used: '该报价已被使用，请重新生成报价',
  quote_expired: '报价已过期，请重新生成',
  quote_hash_mismatch: '报价校验失败，请重新生成',
  policy_block: '内容策略已阻止该请求',
  candidate_already_mastered: '该候选已被设为 Master',
  candidate_revision_mismatch: '候选版本不匹配，请刷新后重试',
  no_refundable_payment: '没有可退款的支付记录'
};
function humanizeError(status, detail) {
  let code = '';
  let message = '';
  if (typeof detail === 'string') {
    message = detail;
  } else if (detail && typeof detail === 'object') {
    const inner = detail.detail !== undefined ? detail.detail : detail;
    if (typeof inner === 'string') {
      message = inner;
    } else if (inner && typeof inner === 'object') {
      code = inner.code || '';
      message = inner.message || '';
    }
  }
  if (code && ERROR_MESSAGES[code]) return ERROR_MESSAGES[code];
  if (message && typeof message === 'string' && message.trim()) return message.trim();
  return `请求失败（HTTP ${status}）`;
}
function setLoading(btn, loading, loadingLabel = '处理中…') {
  if (!btn) return;
  if (loading) {
    if (btn.dataset.loading === '1') return;
    btn.dataset.loading = '1';
    btn.dataset.origLabel = btn.textContent;
    btn.classList.add('loading');
    btn.textContent = loadingLabel;
    btn.disabled = true;
    btn.setAttribute('aria-busy', 'true');
  } else {
    btn.dataset.loading = '0';
    btn.classList.remove('loading');
    if (btn.dataset.origLabel !== undefined) btn.textContent = btn.dataset.origLabel;
    btn.disabled = false;
    btn.removeAttribute('aria-busy');
  }
}
function askDialog({
  title,
  description = '',
  fields = [],
  submitText = '确定',
  cancelText = '取消'
}) {
  return new Promise(resolve => {
    rememberDialogTrigger();
    const dialog = $('#dialog');
    const content = $('#dialogContent');
    if (dialog.open) dialog.close();
    const uid = 'd' + Math.random().toString(36).slice(2, 8);
    let settled = false;
    const finish = value => {
      if (settled) return;
      settled = true;
      resolve(value);
    };
    content.innerHTML = `<p class="eyebrow">${escapeHtml(title)}</p>${description ? `<p class="muted">${escapeHtml(description)}</p>` : ''}<div class="dialog-fields"></div><div class="dialog-error error"></div><div class="row dialog-actions"><button type="button" class="secondary dialog-cancel">${escapeHtml(cancelText)}</button><button type="button" class="dialog-submit">${escapeHtml(submitText)}</button></div>`;
    const fieldsBox = content.querySelector('.dialog-fields');
    const inputs = {};
    for (const f of fields) {
      const wrap = document.createElement('label');
      wrap.textContent = f.label;
      let el;
      if (f.multiline) {
        el = document.createElement('textarea');
        el.rows = f.rows || 4;
      } else if (f.type === 'select') {
        el = document.createElement('select');
        for (const opt of f.options || []) {
          const o = document.createElement('option');
          o.value = opt.value;
          o.textContent = opt.label;
          el.appendChild(o);
        }
      } else {
        el = document.createElement('input');
        el.type = f.type || 'text';
      }
      if (f.placeholder) el.placeholder = f.placeholder;
      if (f.value !== undefined && f.value !== null) el.value = String(f.value);
      if (f.required !== false) el.required = true;
      wrap.appendChild(el);
      fieldsBox.appendChild(wrap);
      inputs[f.name] = el;
    }
    const errorBox = content.querySelector('.dialog-error');
    const submit = () => {
      const out = {};
      for (const f of fields) {
        const el = inputs[f.name];
        const v = String(el.value).trim();
        if (f.required !== false && !v) {
          errorBox.textContent = f.error || `请填写${f.label}`;
          el.focus();
          return;
        }
        out[f.name] = f.type === 'number' ? Number(v) : v;
      }
      finish(out);
      dialog.close();
    };
    content.querySelector('.dialog-submit').onclick = submit;
    content.querySelector('.dialog-cancel').onclick = () => {
      finish(null);
      dialog.close();
    };
    const onClose = () => {
      finish(null);
      dialog.removeEventListener('close', onClose);
    };
    dialog.addEventListener('close', onClose);
    for (const f of fields) {
      inputs[f.name].addEventListener('keydown', e => {
        if (e.key === 'Enter' && !f.multiline) {
          e.preventDefault();
          submit();
        }
      });
    }
    dialog.showModal();
    const first = fields[0];
    if (first) inputs[first.name].focus();
  });
}
function confirmDialog({
  title,
  message,
  confirmText = '确认',
  cancelText = '取消',
  danger = false
}) {
  return new Promise(resolve => {
    rememberDialogTrigger();
    const dialog = $('#dialog');
    const content = $('#dialogContent');
    if (dialog.open) dialog.close();
    let settled = false;
    const finish = value => {
      if (settled) return;
      settled = true;
      resolve(value);
    };
    content.innerHTML = `<p class="eyebrow">${escapeHtml(title)}</p><p class="muted">${escapeHtml(message)}</p><div class="row dialog-actions"><button type="button" class="secondary dialog-cancel">${escapeHtml(cancelText)}</button><button type="button" class="dialog-submit${danger ? ' danger' : ''}">${escapeHtml(confirmText)}</button></div>`;
    content.querySelector('.dialog-submit').onclick = () => {
      finish(true);
      dialog.close();
    };
    content.querySelector('.dialog-cancel').onclick = () => {
      finish(false);
      dialog.close();
    };
    const onClose = () => {
      finish(false);
      dialog.removeEventListener('close', onClose);
    };
    dialog.addEventListener('close', onClose);
    dialog.showModal();
    content.querySelector('.dialog-submit').focus();
  });
}
async function api(path, options = {}, retry = true) {
  const headers = {
    ...(options.headers || {})
  };
  const method = (options.method || 'GET').toUpperCase();
  if (options.body && !(options.body instanceof FormData)) headers['Content-Type'] = 'application/json';
  if (state.token) headers.Authorization = 'Bearer ' + state.token;
  if (state.workspace) headers['X-Workspace-Id'] = state.workspace;
  if (!['GET', 'HEAD', 'OPTIONS'].includes(method) && !headers.Authorization) {
    const csrf = decodeURIComponent(cookie('resonance_csrf'));
    if (csrf) headers['X-CSRF-Token'] = csrf;
  }
  const response = await fetch(API + path, {
    ...options,
    headers,
    credentials: 'same-origin'
  });
  if (response.status === 401 && retry && !path.startsWith('/api/auth/')) {
    const refreshed = await fetch(API + '/api/auth/refresh', {
      method: 'POST',
      credentials: 'same-origin'
    });
    if (refreshed.ok) return api(path, options, false);
  }
  if (!response.ok) {
    let detail;
    try {
      detail = await response.json();
    } catch {
      detail = await response.text();
    }
    const e = new Error(humanizeError(response.status, detail));
    e.status = response.status;
    throw e;
  }
  if (response.status === 204) return null;
  const ct = response.headers.get('content-type') || '';
  return ct.includes('json') ? response.json() : response;
}
function setAuthScreen(logged) {
  $('#login').hidden = logged;
  $('#app').hidden = !logged;
}
$('#loginForm').addEventListener('submit', async e => {
  e.preventDefault();
  $('#loginError').textContent = '';
  const btn = $('#loginForm button[type="submit"]');
  setLoading(btn, true, '登录中…');
  try {
    const data = await api('/api/auth/login', {
      method: 'POST',
      body: JSON.stringify({
        email: $('#email').value,
        password: $('#password').value
      })
    });
    state.token = '';
    state.workspace = data.workspaces[0]?.id || '';
    localStorage.setItem('resonance_workspace', state.workspace);
    await init();
  } catch (err) {
    $('#loginError').textContent = err.message;
  } finally {
    setLoading(btn, false);
  }
});
$('#logout').onclick = async () => {
  try {
    await api('/api/auth/logout', {
      method: 'POST'
    });
  } catch {}
  localStorage.removeItem('resonance_workspace');
  location.reload();
};
async function init() {
  try {
    const me = await api('/api/auth/me');
    state.user = me.user;
    state.memberships = me.workspaces;
    if (!state.memberships.some(w => w.id === state.workspace)) state.workspace = state.memberships[0]?.id || '';
    const ws = $('#workspace');
    ws.innerHTML = state.memberships.map(w => `<option value="${w.id}">${escapeHtml(w.name)} · ${escapeHtml(w.role)}</option>`).join('');
    ws.value = state.workspace;
    ws.onchange = async () => {
      state.workspace = ws.value;
      localStorage.setItem('resonance_workspace', state.workspace);
      state.projectId = null;
      state.detail = null;
      await refreshBootstrap();
      await loadProjects();
      await navigate(state.view);
    };
    $('#identity').textContent = state.user.display_name;
    setAuthScreen(true);
    await refreshBootstrap();
    await loadProjects();
    bindNavigation();
    bindListControls();
    $('#blindToggle').onchange = () => {
      state.blindMode = $('#blindToggle').checked;
      renderCandidates(state.candidates);
    };
    await navigate('creation');
  } catch (err) {
    console.error(err);
    setAuthScreen(false);
  }
}
async function refreshBootstrap() {
  state.bootstrap = await api('/api/bootstrap');
  const available = state.bootstrap.credits?.available ?? 0;
  $('#creditPill').textContent = `${Number(available).toFixed(0)} Credits`;
}
function bindNavigation() {
  $$('#mainNav [data-view]').forEach(b => b.onclick = () => navigate(b.dataset.view));
  $$('.market-tabs [data-market]').forEach(b => b.onclick = () => {
    const name = b.dataset.market;
    $$('.market-tabs button').forEach(x => {
      x.classList.toggle('active', x === b);
      x.setAttribute('aria-selected', x === b ? 'true' : 'false');
    });
    $$('.market-pane').forEach(x => x.classList.toggle('active', x.id === `market-${name}`));
    if (name === 'offers') loadOffers();
    if (name === 'orders') loadOrders();
    if (name === 'briefs') loadBriefs();
  });
}
async function navigate(view) {
  state.view = view;
  $$('#mainNav button').forEach(b => {
    const active = b.dataset.view === view;
    b.classList.toggle('active', active);
    if (active) b.setAttribute('aria-current', 'page');
    else b.removeAttribute('aria-current');
  });
  $$('.view').forEach(v => v.classList.toggle('active', v.id === `view-${view}`));
  try {
    if (view === 'assets') await loadAssets();
    if (view === 'market') await loadCatalog();
    if (view === 'account') await loadAccount();
  } catch (err) {
    toast(err.message, 'error');
  }
}

function fmtDuration(ms) {
  if (ms === null || ms === undefined || isNaN(Number(ms))) return '--:--';
  const total = Math.round(Number(ms) / 1000);
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}:${String(s).padStart(2, '0')}`;
}
function debounce(fn, wait) {
  let timer = null;
  return function(...args) {
    clearTimeout(timer);
    timer = setTimeout(() => fn.apply(this, args), wait);
  };
}

// ---- List pagination / search / status filter (B4) ----
function listQuery(name) {
  const s = state.lists[name];
  const p = new URLSearchParams({ limit: String(s.limit), offset: String(s.offset) });
  if (s.q) p.set('q', s.q);
  if (s.status) p.set('status', s.status);
  return '?' + p.toString();
}
function searchHtml(name, placeholder) {
  return `<input class="list-search" data-list-search="${name}" placeholder="${escapeHtml(placeholder)}" value="${escapeHtml(state.lists[name].q)}" aria-label="搜索：${escapeHtml(placeholder)}">`;
}
function statusTabsHtml(name, options) {
  const cur = state.lists[name].status;
  const tabs = [['', '全部'], ...options].map(([v, label]) => `<button class="tab-btn${cur === v ? ' active' : ''}" data-list-status="${name}" data-status="${v}" aria-pressed="${cur === v ? 'true' : 'false'}">${escapeHtml(label)}</button>`).join('');
  return `<div class="status-tabs" role="group" aria-label="状态筛选">${tabs}</div>`;
}
function pagerHtml(name) {
  const s = state.lists[name];
  const page = Math.floor(s.offset / s.limit) + 1;
  const pages = Math.max(1, Math.ceil(s.total / s.limit));
  return `<div class="pager"><button class="secondary pager-btn" data-pager="${name}" data-dir="-1" aria-label="上一页"${page <= 1 ? ' disabled' : ''}>上一页</button><span class="muted">${page} / ${pages} · 共 ${s.total} 条</span><button class="secondary pager-btn" data-pager="${name}" data-dir="1" aria-label="下一页"${page >= pages ? ' disabled' : ''}>下一页</button></div>`;
}
function pageList(name, dir) {
  const s = state.lists[name];
  const page = Math.floor(s.offset / s.limit) + 1;
  const pages = Math.max(1, Math.ceil(s.total / s.limit));
  const next = page + dir;
  if (next < 1 || next > pages) return;
  s.offset = (next - 1) * s.limit;
  reloadList(name);
}
function reloadList(name) {
  if (name === 'projects') return loadProjects(true);
  if (name === 'assets') return loadAssets();
  if (name === 'orders' || name === 'licenses') return loadOrders();
  if (name === 'tickets') return loadTickets();
}
function bindListControls() {
  document.addEventListener('click', e => {
    const pager = e.target.closest && e.target.closest('[data-pager]');
    if (pager) {
      pageList(pager.dataset.pager, Number(pager.dataset.dir));
      return;
    }
    const tab = e.target.closest && e.target.closest('[data-list-status]');
    if (tab) {
      const name = tab.dataset.listStatus;
      const s = state.lists[name];
      s.status = tab.dataset.status;
      s.offset = 0;
      reloadList(name);
    }
  });
  document.addEventListener('input', debounce(e => {
    const search = e.target.closest && e.target.closest('[data-list-search]');
    if (!search) return;
    const name = search.dataset.listSearch;
    const s = state.lists[name];
    s.q = search.value.trim();
    s.offset = 0;
    reloadList(name);
  }, 300));
}

// ---- Generation step timeline (B1) ----
const STEP_NAMES = {
  provider_submit: '提交供应商',
  media_ingest: '媒体入库',
  submit: '已提交',
  provider: '生成中',
  ingest: '入库',
  settle: '结算'
};
const STEP_STATUS = {
  started: ['进行中', 'processing'],
  completed: ['成功', 'completed'],
  failed: ['失败', 'failed'],
  compensated: ['补偿', 'compensated']
};
function stepLabel(name) {
  return STEP_NAMES[name] || name;
}
function stepDuration(s) {
  if (!s.ended_at || !s.started_at) return '';
  const ms = Date.parse(s.ended_at) - Date.parse(s.started_at);
  if (isNaN(ms)) return '';
  return ms < 1000 ? `${ms}ms` : `${(ms / 1000).toFixed(1)}s`;
}
function renderJobSteps(steps) {
  const box = $('#jobSteps');
  if (!box) return;
  if (!steps || !steps.length) {
    box.innerHTML = '';
    box.hidden = true;
    return;
  }
  box.hidden = false;
  box.innerHTML = `<div class="stepper">${steps.map(s => {
    const meta = STEP_STATUS[s.status] || [s.status, s.status];
    const err = s.error ? (typeof s.error === 'string' ? s.error : (s.error.message || s.error.type || JSON.stringify(s.error))) : '';
    return `<div class="step ${s.status}"><div class="step-dot"></div><div class="step-body"><div class="row"><b>${escapeHtml(stepLabel(s.step_name))}</b><span class="status ${meta[1]}">${escapeHtml(meta[0])}</span></div><small class="muted">第 ${s.attempt} 次${s.started_at ? ' · ' + fmtDate(s.started_at) : ''}${stepDuration(s) ? ' · ' + stepDuration(s) : ''}</small>${err ? `<div class="step-error">${escapeHtml(err)}</div>` : ''}</div></div>`;
  }).join('')}</div>`;
}

// ---- 28-dimension radar (B3, native SVG, zero deps) ----
const RADAR_GROUP_META = {
  Core: { label: '核心维度', color: 'var(--primary)' },
  Important: { label: '重要维度', color: 'var(--blue)' },
  Auxiliary: { label: '辅助维度', color: 'var(--warn)' }
};
const CRITICAL_DIM = {
  CriticalPenalty_TSMI: 'V27',
  CriticalPenalty_PAC: 'V16',
  CriticalPenalty_C3AC: 'V21'
};
function radarChart(group, dims, criticalCodes) {
  const size = 240;
  const levels = 5;
  const cx = size / 2;
  const cy = size / 2;
  const radius = size / 2 - 42;
  const n = dims.length;
  const angle = i => -Math.PI / 2 + (2 * Math.PI * i) / n;
  const pt = (i, r) => [cx + r * Math.cos(angle(i)), cy + r * Math.sin(angle(i))];
  const parts = [];
  for (let l = 1; l <= levels; l++) {
    const r = radius * l / levels;
    const pts = dims.map((_, i) => pt(i, r).map(v => v.toFixed(1)).join(',')).join(' ');
    parts.push(`<polygon points="${pts}" class="radar-ring"></polygon>`);
  }
  for (let i = 0; i < n; i++) {
    const [x, y] = pt(i, radius);
    parts.push(`<line x1="${cx}" y1="${cy}" x2="${x.toFixed(1)}" y2="${y.toFixed(1)}" class="radar-axis"></line>`);
  }
  const dataPts = dims.map((d, i) => {
    const r = Math.max(0, Math.min(1, (Number(d.raw) || 0) / 5)) * radius;
    return pt(i, r).map(v => v.toFixed(1)).join(',');
  }).join(' ');
  const color = RADAR_GROUP_META[group].color;
  parts.push(`<polygon points="${dataPts}" class="radar-data" style="fill:${color}"></polygon>`);
  dims.forEach((d, i) => {
    const [x, y] = pt(i, radius);
    const lx = cx + (radius + 16) * Math.cos(angle(i));
    const ly = cy + (radius + 16) * Math.sin(angle(i));
    const crit = criticalCodes.has(d.code);
    const raw = Number(d.raw) || 0;
    parts.push(`<circle class="radar-point${crit ? ' critical' : ''}" cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="3.4" tabindex="0" role="img" aria-label="${escapeHtml(d.code)} ${escapeHtml(d.name)}：${raw} / ${d.max || 5}" data-radar-point data-code="${escapeHtml(d.code)}" data-name="${escapeHtml(d.name)}" data-group="${group}" data-val="${raw}" data-max="${d.max || 5}" data-note="${escapeHtml(d.note || '')}"></circle>`);
    parts.push(`<text x="${lx.toFixed(1)}" y="${ly.toFixed(1)}" class="radar-label" text-anchor="middle" dominant-baseline="middle">${escapeHtml(d.code)}</text>`);
  });
  return `<div class="radar-chart"><div class="radar-chart-head"><span class="radar-dot" style="background:${color}"></span><b>${RADAR_GROUP_META[group].label}</b><span class="muted">${n} 维</span></div><svg viewBox="0 0 ${size} ${size}" width="${size}" height="${size}" role="img" aria-label="${RADAR_GROUP_META[group].label} 雷达图（${n} 维）">${parts.join('')}</svg></div>`;
}
function asObj(v) {
  if (v === null || v === undefined) return v;
  if (typeof v === 'string') {
    try {
      return JSON.parse(v);
    } catch {
      return v;
    }
  }
  return v;
}
function renderRadar(dims, vars) {
  const box = $('#radarGrid');
  if (!box) return;
  dims = dims || [];
  vars = vars || {};
  const criticalCodes = new Set(Object.entries(CRITICAL_DIM).filter(([k]) => Number(vars[k]) > 0).map(([, v]) => v));
  const groups = ['Core', 'Important', 'Auxiliary'];
  const charts = groups.map(g => {
    const gd = dims.filter(d => d.priority === g);
    return gd.length ? radarChart(g, gd, criticalCodes) : '';
  }).join('');
  const dimsList = dims.length ? `<details class="radar-dims"><summary>维度列表（${dims.length} 维，含分值明细）</summary><ul>${dims.map(d => `<li><b>${escapeHtml(d.code)}</b> ${escapeHtml(d.name)} · ${escapeHtml(d.priority || '')} · 分值 ${Number(d.raw) || 0}/${d.max || 5}${d.note ? ` · ${escapeHtml(d.note)}` : ''}</li>`).join('')}</ul></details>` : '';
  box.innerHTML = `${charts}${dimsList}<div class="radar-legend muted">${groups.map(g => `<span><i class="radar-dot" style="background:${RADAR_GROUP_META[g].color}"></i>${RADAR_GROUP_META[g].label}</span>`).join('')}${criticalCodes.size ? '<span class="radar-critical">红点 = CriticalPenalty 命中</span>' : ''}</div>`;
  $$('#radarGrid [data-radar-point]').forEach(el => {
    el.addEventListener('mousemove', e => moveRadarTip(e, el));
    el.addEventListener('mouseleave', hideRadarTip);
    el.addEventListener('focus', () => showRadarTipFor(el));
    el.addEventListener('blur', hideRadarTip);
  });
}
function radarTipHtml(el) {
  return `<b>${escapeHtml(el.dataset.code)} ${escapeHtml(el.dataset.name)}</b><br><span class="muted">${escapeHtml(el.dataset.group)}</span> · 分值 <b>${escapeHtml(el.dataset.val)}</b> / ${escapeHtml(el.dataset.max)}${el.dataset.note ? `<br><span class="muted">${escapeHtml(el.dataset.note)}</span>` : ''}`;
}
function moveRadarTip(e, el) {
  const tip = $('#radarTip');
  if (!tip) return;
  tip.innerHTML = radarTipHtml(el);
  tip.hidden = false;
  tip.style.left = Math.min(e.clientX + 14, window.innerWidth - 250) + 'px';
  tip.style.top = (e.clientY + 14) + 'px';
}
function showRadarTipFor(el) {
  const tip = $('#radarTip');
  if (!tip) return;
  tip.innerHTML = radarTipHtml(el);
  tip.hidden = false;
  const r = el.getBoundingClientRect();
  tip.style.left = Math.min(r.left + 14, window.innerWidth - 250) + 'px';
  tip.style.top = (r.top + 14) + 'px';
}
function hideRadarTip() {
  const tip = $('#radarTip');
  if (tip) tip.hidden = true;
}

// Creation OS
async function loadProjects(preserveSelection = false) {
  const data = await api('/api/projects' + listQuery('projects'));
  state.lists.projects.total = data.total || 0;
  state.projects = data.items || [];
  renderProjects();
  if (!preserveSelection) {
    if (state.projectId && state.projects.some(p => p.id === state.projectId)) await selectProject(state.projectId);
    else {
      $('#creationEmpty').hidden = false;
      $('#studio').hidden = true;
      $('#branches').innerHTML = '<span class="muted">选择项目后显示</span>';
    }
  }
}
function renderProjects() {
  $('#projectTools').innerHTML = searchHtml('projects', '搜索项目标题…') + statusTabsHtml('projects', [['active', '进行中'], ['legal_hold', '法务冻结']]);
  $('#projectPager').innerHTML = pagerHtml('projects');
  $('#projects').innerHTML = state.projects.length ? state.projects.map(p => `<div class="project-item ${p.id === state.projectId ? 'active' : ''}" data-project="${p.id}"><b>${escapeHtml(p.title)}</b><small>R${p.current_revision} · ${p.candidate_count || 0} 候选${p.latest_asset_id ? ' · 已有 Master' : ''}</small></div>`).join('') : '<p class="muted">暂无项目</p>';
  $$('[data-project]').forEach(el => el.onclick = () => selectProject(el.dataset.project));
}
async function createProject() {
  const result = await askDialog({
    title: '新建歌曲项目',
    description: '输入歌曲名称，系统会为它建立一个不可变的创作版本链。',
    fields: [{
      name: 'title',
      label: '歌曲名称',
      placeholder: '城市夜归',
      value: '城市夜归'
    }],
    submitText: '创建'
  });
  if (!result) return;
  const btn = $('#newProject');
  setLoading(btn, true, '创建中…');
  try {
    const p = await api('/api/projects', {
      method: 'POST',
      body: JSON.stringify({
        title: result.title
      })
    });
    state.projectId = p.id;
    await loadProjects();
    toast('项目已创建', 'ok');
  } catch (err) {
    toast(err.message, 'error');
  } finally {
    setLoading(btn, false);
  }
}
$('#newProject').onclick = createProject;
$('#emptyCreate').onclick = createProject;
async function selectProject(id) {
  state.projectId = id;
  renderProjects();
  $('#creationEmpty').hidden = true;
  $('#studio').hidden = false;
  await refreshProject();
}
async function refreshProject() {
  if (!state.projectId) return;
  const [detail, branches, comments] = await Promise.all([api(`/api/projects/${state.projectId}`), api(`/api/projects/${state.projectId}/branches`), api(`/api/projects/${state.projectId}/comments`)]);
  state.detail = detail;
  renderProjectDetail();
  renderBranches(branches);
  renderComments(comments);
}
function renderProjectDetail() {
  const d = state.detail;
  const spec = d.revision.spec || {};
  renderJobSteps([]);
  $('#projectTitle').textContent = d.project.title;
  $('#revisionBadge').textContent = `Revision ${d.project.current_revision}`;
  $('#projectStatus').textContent = d.project.status;
  $('#styles').value = spec.styles || '';
  $('#lyrics').value = spec.lyrics || '';
  $('#locks').value = (d.project.locked_paths || []).join(',');
  renderQuality(d.quality);
  renderJobs(d.jobs);
  renderCandidates(d.candidates);
  $('#assetInfo').innerHTML = d.assets?.length ? `<b>${d.assets.length} 个不可变资产快照</b> · 最新 ${escapeHtml(d.assets[0].id)}` : '从 Ready Candidate 中选择 Master。';
  $('#saveState').textContent = '已同步';
}
function renderBranches(rows) {
  $('#branches').innerHTML = rows.map(b => `<div class="branch-item"><span>${escapeHtml(b.name)}</span><span>R${b.head_revision}</span></div>`).join('');
}
$('#newBranch').onclick = async () => {
  if (!state.projectId) return toast('请先选择项目', 'error');
  const result = await askDialog({
    title: '新建版本分支',
    description: `从当前 Revision ${state.detail.project.current_revision} 派生一个新分支。`,
    fields: [{
      name: 'name',
      label: '分支名称',
      placeholder: 'alternative',
      value: 'alternative'
    }],
    submitText: '创建'
  });
  if (!result) return;
  const btn = $('#newBranch');
  setLoading(btn, true, '创建中…');
  try {
    await api(`/api/projects/${state.projectId}/branches`, {
      method: 'POST',
      body: JSON.stringify({
        name: result.name,
        base_revision: state.detail.project.current_revision
      })
    });
    await refreshProject();
    toast('分支已创建', 'ok');
  } catch (e) {
    toast(e.message, 'error');
  } finally {
    setLoading(btn, false);
  }
};
$('#refresh').onclick = refreshProject;
$('#saveLocks').onclick = async () => {
  const btn = $('#saveLocks');
  setLoading(btn, true, '保存中…');
  try {
    const locked_paths = $('#locks').value.split(',').map(x => x.trim()).filter(Boolean);
    await api(`/api/projects/${state.projectId}/locks`, {
      method: 'PUT',
      body: JSON.stringify({
        locked_paths
      })
    });
    await refreshProject();
    toast('锁定规则已保存', 'ok');
  } catch (e) {
    toast(e.message, 'error');
  } finally {
    setLoading(btn, false);
  }
};
async function doChat(apply) {
  const message = $('#chatMessage').value.trim();
  if (!message) return toast('请输入修改要求', 'error');
  const btn = apply ? $('#chatApply') : $('#chatPreview');
  $('#conversation').insertAdjacentHTML('beforeend', `<div class="user-message">${escapeHtml(message)}</div>`);
  setLoading(btn, true, apply ? '生成中…' : '预览中…');
  try {
    const data = await api(`/api/projects/${state.projectId}/chat`, {
      method: 'POST',
      body: JSON.stringify({
        message,
        base_revision: state.detail.project.current_revision,
        apply
      })
    });
    $('#aiSource').textContent = data.proposal.source || 'AI';
    const ops = data.proposal.operations || [];
    $('#conversation').insertAdjacentHTML('beforeend', `<div class="assistant-message"><b>${escapeHtml(data.proposal.reason || 'Patch proposal')}</b><br>${ops.length ? ops.map(o => `${escapeHtml(o.op)} ${escapeHtml(o.path)}`).join('<br>') : '没有可应用的修改'}${data.applied ? `<br><span class="tag">已创建 R${data.applied.revision}</span>` : ''}</div>`);
    $('#conversation').scrollTop = $('#conversation').scrollHeight;
    if (apply) {
      await refreshProject();
      await loadProjects();
    } else showDialog(`<p class="eyebrow">PATCH PREVIEW</p><h2>结构化修改提议</h2><pre>${escapeHtml(JSON.stringify(data.proposal, null, 2))}</pre>`);
  } catch (e) {
    toast(e.message, 'error');
  } finally {
    setLoading(btn, false);
  }
}
$('#chatApply').onclick = () => doChat(true);
$('#chatPreview').onclick = () => doChat(false);
$('#manualSave').onclick = async () => {
  const spec = state.detail.revision.spec;
  const operations = [];
  if ($('#styles').value !== spec.styles) operations.push({
    op: 'replace',
    path: '/styles',
    value: $('#styles').value
  });
  if ($('#lyrics').value !== spec.lyrics) operations.push({
    op: 'replace',
    path: '/lyrics',
    value: $('#lyrics').value
  });
  if (!operations.length) return toast('没有检测到修改');
  const btn = $('#manualSave');
  setLoading(btn, true, '保存中…');
  try {
    $('#saveState').textContent = '保存中…';
    await api(`/api/projects/${state.projectId}/patch`, {
      method: 'POST',
      body: JSON.stringify({
        base_revision: state.detail.project.current_revision,
        operations,
        reason: 'manual editor save'
      })
    });
    await refreshProject();
    await loadProjects();
    toast('已保存为新 Revision', 'ok');
  } catch (e) {
    $('#saveState').textContent = '保存失败';
    toast(e.message, 'error');
  } finally {
    setLoading(btn, false);
  }
};
$('#showVersions').onclick = async () => {
  if (!state.projectId) return;
  const rows = await api(`/api/projects/${state.projectId}/revisions`);
  showDialog(`<p class="eyebrow">VERSION HISTORY</p><h2>${escapeHtml(state.detail.project.title)}</h2><table class="dialog-table"><thead><tr><th>Revision</th><th>来源</th><th>原因</th><th>时间</th></tr></thead><tbody>${rows.map(r => `<tr><td>R${r.revision}</td><td>${escapeHtml(r.actor_type)}</td><td>${escapeHtml(r.reason || '')}</td><td>${fmtDate(r.created_at)}</td></tr>`).join('')}</tbody></table>`);
};
$('#quality').onclick = async () => {
  const btn = $('#quality');
  setLoading(btn, true, '评估中…');
  try {
    const q = await api(`/api/projects/${state.projectId}/quality`, {
      method: 'POST'
    });
    renderQuality(q);
    await api('/api/events', {
      method: 'POST',
      body: JSON.stringify({
        event_name: 'quality_viewed',
        project_id: state.projectId,
        properties: {
          revision: state.detail.project.current_revision,
          score: q.score
        }
      })
    });
    toast('质量评估完成', 'ok');
  } catch (e) {
    toast(e.message, 'error');
  } finally {
    setLoading(btn, false);
  }
};
function renderQuality(q) {
  if (!q) {
    $('#qualityGrade').textContent = '—';
    $('#qualitySummary').textContent = '尚未评估';
    $('#radarGrid').innerHTML = '';
    $('#risks').innerHTML = '';
    return;
  }
  const dimensions = asObj(q.dimensions) || [];
  const variables = asObj(q.variables) || {};
  const risks = asObj(q.risks) || [];
  $('#qualityGrade').textContent = `${q.grade}\n${q.score}`;
  $('#qualitySummary').textContent = `Styles ${q.styles_score ?? '—'} · Lyrics ${q.lyrics_score ?? '—'} · TEE ${variables.TEE_pct_final ?? '—'}`;
  renderRadar(dimensions, variables);
  $('#risks').innerHTML = risks.slice(0, 5).map(x => `<div class="risk ${x.severity}">${escapeHtml(x.dimension)} · ${escapeHtml(x.message)}</div>`).join('');
}
$('#makeQuote').onclick = async () => {
  const btn = $('#makeQuote');
  setLoading(btn, true, '报价生成中…');
  try {
    state.quote = await api(`/api/projects/${state.projectId}/quotes`, {
      method: 'POST',
      body: JSON.stringify({
        spec_revision: state.detail.project.current_revision,
        candidate_count: Number($('#candidateCount').value),
        scenario: $('#scenario').value
      })
    });
    const q = state.quote.quote;
    $('#quoteBox').innerHTML = `<b>${q.candidate_count} 个候选 · ${q.total_credits} Credits</b><br>绑定 Revision ${q.spec_revision}<br>Provider：${escapeHtml(q.provider)}<br>权利：${escapeHtml(q.rights_summary?.commercial_use || 'unknown')}<br>${escapeHtml(q.partial_success_policy)}`;
    setLoading($('#submitJob'), false);
  } catch (e) {
    toast(e.message, 'error');
  } finally {
    setLoading(btn, false);
  }
};
$('#submitJob').onclick = async () => {
  if (!state.quote) return;
  const btn = $('#submitJob');
  setLoading(btn, true, '提交中…');
  try {
    const job = await api('/api/jobs', {
      method: 'POST',
      headers: {
        'Idempotency-Key': crypto.randomUUID()
      },
      body: JSON.stringify({
        quote_id: state.quote.id,
        quote_hash: state.quote.quote_hash,
        user_confirmation: true
      })
    });
    state.quote = null;
    btn.textContent = '任务执行中…';
    toast('任务已提交，Credits 已冻结', 'ok');
    await refreshBootstrap();
    await waitJob(job.id);
  } catch (e) {
    setLoading(btn, false);
    toast(e.message, 'error');
  }
};
async function waitJob(id) {
  for (let i = 0; i < 90; i++) {
    await sleep(1200);
    const d = await api('/api/jobs/' + id);
    renderJobs([d.job, ...(state.detail.jobs || []).filter(x => x.id !== id)]);
    renderJobSteps(d.steps);
    if (['completed', 'partial', 'failed', 'dead_letter', 'cancelled'].includes(d.job.status)) {
      await refreshProject();
      await refreshBootstrap();
      await loadProjects();
      renderJobSteps(d.steps);
      toast(`任务结束：${d.job.status}${d.job.status === 'completed' ? '，候选已就绪' : '，可查看步骤详情'}`, d.job.status === 'completed' ? 'ok' : 'info');
      return;
    }
  }
  toast('任务仍在后台运行，可稍后点击「刷新」查看最新进度');
}
function renderJobs(jobs) {
  $('#jobs').innerHTML = (jobs || []).length ? (jobs || []).map(j => `<div class="job-row"><span><b>${j.id.slice(0, 8)}</b> · ${j.attempt_count || 0} attempts</span><span class="status ${j.status}">${j.status}</span><span>${j.settled_credits || 0} cr</span></div>`).join('') : '<span class="muted">暂无生成任务</span>';
}
async function renderCandidates(candidates) {
  state.candidates = candidates || [];
  const box = $('#candidates');
  box.innerHTML = '';
  const q = state.detail?.quality;
  const qualityBadge = q && q.score != null ? `${q.grade} ${q.score}` : '';
  let readyIndex = 0;
  for (const c of state.candidates) {
    const card = document.createElement('article');
    card.className = 'candidate-card';
    const isReady = c.status === 'ready';
    const blind = state.blindMode && isReady;
    const blindLabel = blind ? String.fromCharCode(65 + readyIndex) : null;
    const candLabel = blind ? `候选 ${blindLabel}` : `候选 ${c.ordinal}`;
    const head = blind
      ? `<b class="blind-label">${blindLabel}</b><span class="status ${c.status}">${c.status}</span>`
      : `<b>Candidate ${c.ordinal}</b><span class="status ${c.status}">${c.status}</span>`;
    card.innerHTML = `<div class="row">${head}</div><p class="hash">${blind ? '· · · ·' : escapeHtml(c.sha256 || c.provider_clip_id || '')}</p>`;
    if (isReady) {
      readyIndex += 1;
      try {
        const token = await api(`/api/candidates/${c.id}/media-token`, {
          method: 'POST'
        });
        card.innerHTML += `<audio controls preload="none" src="${token.url}" aria-label="${candLabel} 试听"></audio><div class="play-progress"><div class="play-progress-fill"></div></div><div class="row candidate-meta"><span class="muted duration">${fmtDuration(c.duration_ms)}</span>${qualityBadge ? `<span class="tag neutral">${escapeHtml(qualityBadge)}</span>` : ''}</div><div class="row"><button class="master-btn" data-candidate="${c.id}" data-revision="${c.spec_revision}" aria-label="将${candLabel}设为 Master">设为 Master</button><button class="secondary comment-candidate" data-candidate="${c.id}" aria-label="对${candLabel}添加时间点评论">时间点评论</button></div>`;
        const audio = card.querySelector('audio');
        const fill = card.querySelector('.play-progress-fill');
        const dur = card.querySelector('.duration');
        audio.addEventListener('timeupdate', () => {
          const d = audio.duration || (c.duration_ms ? c.duration_ms / 1000 : 0);
          const pct = d ? Math.min(100, (audio.currentTime / d) * 100) : 0;
          if (fill) fill.style.width = pct + '%';
        });
        audio.addEventListener('loadedmetadata', () => {
          if (dur) dur.textContent = fmtDuration(audio.duration ? audio.duration * 1000 : c.duration_ms);
        });
        audio.onplay = () => api('/api/events', {
          method: 'POST',
          body: JSON.stringify({
            event_name: 'candidate_played',
            project_id: state.projectId,
            properties: {
              candidate_id: c.id,
              blind: state.blindMode
            }
          })
        }).catch(() => {});
      } catch (e) {
        card.innerHTML += `<p class="error">${escapeHtml(e.message)}</p>`;
      }
    } else {
      card.innerHTML += `<p class="muted">${escapeHtml(c.status)}</p>`;
    }
    box.appendChild(card);
  }
  $$('.master-btn').forEach(b => b.onclick = () => selectMaster(b.dataset.candidate, Number(b.dataset.revision)));
  $$('.comment-candidate').forEach(b => b.onclick = () => addComment(b.dataset.candidate));
}
async function selectMaster(candidateId, revision) {
  const ok = await confirmDialog({
    title: '设为 Master',
    message: '确认创建不可变 Master、Asset Snapshot 与 Rights Manifest？该操作不可撤销。',
    confirmText: '确认创建',
    danger: true
  });
  if (!ok) return;
  try {
    const data = await api(`/api/projects/${state.projectId}/master`, {
      method: 'POST',
      body: JSON.stringify({
        candidate_id: candidateId,
        expected_spec_revision: revision,
        confirmation: true
      })
    });
    const chosen = (state.candidates || []).find(x => x.id === candidateId);
    await api('/api/events', {
      method: 'POST',
      body: JSON.stringify({
        event_name: 'ab_choice',
        project_id: state.projectId,
        properties: {
          candidate_id: candidateId,
          ordinal: chosen?.ordinal ?? null,
          blind: !!state.blindMode,
          alternatives: (state.candidates || []).filter(x => x.id !== candidateId && x.status === 'ready').map(x => x.id)
        }
      })
    }).catch(() => {});
    await api('/api/events', {
      method: 'POST',
      body: JSON.stringify({
        event_name: 'master_selected',
        project_id: state.projectId,
        asset_snapshot_id: data.asset_snapshot_id,
        properties: {
          candidate_id: candidateId
        }
      })
    });
    await refreshProject();
    toast('Master 资产已创建', 'ok');
  } catch (e) {
    toast(e.message, 'error');
  }
}
function renderComments(rows) {
  $('#comments').innerHTML = rows.length ? rows.map(c => `<div class="comment"><span>${c.timecode_ms != null ? Math.round(c.timecode_ms / 1000) + 's' : 'R' + (c.spec_revision || '—')}</span><span><b>${escapeHtml(c.author_name)}</b> · ${escapeHtml(c.body)}</span>${c.status === 'open' ? `<button class="text-btn resolve-comment" data-id="${c.id}">解决</button>` : `<span class="status completed">已解决</span>`}</div>`).join('') : '<span class="muted">暂无评论</span>';
  $$('.resolve-comment').forEach(b => b.onclick = async () => {
    await api(`/api/projects/${state.projectId}/comments/${b.dataset.id}/resolve`, {
      method: 'POST'
    });
    await refreshProject();
  });
}
async function addComment(candidateId = null) {
  if (!state.projectId) return;
  const fields = [{
    name: 'body',
    label: '评论内容',
    multiline: true,
    placeholder: '写下你对这段创作的想法…'
  }];
  if (candidateId) fields.push({
    name: 'seconds',
    label: '时间点（秒，可留空）',
    placeholder: '例如 12.5',
    required: false
  });
  const result = await askDialog({
    title: candidateId ? '时间点评论' : '添加评论',
    fields,
    submitText: '提交'
  });
  if (!result) return;
  try {
    await api(`/api/projects/${state.projectId}/comments`, {
      method: 'POST',
      body: JSON.stringify({
        body: result.body,
        spec_revision: state.detail.project.current_revision,
        candidate_id: candidateId,
        timecode_ms: result.seconds ? Math.round(Number(result.seconds) * 1000) : null
      })
    });
    await refreshProject();
    toast('评论已添加', 'ok');
  } catch (e) {
    toast(e.message, 'error');
  }
}
$('#addComment').onclick = () => addComment();

// Asset OS
$('#reloadAssets').onclick = loadAssets;
async function loadAssets() {
  const data = await api('/api/assets' + listQuery('assets'));
  state.lists.assets.total = data.total || 0;
  state.assets = data.items || [];
  $('#assetTools').innerHTML = searchHtml('assets', '搜索资产标题 / ID…') + statusTabsHtml('assets', [['verified', '已复核'], ['unverified', '未复核'], ['restricted', '受限'], ['disputed', '争议'], ['development_only', '开发']]);
  $('#assetPager').innerHTML = pagerHtml('assets');
  $('#assetCards').innerHTML = state.assets.length ? state.assets.map(a => {
    const caps = a.manifest?.capabilities || {};
    return `<article class="asset-card" data-asset="${a.id}"><p class="eyebrow">ASSET SNAPSHOT</p><h3>${escapeHtml(a.title || a.snapshot?.spec?.title || 'Untitled')}</h3><p>R${a.spec_revision} · ${fmtDate(a.created_at)}</p><p class="hash">${escapeHtml(a.media_hash)}</p><div class="capabilities">${Object.entries(caps).slice(0, 6).map(([k, v]) => `<span class="cap ${v.status}">${escapeHtml(k)} · ${escapeHtml(v.status)}</span>`).join('')}</div></article>`;
  }).join('') : '<div class="panel muted">尚未创建 Master 资产。</div>';
  $$('[data-asset]').forEach(c => c.onclick = () => selectAsset(c.dataset.asset));
}
async function selectAsset(id) {
  const data = await api(`/api/assets/${id}/provenance`);
  state.activeAsset = data;
  const latest = data.rights_manifests.at(-1);
  const caps = latest?.manifest?.capabilities || {};
  const role = state.memberships.find(w => w.id === state.workspace)?.role;
  $('#assetDetail').innerHTML = `<p class="eyebrow">ASSET DETAIL</p><h2>${escapeHtml(data.project.title)}</h2><p>Revision ${data.asset.spec_revision} · Rights v${latest?.version || 0}</p><p class="hash">${escapeHtml(data.asset.snapshot_hash)}</p><h3>权利能力</h3><div class="capabilities">${Object.entries(caps).map(([k, v]) => `<span class="cap ${v.status}" title="${escapeHtml(v.reason || '')}">${escapeHtml(k)} · ${escapeHtml(v.status)}</span>`).join('')}</div><h3>来源链</h3><div class="provenance-list"><div class="provenance-item">媒体 SHA-256<br><span class="hash">${escapeHtml(data.candidate.sha256)}</span></div><div class="provenance-item">生成任务<br>${escapeHtml(data.candidate.job_id)}</div><div class="provenance-item">人类贡献事件<br>${data.contributions.length} 条</div><div class="provenance-item">权利证据<br>${data.rights_evidence.length} 条</div></div><div class="row" style="margin-top:16px"><button id="downloadAsset">导出资产包</button><button id="addEvidence" class="secondary">添加证据</button></div><div class="row" style="margin-top:8px"><button id="createOfferFromAsset" class="secondary">创建许可报价</button>${['owner', 'admin', 'legal'].includes(role) ? '<button id="legalReview" class="secondary">权利复核</button>' : ''}</div>`;
  $('#downloadAsset').onclick = () => downloadAsset(id);
  $('#addEvidence').onclick = () => addRightsEvidence(id);
  $('#createOfferFromAsset').onclick = () => createOffer(id);
  if ($('#legalReview')) $('#legalReview').onclick = () => legalReview(id);
}
async function downloadAsset(id) {
  try {
    const response = await api(`/api/assets/${id}/export`);
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `asset-${id.slice(0, 8)}.zip`;
    a.click();
    URL.revokeObjectURL(url);
    await api('/api/events', {
      method: 'POST',
      body: JSON.stringify({
        event_name: 'asset_downloaded',
        asset_snapshot_id: id,
        properties: {}
      })
    });
    toast('资产包已导出', 'ok');
  } catch (e) {
    toast(e.message, 'error');
  }
}
async function addRightsEvidence(id) {
  const result = await askDialog({
    title: '添加权利证据',
    description: '记录素材来源与授权说明，作为 Rights Manifest 的审查依据。',
    fields: [{
      name: 'type',
      label: '证据类型',
      type: 'select',
      value: 'user_declaration',
      options: [{
        value: 'user_declaration',
        label: 'user_declaration · 用户声明'
      }, {
        value: 'license',
        label: 'license · 授权许可'
      }, {
        value: 'consent',
        label: 'consent · 知情同意'
      }, {
        value: 'provider_contract',
        label: 'provider_contract · 供应商合同'
      }]
    }, {
      name: 'note',
      label: '证据说明',
      multiline: true,
      value: '我确认输入素材由本人创作或已获得必要授权。'
    }],
    submitText: '记录证据'
  });
  if (!result) return;
  try {
    await api(`/api/assets/${id}/rights-evidence`, {
      method: 'POST',
      body: JSON.stringify({
        evidence_type: result.type,
        evidence: {
          note: result.note,
          submitted_via: 'web'
        }
      })
    });
    await selectAsset(id);
    toast('权利证据已记录', 'ok');
  } catch (e) {
    toast(e.message, 'error');
  }
}
async function legalReview(id) {
  const proceed = await confirmDialog({
    title: '权利复核',
    message: '这是本地参考系统的权利复核动作。它不会构成版权登记或法律意见。继续？',
    confirmText: '继续'
  });
  if (!proceed) return;
  const commercial = await confirmDialog({
    title: '商业能力授权',
    message: '是否将 commercial_use 与 license 标记为 allowed？取消则保持 manual_review。',
    confirmText: '标记 allowed',
    cancelText: '保持 manual_review',
    danger: true
  });
  const evidence = (state.activeAsset?.rights_evidence || []).filter(e => !['rejected', 'expired'].includes(e.status) && (!e.expires_at || Date.parse(e.expires_at) > Date.now()));
  const commercialEvidence = evidence.filter(e => ['provider_contract', 'license'].includes(e.evidence_type)).map(e => e.id);
  if (commercial && !commercialEvidence.length) {
    toast('商业能力复核前必须先添加有效的 Provider 合同或许可证据', 'error');
    return;
  }
  const evidenceIds = commercial ? commercialEvidence : evidence.map(e => e.id);
  const capabilities = {
    stream: {
      status: 'allowed',
      reason: 'Platform media copy verified'
    },
    download: {
      status: 'allowed',
      reason: 'Asset owner download'
    },
    share: {
      status: 'allowed',
      reason: 'Reviewed for sharing'
    },
    commercial_use: {
      status: commercial ? 'allowed' : 'manual_review',
      reason: 'Manual rights review'
    },
    license: {
      status: commercial ? 'allowed' : 'manual_review',
      reason: 'Manual rights review'
    },
    sublicense: {
      status: 'blocked',
      reason: 'Not included'
    },
    distribute: {
      status: commercial ? 'allowed' : 'manual_review',
      reason: 'Manual rights review'
    },
    mint: {
      status: 'blocked',
      reason: 'Not enabled'
    },
    content_id: {
      status: 'blocked',
      reason: 'Not enabled'
    }
  };
  try {
    await api(`/api/assets/${id}/rights-review`, {
      method: 'POST',
      body: JSON.stringify({
        status: commercial ? 'verified' : 'unverified',
        capabilities,
        legal_hold: false,
        review_note: 'Local reference review performed by workspace legal role',
        evidence_ids: evidenceIds
      })
    });
    await loadAssets();
    await selectAsset(id);
    toast('新 Rights Manifest 已签发', 'ok');
  } catch (e) {
    toast(e.message, 'error');
  }
}
async function createOffer(assetId) {
  const result = await askDialog({
    title: '创建许可报价',
    description: '设定报价名称与价格，再选择是否立即公开到市场。',
    fields: [{
      name: 'title',
      label: '许可报价名称',
      value: state.activeAsset?.project?.title || 'Commercial Music License'
    }, {
      name: 'price',
      label: '价格（美分）',
      type: 'number',
      value: '4900'
    }],
    submitText: '下一步'
  });
  if (!result) return;
  const active = await confirmDialog({
    title: '发布范围',
    message: '立即公开到市场？需要 Rights Manifest 同时允许 commercial_use 与 license。',
    confirmText: '公开到市场',
    cancelText: '仅存草稿'
  });
  try {
    await api('/api/offers', {
      method: 'POST',
      body: JSON.stringify({
        asset_snapshot_id: assetId,
        title: result.title,
        description: 'Standard commercial music license',
        price_amount: Number(result.price),
        currency: 'USD',
        territory: 'worldwide',
        duration_days: 365,
        exclusive: false,
        status: active ? 'active' : 'draft'
      })
    });
    toast(active ? '许可报价已发布' : '草稿已保存', 'ok');
  } catch (e) {
    toast(e.message, 'error');
  }
}

// Market OS
async function loadCatalog() {
  const rows = await api('/api/catalog');
  $('#catalog').innerHTML = rows.map(p => `<article class="catalog-card"><p class="eyebrow">${escapeHtml(p.product_type)}</p><h3>${escapeHtml(p.name)}</h3><p>${escapeHtml(p.description)}</p><div class="price">${fmtMoney(p.unit_amount, p.currency)}</div><p>${p.credits ? `${p.credits} Credits` : ''}</p><button data-buy-sku="${p.sku}">购买</button></article>`).join('');
  $$('[data-buy-sku]').forEach(b => b.onclick = () => buyCatalog(b.dataset.buySku, b));
}
async function buyCatalog(sku, btn) {
  setLoading(btn, true, '购买中…');
  try {
    const order = await api('/api/orders/credits', {
      method: 'POST',
      body: JSON.stringify({
        sku,
        quantity: 1
      })
    });
    await payOrder(order.id);
    toast('支付已提交，等待回调', 'ok');
    setTimeout(async () => {
      await refreshBootstrap();
      await loadOrders();
    }, 1600);
  } catch (e) {
    toast(e.message, 'error');
  } finally {
    setLoading(btn, false);
  }
}
async function payOrder(orderId, btn) {
  if (btn) setLoading(btn, true, '支付中…');
  try {
    return await api(`/api/orders/${orderId}/pay`, {
      method: 'POST',
      headers: {
        'Idempotency-Key': crypto.randomUUID()
      },
      body: JSON.stringify({
        scenario: 'success'
      })
    });
  } finally {
    if (btn) setLoading(btn, false);
  }
}
$('#reloadOffers').onclick = loadOffers;
async function loadOffers() {
  const data = await api('/api/marketplace/offers');
  const rows = data.items || [];
  $('#marketOffers').innerHTML = rows.length ? rows.map(o => `<article class="offer-card"><p class="eyebrow">${escapeHtml(o.seller_name)}</p><h3>${escapeHtml(o.title)}</h3><p>${escapeHtml(o.description)}</p><div class="price">${fmtMoney(o.price_amount, o.currency)}</div><p>${escapeHtml(o.territory)} · ${o.duration_days || '永久'} 天 · ${o.exclusive ? '独家' : '非独家'}</p><button data-purchase-offer="${o.id}">购买许可</button></article>`).join('') : '<div class="panel muted">暂无公开许可报价。先在资产页完成权利复核并发布报价。</div>';
  $$('[data-purchase-offer]').forEach(b => b.onclick = () => purchaseOffer(b.dataset.purchaseOffer, b));
}
async function purchaseOffer(id, btn) {
  const result = await askDialog({
    title: '购买许可',
    description: '输入被许可人名称，随后将创建订单并发起支付。',
    fields: [{
      name: 'name',
      label: '被许可人名称',
      value: state.user.display_name
    }],
    submitText: '购买并支付'
  });
  if (!result) return;
  setLoading(btn, true, '购买中…');
  try {
    const order = await api(`/api/marketplace/offers/${id}/purchase`, {
      method: 'POST',
      body: JSON.stringify({
        licensee_name: result.name
      })
    });
    await payOrder(order.id);
    toast('许可订单支付中', 'ok');
    setTimeout(loadOrders, 1600);
  } catch (e) {
    toast(e.message, 'error');
  } finally {
    setLoading(btn, false);
  }
}
$('#createBrief').onclick = async () => {
  const result = await askDialog({
    title: '发布品牌任务',
    description: '描述音乐需求与预算，公开到品牌市场供创作者提交资产。',
    fields: [{
      name: 'title',
      label: '品牌任务标题',
      value: '为新品短片创作 30 秒主题音乐'
    }, {
      name: 'description',
      label: '需求说明',
      multiline: true,
      value: '温暖、现代、具有清晰记忆点；需要可用于全球社交媒体广告。'
    }, {
      name: 'budget',
      label: '预算（美分）',
      type: 'number',
      value: '100000'
    }],
    submitText: '发布'
  });
  if (!result) return;
  const btn = $('#createBrief');
  setLoading(btn, true, '发布中…');
  try {
    await api('/api/brand-briefs', {
      method: 'POST',
      body: JSON.stringify({
        title: result.title,
        description: result.description,
        budget_amount: Number(result.budget),
        currency: 'USD',
        requirements: {
          duration_seconds: 30,
          usage: 'social_ads'
        },
        status: 'open'
      })
    });
    await loadBriefs();
    toast('品牌任务已发布', 'ok');
  } catch (e) {
    toast(e.message, 'error');
  } finally {
    setLoading(btn, false);
  }
};
async function loadBriefs() {
  const rows = await api('/api/brand-briefs/public');
  $('#briefs').innerHTML = rows.length ? rows.map(b => `<article class="brief-card"><p class="eyebrow">${escapeHtml(b.buyer_name)}</p><h3>${escapeHtml(b.title)}</h3><p>${escapeHtml(b.description)}</p><div class="price">${fmtMoney(b.budget_amount, b.currency)}</div><button data-submit-brief="${b.id}">提交资产</button></article>`).join('') : '<div class="panel muted">暂无公开品牌需求。</div>';
  $$('[data-submit-brief]').forEach(b => b.onclick = () => submitBrief(b.dataset.submitBrief));
}
async function submitBrief(briefId) {
  if (!state.assets.length) await loadAssets();
  const options = state.assets.map((a, i) => `${i + 1}. ${a.title} · ${a.id}`).join('<br>');
  const result = await askDialog({
    title: '提交资产',
    description: `选择要提交的资产序号：<br>${options}`,
    fields: [{
      name: 'idx',
      label: '资产序号',
      type: 'number',
      value: '1',
      error: '请输入有效的资产序号'
    }],
    submitText: '提交资产'
  });
  if (!result) return;
  const asset = state.assets[Number(result.idx) - 1];
  if (!asset) return toast('无效序号', 'error');
  try {
    await api(`/api/brand-briefs/${briefId}/submissions`, {
      method: 'POST',
      body: JSON.stringify({
        asset_snapshot_id: asset.id,
        notes: 'Submitted from Resonance Studio'
      })
    });
    toast('资产已提交', 'ok');
  } catch (e) {
    toast(e.message, 'error');
  }
}
$('#reloadOrders').onclick = loadOrders;
async function loadOrders() {
  const [orders, licenses, deliveries] = await Promise.all([
    api('/api/orders' + listQuery('orders')),
    api('/api/licenses' + listQuery('licenses')),
    api('/api/deliveries')
  ]);
  state.lists.orders.total = orders.total || 0;
  state.lists.licenses.total = licenses.total || 0;
  renderOrders(orders.items || []);
  renderLicenses(licenses.items || [], deliveries.items || []);
}
function renderOrders(items) {
  $('#ordersTools').innerHTML = searchHtml('orders', '搜索订单号 / 类型…') + statusTabsHtml('orders', [['pending', '待支付'], ['paid', '已支付'], ['fulfilled', '已完成'], ['refunded', '已退款']]);
  $('#ordersPager').innerHTML = pagerHtml('orders');
  $('#orders').innerHTML = items.length ? items.map(o => `<div class="order-row"><span><b>${escapeHtml(o.order_number)}</b><br><small>${escapeHtml(o.order_type)}</small></span><span>${fmtMoney(o.total, o.currency)}</span><span class="status ${o.status}">${o.status}</span><span>${['pending', 'payment_pending'].includes(o.status) ? `<button class="text-btn pay-order" data-id="${o.id}">支付</button>` : ''}${o.status === 'fulfilled' ? `<button class="text-btn refund-order" data-id="${o.id}">退款</button>` : ''}</span></div>`).join('') : '<p class="muted">暂无订单</p>';
  $$('.pay-order').forEach(b => b.onclick = async () => {
    await payOrder(b.dataset.id, b);
    setTimeout(loadOrders, 1500);
  });
  $$('.refund-order').forEach(b => b.onclick = () => refundOrder(b.dataset.id, b));
}
function renderLicenses(items, deliveries) {
  $('#licensesTools').innerHTML = searchHtml('licenses', '搜索被许可人 / 区域…') + statusTabsHtml('licenses', [['active', '生效中'], ['refunded', '已退款']]);
  $('#licensesPager').innerHTML = pagerHtml('licenses');
  $('#licenses').innerHTML = [...items.map(l => `<div class="order-row"><span><b>License ${l.id.slice(0, 8)}</b><br><small>${escapeHtml(l.licensee_name)}</small></span><span>${escapeHtml(l.territory)}</span><span class="status ${l.status}">${l.status}</span><span class="hash">${l.license_hash.slice(0, 12)}</span></div>`), ...(deliveries || []).map(d => `<div class="order-row"><span><b>Delivery ${d.id.slice(0, 8)}</b></span><span>${d.asset_snapshot_id ? d.asset_snapshot_id.slice(0, 8) : '—'}</span><span class="status ${d.status}">${d.status}</span><span>${['ready', 'downloaded'].includes(d.status) ? `<button class="text-btn download-delivery" data-id="${d.id}">下载</button>` : fmtDate(d.created_at)}</span></div>`)].join('') || '<p class="muted">暂无许可或交付</p>';
  $$('.download-delivery').forEach(b => b.onclick = () => downloadDelivery(b.dataset.id));
}
async function downloadDelivery(id) {
  try {
    const response = await api(`/api/deliveries/${id}/export`);
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `licensed-delivery-${id.slice(0, 8)}.zip`;
    a.click();
    URL.revokeObjectURL(url);
    toast('许可交付包已下载', 'ok');
    await loadOrders();
  } catch (e) {
    toast(e.message, 'error');
  }
}
async function refundOrder(id, btn) {
  const result = await askDialog({
    title: '申请退款',
    description: '退款会撤销相应许可与交付并回退额度，请填写原因。',
    fields: [{
      name: 'reason',
      label: '退款原因',
      multiline: true,
      value: '用户请求取消'
    }],
    submitText: '提交退款'
  });
  if (!result) return;
  setLoading(btn, true, '退款中…');
  try {
    await api(`/api/orders/${id}/refunds`, {
      method: 'POST',
      headers: {
        'Idempotency-Key': crypto.randomUUID()
      },
      body: JSON.stringify({
        reason: result.reason
      })
    });
    toast('退款已提交', 'ok');
    setTimeout(async () => {
      await loadOrders();
      await refreshBootstrap();
    }, 1600);
  } catch (e) {
    toast(e.message, 'error');
  } finally {
    setLoading(btn, false);
  }
}

// Intelligence / Account
async function loadAccount() {
  const [analytics, prefs, ledger] = await Promise.all([api('/api/analytics/overview'), api('/api/preferences'), api('/api/ledger')]);
  const m = analytics.metrics;
  const cards = [['28 天生成', m.generations], ['成功任务', m.successful_jobs], ['Master', m.masters], ['Master 转化', `${(Number(m.master_conversion) * 100).toFixed(1)}%`], ['平均质量', Number(m.avg_quality || 0).toFixed(1)], ['许可', m.licenses]];
  $('#analyticsCards').innerHTML = cards.map(([k, v]) => `<div class="metric-card"><span class="muted">${escapeHtml(k)}</span><b>${escapeHtml(v)}</b></div>`).join('');
  const pref = prefs.find(x => x.scope === 'workspace') || prefs[0];
  $('#preferences').value = JSON.stringify(pref?.preferences || {}, null, 2);
  $('#learningEnabled').checked = pref?.learning_enabled !== false;
  renderLedger(ledger);
  await loadTickets();
}
async function loadTickets() {
  const data = await api('/api/support/tickets' + listQuery('tickets'));
  state.lists.tickets.total = data.total || 0;
  renderTickets(data.items || []);
}
$('#savePreferences').onclick = async () => {
  let preferences;
  try {
    preferences = JSON.parse($('#preferences').value);
  } catch {
    return toast('偏好必须是合法 JSON', 'error');
  }
  const btn = $('#savePreferences');
  setLoading(btn, true, '保存中…');
  try {
    await api('/api/preferences', {
      method: 'PUT',
      body: JSON.stringify({
        scope: 'workspace',
        preferences,
        learning_enabled: $('#learningEnabled').checked
      })
    });
    toast('偏好已保存', 'ok');
  } catch (e) {
    toast(e.message, 'error');
  } finally {
    setLoading(btn, false);
  }
};
$('#newTicket').onclick = async () => {
  const result = await askDialog({
    title: '新建支持工单',
    description: '请描述遇到的问题，支持团队会据此跟进。',
    fields: [{
      name: 'subject',
      label: '工单主题',
      value: '生成任务需要协助'
    }, {
      name: 'description',
      label: '问题描述',
      multiline: true,
      value: '请描述任务 ID、预期结果和实际结果。'
    }],
    submitText: '创建工单'
  });
  if (!result) return;
  const btn = $('#newTicket');
  setLoading(btn, true, '创建中…');
  try {
    await api('/api/support/tickets', {
      method: 'POST',
      body: JSON.stringify({
        category: 'other',
        priority: 'normal',
        subject: result.subject,
        description: result.description
      })
    });
    await loadAccount();
    toast('工单已创建', 'ok');
  } catch (e) {
    toast(e.message, 'error');
  } finally {
    setLoading(btn, false);
  }
};
function renderTickets(rows) {
  $('#ticketTools').innerHTML = searchHtml('tickets', '搜索工单主题 / 类别…') + statusTabsHtml('tickets', [['open', '待处理'], ['resolved', '已解决']]);
  $('#ticketPager').innerHTML = pagerHtml('tickets');
  $('#tickets').innerHTML = rows.length ? rows.map(t => `<div class="ticket-row"><span><b>${escapeHtml(t.subject)}</b><br><small>${escapeHtml(t.category)}</small></span><span>${escapeHtml(t.priority)}</span><span class="status ${t.status}">${t.status}</span><span>${fmtDate(t.created_at)}</span></div>`).join('') : '<p class="muted">暂无工单</p>';
}
function renderLedger(data) {
  $('#ledgerBalances').innerHTML = Object.entries(data.balances || {}).map(([k, v]) => `<div class="balance"><span class="muted">${escapeHtml(k)}</span><br><b>${Number(v).toFixed(2)}</b></div>`).join('');
  $('#ledgerTransactions').innerHTML = (data.transactions || []).slice(0, 20).map(t => `<div class="ledger-row"><span><b>${escapeHtml(t.transaction_type)}</b><br><small>${escapeHtml(t.operation_key)}</small></span><span>${escapeHtml(t.reference_type)}</span><span>${escapeHtml(t.reference_id).slice(0, 14)}</span><span>${fmtDate(t.created_at)}</span></div>`).join('');
}
init();
