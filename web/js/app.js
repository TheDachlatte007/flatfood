/**
 * Flatfood — Client Application Logic
 */

const state = {
  currentMonth: '',
  meta: null,
  persons: [],
  categories: [],
  currentReceipt: null,
  charts: { tmm: null, cat: null }
};

// -----------------------------------------------------------------------------
// Utilities & Helpers
// -----------------------------------------------------------------------------

function fmtEur(cents) {
  if (cents === null || cents === undefined) return '-';
  const sign = cents < 0 ? '-' : '';
  const c = Math.abs(cents);
  return `${sign}${(c / 100).toFixed(2).replace('.', ',')} €`;
}

function parseCents(eurStr) {
  if (!eurStr) return 0;
  const cleaned = String(eurStr).replace('€', '').trim().replace(',', '.');
  const num = parseFloat(cleaned);
  return isNaN(num) ? 0 : Math.round(num * 100);
}

function toast(message, type = 'info') {
  const container = document.getElementById('toast-container');
  const t = document.createElement('div');
  t.className = `toast toast-${type}`;
  t.textContent = message;
  container.appendChild(t);
  setTimeout(() => {
    t.style.opacity = '0';
    setTimeout(() => t.remove(), 250);
  }, 4000);
}

async function api(path, options = {}) {
  try {
    const res = await fetch(path, options);
    const contentType = res.headers.get('content-type') || '';
    let data = null;
    if (contentType.includes('application/json')) {
      data = await res.json();
    } else {
      data = await res.text();
    }
    if (!res.ok) {
      const err = (data && data.detail) || res.statusText || 'Fehler aufgetreten';
      toast(err, 'error');
      throw new Error(err);
    }
    return data;
  } catch (err) {
    console.error('API Error:', path, err);
    throw err;
  }
}

// -----------------------------------------------------------------------------
// Image Downscaling (Canvas)
// -----------------------------------------------------------------------------

async function downscaleImage(file, maxDimension = 1600) {
  return new Promise((resolve) => {
    const reader = new FileReader();
    reader.onload = (e) => {
      const img = new Image();
      img.onload = () => {
        let { width, height } = img;
        if (width > maxDimension || height > maxDimension) {
          if (width > height) {
            height = Math.round((height * maxDimension) / width);
            width = maxDimension;
          } else {
            width = Math.round((width * maxDimension) / height);
            height = maxDimension;
          }
        }
        const canvas = document.createElement('canvas');
        canvas.width = width;
        canvas.height = height;
        const ctx = canvas.getContext('2d');
        ctx.drawImage(img, 0, 0, width, height);
        canvas.toBlob((blob) => resolve(blob || file), 'image/jpeg', 0.85);
      };
      img.src = e.target.result;
    };
    reader.readAsDataURL(file);
  });
}

// -----------------------------------------------------------------------------
// Router & Initialization
// -----------------------------------------------------------------------------

function navigate(viewName) {
  const clean = (viewName || 'dashboard').replace('#', '');
  
  // Instant visual switch (0ms delay)
  document.querySelectorAll('.view-panel').forEach((el) => {
    el.classList.toggle('active', el.id === `view-${clean}`);
  });
  document.querySelectorAll('.nav-link').forEach((el) => {
    el.classList.toggle('active', el.dataset.view === clean);
  });
  window.scrollTo({ top: 0, behavior: 'instant' });

  // Load view data in background
  if (clean === 'dashboard') loadDashboard();
  if (clean === 'receipts') loadReceipts();
  if (clean === 'products') loadProducts();
  if (clean === 'stats') loadStats();
  if (clean === 'settings') loadSettings();
}

window.addEventListener('hashchange', (e) => {
  e.preventDefault();
  navigate(window.location.hash);
});

document.addEventListener('DOMContentLoaded', async () => {
  setupModals();
  setupEventListeners();
  await initAppData();
  navigate(window.location.hash || '#dashboard');

  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('/static/sw.js').catch(() => {});
  }
});

async function initAppData() {
  try {
    state.meta = await api('/api/meta');
    state.currentMonth = state.meta.current_month;
    await reloadPersonsAndCategories();
    await updateMonthSelector();
  } catch (e) {
    toast('Initialisierung fehlgeschlagen.', 'error');
  }
}

async function reloadPersonsAndCategories() {
  const [persons, categories] = await Promise.all([
    api('/api/persons'),
    api('/api/categories')
  ]);
  state.persons = persons;
  state.categories = categories;

  const firstRun = document.getElementById('first-run-banner');
  if (firstRun) {
    firstRun.style.display = persons.length === 0 ? 'block' : 'none';
  }
}

async function updateMonthSelector() {
  const months = await api('/api/months');
  const sel = document.getElementById('month-selector');
  sel.innerHTML = '';
  months.forEach((m) => {
    const opt = document.createElement('option');
    opt.value = m.id;
    opt.textContent = `${m.id} ${m.status === 'closed' ? '🔒' : ''}`;
    sel.appendChild(opt);
  });
  if (months.some((m) => m.id === state.currentMonth)) {
    sel.value = state.currentMonth;
  } else if (months.length > 0) {
    state.currentMonth = months[0].id;
    sel.value = state.currentMonth;
  }
}

