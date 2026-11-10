'use strict';
const $ = selector => document.querySelector(selector);
const $$ = selector => [...document.querySelectorAll(selector)];
let token = $('meta[name="session-token"]').content;
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const icon = name => `<svg aria-hidden="true"><use href="#i-${name}"/></svg>`;
const fmt = value => Number(value || 0).toLocaleString('zh-CN');
const bytes = value => value >= 1024 ** 3 ? `${(value / 1024 ** 3).toFixed(2)} GB` : value >= 1024 ** 2 ? `${(value / 1024 ** 2).toFixed(1)} MB` : `${Math.round(value / 1024)} KB`;
let state, items = [], total = 0, page = 1, view = 'all', category = '', filenameQuery = '';
let selected = new Set(), semanticResults = null, semanticQuery = '', detailIndex = 0;
let currentJobId = '', lastCompletedJobId = '', toastTimer, loadVersion = 0;
let jobDismissed = false, polling = false;
let folderTarget = null, folderData = null, folderRequestId = 0, folderController = null, folderFilterTimer;
let folderPath = '';
let disconnected = false;
const titles = {all: '全部照片', duplicates: '重复与相似', quality: '质量待检查', kept: '已保留', rejected: '已排除'};

async function api(path, data, signal, renewSession = true) {
  let response;
  try {
    response = await fetch(path, data === undefined ? {signal} : {
      method: 'POST', headers: {'Content-Type': 'application/json', 'X-Session-Token': token},
      body: JSON.stringify(data), signal
    });
  } catch (error) {
    if (error.name === 'AbortError') throw new Error('读取文件夹已超时，请检查网络连接后重试');
    setConnection(false);
    throw new Error('本地服务已断开，请双击“启动拾光.cmd”重新启动。连接恢复后可继续操作。');
  }
  let result;
  try { result = await response.json(); }
  catch (_) { throw new Error(`本地服务返回异常响应（HTTP ${response.status}），请刷新页面后重试`); }
  if (response.status === 403 && data !== undefined && renewSession) {
    // Rejected writes were not executed. Renew the page token once after a server restart.
    const home = await fetch('/', {cache: 'no-store', signal});
    if (home.ok) {
      const document = new DOMParser().parseFromString(await home.text(), 'text/html');
      const nextToken = document.querySelector('meta[name="session-token"]')?.content;
      if (nextToken && nextToken !== token) {
        token = nextToken;
        return api(path, data, signal, false);
      }
    }
  }
  if (!response.ok) throw new Error(result.error || '操作未完成');
  return result;
}

function setConnection(connected) {
  disconnected = !connected;
  hide('connection-banner', connected);
  if (!connected) $('#model-status').textContent = '本地服务已断开';
}

