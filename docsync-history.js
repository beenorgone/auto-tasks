'use strict';

const form = document.querySelector('#classification-command');
const button = form.querySelector('button');
const input = form.querySelector('#command');
const status = document.querySelector('#run-status');
const details = document.querySelector('#run-details');
const log = document.querySelector('#run-log');
const decision = document.querySelector('#file-decision');
const decisionForm = document.querySelector('#file-decision-form');
const topic = document.querySelector('#decision-topic');
const decisionError = document.querySelector('#decision-error');
const cancelDecision = document.querySelector('#cancel-decision');
const submitDecision = decisionForm.querySelector('[type=submit]');
const fileStatus = document.querySelector('#file-action-status');
let selectedFile;

function showStatus(message, error = false) {
    status.textContent = message;
    status.toggleAttribute('data-error', error);
}

async function refreshHistory() {
    const response = await fetch('/history');
    if (!response.ok) throw new Error();
    const page = new DOMParser().parseFromString(await response.text(), 'text/html');
    const summary = page.querySelector('#history-summary');
    const rows = page.querySelector('#history-rows');
    if (!summary || !rows) throw new Error();
    document.querySelector('#history-summary').textContent = summary.textContent;
    document.querySelector('#history-rows').replaceChildren(...rows.children);
}

async function reportRun(data, follow) {
    log.textContent = data.logs.join('\n');
    details.hidden = !log.textContent;
    if (data.running) {
        showStatus('DocSync đang xử lý…');
        setTimeout(() => refreshStatus(true), 1000);
        return;
    }
    const failed = data.exitCode !== null && data.exitCode !== 0;
    let message = data.exitCode === null ? '' : failed
        ? `Lượt chạy dừng với mã ${data.exitCode}. Xem nhật ký để biết chi tiết.`
        : data.mode === 'preview' ? 'Đã xem trước. Xem đề xuất trong nhật ký chạy.' : 'Đã hoàn tất lượt chạy.';
    let historyFailed = false;
    if (follow && data.mode === 'apply') {
        try {
            await refreshHistory();
        } catch {
            message += ' Bấm Làm mới để tải lại bảng.';
            historyFailed = true;
        }
    }
    if (follow && (data.mode === 'preview' || failed)) details.open = true;
    showStatus(message, failed || historyFailed);
    button.disabled = false;
}

async function refreshStatus(follow = false) {
    try {
        const response = await fetch('/api/status');
        if (!response.ok) throw new Error();
        await reportRun(await response.json(), follow);
    } catch {
        showStatus('Chưa đọc được trạng thái DocSync. Đang thử lại…', true);
        setTimeout(() => refreshStatus(follow), 2000);
    }
}

form.addEventListener('submit', async event => {
    event.preventDefault();
    if (button.disabled) return;
    button.disabled = true;
    showStatus('Đang gửi lệnh…');
    try {
        const response = await fetch('/api/run', {
            method: 'POST',
            headers: {'Content-Type': 'application/json', 'X-CSRF-Token': form.dataset.token},
            body: JSON.stringify({command: input.value})
        });
        const data = await response.json();
        if (response.ok || data.running) {
            await reportRun(data, true);
            return;
        }
        showStatus(response.status === 403 ? 'Bấm Làm mới rồi nhập lệnh lại.' : data.message, true);
        button.disabled = false;
    } catch {
        showStatus('Chưa nhận được phản hồi. Đang kiểm tra trạng thái DocSync…', true);
        setTimeout(() => refreshStatus(true), 1000);
    }
});

refreshStatus();

document.querySelector('#history-rows').addEventListener('click', event => {
    const rowButton = event.target.closest('.classify-file');
    if (!rowButton) return;
    selectedFile = rowButton.dataset;
    document.querySelector('#decision-file').textContent = selectedFile.name;
    decisionForm.reset();
    decisionError.textContent = '';
    document.querySelector('#decision-destination').textContent = '';
    decision.showModal();
});

topic.addEventListener('change', () => {
    const destination = document.querySelector('#decision-destination');
    const option = topic.selectedOptions[0];
    destination.textContent = !topic.value ? '' : option.dataset.hold === 'true'
        ? 'Giữ file ở thư mục hiện tại.'
        : `Đích dự kiến: ${option.textContent}/${selectedFile.name}`;
});

cancelDecision.addEventListener('click', () => decision.close());
decision.addEventListener('cancel', event => {
    if (submitDecision.disabled) event.preventDefault();
});

decisionForm.addEventListener('submit', async event => {
    event.preventDefault();
    if (submitDecision.disabled) return;
    submitDecision.disabled = cancelDecision.disabled = topic.disabled = true;
    decisionError.textContent = 'Đang áp dụng…';
    try {
        const response = await fetch('/api/history/classify', {
            method: 'POST',
            headers: {'Content-Type': 'application/json', 'X-CSRF-Token': form.dataset.token},
            body: JSON.stringify({path: selectedFile.relative, token: selectedFile.token, topic: topic.value})
        });
        const data = await response.json();
        if (!response.ok) {
            decisionError.textContent = response.status === 403 ? 'Tải lại trang rồi chọn lại file.' : data.message;
            return;
        }
        decision.close();
        fileStatus.textContent = data.destination ? `Đã chuyển vào ${data.destination}.` : 'Đã giữ lại file.';
        try {
            await refreshHistory();
        } catch {
            fileStatus.textContent += ' Bấm Làm mới để cập nhật bảng.';
        }
    } catch {
        decisionError.textContent = 'Chưa nhận được kết quả. Hãy làm mới lịch sử trước khi thử lại.';
    } finally {
        submitDecision.disabled = cancelDecision.disabled = topic.disabled = false;
    }
});
