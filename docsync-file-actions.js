'use strict';

function setupDocSyncFileActions(body, token, refresh) {
    const id = name => document.getElementById('bulk-' + name);
    const limit = Number(document.getElementById('file-bulk').dataset.limit);
    const dialog = id('dialog');
    const form = id('form');
    let chosen = [];
    let action = '';
    let busy = false;
    const actions = {
        classify: ['Phân loại', 'Các file đã chọn sẽ được chuyển vào nhóm bạn chọn.'],
        hold: ['Giữ lại', 'DocSync sẽ bỏ qua đúng bản file này trong các lượt tự động.'],
        rename: ['Đổi tên', 'Nhập tên mới cho từng file, gồm cả đuôi file.'],
        trash: ['Xóa file', 'Các file sẽ được chuyển vào thư mục .docsync/Trash trong WIP.']
    };
    const checkboxes = () => [...body.querySelectorAll('[data-file-select]')];
    const selected = () => checkboxes().filter(box => box.checked);

    function syncSelection() {
        const count = selected().length;
        const total = checkboxes().length;
        id('all').disabled = !total;
        id('all').checked = Boolean(total) && count === Math.min(limit, total);
        id('all').indeterminate = count > 0 && !id('all').checked;
        id('count').textContent = `${count} file đã chọn`;
        id('open').disabled = !count || !id('action').value;
    }
    function result(text, error = false) {
        id('result').textContent = text;
        id('result').toggleAttribute('data-error', error);
    }
    body.addEventListener('change', event => {
        if (!event.target.matches('[data-file-select]')) return;
        if (selected().length > limit) {
            event.target.checked = false;
            result(`Chọn tối đa ${limit} file mỗi lượt.`, true);
        }
        syncSelection();
    });
    id('all').addEventListener('change', event => {
        const checked = event.target.checked;
        checkboxes().forEach((box, index) => box.checked = checked && index < limit);
        syncSelection();
    });
    id('action').addEventListener('change', syncSelection);
    new MutationObserver(syncSelection).observe(body, {childList: true});
    syncSelection();

    id('open').addEventListener('click', () => {
        action = id('action').value;
        chosen = selected().map(box => JSON.parse(box.dataset.file));
        const [title, note] = actions[action];
        id('title').textContent = `${title} ${chosen.length} file`;
        id('note').textContent = note;
        id('submit').textContent = action === 'trash' ? 'Đưa vào thùng rác' : 'Xác nhận';
        id('errors').textContent = '';
        id('topic-label').hidden = action !== 'classify';
        id('topic').disabled = action !== 'classify';
        id('topic').required = action === 'classify';
        id('topic').value = '';
        id('list').replaceChildren();
        for (const file of chosen) {
            const item = document.createElement('li');
            if (action === 'rename') {
                const label = document.createElement('label');
                const input = document.createElement('input');
                input.value = file.name;
                input.maxLength = 255;
                input.required = true;
                label.append(file.path, input);
                item.append(label);
            } else {
                item.textContent = file.path;
            }
            id('list').append(item);
        }
        dialog.showModal();
    });
    id('cancel').addEventListener('click', () => dialog.close());
    dialog.addEventListener('cancel', event => { if (busy) event.preventDefault(); });
    form.addEventListener('submit', async event => {
        event.preventDefault();
        if (busy) return;
        const files = chosen.map(({path, token}) => ({path, token}));
        if (action === 'rename') {
            [...id('list').querySelectorAll('input')].forEach((input, index) => files[index].name = input.value);
        }
        busy = id('submit').disabled = id('cancel').disabled = true;
        id('errors').textContent = 'Đang xử lý…';
        try {
            const response = await fetch('/api/files/actions', {
                method: 'POST',
                headers: {'Content-Type': 'application/json', 'X-CSRF-Token': token},
                body: JSON.stringify({action, files, topic: id('topic').value})
            });
            const data = await response.json();
            if (!response.ok) throw new Error(data.message);
            const failures = data.results.filter(item => item.error);
            const success = data.results.length - failures.length;
            const unchanged = data.unchanged ? ` · ${data.unchanged} tên không đổi` : '';
            result(`Đã xử lý ${success} file${unchanged}.` + failures.map(item => ` ${item.path}: ${item.error}`).join(''), Boolean(failures.length));
            dialog.close();
            try {
                await refresh();
            } catch {
                id('result').append(' Bấm Làm mới để cập nhật bảng.');
            }
        } catch (error) {
            id('errors').textContent = error.message || 'Làm mới bảng trước khi thử lại.';
        } finally {
            busy = id('submit').disabled = id('cancel').disabled = false;
        }
    });
}