function toast(message, error = false) {
  const element = $('#toast');
  element.textContent = message;
  element.className = `toast${error ? ' error' : ''}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => element.classList.add('hidden'), error ? 6500 : 3300);
}

function handleError(error) { toast(error.message || String(error), true); }
function openDialog(id) { const dialog = document.getElementById(id); if (!dialog.open) dialog.showModal(); }
function hide(id, shouldHide) { document.getElementById(id).classList.toggle('hidden', shouldHide); }

function renderState() {
  if (!state) return;
  $$('[data-count]').forEach(element => element.textContent = fmt(state.stats[element.dataset.count]));
  $('#library-size').textContent = state.stats.total ? `${bytes(state.stats.bytes)} · ${state.roots.length} 个素材目录` : '尚未导入素材';
  $('#duplicate-summary').textContent = state.stats.total ? `${fmt(state.stats.duplicate_groups)} 组需要比较` : '等待检测';
  const model = state.model;
  $('#model-status').textContent = model.loaded ? `SigLIP 2 · ${model.active_device === 'cuda' ? 'CUDA' : 'CPU'}` : model.ready ? 'SigLIP 2 已配置' : model.configured ? 'AI 模型未就绪' : 'AI 模型待配置';
  $('#category-nav').innerHTML = state.categories.length ? state.categories.map(item => `<button class="category-item${category === item.category ? ' active' : ''}" data-category="${esc(item.category)}">${esc(item.category)}<span>${fmt(item.count)}</span></button>`).join('') : '<span class="sidebar-muted">运行 AI 分类后显示</span>';
  $('#classify').disabled = state.job.running || !state.stats.total;
  $('#regroup').disabled = state.job.running;
  $('#recommend').disabled = state.job.running;
  $('#cancel-job').disabled = false;
  renderJob(state.job);
}

function renderJob(job) {
  if (job.id !== currentJobId) { jobDismissed = false; currentJobId = job.id; }
  hide('job-panel', jobDismissed || (!job.running && !job.type));
  $('#job-message').textContent = job.message;
  $('#job-counter').textContent = job.total ? `${fmt(job.done)} / ${fmt(job.total)}` : '';
  $('#job-progress').style.width = job.total ? `${Math.min(100, job.done / job.total * 100)}%` : job.running ? '8%' : '100%';
  hide('job-spinner', !job.running);
  hide('cancel-job', !job.running);
  hide('dismiss-job', job.running);
  hide('job-errors', !job.errors.length);
  $('#job-error-text').textContent = job.errors.join('\n');
  let result = '';
  if (job.result?.destination) result = `已复制 ${fmt(job.result.copied)} 张 → ${job.result.destination}`;
  if (job.result?.found !== undefined) result = `发现 ${fmt(job.result.found)} 张 · 新增或更新 ${fmt(job.result.analyzed)} 张 · 未变化 ${fmt(job.result.unchanged)} 张`;
  $('#job-result').textContent = result;
  if (!job.running && job.type && job.id !== lastCompletedJobId) {
    lastCompletedJobId = job.id;
    if (job.type === 'search' && job.result?.items) {
      semanticResults = job.result.items;
      semanticQuery = job.result.query;
      page = 1;
      selected.clear();
    }
    loadAssets().catch(handleError);
    if (!job.errors.length && !job.message.includes('取消')) toast(job.type === 'search' ? `找到 ${job.result?.items?.length || 0} 个匹配结果` : job.message);
  }
}

async function refreshState() {
  const wasDisconnected = disconnected;
  state = await api('/api/state');
  setConnection(true);
  renderState();
  if (wasDisconnected) { await loadAssets(); toast('本地服务已重新连接'); }
}

function setView(next, nextCategory = '') {
  view = next;
  category = nextCategory;
  semanticResults = null;
  semanticQuery = '';
  filenameQuery = '';
  $('#search-input').value = '';
  page = 1;
  selected.clear();
  $$('.nav-item[data-view]').forEach(element => element.classList.toggle('active', !category && element.dataset.view === view));
  $('#library-title').textContent = category || titles[view];
  $('#view-title').innerHTML = view === 'all' && !category ? '每一帧，都有归处<span class="heading-dot">.</span>' : `${esc(category || titles[view])}<span class="heading-dot">.</span>`;
  const descriptions = {all: '把整理交给工具，把时间留给拍摄。', duplicates: '比较相似画面，为每组照片留下更好的选择。', quality: '用清晰度、曝光和分辨率，辅助你的判断。', kept: '值得留下的画面，已经为你整理在这里。', rejected: '暂时放下的照片，随时可以恢复待整理。'};
  $('#view-description').textContent = category ? '由本地 SigLIP 2 根据图像内容归类。' : descriptions[view];
  hide('regroup', view !== 'duplicates');
  hide('recommend', view !== 'duplicates');
  const banner = $('#context-banner');
  if (view === 'duplicates') banner.innerHTML = `${icon('copy')}<span>完全重复按文件 SHA-256 判断；相似组按感知哈希关联，可能包含连拍或不同照片。建议排除项按质量分数挑选，请逐组确认。</span>`;
  if (view === 'quality') banner.innerHTML = `${icon('quality')}<span>质量筛选是启发式检查，浅景深、夜景和高调照片也可能被标记。质量低于 ${esc(state?.config.quality_threshold ?? 65)} 分或带有风险提示的照片会显示在这里，需人工确认。</span>`;
  if (view === 'rejected') banner.innerHTML = `${icon('folder')}<span>排除标记仅保存在素材库中，原始照片仍在原位置。选择照片后可恢复待整理。</span>`;
  hide('context-banner', !['duplicates', 'quality', 'rejected'].includes(view));
  renderState();
  loadAssets().catch(handleError);
}

async function loadAssets() {
  const version = ++loadVersion;
  if (semanticResults !== null) {
    items = semanticResults;
    total = items.length;
  } else {
    const params = new URLSearchParams({view, category, q: filenameQuery, page, limit: 80, sort: $('#sort').value});
    const result = await api(`/api/assets?${params}`);
    if (version !== loadVersion) return;
    items = result.items;
    total = result.total;
  }
  renderGallery();
}

function photoCard(item) {
  const review = item.review === 'kept' ? `<span class="photo-review">${icon('check')}已保留</span>` : item.review === 'rejected' ? `<span class="photo-review rejected">${icon('x')}已排除</span>` : '';
  let tags = item.category ? `<span class="tag">${esc(item.category)}</span>` : '<span class="tag muted">待分类</span>';
  tags += item.flags.slice(0, 2).map(flag => `<span class="tag warning">${esc(flag)}</span>`).join('');
  return `<article class="photo-card${selected.has(item.id) ? ' selected' : ''}" data-id="${item.id}"><div class="photo-image" tabindex="0" role="button" aria-label="预览 ${esc(item.name)}"><img src="${item.thumbnail}" alt="${esc(item.name)}" loading="lazy" decoding="async"><input class="photo-select" type="checkbox" aria-label="选择 ${esc(item.name)}" ${selected.has(item.id) ? 'checked' : ''}>${review}<span class="photo-quality">${item.search_similarity !== undefined ? `相似度 ${item.search_similarity.toFixed(3)}` : `质量 ${item.quality}`}</span></div><div class="photo-info"><div class="photo-title" title="${esc(item.path)}">${esc(item.name)}</div><div class="photo-metadata"><span>${item.width} × ${item.height}</span><span>${bytes(item.size)}</span></div><div class="photo-bottom">${tags}</div></div></article>`;
}

function renderGallery() {
  $('#result-count').textContent = `${fmt(total)} 张`;
  const emptyLibrary = state && !state.stats.total;
  hide('empty', !emptyLibrary);
  hide('gallery', emptyLibrary);
  hide('search-label', semanticResults === null && !filenameQuery);
  if (semanticResults !== null) $('#search-label').innerHTML = `${icon('spark')} <span>“${esc(semanticQuery)}” 的语义检索 · 按余弦相似度排序，最多 200 项 · 分数不代表正确率</span><button class="text-button" id="clear-search">清除</button>`;
  else if (filenameQuery) $('#search-label').innerHTML = `<span>文件名 / 路径包含 “${esc(filenameQuery)}”</span><button class="text-button" id="clear-search">清除</button>`;
  if (!items.length && !emptyLibrary) {
    $('#gallery').innerHTML = `<div class="no-results">${icon('image')}<h3>这里暂时没有照片</h3><p>${view === 'kept' ? '选择喜欢的照片，标记为保留，它们就会出现在这里。' : view === 'duplicates' ? '当前素材中没有检测到重复或相似组。' : view === 'quality' ? '当前素材没有触发质量筛选条件。' : '试试其他关键词，或导入更多拍摄素材。'}</p></div>`;
  } else {
    let group = '', groupNumber = (page - 1) * 80;
    $('#gallery').innerHTML = items.map(item => {
      let header = '';
      if (view === 'duplicates' && semanticResults === null && item.duplicate_group !== group) {
        group = item.duplicate_group;
        groupNumber++;
        const count = items.filter(row => row.duplicate_group === group).length;
        header = `<div class="group-header">${icon('copy')}<strong>照片组 ${esc(group.slice(0, 6))}</strong><span class="group-badge">${item.duplicate_kind === 'exact' ? '完全重复' : '画面相似'}</span><span>本页 ${count} 张 · 组内按质量排序，跨页组可能续接</span></div>`;
      }
      return header + photoCard(item);
    }).join('');
  }
  hide('pagination', semanticResults !== null || total <= 80);
  $('#page-label').textContent = `${page} / ${Math.max(1, Math.ceil(total / 80))}`;
  $('#prev-page').disabled = page <= 1;
  $('#next-page').disabled = page * 80 >= total;
  renderSelection();
}

function renderSelection() {
  hide('selection-bar', !selected.size);
  $('#selected-count').textContent = `已选择 ${fmt(selected.size)} 张`;
  const checked = items.filter(item => selected.has(item.id)).length;
  $('#select-all').checked = !!items.length && checked === items.length;
  $('#select-all').indeterminate = checked > 0 && checked < items.length;
  $('#select-all').disabled = !items.length;
}

async function markSelected(status) {
  if (!selected.size) return;
  const ids = [...selected];
  await api('/api/review', {ids, status});
  if (semanticResults) semanticResults.forEach(item => { if (selected.has(item.id)) item.review = status; });
  selected.clear();
  await refreshState();
  await loadAssets();
  toast(`已${status === 'kept' ? '保留' : status === 'rejected' ? '排除' : '恢复'} ${ids.length} 张照片`);
}

function showDetail(index) {
  if (!items.length) return;
  detailIndex = (index + items.length) % items.length;
  const item = items[detailIndex];
  $('#detail-name').textContent = item.name;
  $('#detail-path').textContent = item.path;
  $('#detail-image').src = `/preview/${item.id}`;
  $('#detail-image').alt = item.name;
  const rows = [['状态', {kept:'已保留',rejected:'已排除',unreviewed:'待整理'}[item.review]], ['尺寸', `${item.width} × ${item.height}`], ['文件大小', bytes(item.size)], ['图像分类', item.category || '尚未分类'], ['质量评分', `${item.quality} / 100`], ['清晰度指标', item.sharpness.toFixed(1)], ['平均亮度', `${item.brightness.toFixed(1)} / 255`], ['暗部比例', `${(item.dark_ratio * 100).toFixed(1)}%`], ['高光比例', `${(item.bright_ratio * 100).toFixed(1)}%`]];
  if (item.similarity !== null) rows.push(['分类余弦相似度', `${item.similarity.toFixed(3)}`]);
  if (item.search_similarity !== undefined) rows.push(['检索余弦相似度', `${item.search_similarity.toFixed(3)}`]);
  $('#detail-metrics').innerHTML = rows.map(([label, value]) => `<div class="detail-row"><span>${esc(label)}</span><span>${esc(value)}</span></div>`).join('') + `<div class="detail-flags">${item.flags.length ? item.flags.map(flag => `<span class="tag warning">${esc(flag)}</span>`).join('') : '<span class="tag">未发现明显质量风险</span>'}</div>`;
  openDialog('detail-dialog');
}

async function markDetail(status) {
  const item = items[detailIndex];
  if (!item) return;
  await api('/api/review', {ids: [item.id], status});
  item.review = status;
  showDetail(detailIndex);
  renderGallery();
  await refreshState();
}

function categoryRow(name = '', prompt = '') {
  return `<div class="category-row"><input class="category-name" aria-label="分类名称" placeholder="分类名称" value="${esc(name)}" required maxlength="100"><input class="category-prompt" aria-label="图像描述" placeholder="描述这类照片的画面内容" value="${esc(prompt)}" required maxlength="500"><button type="button" class="icon-button remove-category" aria-label="移除分类">${icon('x')}</button></div>`;
}

function openSettings() {
  if (!state) return;
  const config = state.config;
  $('#model-path').value = config.model_path;
  $('#device').value = config.device;
  $('#quality-threshold').value = config.quality_threshold;
  $('#duplicate-threshold').value = config.duplicate_threshold;
  $('#category-editor').innerHTML = config.categories.map(item => categoryRow(item.name, item.prompt)).join('');
  $('#dependency-status').textContent = `PyTorch ${state.model.dependencies.torch ? '✓' : '未安装'} · Transformers ${state.model.dependencies.transformers ? '✓' : '未安装'} · RAW ${state.model.dependencies.rawpy ? '✓' : '未安装'}`;
  $('#model-config-status').textContent = state.model.ready ? '模型文件已就绪，可运行 AI 分类。' : state.model.problem || '请先准备完整的本地模型文件。';
  openDialog('settings-dialog');
}

function openExport() {
  if (!state?.stats.total) return toast('请先导入照片');
  if (!selected.size && !state.stats.kept) return toast('请先选择照片，或标记需要保留的照片');
  $('#export-description').textContent = selected.size ? `将复制当前选中的 ${selected.size} 张照片。` : `将复制已标记为保留的 ${state.stats.kept} 张照片。`;
  openDialog('export-dialog');
}

async function browseFolder(path = '', offset = 0, retainFilter = false) {
  const requestId = ++folderRequestId;
  folderController?.abort();
  const controller = new AbortController();
  folderController = controller;
  const timeout = setTimeout(() => controller.abort(), 30000);
  folderPath = path;
  folderData = null;
  if (!retainFilter) $('#folder-filter').value = '';
  $('#folder-address').value = path;
  $('#folder-select').disabled = true;
  $('#folder-up').disabled = true;
  $('#folder-status').textContent = '正在读取文件夹…';
  $('#folder-current').textContent = path || '选择一个位置，或输入 NAS 路径';
  $('#folder-list').innerHTML = '';
  hide('folder-error', true);
  hide('folder-pagination', true);
  try {
    const data = await api('/api/folders', {path, offset, query: $('#folder-filter').value}, controller.signal);
    if (requestId !== folderRequestId || !$('#folder-dialog').open) return;
    folderData = data;
    folderPath = data.path;
    $('#folder-address').value = data.path;
    $('#folder-current').textContent = data.path || '请选择一个位置，或输入 NAS 路径';
    $('#folder-select').disabled = !data.selectable;
    $('#folder-up').disabled = data.parent === null;
    $('#folder-status').textContent = data.path ? `${data.total} 个${data.selectable ? '子文件夹' : '共享位置'} · 点击文件夹进入` : '从左侧选择磁盘或最近位置，也可以在上方输入网络地址。';
    $('#folder-locations').innerHTML = data.locations.map(entry => `<button type="button" class="folder-location${entry.path === data.path ? ' active' : ''}" data-folder-path="${esc(entry.path)}" title="${esc(entry.path)}">${icon(entry.kind === 'network' || entry.path.startsWith('\\\\') ? 'monitor' : entry.kind === 'recent' ? 'clock' : 'folder')}<span>${esc(entry.name)}</span></button>`).join('');
    $('#folder-list').innerHTML = data.folders.length ? data.folders.map(entry => `<button type="button" class="folder-entry" data-folder-path="${esc(entry.path)}">${icon('folder')}<span>${esc(entry.name)}</span>${icon('chevron')}</button>`).join('') : `<div class="folder-empty">${data.path ? ($('#folder-filter').value ? '没有匹配的子文件夹' : data.selectable ? '没有子文件夹，可以直接选择此文件夹。' : '未列出共享；请在地址栏输入完整共享路径。') : '本地磁盘与 NAS，统一浏览。'}</div>`;
    hide('folder-pagination', data.total <= data.limit);
    $('#folder-page-label').textContent = `${Math.floor(data.offset / data.limit) + 1} / ${Math.max(1, Math.ceil(data.total / data.limit))}`;
    $('#folder-prev').disabled = !data.offset;
    $('#folder-next').disabled = data.offset + data.limit >= data.total;
  } catch (error) {
    if (requestId !== folderRequestId || !$('#folder-dialog').open) return;
    $('#folder-status').textContent = '未能打开此位置，可修改地址或选择其他位置。';
    $('#folder-error').textContent = error.message;
    hide('folder-error', false);
  } finally { clearTimeout(timeout); }
}

$('#folder-address-form').onsubmit = event => { event.preventDefault(); browseFolder($('#folder-address').value.trim()); };
$('#folder-home').onclick = () => browseFolder('');
$('#folder-up').onclick = () => { if (folderData?.parent !== null && folderData) browseFolder(folderData.parent); };
$('#folder-refresh').onclick = () => browseFolder(folderPath, 0, true);
$('#folder-filter').oninput = () => { clearTimeout(folderFilterTimer); folderFilterTimer = setTimeout(() => browseFolder(folderPath, 0, true), 250); };
$('#folder-prev').onclick = () => { if (folderData) browseFolder(folderData.path, Math.max(0, folderData.offset - folderData.limit), true); };
$('#folder-next').onclick = () => { if (folderData) browseFolder(folderData.path, folderData.offset + folderData.limit, true); };
$('#folder-dialog').addEventListener('click', event => {
  const location = event.target.closest('[data-folder-path]');
  if (location) browseFolder(location.dataset.folderPath);
});
$('#folder-dialog').addEventListener('close', () => {
  folderRequestId++;
  folderController?.abort();
  clearTimeout(folderFilterTimer);
});
$('#folder-select').onclick = async () => {
  if (!folderData?.selectable || !folderTarget) return;
  const target = folderTarget, path = folderData.path, requestId = ++folderRequestId;
  folderController?.abort();
  const controller = new AbortController();
  folderController = controller;
  const timeout = setTimeout(() => controller.abort(), 30000);
  $('#folder-select').disabled = true;
  $('#folder-status').textContent = '正在确认文件夹可访问…';
  try {
    const result = await api('/api/folders/select', {path}, controller.signal);
    if (requestId !== folderRequestId || !$('#folder-dialog').open) return;
    target.value = result.folder;
    $('#folder-dialog').close();
    target.focus();
  } catch (error) {
    if (requestId === folderRequestId && $('#folder-dialog').open) {
      $('#folder-error').textContent = error.message;
      hide('folder-error', false);
      $('#folder-select').disabled = false;
    }
  } finally { clearTimeout(timeout); }
};

document.addEventListener('click', event => {
  const action = event.target.closest('[data-action]')?.dataset.action;
  if (action === 'import') {
    if (state?.job.running) return toast('请等待当前任务完成，或先取消任务');
    openDialog('import-dialog');
    setTimeout(() => $('#import-path').focus(), 100);
  }
  if (action === 'settings') openSettings();
  if (action === 'export') openExport();
  const close = event.target.closest('[data-close]')?.dataset.close;
  if (close) document.getElementById(close).close();
  const targetView = event.target.closest('[data-view]')?.dataset.view;
  if (targetView) setView(targetView);
  const targetCategory = event.target.closest('[data-category]')?.dataset.category;
  if (targetCategory) setView('all', targetCategory);
  const browse = event.target.closest('[data-browse]');
  if (browse) {
    folderTarget = document.getElementById(browse.dataset.browse);
    folderData = null;
    $('#folder-filter').value = '';
    openDialog('folder-dialog');
    browseFolder(folderTarget.value.trim());
  }
  const remove = event.target.closest('.remove-category');
  if (remove) remove.closest('.category-row').remove();
  if (event.target.closest('#clear-search')) setView(view, category);
});

$('#gallery').addEventListener('click', event => {
  const card = event.target.closest('.photo-card');
  if (!card) return;
  if (event.target.matches('.photo-select')) {
    event.target.checked ? selected.add(card.dataset.id) : selected.delete(card.dataset.id);
    card.classList.toggle('selected', selected.has(card.dataset.id));
    renderSelection();
  } else if (event.target.closest('.photo-image')) showDetail(items.findIndex(item => item.id === card.dataset.id));
});
$('#gallery').addEventListener('keydown', event => {
  if (event.key === 'Enter' && event.target.matches('.photo-image')) {
    event.preventDefault();
    showDetail(items.findIndex(item => item.id === event.target.closest('.photo-card').dataset.id));
  }
});
$('#select-all').addEventListener('change', event => {
  items.forEach(item => event.target.checked ? selected.add(item.id) : selected.delete(item.id));
  renderGallery();
});
$('#clear-selection').onclick = () => { selected.clear(); renderGallery(); };
$('#keep-selected').onclick = () => markSelected('kept').catch(handleError);
$('#reject-selected').onclick = () => markSelected('rejected').catch(handleError);
$('#reset-selected').onclick = () => markSelected('unreviewed').catch(handleError);
$('#sort').onchange = () => { page = 1; loadAssets().catch(handleError); };
$('#prev-page').onclick = () => { if (page > 1) { page--; loadAssets().catch(handleError); } };
$('#next-page').onclick = () => { if (page * 80 < total) { page++; loadAssets().catch(handleError); } };
$('#detail-close').onclick = () => $('#detail-dialog').close();
$('#detail-dialog').addEventListener('close', () => loadAssets().catch(handleError));
$('#detail-prev').onclick = () => showDetail(detailIndex - 1);
$('#detail-next').onclick = () => showDetail(detailIndex + 1);
$('#detail-keep').onclick = () => markDetail('kept').catch(handleError);
$('#detail-reject').onclick = () => markDetail('rejected').catch(handleError);
$('#detail-reset').onclick = () => markDetail('unreviewed').catch(handleError);
$('#add-category').onclick = () => $('#category-editor').insertAdjacentHTML('beforeend', categoryRow());
$('#dismiss-job').onclick = () => { jobDismissed = true; hide('job-panel', true); };
$('#cancel-job').onclick = async () => {
  try { await api('/api/cancel', {}); $('#cancel-job').disabled = true; toast('已请求取消，当前文件或模型载入完成后停止'); } catch (error) { handleError(error); }
};

$('#search-form').onsubmit = async event => {
  event.preventDefault();
  const query = $('#search-input').value.trim();
  if (!query) { setView(view, category); return; }
  try {
    if ($('#search-mode').value === 'semantic') {
      if (!state.model.ready) { openSettings(); return toast(state.model.problem || '请先配置本地 SigLIP 2 模型'); }
      semanticResults = null;
      selected.clear();
      jobDismissed = false;
      state.job = await api('/api/search', {query});
      renderState();
    } else {
      semanticResults = null;
      filenameQuery = query;
      page = 1;
      selected.clear();
      await loadAssets();
    }
  } catch (error) { handleError(error); }
};

$('#classify').onclick = async () => {
  try {
    if (!state.model.ready) { openSettings(); return toast(state.model.problem || '请先指定本地 SigLIP 2 模型文件夹'); }
    semanticResults = null;
    jobDismissed = false;
    state.job = await api('/api/classify', {});
    renderState();
  } catch (error) { handleError(error); }
};
$('#regroup').onclick = async () => {
  try { jobDismissed = false; state.job = await api('/api/regroup', {}); renderState(); } catch (error) { handleError(error); }
};
$('#recommend').onclick = async () => {
  try {
    const result = await api('/api/recommend');
    selected = new Set(result.ids);
    renderGallery();
    toast(result.ids.length ? `已从全库选择 ${result.ids.length} 张建议排除项（包含其他分页），请确认后标记` : '没有可建议排除的待整理照片');
  } catch (error) { handleError(error); }
};

$('#import-form').onsubmit = async event => {
  event.preventDefault();
  const button = event.submitter;
  button.disabled = true;
  try {
    jobDismissed = false;
    state.job = await api('/api/scan', {folder: $('#import-path').value, recursive: $('#recursive').checked});
    $('#import-dialog').close();
    semanticResults = null;
    renderState();
  } catch (error) { handleError(error); } finally { button.disabled = false; }
};
$('#export-form').onsubmit = async event => {
  event.preventDefault();
  const button = event.submitter;
  button.disabled = true;
  try {
    jobDismissed = false;
    state.job = await api('/api/export', {destination: $('#export-path').value, ids: [...selected], by_category: $('#export-categories').checked});
    $('#export-dialog').close();
    renderState();
  } catch (error) { handleError(error); } finally { button.disabled = false; }
};
$('#settings-form').onsubmit = async event => {
  event.preventDefault();
  const button = event.submitter;
  button.disabled = true;
  try {
    await api('/api/config', {model_path: $('#model-path').value, device: $('#device').value, quality_threshold: Number($('#quality-threshold').value), duplicate_threshold: Number($('#duplicate-threshold').value), categories: $$('.category-row').map(row => ({name: row.querySelector('.category-name').value, prompt: row.querySelector('.category-prompt').value}))});
    $('#settings-dialog').close();
    semanticResults = null;
    await refreshState();
    setView('all');
    toast('设置已保存；修改分类后请重新运行 AI 分类');
  } catch (error) { handleError(error); } finally { button.disabled = false; }
};

document.addEventListener('keydown', event => {
  if (['INPUT', 'SELECT', 'TEXTAREA'].includes(document.activeElement?.tagName)) return;
  if ($('#detail-dialog').open) {
    if (event.key === 'ArrowLeft') { event.preventDefault(); showDetail(detailIndex - 1); }
    if (event.key === 'ArrowRight') { event.preventDefault(); showDetail(detailIndex + 1); }
    if (event.key.toLowerCase() === 'p') markDetail('kept').catch(handleError);
    if (event.key.toLowerCase() === 'x') markDetail('rejected').catch(handleError);
  } else if (event.key === '/' && !$$('dialog[open]').length) {
    event.preventDefault(); $('#search-input').focus();
  }
});

async function init() {
  try {
    await refreshState();
    await loadAssets();
  } catch (error) { handleError(error); }
  setInterval(async () => {
    if (polling) return;
    polling = true;
    try { await refreshState(); }
    catch (error) { setConnection(false); }
    finally { polling = false; }
  }, 1600);
}
$('#retry-connection').onclick = () => refreshState().catch(handleError);
init();