// -----------------------------------------------------------------------------
// 1. Dashboard View
// -----------------------------------------------------------------------------

async function loadDashboard() {
  if (!state.currentMonth) return;
  try {
    const month = await api(`/api/months/${state.currentMonth}`);
    renderDashboardKPIs(month);
    renderTransfers(month);
    renderPersonCards(month);
    renderCategoryBars(month);
  } catch (err) {
    console.error(err);
  }
}

function renderDashboardKPIs(month) {
  document.getElementById('kpi-total').textContent = fmtEur(month.total_cents);
  document.getElementById('kpi-receipts-count').textContent = `${month.receipts_confirmed} Belege (${month.receipts_draft} Entwürfe)`;
  document.getElementById('kpi-days').textContent = month.total_days;

  const tmmElem = document.getElementById('kpi-tmm');
  if (month.cost_per_tmm !== null && month.cost_per_tmm !== undefined) {
    const tmmCents = Math.round(month.cost_per_tmm);
    tmmElem.textContent = fmtEur(tmmCents);
    tmmElem.style.color = tmmCents <= month.reference_tmm_cents ? '#22c55e' : '#f59e0b';
  } else {
    tmmElem.textContent = '-';
    tmmElem.style.color = 'var(--text)';
  }

  const statusElem = document.getElementById('kpi-status');
  statusElem.textContent = month.status === 'closed' ? 'Abgeschlossen' : 'Offen';
  statusElem.style.color = month.status === 'closed' ? 'var(--text-muted)' : '#22c55e';

  // Update month-action toggle button text
  const toggleBtn = document.getElementById('action-toggle-close');
  if (toggleBtn) {
    toggleBtn.textContent = month.status === 'closed' ? '🔓 Monat wieder öffnen' : '🔒 Monat abschließen';
  }
}

function renderTransfers(month) {
  const container = document.getElementById('transfer-list');
  container.innerHTML = '';

  if (!month.transfers || month.transfers.length === 0) {
    container.innerHTML = '<div style="color:var(--text-muted); font-size:0.9rem; padding:8px;">Alles ausgeglichen oder noch keine Belege.</div>';
    return;
  }

  month.transfers.forEach((t) => {
    const div = document.createElement('div');
    div.className = 'transfer-item';
    div.innerHTML = `
      <div class="transfer-flow">
        <span style="color:#f87171;">${t.from_name}</span>
        <span class="transfer-arrow">➔</span>
        <span style="color:#22c55e;">${t.to_name}</span>
      </div>
      <div class="transfer-action">
        <span style="font-weight:800; font-size:1.05rem;">${fmtEur(t.cents)}</span>
        <button class="btn btn-secondary btn-sm btn-pay" data-from="${t.from_id}" data-to="${t.to_id}" data-amount="${(t.cents/100).toFixed(2)}">
          Zahlung erfassen
        </button>
      </div>
    `;
    container.appendChild(div);
  });

  container.querySelectorAll('.btn-pay').forEach((btn) => {
    btn.onclick = () => {
      openPaymentModal(btn.dataset.from, btn.dataset.to, btn.dataset.amount);
    };
  });
}

function renderPersonCards(month) {
  const container = document.getElementById('person-cards');
  container.innerHTML = '';

  month.persons.forEach((p) => {
    const card = document.createElement('div');
    card.className = 'person-card';
    card.style.borderLeftColor = p.color || '#3b82f6';

    const saldoClass = p.saldo > 0 ? 'saldo-positive' : p.saldo < 0 ? 'saldo-negative' : 'saldo-zero';
    const tagsHtml = (p.tags || []).map((t) => `<span class="tag-badge">${t}</span>`).join('');

    card.innerHTML = `
      <div class="person-header">
        <span class="person-name">
          <span style="display:inline-block; width:10px; height:10px; border-radius:50%; background:${p.color};"></span>
          ${p.name}
        </span>
        <span class="person-saldo ${saldoClass}">${p.saldo > 0 ? '+' : ''}${fmtEur(p.saldo)}</span>
      </div>
      <div class="person-details">
        <span>📅 ${p.days} Tage</span>
        <span>🛒 Gekauft: ${fmtEur(p.haben)}</span>
        <span>🍽️ Soll: ${fmtEur(p.soll)}</span>
        ${p.private > 0 ? `<span>🔒 Privat: ${fmtEur(p.private)}</span>` : ''}
      </div>
      ${tagsHtml ? `<div class="person-tags">${tagsHtml}</div>` : ''}
    `;
    container.appendChild(card);
  });
}

function renderCategoryBars(month) {
  const container = document.getElementById('category-bars');
  container.innerHTML = '';
  const total = month.total_cents || 1;

  state.categories.forEach((cat) => {
    if (cat.mode === 'buyer') return;
    const catTotal = month.category_totals[cat.id] || 0;
    if (catTotal === 0) return;
    const pct = Math.round((catTotal / total) * 100);

    const div = document.createElement('div');
    div.innerHTML = `
      <div style="display:flex; justify-content:space-between; font-size:0.85rem; margin-bottom:3px;">
        <span style="font-weight:600;">${cat.name}</span>
        <span>${fmtEur(catTotal)} (${pct}%)</span>
      </div>
      <div style="background:var(--surface-2); height:8px; border-radius:4px; overflow:hidden;">
        <div style="background:${cat.color}; width:${pct}%; height:100%;"></div>
      </div>
    `;
    container.appendChild(div);
  });
}

