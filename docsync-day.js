const token = document.querySelector('meta[name="docsync-token"]').content;
const byId = id => document.getElementById(id);
const picker = byId('picker');
const selection = {};
const sent = new Map();
const activityLabels = {done:'EIM đã nhận', checking:'Đang kiểm tra file', running:'Đang gửi EIM', retry:'Chờ thử lại', review:'Cần kiểm tra', paused:'Đã tạm dừng'};
let currentPage = 1, configured = false, listing = 0;

function element(tag, text, className) {
  const result = document.createElement(tag);
  if (text != null) result.textContent = text;
  if (className) result.className = className;
  return result;
}
function message(id, text, error = false) {
  const target = byId(id);
  target.textContent = text;
  target.classList.toggle('error', error);
}
async function api(url, data) {
  const response = await fetch(url, data === undefined ? {cache:'no-store'} : {
    method:'POST', cache:'no-store',
    headers:{'Content-Type':'application/json','X-CSRF-Token':token}, body:JSON.stringify(data)
  });
  const value = await response.json();
  if (!response.ok) throw new Error(value.message || 'Không đọc được phản hồi từ DocSync.');
  return value;
}
function accountStatus(value) {
  configured = value;
  byId('account-summary').textContent = value ? 'Tài khoản gửi ERP · Đã lưu trên máy' : 'Thiết lập tài khoản gửi ERP';
}
function renderRows(files) {
  const body = byId('files');
  body.replaceChildren();
  if (!files.length) {
    const row = element('tr', '', 'empty-row');
    const cell = element('td', 'Chưa có file trong ngày này.', 'empty');
    cell.colSpan = 5;
    row.append(cell);
    body.append(row);
  }
  for (const item of files) {
    const row = element('tr');
    const file = element('td');
    if (!item.automatic) {
      const checkbox = element('input');
      checkbox.type = 'checkbox';
      checkbox.setAttribute('aria-label', 'Chọn ' + item.name);
      checkbox.setAttribute('data-file-select', '');
      checkbox.dataset.file = JSON.stringify({path: item.path, name: item.name, token: item.token});
      file.append(checkbox, ' ');
    }
    file.append(element('strong', item.name, 'file-name'));
    if (item.path !== item.name) file.append(element('p', item.path, 'file-detail'));
    const category = element('td');
    category.append(element('span', item.category, 'chip'));
    const time = new Date(item.time).toLocaleTimeString('vi-VN', {hour:'2-digit', minute:'2-digit'});
    const size = item.size < 1024 ? item.size + ' B'
      : new Intl.NumberFormat('vi-VN', {maximumFractionDigits:1}).format(item.size / 1024) + ' KB';
    const action = element('td');
    if (item.automatic) {
      const link = element('a', item.activity ? activityLabels[item.activity.state] || 'Xem kết quả EIM' : 'Theo luồng EIM tự động');
      link.href = '/workflows';
      action.append(link);
    } else {
      const button = element('button', 'Gắn vào ERP', 'secondary');
      button.type = 'button';
      button.setAttribute('aria-label', 'Gắn vào ERP ' + item.name);
      button.onclick = () => openPicker(item, button);
      action.append(button);
    }
    if (item.activity) action.append(element('p', item.activity.outcome, 'file-detail'));
    if (sent.has(item.token)) action.append(element('span', sent.get(item.token), 'sent'));
    row.append(file, category, element('td', time), element('td', size, 'size'), action);
    body.append(row);
  }
}
async function list(page = 1) {
  const revision = ++listing;
  message('list-message', 'Đang đọc file…');
  try {
    const query = new URLSearchParams({date:byId('date').value, page});
    const data = await api('/api/day?' + query);
    if (revision !== listing) return;
    currentPage = data.page;
    byId('date').value = data.day;
    byId('total').textContent = data.total.toLocaleString('vi-VN') + ' file';
    byId('pagination').hidden = data.pages <= 1;
    byId('page').textContent = data.page + ' / ' + data.pages;
    byId('previous').disabled = data.page <= 1;
    byId('next').disabled = data.page >= data.pages;
    accountStatus(data.configured);
    renderRows(data.files);
    message('list-message', '');
  } catch (error) {
    if (revision === listing) message('list-message', error.message, true);
  }
}
function options(id, placeholder, rows = []) {
  const select = byId(id);
  select.replaceChildren(new Option(placeholder, ''));
  for (const [key, label] of rows) select.add(new Option(label, key));
  select.disabled = !rows.length;
}
function resetTarget() {
  options('field', 'Chọn trường nhận file');
  byId('attach').disabled = true;
  byId('ai').checked = false;
  byId('ai').disabled = true;
  byId('ai-help').textContent = '';
  byId('record-link').hidden = true;
}
async function openPicker(item, opener) {
  if (!configured) {
    byId('account').open = true;
    byId('user').focus();
    message('account-message', 'Lưu tài khoản để chọn bản ghi ERP.');
    return;
  }
  selection.file = item;
  selection.opener = opener;
  byId('destination').reset();
  byId('file-selected').textContent = item.path;
  options('type', 'Chọn loại bản ghi');
  options('record', 'Chọn bản ghi');
  resetTarget();
  byId('search').disabled = true;
  message('picker-message', 'Đang kiểm tra quyền ERP…');
  picker.showModal();
  try {
    const data = await api('/api/day/types', {});
    if (selection.file !== item) return;
    selection.aiAllowed = data.ai_allowed;
    if (item.size > data.max_bytes || !item.size) {
      message('picker-message', 'File rỗng hoặc vượt giới hạn của tài khoản ERP.', true);
      return;
    }
    options('type', 'Chọn loại bản ghi', data.types.map(row => [row.type, row.label]));
    message('picker-message', '');
    byId('type').focus();
  } catch (error) {
    if (selection.file === item) message('picker-message', error.message, true);
  }
}
async function search() {
  const type = byId('type').value, query = byId('query').value.trim(), file = selection.file;
  if (!type || !query || selection.uploading) return;
  const revision = selection.search = (selection.search || 0) + 1;
  byId('search').disabled = true;
  options('record', 'Chọn bản ghi');
  resetTarget();
  message('picker-message', 'Đang tìm bản ghi…');
  try {
    const data = await api('/api/day/targets', {type, query});
    if (selection.file !== file || selection.search !== revision || byId('type').value !== type) return;
    options('record', 'Chọn bản ghi', data.records.map(row => [row.id, `${row.label} · #${row.id}`]));
    message('picker-message', data.records.length ? 'Chọn bản ghi cần gắn file.' : 'Không tìm thấy bản ghi được phép sửa.');
    if (data.records.length) byId('record').focus();
  } catch (error) {
    if (selection.file === file && selection.search === revision) message('picker-message', error.message, true);
  } finally {
    if (selection.file === file && selection.search === revision) byId('search').disabled = !byId('type').value;
  }
}
async function recordChanged() {
  resetTarget();
  const type = byId('type').value, id = Number(byId('record').value), file = selection.file;
  if (!id) return;
  message('picker-message', 'Đang đọc các trường nhận file…');
  try {
    const data = await api('/api/day/record', {type, id});
    if (selection.file !== file || byId('type').value !== type || Number(byId('record').value) !== id) return;
    options('field', 'Chọn trường nhận file', data.fields.map(row => [row.key, `${row.label} (${row.count}/${row.max})`]));
    selection.aiAvailable = selection.aiAllowed && data.ai_available;
    byId('ai-help').textContent = selection.aiAvailable
      ? 'Khi chọn AI, ERP gửi nội dung cho nhà cung cấp AI đã cấu hình.'
      : 'Tài khoản hoặc bản ghi hiện chưa có quyền phân tích AI.';
    message('picker-message', data.fields.length ? '' : 'Bản ghi chưa có trường nhận file.');
    if (data.fields.length) byId('field').focus();
  } catch (error) {
    if (selection.file === file && byId('type').value === type && Number(byId('record').value) === id) {
      message('picker-message', error.message, true);
    }
  }
}
byId('destination').onsubmit = async event => {
  event.preventDefault();
  const item = selection.file, type = byId('type').value, id = Number(byId('record').value), field = byId('field').value;
  if (!item || !type || !id || !field || selection.uploading || byId('attach').disabled) return;
  const ai = byId('ai').checked;
  const controls = [...event.currentTarget.querySelectorAll('button, input, select')];
  const previouslyDisabled = controls.filter(control => control.disabled);
  selection.uploading = true;
  controls.forEach(control => control.disabled = true);
  message('picker-message', 'Đang gửi file lên ERP…');
  let success = false;
  try {
    const data = await api('/api/day/attach', {path:item.path, token:item.token, type, id, field, ai});
    const result = data.result;
    const label = data.duplicate ? 'File đã có trong trường này' : 'Đã gắn file';
    sent.set(item.token, label + ' · #' + id);
    const aiResult = result.ai?.queued ? ' · Đã xếp hàng tạo Ghi chú AI' : ai ? ' · ERP chưa xếp hàng AI' : '';
    message('picker-message', `${label}: ${result.record?.label || '#'+id} → ${result.field?.label || field}${aiResult}.`);
    if (typeof result.record?.url === 'string' && result.record.url.startsWith('https://ivarvietnam.com/')) {
      byId('record-link').href = result.record.url;
      byId('record-link').hidden = false;
    }
    success = true;
    list(currentPage);
  } catch (error) {
    message('picker-message', error.message, true);
  } finally {
    selection.uploading = false;
    controls.forEach(control => control.disabled = previouslyDisabled.includes(control));
    byId('attach').disabled = success;
    if (success) byId('close-picker').focus();
  }
};
byId('credential').onsubmit = async event => {
  event.preventDefault();
  const button = event.submitter;
  button.disabled = true;
  const password = byId('password').value;
  byId('password').value = '';
  message('account-message', 'Đang kiểm tra quyền gắn file…');
  try {
    const data = await api('/api/day/credential', {user:byId('user').value,password});
    accountStatus(data.configured);
    message('account-message', 'Đã lưu tài khoản trên máy.');
  } catch (error) {
    message('account-message', error.message, true);
  } finally {
    button.disabled = false;
  }
};
byId('type').onchange = () => {
  selection.search = (selection.search || 0) + 1;
  byId('search').disabled = !byId('type').value;
  options('record', 'Chọn bản ghi');
  resetTarget();
  message('picker-message', byId('type').value ? 'Nhập mã hoặc tên rồi bấm Tìm.' : '');
};
byId('query').onkeydown = event => {if (event.key === 'Enter') {event.preventDefault(); if (!byId('search').disabled) search();}};
byId('search').onclick = search;
byId('record').onchange = recordChanged;
byId('field').onchange = () => {
  const selected = Boolean(byId('field').value);
  byId('attach').disabled = !selected;
  byId('ai').disabled = !selected || !selection.aiAvailable;
  byId('record-link').hidden = true;
};
byId('cancel-account').onclick = () => byId('account').open = false;
byId('close-picker').onclick = () => picker.close();
picker.addEventListener('cancel', event => {if (selection.uploading) event.preventDefault();});
picker.addEventListener('close', () => {
  const {opener, file} = selection;
  selection.file = null;
  if (opener?.isConnected) opener.focus();
  else if (file) [...byId('files').querySelectorAll('button')].find(button => button.getAttribute('aria-label') === 'Gắn vào ERP ' + file.name)?.focus();
});
byId('date').onchange = () => list();
byId('refresh').onclick = () => list(currentPage);
byId('previous').onclick = () => list(currentPage - 1);
byId('next').onclick = () => list(currentPage + 1);
setupDocSyncFileActions(byId('files'), token, () => list(currentPage));
list();
