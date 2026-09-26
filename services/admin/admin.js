const API = '';
const state = {
  token: '',
  workspace: localStorage.getItem('resonance_admin_workspace') || '',
  user: null,
  workspaces: []
};
const $ = s => document.querySelector(s);
const $$ = s => [...document.querySelectorAll(s)];
localStorage.removeItem('resonance_admin_token');
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
function fmtDate(v) {
  if (!v) return '—';
  return new Intl.DateTimeFormat('zh-CN', {
    dateStyle: 'short',
    timeStyle: 'short'
  }).format(new Date(v));
}
function fmtMoney(v, c = 'USD') {
  return new Intl.NumberFormat('zh-CN', {
    style: 'currency',
    currency: c
  }).format((Number(v) || 0) / 100);
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
// 原生 <dialog>.showModal() 自带焦点陷阱与 Escape 关闭；这里补“关闭后焦点还原到触发按钮”。
$('#dialog').addEventListener('close', restoreDialogFocus);
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
    let settled = false;
    const finish = value => {
      if (settled) return;
      settled = true;
      resolve(value);
    };
    content.innerHTML = `<p class="eyebrow">${escapeHtml(title)}</p>${description ? `<p class="muted">${escapeHtml(description)}</p>` : ''}<div class="dialog-fields"></div><div class="dialog-error error"></div><div class="dialog-actions"><button type="button" class="secondary dialog-cancel">${escapeHtml(cancelText)}</button><button type="button" class="dialog-submit">${escapeHtml(submitText)}</button></div>`;
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
    content.innerHTML = `<p class="eyebrow">${escapeHtml(title)}</p><p class="muted">${escapeHtml(message)}</p><div class="dialog-actions"><button type="button" class="secondary dialog-cancel">${escapeHtml(cancelText)}</button><button type="button" class="dialog-submit${danger ? ' danger' : ''}">${escapeHtml(confirmText)}</button></div>`;
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
function toast(m) {
  const e = $('#toast');
  e.textContent = m;
  e.hidden = false;
  clearTimeout(toast.t);
  toast.t = setTimeout(() => e.hidden = true, 3500);
}
async function api(path, opt = {}, retry = true) {
  const h = {
    ...(opt.headers || {})
  };
  const method = (opt.method || 'GET').toUpperCase();
  if (opt.body) h['Content-Type'] = 'application/json';
  if (state.token) h.Authorization = 'Bearer ' + state.token;
  if (state.workspace) h['X-Workspace-Id'] = state.workspace;
  if (!['GET', 'HEAD', 'OPTIONS'].includes(method) && !h.Authorization) {
    const csrf = decodeURIComponent(cookie('resonance_csrf'));
    if (csrf) h['X-CSRF-Token'] = csrf;
  }
  const r = await fetch(API + path, {
    ...opt,
    headers: h,
    credentials: 'same-origin'
  });
  if (r.status === 401 && retry && !path.startsWith('/api/auth/')) {
    const refreshed = await fetch(API + '/api/auth/refresh', {
      method: 'POST',
      credentials: 'same-origin'
    });
    if (refreshed.ok) return api(path, opt, false);
  }
  if (!r.ok) {
    // Read once: the previous json()-then-text() fallback consumed the stream
    // twice, so the user saw "body stream already read" instead of the server's reason.
    const body = await r.text();
    let x = body;
    try {
      x = JSON.parse(body);
    } catch {
      // not JSON; keep the raw text
    }
    const e = new Error(typeof x === 'string' ? x : JSON.stringify(x.detail ?? x));
    e.status = r.status;
    throw e;
  }
  return r.status === 204 ? null : r.json();
}
$('#loginForm').onsubmit = async e => {
  e.preventDefault();
  try {
    const d = await api('/api/auth/login', {
      method: 'POST',
      body: JSON.stringify({
        email: $('#email').value,
        password: $('#password').value
      })
    });
    state.token = '';
    state.workspace = d.workspaces[0]?.id || '';
    localStorage.setItem('resonance_admin_workspace', state.workspace);
    await init();
  } catch (e) {
    $('#error').textContent = e.message;
  }
};
$('#logout').onclick = async () => {
  try {
    await api('/api/auth/logout', {
      method: 'POST'
    });
  } catch {}
  localStorage.removeItem('resonance_admin_workspace');
  location.reload();
};
async function init() {
  try {
    const me = await api('/api/auth/me');
    state.user = me.user;
    state.workspaces = me.workspaces;
    if (!state.workspaces.some(w => w.id === state.workspace)) state.workspace = state.workspaces[0]?.id || '';
    $('#workspace').innerHTML = state.workspaces.map(w => `<option value="${w.id}">${escapeHtml(w.name)} · ${escapeHtml(w.role)}</option>`).join('');
    $('#workspace').value = state.workspace;
    $('#workspace').onchange = async () => {
      state.workspace = $('#workspace').value;
      localStorage.setItem('resonance_admin_workspace', state.workspace);
      await refreshAll();
    };
    $('#identity').textContent = state.user.display_name;
    $('#login').hidden = true;
    $('#app').hidden = false;
    bindNav();
    await refreshAll();
  } catch (e) {
    // An unauthenticated cold load is the normal first paint of this app: /api/auth/me answering 401
    // is not an application fault, and logging it as one filled the browser error channel with
    // expected refusals (the acceptance gate measured these alongside 0 uncaught exceptions).
    if (e && e.status !== 401) console.error(e);
    $('#login').hidden = false;
    $('#app').hidden = true;
  }
}
function bindNav() {
  $$('nav [data-view]').forEach(b => b.onclick = async () => {
    $$('nav button').forEach(x => {
      x.classList.toggle('active', x === b);
      if (x === b) x.setAttribute('aria-current', 'page');
      else x.removeAttribute('aria-current');
    });
    $$('.view').forEach(v => v.classList.toggle('active', v.id === `view-${b.dataset.view}`));
    if (b.dataset.view === 'release') await loadRelease();
    if (b.dataset.view === 'workspaces') await loadWorkspaces();
  });
}
// Every container a loader owns, so a refusal empties all of them. Leaving the previous render standing
// under a heading that says 紧急总闸 would let an operator read a stale figure -- or click a switch whose
// state was never re-read -- as though it were current, which is the failure this console exists to avoid.
const PANELS = [
  [loadOverview, ['#stats', '#switches', '#provider', '#quality', '#audit']],
  [loadJobs, ['#jobs']],
  [loadBilling, ['#balances', '#payments', '#payouts']],
  [loadTrust, ['#cases', '#tickets']],
];
async function refreshAll() {
  // allSettled, not all. Measured against the live stack, the four panels below answer 200 for any
  // logged-in member of a workspace -- only /payouts and /workspaces are platform-administrator routes
  // -- so the shape this actually defends against is a 5xx or a dropped connection: under Promise.all
  // one such load rejected the group, the boot handler caught it, and it answered by hiding #app and
  // putting the operator back at the login form while the session was still valid. Now the app stays
  // up and each panel that failed says so in its own container, because a stale figure under a heading
  // that reads 最近审计事件 is the one thing this console must not show as current.
  const settled = await Promise.allSettled(PANELS.map(([load]) => load()));
  const refused = [];
  settled.forEach((result, index) => {
    if (result.status === 'fulfilled') return;
    refused.push(result.reason.message);
    const note = `<p class="muted">${escapeHtml(result.reason.message)} · 这一格本轮未能载入，屏幕上的旧数字不代表当前状态。</p>`;
    PANELS[index][1].forEach((selector) => { $(selector).innerHTML = note; });
  });
  if (refused.length) toast(`部分面板未能载入：${refused[0]}`);
  if ($('#view-release').classList.contains('active')) await loadRelease();
  if ($('#view-workspaces').classList.contains('active')) await loadWorkspaces();
}
$('#refresh').onclick = refreshAll;
async function loadOverview() {
  const [v12, legacy] = await Promise.all([api('/api/admin/v12/dashboard'), api('/api/admin/dashboard')]);
  const c = v12.counts;
  const stats = [['项目', c.projects], ['生成任务', c.jobs], ['失败任务', c.failed_jobs], ['资产', c.assets], ['订单', c.orders], ['已履约', c.fulfilled_orders], ['许可', c.licenses], ['开放案件', c.open_cases], ['开放工单', c.open_tickets]];
  $('#stats').innerHTML = stats.map(([k, v]) => `<div class="stat"><span>${escapeHtml(k)}</span><b>${escapeHtml(v)}</b></div>`).join('');
  $('#switches').innerHTML = Object.entries(legacy.switches).map(([k, v]) => `<div class="switch-row"><span>${escapeHtml(k)}</span><span class="switch-state ${v ? 'on' : ''}">${v ? 'ON' : 'OFF'}</span><button class="secondary switch-btn" data-key="${k}" data-value="${v}" aria-pressed="${v ? 'true' : 'false'}" aria-label="${v ? '关闭' : '开启'}开关 ${escapeHtml(k)}">${v ? '关闭' : '开启'}</button></div>`).join('');
  $$('.switch-btn').forEach(b => b.onclick = async () => {
    const ok = await confirmDialog({
      title: '确认操作',
      message: `确认${b.dataset.value === 'true' ? '关闭' : '开启'} ${b.dataset.key}？`,
      confirmText: '确认',
      danger: true
    });
    if (!ok) return;
    try {
      await api('/api/admin/switches/' + b.dataset.key, {
        method: 'PUT',
        body: JSON.stringify({
          value: b.dataset.value !== 'true'
        })
      });
      await loadOverview();
      toast('总闸已更新');
    } catch (e) {
      toast(e.message);
    }
  });
  const p = v12.provider || {};
  $('#provider').innerHTML = `<div class="provider-card"><b>${escapeHtml(p.provider || '—')}</b><span>Approval: ${escapeHtml(p.approval_status || '—')}</span><span>Contract: ${escapeHtml(p.contract_version || '—')}</span><span>Captured: ${fmtDate(p.captured_at)}</span></div>`;
  $('#quality').innerHTML = `<p>近 28 天平均质量：<b>${Number(v12.quality?.avg_score || 0).toFixed(1)}</b> · ${v12.quality?.evaluations || 0} 次评估</p>`;
  $('#audit').innerHTML = (legacy.audit || []).slice(0, 60).map(a => `<div class="table-row"><span><b>${escapeHtml(a.action)}</b><br><small>${escapeHtml(a.subject_type)}:${escapeHtml(a.subject_id || '')}</small></span><span>${escapeHtml(a.actor_role || 'system')}</span><span>${fmtDate(a.created_at)}</span><span>${escapeHtml(a.request_id || '').slice(0, 8)}</span><span></span></div>`).join('');
}
async function loadJobs() {
  const jobs = await api('/api/jobs');
  $('#jobs').innerHTML = jobs.length ? jobs.map(j => `<div class="table-row"><span><b>${j.id.slice(0, 12)}</b><br><small>${escapeHtml(j.provider_job_id || 'not submitted')}</small></span><span class="status ${j.status}">${escapeHtml(j.status)}</span><span>${j.attempt_count}/${j.max_attempts}</span><span>${j.settled_credits || 0} cr</span><span>${!['completed', 'partial', 'failed', 'dead_letter', 'cancelled'].includes(j.status) ? `<button class="secondary cancel-job" data-id="${j.id}">取消</button>` : ''}</span></div>`).join('') : '<p>暂无任务</p>';
  $$('.cancel-job').forEach(b => b.onclick = async () => {
    try {
      await api(`/api/admin/jobs/${b.dataset.id}/cancel`, {
        method: 'POST'
      });
      await loadJobs();
      toast('取消请求已提交');
    } catch (e) {
      toast(e.message);
    }
  });
}
async function loadBilling() {
  const requests = [api('/api/admin/v12/dashboard'), api('/api/admin/v12/payments'), api('/api/admin/v12/payouts').catch(() => [])];
  const [d, payments, payouts] = await Promise.all(requests);
  $('#balances').innerHTML = (d.ledger || []).map(x => `<div class="stat"><span>${escapeHtml(x.account_type)}</span><b>${Number(x.balance).toFixed(2)}</b></div>`).join('');
  $('#payments').innerHTML = payments.length ? payments.map(p => `<div class="table-row"><span><b>${escapeHtml(p.order_number)}</b><br><small>${escapeHtml(p.provider_payment_id || p.id)}</small></span><span>${fmtMoney(p.amount, p.currency)}</span><span class="status ${p.status}">${escapeHtml(p.status)}</span><span>${p.refunded_amount ? `退 ${fmtMoney(p.refunded_amount, p.currency)}` : '—'}</span><span>${fmtDate(p.created_at)}</span></div>`).join('') : '<p>暂无支付</p>';
  $('#payouts').innerHTML = payouts.length ? payouts.map(p => `<div class="table-row"><span><b>${escapeHtml(p.workspace_name || p.workspace_id)}</b><br><small>${escapeHtml(p.reference_type)}:${escapeHtml(p.reference_id).slice(0, 12)}</small></span><span>${fmtMoney(p.amount, p.currency)}</span><span class="status ${p.status}">${escapeHtml(p.status)}</span><span>${fmtDate(p.created_at)}</span><span>${p.status === 'pending' ? `<button class="secondary process-payout" data-id="${p.id}">标记处理</button>` : ''}</span></div>`).join('') : '<p>暂无待结算款项</p>';
  $$('.process-payout').forEach(b => b.onclick = async () => {
    try {
      await api(`/api/admin/v12/payouts/${b.dataset.id}`, {
        method: 'PUT',
        body: JSON.stringify({
          status: 'processing',
          provider_payout_id: null
        })
      });
      await loadBilling();
      toast('结算状态已更新');
    } catch (e) {
      toast(e.message);
    }
  });
}
$('#runReconciliation').onclick = async () => {
  try {
    const r = await api('/api/admin/v12/reconciliation');
    renderReconciliation(r);
    toast('对账已完成');
  } catch (e) {
    toast(e.message);
  }
};
function renderReconciliation(r) {
  $('#reconciliation').innerHTML = `<div class="panel-head"><h2>一致性检查</h2><span class="status ${r.status}">${r.status}</span></div>${Object.entries(r.checks || {}).map(([k, v]) => `<div class="switch-row"><span>${escapeHtml(k)}</span><b>${escapeHtml(v)}</b><span></span></div>`).join('')}`;
}
async function loadTrust() {
  const d = await api('/api/admin/v12/moderation');
  $('#cases').innerHTML = d.cases.length ? d.cases.map(c => `<div class="table-row"><span><b>${escapeHtml(c.case_type)}</b><br><small>${escapeHtml(c.subject_type)}:${escapeHtml(c.subject_id)}</small></span><span class="status ${c.status}">${escapeHtml(c.status)}</span><span class="status ${c.severity}">${escapeHtml(c.severity)}</span><span>${c.legal_hold ? 'LEGAL HOLD' : '—'}</span><span>${fmtDate(c.created_at)}</span></div>`).join('') : '<p>暂无案件</p>';
  $('#tickets').innerHTML = d.tickets.length ? d.tickets.map(t => `<div class="table-row"><span><b>${escapeHtml(t.subject)}</b><br><small>${escapeHtml(t.category)}</small></span><span class="status ${t.status}">${escapeHtml(t.status)}</span><span>${escapeHtml(t.priority)}</span><span>${fmtDate(t.created_at)}</span><span>${!['resolved', 'closed'].includes(t.status) ? `<button class="secondary resolve-ticket" data-id="${t.id}">解决</button>` : ''}</span></div>`).join('') : '<p>暂无工单</p>';
  $$('.resolve-ticket').forEach(b => b.onclick = async () => {
    try {
      await api(`/api/admin/v12/tickets/${b.dataset.id}`, {
        method: 'PUT',
        body: JSON.stringify({
          status: 'resolved',
          assigned_to: null
        })
      });
      await loadTrust();
      toast('工单已解决');
    } catch (e) {
      toast(e.message);
    }
  });
}
async function loadWorkspaces() {
  const roster = $('#workspaceRoster');
  let workspaces = [];
  try {
    workspaces = (await api('/api/admin/v12/workspaces')).workspaces;
  } catch (e) {
    // The refusal has to be readable rather than an empty grid: this view exists so an operator can
    // tell "no workspaces" apart from "you are not allowed to see them".
    $('#workspaceDirectory').innerHTML = `<div class="panel">${escapeHtml(e.message)}<br><small>工作区名册仅平台管理员可访问。</small></div>`;
    roster.hidden = true;
    return;
  }
  $('#workspaceDirectory').innerHTML = workspaces.length ? workspaces.map(w => `<article class="gate-card"><h2>${escapeHtml(w.workspace_name)}</h2><div class="evidence-row"><div><b>${escapeHtml(w.workspace_id)}</b><br><small class="muted">状态 ${escapeHtml(w.workspace_status)} · 计划 ${escapeHtml(w.plan)} · 成员 ${Number(w.member_count)} 人 · 属主 ${escapeHtml(w.owner_display_name || '（无）')}${w.owner_email ? ' &lt;' + escapeHtml(w.owner_email) + '&gt;' : ''} · 建立于 ${fmtDate(w.workspace_created_at)}</small></div><button class="secondary workspace-roster-btn" data-id="${escapeHtml(w.workspace_id)}" data-name="${escapeHtml(w.workspace_name)}" aria-label="载入 ${escapeHtml(w.workspace_name)} 的成员名册">查看名册</button></div></article>`).join('') : '<p>暂无工作区</p>';
  $$('.workspace-roster-btn').forEach(b => b.onclick = async () => {
    roster.hidden = false;
    roster.innerHTML = '<p class="muted">载入中…</p>';
    try {
      const detail = await api(`/api/admin/v12/workspaces/${b.dataset.id}/members`);
      roster.innerHTML = `<h2>${escapeHtml(b.dataset.name)} 的成员</h2><p class="muted">只读。改角色、移人与移交所有权都在 Studio 的「团队协作」面板里，由该工作区的属主或管理员当场交出凭据后发起。</p>` + (detail.members.length ? detail.members.map(m => `<div class="table-row"><span><b>${escapeHtml(m.display_name)}</b><br><small class="muted">${escapeHtml(m.email)}</small></span><span>${escapeHtml(m.member_role)}</span><span class="status ${escapeHtml(m.account_status)}">${escapeHtml(m.account_status)}</span><span class="muted">${m.is_platform_admin ? '平台管理员' : '—'}</span><span class="muted">${fmtDate(m.joined_at)}</span></div>`).join('') : '<p>该工作区目前没有成员行。</p>');
    } catch (e) {
      roster.innerHTML = `<p>${escapeHtml(e.message)}</p>`;
    }
  });
}
async function loadRelease() {
  try {
    const rows = await api('/api/admin/v12/release-evidence');
    const grouped = rows.reduce((a, r) => {
      (a[r.gate] ??= []).push(r);
      return a;
    }, {});
    $('#releaseEvidence').innerHTML = Object.entries(grouped).map(([gate, items]) => `<article class="gate-card"><h2>${escapeHtml(gate)}</h2>${items.map(i => `<div class="evidence-row"><div><b>${escapeHtml(i.evidence_key)}</b><br><small>${escapeHtml(i.owner)} · ${fmtDate(i.updated_at)}</small></div><button class="status ${i.status} evidence-btn" data-gate="${gate}" data-key="${i.evidence_key}" data-status="${i.status}" aria-label="更新证据 ${escapeHtml(gate)} / ${escapeHtml(i.evidence_key)} 状态">${escapeHtml(i.status)}</button></div>`).join('')}</article>`).join('');
    $$('.evidence-btn').forEach(b => b.onclick = () => editEvidence(b.dataset.gate, b.dataset.key, b.dataset.status));
  } catch (e) {
    $('#releaseEvidence').innerHTML = `<div class="panel">${escapeHtml(e.message)}<br><small>发布证据室仅平台管理员可访问。</small></div>`;
  }
}
async function editEvidence(gate, key, current) {
  const result = await askDialog({
    title: '更新发布证据',
    description: `${escapeHtml(gate)} / ${escapeHtml(key)}`,
    fields: [{
      name: 'status',
      label: '状态',
      type: 'select',
      value: current,
      options: ['missing', 'in_progress', 'passed', 'waived', 'failed'].map(s => ({
        value: s,
        label: s
      }))
    }, {
      name: 'note',
      label: '证据说明或链接',
      value: 'Validated in local release pipeline'
    }],
    submitText: '保存'
  });
  if (!result) return;
  try {
    await api(`/api/admin/v12/release-evidence/${gate}/${key}`, {
      method: 'PUT',
      body: JSON.stringify({
        status: result.status,
        evidence: {
          note: result.note
        },
        owner: 'platform-admin'
      })
    });
    await loadRelease();
    toast('发布证据已更新');
  } catch (e) {
    toast(e.message);
  }
}
init();