// -----------------------------------------------------------------------------
// 2. Capture & Review View
// -----------------------------------------------------------------------------

function setupCaptureHandlers() {
  const fileInput = document.getElementById('file-input');
  const btnCamera = document.getElementById('btn-camera');
  const dropzone = document.getElementById('dropzone');
  const btnManual = document.getElementById('btn-manual-receipt');

  btnCamera.onclick = () => fileInput.click();
  fileInput.onchange = (e) => {
    if (e.target.files && e.target.files[0]) {
      uploadReceiptFile(e.target.files[0]);
    }
  };

  dropzone.ondragover = (e) => {
    e.preventDefault();
    dropzone.classList.add('dragover');
  };
  dropzone.ondragleave = () => dropzone.classList.remove('dragover');
  dropzone.ondrop = (e) => {
    e.preventDefault();
    dropzone.classList.remove('dragover');
    if (e.dataTransfer.files && e.dataTransfer.files[0]) {
      uploadReceiptFile(e.dataTransfer.files[0]);
    }
  };

  btnManual.onclick = async () => {
    try {
      const rec = await api('/api/receipts/manual', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ month_id: state.currentMonth })
      });
      loadReceiptIntoReview(rec);
    } catch (e) {}
  };
}

async function uploadReceiptFile(file) {
  const spinner = document.getElementById('upload-spinner');
  const dropzone = document.getElementById('dropzone');
  spinner.style.display = 'block';
  dropzone.style.display = 'none';

  try {
    const downscaledBlob = await downscaleImage(file, 1600);
    const formData = new FormData();
    formData.append('image', downscaledBlob, file.name);
    formData.append('month_id', state.currentMonth);

    const rec = await api('/api/receipts/upload', {
      method: 'POST',
      body: formData
    });

    loadReceiptIntoReview(rec);
    toast('Kassenbon erfolgreich verarbeitet!', 'success');
  } catch (err) {
    toast('Fehler beim Hochladen / Parsen.', 'error');
  } finally {
    spinner.style.display = 'none';
    dropzone.style.display = 'flex';
  }
}

function loadReceiptIntoReview(receipt) {
  state.currentReceipt = receipt;
  document.getElementById('capture-upload-box').style.display = 'none';
  const container = document.getElementById('review-container');
  container.style.display = 'block';

  document.getElementById('review-store').value = receipt.store || '';
  document.getElementById('review-date').value = receipt.date || '';
  document.getElementById('review-total').value = receipt.total_cents ? (receipt.total_cents / 100).toFixed(2) : '';

  // Buyers
  const buyerSel = document.getElementById('review-buyer');
  buyerSel.innerHTML = '<option value="">-- Käufer wählen --</option>';
  state.persons.forEach((p) => {
    const opt = document.createElement('option');
    opt.value = p.id;
    opt.textContent = p.name;
    if (receipt.buyer_id === p.id) opt.selected = true;
    buyerSel.appendChild(opt);
  });

  const badge = document.getElementById('review-status-badge');
  badge.textContent = receipt.status === 'confirmed' ? 'Bestätigt' : 'Entwurf';
  badge.style.background = receipt.status === 'confirmed' ? '#16a34a' : '#f59e0b';

  // Alerts
  renderReviewAlerts(receipt);

  // View image button
  const btnImg = document.getElementById('btn-view-image');
  if (receipt.has_image) {
    btnImg.style.display = 'inline-flex';
    btnImg.onclick = () => {
      document.getElementById('modal-img-elem').src = `/api/receipts/${receipt.id}/image`;
      openModal('modal-image');
    };
  } else {
    btnImg.style.display = 'none';
  }

  renderReviewItemsTable(receipt.items || []);
  recalculateReviewSum();
}

function renderReviewAlerts(receipt) {
  const container = document.getElementById('review-alerts');
  container.innerHTML = '';
  (receipt.warnings || []).forEach((w) => {
    const alertDiv = document.createElement('div');
    const lvl = w.level === 'error' ? 'alert-error' : w.level === 'warn' ? 'alert-warn' : 'alert-info';
    alertDiv.className = `alert ${lvl}`;
    alertDiv.textContent = `⚠️ ${w.message}`;
    container.appendChild(alertDiv);
  });
}

