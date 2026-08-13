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
    let x;
    try {
      x = await r.json();
    } catch {
      x = await r.text();
    }
    throw new Error(typeof x === 'string' ? x : JSON.stringify(x.detail ?? x));
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
    console.error(e);
    $('#login').hidden = false;
    $('#app').hidden = true;
  }
}
function bindNav() {
  $$('nav [data-view]').forEach(b => b.onclick = async () => {
    $$('nav button').forEach(x => x.classList.toggle('active', x === b));
    $$('.view').forEach(v => v.classList.toggle('active', v.id === `view-${b.dataset.view}`));
    if (b.dataset.view === 'release') await loadRelease();
  });
}
async function refreshAll() {
  await Promise.all([loadOverview(), loadJobs(), loadBilling(), loadTrust()]);
  if ($('#view-release').classList.contains('active')) await loadRelease();
}
$('#refresh').onclick = refreshAll;
async function loadOverview() {
  const [v12, legacy] = await Promise.all([api('/api/admin/v12/dashboard'), api('/api/admin/dashboard')]);
  const c = v12.counts;
  const stats = [['项目', c.projects], ['生成任务', c.jobs], ['失败任务', c.failed_jobs], ['资产', c.assets], ['订单', c.orders], ['已履约', c.fulfilled_orders], ['许可', c.licenses], ['开放案件', c.open_cases], ['开放工单', c.open_tickets]];
  $('#stats').innerHTML = stats.map(([k, v]) => `<div class="stat"><span>${escapeHtml(k)}</span><b>${escapeHtml(v)}</b></div>`).join('');
  $('#switches').innerHTML = Object.entries(legacy.switches).map(([k, v]) => `<div class="switch-row"><span>${escapeHtml(k)}</span><span class="switch-state ${v ? 'on' : ''}">${v ? 'ON' : 'OFF'}</span><button class="secondary switch-btn" data-key="${k}" data-value="${v}">${v ? '关闭' : '开启'}</button></div>`).join('');
  $$('.switch-btn').forEach(b => b.onclick = async () => {
    if (!confirm(`确认${b.dataset.value === 'true' ? '关闭' : '开启'} ${b.dataset.key}？`)) return;
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
async function loadRelease() {
  try {
    const rows = await api('/api/admin/v12/release-evidence');
    const grouped = rows.reduce((a, r) => {
      (a[r.gate] ??= []).push(r);
      return a;
    }, {});
    $('#releaseEvidence').innerHTML = Object.entries(grouped).map(([gate, items]) => `<article class="gate-card"><h2>${escapeHtml(gate)}</h2>${items.map(i => `<div class="evidence-row"><div><b>${escapeHtml(i.evidence_key)}</b><br><small>${escapeHtml(i.owner)} · ${fmtDate(i.updated_at)}</small></div><button class="status ${i.status} evidence-btn" data-gate="${gate}" data-key="${i.evidence_key}" data-status="${i.status}">${escapeHtml(i.status)}</button></div>`).join('')}</article>`).join('');
    $$('.evidence-btn').forEach(b => b.onclick = () => editEvidence(b.dataset.gate, b.dataset.key, b.dataset.status));
  } catch (e) {
    $('#releaseEvidence').innerHTML = `<div class="panel">${escapeHtml(e.message)}<br><small>发布证据室仅平台管理员可访问。</small></div>`;
  }
}
async function editEvidence(gate, key, current) {
  const status = prompt('状态：missing / in_progress / passed / waived / failed', current);
  if (!status) return;
  const note = prompt('证据说明或链接', 'Validated in local release pipeline');
  try {
    await api(`/api/admin/v12/release-evidence/${gate}/${key}`, {
      method: 'PUT',
      body: JSON.stringify({
        status,
        evidence: {
          note
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
