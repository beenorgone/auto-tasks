'use strict';

const form = document.querySelector('#folder-settings');
const controls = form.querySelector('fieldset');
const feedback = document.querySelector('#feedback');
const fields = [...form.querySelectorAll('input')];

function message(text, error = false) {
    feedback.textContent = text;
    feedback.toggleAttribute('data-error', error);
}

function showPaths(settings) {
    for (const field of fields) {
        field.value = settings.paths[field.name];
        field.readOnly = settings.overridden.includes(field.name);
    }
    return settings.overridden.length ? ' Các ô chỉ đọc lấy đường dẫn từ môi trường chạy.' : '';
}

async function loadPaths() {
    message('Đang đọc cấu hình…');
    try {
        const response = await fetch('/api/settings');
        const settings = await response.json();
        if (!response.ok) throw new Error(settings.message);
        message(showPaths(settings).trim());
        controls.disabled = false;
    } catch {
        message('Không đọc được cấu hình. Tải lại trang để thử lại.', true);
    }
}

form.addEventListener('submit', async event => {
    event.preventDefault();
    controls.disabled = true;
    message('Đang lưu…');
    try {
        const response = await fetch('/api/settings', {
            method: 'POST',
            headers: {'Content-Type': 'application/json', 'X-CSRF-Token': form.dataset.token},
            body: JSON.stringify({paths: Object.fromEntries(fields.map(field => [field.name, field.value]))})
        });
        const settings = await response.json();
        if (!response.ok) {
            message(response.status === 403 ? 'Tải lại trang rồi lưu lại.' : settings.message, true);
            return;
        }
        message('Đã lưu thư mục.' + showPaths(settings));
    } catch {
        message('Chưa nhận được xác nhận. Tải lại trang để kiểm tra thư mục đã lưu.', true);
    } finally {
        controls.disabled = false;
    }
});

loadPaths();