function renderReviewItemsTable(items) {
  const tbody = document.getElementById('review-items-body');
  tbody.innerHTML = '';

  items.forEach((item, index) => {
    const tr = document.createElement('tr');
    tr.className = `item-row ${item.review ? 'is-review' : ''} ${item.price_cents < 0 ? 'is-negative' : ''}`;
    tr.dataset.itemId = item.id || '';

    // Category options
    let catOptions = '';
    state.categories.forEach((c) => {
      catOptions += `<option value="${c.id}" ${item.category_id === c.id ? 'selected' : ''}>${c.name}</option>`;
    });

    tr.innerHTML = `
      <td>
        <input type="text" class="form-control item-name" value="${escapeHtml(item.name)}" style="font-size:0.86rem; padding:4px 6px;">
      </td>
      <td>
        <input type="number" step="0.01" class="form-control item-price" value="${(item.price_cents / 100).toFixed(2)}" style="font-size:0.86rem; padding:4px 6px;">
      </td>
      <td>
        <select class="form-control item-category" style="font-size:0.86rem; padding:4px 6px;">
          ${catOptions}
        </select>
      </td>
      <td>
        <button class="btn btn-secondary btn-sm btn-delete-item" title="Posten löschen">🗑️</button>
      </td>
    `;
    tbody.appendChild(tr);
  });

  tbody.querySelectorAll('.item-price').forEach((el) => el.oninput = recalculateReviewSum);
  tbody.querySelectorAll('.btn-delete-item').forEach((btn) => {
    btn.onclick = (e) => {
      e.target.closest('tr').remove();
      recalculateReviewSum();
    };
  });
}

function recalculateReviewSum() {
  const prices = Array.from(document.querySelectorAll('.item-price')).map((el) => parseCents(el.value));
  const sum = prices.reduce((a, b) => a + b, 0);
  document.getElementById('review-items-sum').textContent = fmtEur(sum);
}

function getReviewPayload() {
  const items = [];
  document.querySelectorAll('#review-items-body tr').forEach((tr, pos) => {
    const id = tr.dataset.itemId ? parseInt(tr.dataset.itemId) : null;
    const name = tr.querySelector('.item-name').value.trim();
    const price_cents = parseCents(tr.querySelector('.item-price').value);
    const category_id = parseInt(tr.querySelector('.item-category').value);
    if (name) {
      items.push({ id, name, price_cents, category_id, qty: 1.0 });
    }
  });

  const totalStr = document.getElementById('review-total').value;
  return {
    store: document.getElementById('review-store').value.trim(),
    date: document.getElementById('review-date').value || null,
    buyer_id: parseInt(document.getElementById('review-buyer').value) || null,
    total_cents: totalStr ? parseCents(totalStr) : null,
    items
  };
}

// -----------------------------------------------------------------------------
// 3. Receipts View
// -----------------------------------------------------------------------------

async function loadReceipts() {
  const buyerId = document.getElementById('filter-receipt-buyer').value;
  const status = document.getElementById('filter-receipt-status').value;
  const q = document.getElementById('filter-receipt-q').value;

  let url = `/api/receipts?month=${state.currentMonth}`;
  if (buyerId) url += `&buyer_id=${buyerId}`;
  if (status) url += `&status=${status}`;
  if (q) url += `&q=${encodeURIComponent(q)}`;

  const receipts = await api(url);
  document.getElementById('receipts-count-badge').textContent = receipts.length;

  const container = document.getElementById('receipts-list');
  container.innerHTML = '';

  if (receipts.length === 0) {
    container.innerHTML = '<div style="color:var(--text-muted); padding:12px;">Keine Belege für diesen Monat gefunden.</div>';
    return;
  }

  receipts.forEach((r) => {
    const div = document.createElement('div');
    div.className = 'transfer-item';
    div.style.cursor = 'pointer';

    const statusBadge = r.status === 'confirmed'
      ? '<span class="tag-badge" style="background:#16a34a; color:#fff;">Bestätigt</span>'
      : '<span class="tag-badge" style="background:#f59e0b; color:#fff;">Entwurf</span>';

    div.innerHTML = `
      <div>
        <div style="font-weight:700; font-size:0.96rem;">${escapeHtml(r.store || 'Unbekanntes Geschäft')}</div>
        <div style="font-size:0.8rem; color:var(--text-muted); display:flex; gap:8px; margin-top:2px;">
          <span>📅 ${r.date || r.created_at.slice(0, 10)}</span>
          <span>👤 ${r.buyer_name || 'Kein Käufer'}</span>
          <span>🛒 ${r.item_count} Posten</span>
        </div>
      </div>
      <div style="display:flex; align-items:center; gap:10px;">
        <span style="font-size:1.1rem; font-weight:800;">${fmtEur(r.items_sum_cents || r.total_cents)}</span>
        ${statusBadge}
      </div>
    `;

    div.onclick = async () => {
      const detail = await api(`/api/receipts/${r.id}`);
      window.location.hash = '#capture';
      loadReceiptIntoReview(detail);
    };

    container.appendChild(div);
  });
}

// -----------------------------------------------------------------------------
// 4. Products (Rules Catalog) View
// -----------------------------------------------------------------------------

let productSearchTimeout = null;

async function loadProducts() {
  const q = document.getElementById('filter-product-q').value;
  const cat = document.getElementById('filter-product-cat').value;
  const flag = document.getElementById('filter-product-flag').value;
  const src = document.getElementById('filter-product-source').value;

  let url = '/api/products?limit=150';
  if (q) url += `&q=${encodeURIComponent(q)}`;
  if (cat) url += `&category_id=${cat}`;
  if (flag) url += `&flag=${flag}`;
  if (src) url += `&source=${src}`;

  const res = await api(url);
  document.getElementById('products-count').textContent = res.total;

  const tbody = document.getElementById('products-table-body');
  tbody.innerHTML = '';

  res.items.forEach((p) => {
    const tr = document.createElement('tr');

    let catSelect = '<select class="form-control prod-cat" style="font-size:0.82rem; padding:2px 4px;">';
    state.categories.forEach((c) => {
      catSelect += `<option value="${c.id}" ${p.category_id === c.id ? 'selected' : ''}>${c.name}</option>`;
    });
    catSelect += '</select>';

    const flagBadge = p.flag === 'review'
      ? '<span class="tag-badge" style="background:#f59e0b; color:#fff;">Review</span>'
      : '<span class="tag-badge" style="background:#16a34a; color:#fff;">Sicher</span>';

    tr.innerHTML = `
      <td style="font-weight:600;">${escapeHtml(p.pattern)}</td>
      <td>${catSelect}</td>
      <td>${flagBadge}</td>
      <td style="font-size:0.75rem; color:var(--text-muted);">${p.source}</td>
      <td style="text-align:center;">${p.hits}</td>
      <td>
        <button class="btn btn-secondary btn-sm btn-delete-prod" data-id="${p.id}">🗑️</button>
      </td>
    `;

    tr.querySelector('.prod-cat').onchange = async (e) => {
      await api(`/api/products/${p.id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ category_id: parseInt(e.target.value) })
      });
      toast(`Kategorie für "${p.pattern}" geändert.`, 'success');
    };

    tr.querySelector('.btn-delete-prod').onclick = async () => {
      if (confirm(`Regel für "${p.pattern}" wirklich löschen?`)) {
        await api(`/api/products/${p.id}`, { method: 'DELETE' });
        tr.remove();
        toast('Regel gelöscht.', 'info');
      }
    };

    tbody.appendChild(tr);
  });
}

// -----------------------------------------------------------------------------
// 5. Statistics View
// -----------------------------------------------------------------------------

async function loadStats() {
  const data = await api('/api/stats?months=12');

  // 1. TmM Trend Chart
  const ctxTmm = document.getElementById('chart-tmm').getContext('2d');
  if (state.charts.tmm) state.charts.tmm.destroy();

  const labels = data.months.map((m) => m.month);
  const values = data.months.map((m) => (m.cost_per_tmm ? (m.cost_per_tmm / 100).toFixed(2) : null));
  const refLine = labels.map(() => (data.reference_tmm_cents / 100).toFixed(2));

  state.charts.tmm = new Chart(ctxTmm, {
    type: 'line',
    data: {
      labels,
      datasets: [
        {
          label: '€ / Tag (TmM)',
          data: values,
          borderColor: '#16a34a',
          backgroundColor: 'rgba(22, 163, 74, 0.1)',
          fill: true,
          tension: 0.25,
          pointRadius: 5
        },
        {
          label: '30-Monate-Median (7,26 €)',
          data: refLine,
          borderColor: '#94a3b8',
          borderDash: [5, 5],
          pointRadius: 0,
          fill: false
        }
      ]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { labels: { color: '#f8fafc' } } },
      scales: {
        x: { ticks: { color: '#94a3b8' }, grid: { color: '#334155' } },
        y: { ticks: { color: '#94a3b8' }, grid: { color: '#334155' } }
      }
    }
  });

  // 2. Category Breakdown Chart
  const ctxCat = document.getElementById('chart-categories').getContext('2d');
  if (state.charts.cat) state.charts.cat.destroy();

  const catMap = {};
  data.months.forEach((m) => {
    Object.entries(m.category_totals).forEach(([cid, cents]) => {
      catMap[cid] = (catMap[cid] || 0) + cents;
    });
  });

  const catLabels = [];
  const catValues = [];
  const catColors = [];

  state.categories.forEach((c) => {
    if (catMap[c.id]) {
      catLabels.push(c.name);
      catValues.push((catMap[c.id] / 100).toFixed(2));
      catColors.push(c.color);
    }
  });

  state.charts.cat = new Chart(ctxCat, {
    type: 'doughnut',
    data: {
      labels: catLabels,
      datasets: [{ data: catValues, backgroundColor: catColors }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { labels: { color: '#f8fafc' } } }
    }
  });

  // 3. Top Stores
  const storesContainer = document.getElementById('stats-stores-list');
  storesContainer.innerHTML = '';
  data.stores.forEach((s) => {
    const div = document.createElement('div');
    div.style.display = 'flex';
    div.style.justifyContent = 'space-between';
    div.style.padding = '6px 0';
    div.style.borderBottom = '1px solid var(--border-light)';
    div.innerHTML = `
      <span style="font-weight:600;">${escapeHtml(s.store)} (${s.receipts}x)</span>
      <span>${fmtEur(s.total_cents)}</span>
    `;
    storesContainer.appendChild(div);
  });
}

// -----------------------------------------------------------------------------
// 6. Settings View
// -----------------------------------------------------------------------------

async function loadSettings() {
  await reloadPersonsAndCategories();
  renderSettingsPersons();
  renderSettingsCategories();
}

function renderSettingsPersons() {
  const container = document.getElementById('settings-persons-list');
  container.innerHTML = '';

  state.persons.forEach((p) => {
    const div = document.createElement('div');
    div.className = 'transfer-item';
    div.innerHTML = `
      <div>
        <div style="font-weight:700; display:flex; align-items:center; gap:6px;">
          <span style="width:12px; height:12px; border-radius:50%; background:${p.color};"></span>
          ${escapeHtml(p.name)} ${!p.active ? '(inaktiv)' : ''}
        </div>
        <div style="font-size:0.8rem; color:var(--text-muted); margin-top:2px;">
          Tags: ${(p.tags || []).join(', ') || 'keine'}
        </div>
      </div>
      <div style="display:flex; gap:6px;">
        <button class="btn btn-secondary btn-sm btn-edit-person" data-id="${p.id}">Bearbeiten</button>
      </div>
    `;

    div.querySelector('.btn-edit-person').onclick = () => openPersonModal(p);
    container.appendChild(div);
  });
}

function renderSettingsCategories() {
  const container = document.getElementById('settings-categories-list');
  container.innerHTML = '';

  state.categories.forEach((c) => {
    const div = document.createElement('div');
    div.className = 'transfer-item';
    div.innerHTML = `
      <div>
        <div style="font-weight:700; display:flex; align-items:center; gap:6px;">
          <span style="width:12px; height:12px; border-radius:3px; background:${c.color};"></span>
          ${escapeHtml(c.name)}
        </div>
        <div style="font-size:0.8rem; color:var(--text-muted); margin-top:2px;">
          Schlüssel: <code>${c.key}</code> | Modus: ${c.mode} | Pflicht-Tag: <code>${c.required_tag || 'alle'}</code>
        </div>
      </div>
    `;
    container.appendChild(div);
  });
}

// -----------------------------------------------------------------------------
// Modals Handling
// -----------------------------------------------------------------------------

function openModal(id) {
  document.getElementById(id).classList.add('open');
}

function closeModal(id) {
  document.getElementById(id).classList.remove('open');
}

function setupModals() {
  document.querySelectorAll('.modal-close').forEach((btn) => {
    btn.onclick = (e) => {
      const backdrop = e.target.closest('.modal-backdrop');
      if (backdrop) backdrop.classList.remove('open');
    };
  });
}

function openPersonModal(person = null) {
  document.getElementById('person-id').value = person ? person.id : '';
  document.getElementById('person-name').value = person ? person.name : '';
  document.getElementById('person-color').value = person ? person.color : '#10b981';
  document.getElementById('modal-person-title').textContent = person ? 'Mitbewohner bearbeiten' : 'Neuer Mitbewohner';
  
  const currentTags = person ? (person.tags || []) : [];
  renderPersonTagsSelector(currentTags);
  
  openModal('modal-person');
}

function renderPersonTagsSelector(selectedTags = []) {
  const container = document.getElementById('person-tags-selector');
  container.innerHTML = '';

  // Get all known tags from categories (e.g. fleisch, alkohol, milch, ei)
  const knownTags = new Set(['fleisch', 'alkohol', 'milch', 'ei']);
  state.categories.forEach((c) => {
    if (c.required_tag) knownTags.add(c.required_tag);
  });
  selectedTags.forEach((t) => knownTags.add(t));

  const currentSet = new Set(selectedTags);

  knownTags.forEach((tag) => {
    const isSelected = currentSet.has(tag);
    const pill = document.createElement('div');
    pill.className = `tag-pill ${isSelected ? 'active' : ''}`;
    pill.dataset.tag = tag;
    pill.innerHTML = `<span class="pill-check">${isSelected ? '✓' : '+'}</span> <span>${tag}</span>`;

    pill.onclick = () => {
      if (currentSet.has(tag)) {
        currentSet.delete(tag);
        pill.classList.remove('active');
        pill.querySelector('.pill-check').textContent = '+';
      } else {
        currentSet.add(tag);
        pill.classList.add('active');
        pill.querySelector('.pill-check').textContent = '✓';
      }
      document.getElementById('person-tags').value = Array.from(currentSet).join(', ');
    };
    container.appendChild(pill);
  });

  document.getElementById('person-tags').value = Array.from(currentSet).join(', ');
}

function openPaymentModal(fromId = '', toId = '', amount = '') {
  const fromSel = document.getElementById('pay-from');
  const toSel = document.getElementById('pay-to');
  fromSel.innerHTML = '';
  toSel.innerHTML = '';

  state.persons.forEach((p) => {
    fromSel.innerHTML += `<option value="${p.id}" ${p.id == fromId ? 'selected' : ''}>${p.name}</option>`;
    toSel.innerHTML += `<option value="${p.id}" ${p.id == toId ? 'selected' : ''}>${p.name}</option>`;
  });

  document.getElementById('pay-amount').value = amount;
  openModal('modal-payment');
}

// -----------------------------------------------------------------------------
// Global Event Listeners & Buttons
// -----------------------------------------------------------------------------

function setupEventListeners() {
  setupCaptureHandlers();

  // Instant Nav Link handling (eliminates mobile anchor scroll jump and lag)
  document.querySelectorAll('.nav-link, .brand').forEach((el) => {
    el.addEventListener('click', (e) => {
      e.preventDefault();
      const view = el.dataset.view || 'dashboard';
      if (window.location.hash !== `#${view}`) {
        history.replaceState(null, '', `#${view}`);
      }
      navigate(view);
    });
  });

  // Custom Tag addition in person modal
  document.getElementById('btn-add-custom-tag').onclick = () => {
    const input = document.getElementById('person-custom-tag');
    const val = input.value.trim().toLowerCase().replace(/[^a-z0-9_-]/g, '');
    if (!val) return;
    const current = (document.getElementById('person-tags').value || '')
      .split(',')
      .map((t) => t.trim())
      .filter(Boolean);
    if (!current.includes(val)) {
      current.push(val);
      renderPersonTagsSelector(current);
    }
    input.value = '';
  };

  // Month selector change
  document.getElementById('month-selector').onchange = (e) => {
    state.currentMonth = e.target.value;
    loadDashboard();
  };

  // Refresh button
  document.getElementById('btn-refresh').onclick = () => {
    toast('Aktualisiere...', 'info');
    loadDashboard();
  };

  // Edit days modal
  document.getElementById('btn-edit-days').onclick = async () => {
    const month = await api(`/api/months/${state.currentMonth}`);
    const body = document.getElementById('modal-days-body');
    body.innerHTML = '';

    month.persons.forEach((p) => {
      body.innerHTML += `
        <div class="form-group" style="display:flex; justify-content:space-between; align-items:center;">
          <label class="form-label" style="margin:0; font-weight:700;">${p.name}</label>
          <div style="display:flex; align-items:center; gap:6px;">
            <input type="number" min="0" max="31" class="form-control day-input" data-person="${p.id}" value="${p.days}" style="width:80px; text-align:center;">
            <span>Tage</span>
          </div>
        </div>
      `;
    });
    openModal('modal-days');
  };

  document.getElementById('btn-save-days').onclick = async () => {
    const days = {};
    document.querySelectorAll('.day-input').forEach((el) => {
      days[el.dataset.person] = parseInt(el.value) || 0;
    });
    await api(`/api/months/${state.currentMonth}/presence`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ days })
    });
    closeModal('modal-days');
    toast('Anwesenheitstage gespeichert.', 'success');
    loadDashboard();
  };

  // Copy days from previous month
  document.getElementById('btn-copy-days').onclick = async () => {
    if (confirm('Anwesenheitstage vom Vormonat wirklich für diesen Monat übernehmen?')) {
      await api(`/api/months/${state.currentMonth}/copy-presence-from-previous`, { method: 'POST' });
      toast('Tage aus Vormonat übernommen.', 'success');
      loadDashboard();
    }
  };

  // Month actions dropdown menu toggle
  const monthActionsBtn = document.getElementById('btn-month-actions');
  const monthActionsMenu = document.getElementById('month-actions-menu');

  monthActionsBtn.onclick = (e) => {
    e.stopPropagation();
    const isShown = monthActionsMenu.style.display === 'flex';
    monthActionsMenu.style.display = isShown ? 'none' : 'flex';
  };

  document.addEventListener('click', () => {
    if (monthActionsMenu) monthActionsMenu.style.display = 'none';
  });

  document.getElementById('action-open-days').onclick = () => {
    document.getElementById('btn-edit-days').click();
  };

  document.getElementById('action-copy-days').onclick = () => {
    document.getElementById('btn-copy-days').click();
  };

  document.getElementById('action-new-payment').onclick = () => {
    openPaymentModal();
  };

  document.getElementById('action-print').onclick = () => {
    window.print();
  };

  // Month actions (close / reopen)
  document.getElementById('action-toggle-close').onclick = async () => {
    const month = await api(`/api/months/${state.currentMonth}`);
    if (month.status === 'open') {
      if (confirm(`Monat ${state.currentMonth} jetzt final abschließen? (Belege können danach nicht mehr editiert werden)`)) {
        await api(`/api/months/${state.currentMonth}/close`, { method: 'POST' });
        toast(`Monat ${state.currentMonth} abgeschlossen.`, 'success');
        await updateMonthSelector();
        loadDashboard();
      }
    } else {
      if (confirm(`Monat ${state.currentMonth} wieder für Bearbeitung öffnen?`)) {
        await api(`/api/months/${state.currentMonth}/reopen`, { method: 'POST' });
        toast(`Monat ${state.currentMonth} wieder geöffnet.`, 'success');
        await updateMonthSelector();
        loadDashboard();
      }
    }
  };

  // Review Editor Actions
  document.getElementById('btn-add-item').onclick = () => {
    const tbody = document.getElementById('review-items-body');
    const tr = document.createElement('tr');
    tr.className = 'item-row';
    let catOptions = '';
    state.categories.forEach((c) => {
      catOptions += `<option value="${c.id}">${c.name}</option>`;
    });

    tr.innerHTML = `
      <td><input type="text" class="form-control item-name" placeholder="Postenname" style="font-size:0.86rem; padding:4px 6px;"></td>
      <td><input type="number" step="0.01" class="form-control item-price" value="0.00" style="font-size:0.86rem; padding:4px 6px;"></td>
      <td><select class="form-control item-category" style="font-size:0.86rem; padding:4px 6px;">${catOptions}</select></td>
      <td><button class="btn btn-secondary btn-sm btn-delete-item">🗑️</button></td>
    `;
    tbody.appendChild(tr);
    tr.querySelector('.item-price').oninput = recalculateReviewSum;
    tr.querySelector('.btn-delete-item').onclick = () => { tr.remove(); recalculateReviewSum(); };
  };

  document.getElementById('btn-save-draft').onclick = async () => {
    const payload = getReviewPayload();
    await api(`/api/receipts/${state.currentReceipt.id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    toast('Entwurf gespeichert.', 'success');
  };

  document.getElementById('btn-confirm-receipt').onclick = async () => {
    const payload = getReviewPayload();
    if (!payload.buyer_id) {
      toast('Bitte zuerst den Käufer auswählen.', 'error');
      return;
    }
    // 1. Save changes
    await api(`/api/receipts/${state.currentReceipt.id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    // 2. Confirm & Learn
    const res = await api(`/api/receipts/${state.currentReceipt.id}/confirm`, { method: 'POST' });
    toast(`Beleg bestätigt! ${res.learn_result ? res.learn_result.learned : 0} neue Regeln gelernt.`, 'success');
    window.location.hash = '#dashboard';
  };

  document.getElementById('btn-delete-review').onclick = async () => {
    if (confirm('Diesen Beleg wirklich löschen?')) {
      await api(`/api/receipts/${state.currentReceipt.id}`, { method: 'DELETE' });
      toast('Beleg gelöscht.', 'info');
      document.getElementById('review-container').style.display = 'none';
      document.getElementById('capture-upload-box').style.display = 'block';
      window.location.hash = '#receipts';
    }
  };

  // Payment Submit
  document.getElementById('btn-submit-payment').onclick = async () => {
    const fromId = parseInt(document.getElementById('pay-from').value);
    const toId = parseInt(document.getElementById('pay-to').value);
    const cents = parseCents(document.getElementById('pay-amount').value);
    const note = document.getElementById('pay-note').value.trim();

    if (!cents || cents <= 0) {
      toast('Ungültiger Betrag.', 'error');
      return;
    }
    await api('/api/payments', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        month_id: state.currentMonth,
        from_id: fromId,
        to_id: toId,
        cents,
        note
      })
    });
    closeModal('modal-payment');
    toast('Zahlung verbucht!', 'success');
    loadDashboard();
  };

  // Person Save
  document.getElementById('btn-add-person').onclick = () => openPersonModal();
  document.getElementById('btn-save-person').onclick = async () => {
    const id = document.getElementById('person-id').value;
    const name = document.getElementById('person-name').value.trim();
    const color = document.getElementById('person-color').value;
    const tags = document.getElementById('person-tags').value.split(',').map((t) => t.trim()).filter(Boolean);

    if (!name) {
      toast('Name ist erforderlich.', 'error');
      return;
    }
    if (id) {
      await api(`/api/persons/${id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, color, tags })
      });
      toast('Mitbewohner aktualisiert.', 'success');
    } else {
      await api('/api/persons', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, color, tags })
      });
      toast('Mitbewohner angelegt.', 'success');
    }
    closeModal('modal-person');
    await loadSettings();
  };

  // Filter receipt inputs
  ['filter-receipt-buyer', 'filter-receipt-status'].forEach((id) => {
    document.getElementById(id).onchange = loadReceipts;
  });
  document.getElementById('filter-receipt-q').oninput = () => {
    clearTimeout(productSearchTimeout);
    productSearchTimeout = setTimeout(loadReceipts, 300);
  };

  // Filter product inputs
  ['filter-product-cat', 'filter-product-flag', 'filter-product-source'].forEach((id) => {
    document.getElementById(id).onchange = loadProducts;
  });
  document.getElementById('filter-product-q').oninput = () => {
    clearTimeout(productSearchTimeout);
    productSearchTimeout = setTimeout(loadProducts, 300);
  };

  // Product New modal
  document.getElementById('btn-new-product').onclick = () => {
    const sel = document.getElementById('product-category');
    sel.innerHTML = '';
    state.categories.forEach((c) => {
      sel.innerHTML += `<option value="${c.id}">${c.name}</option>`;
    });
    openModal('modal-product');
  };

  document.getElementById('btn-save-product').onclick = async () => {
    const pattern = document.getElementById('product-pattern').value.trim();
    const category_id = parseInt(document.getElementById('product-category').value);
    const flag = document.getElementById('product-flag').value;
    const store = document.getElementById('product-store').value.trim() || null;

    if (!pattern) {
      toast('Muster erforderlich.', 'error');
      return;
    }
    await api('/api/products', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ pattern, category_id, flag, store })
    });
    closeModal('modal-product');
    toast(`Regel für "${pattern}" erstellt.`, 'success');
    loadProducts();
  };

  // Reset seed
  document.getElementById('btn-reset-seed').onclick = async () => {
    if (confirm('Fehlende Standard-Muster aus dem Katalog nachladen?')) {
      const res = await api('/api/products/reset-seed', { method: 'POST' });
      toast(res.message, 'success');
      loadProducts();
    }
  };

  // CSV Export
  document.getElementById('btn-export-csv').onclick = () => {
    window.location.href = '/api/products/export-csv';
  };
}

function escapeHtml(str) {
  if (!str) return '';
  return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}
