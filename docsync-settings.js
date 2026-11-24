'use strict';

const form = document.querySelector('#folder-settings');
const controls = form.querySelector('fieldset');
const feedback = document.querySelector('#feedback');
const fields = [...form.querySelectorAll('input')];
const systemRules = document.querySelector('#system-rules-list');
const systemRulesStatus = document.querySelector('#system-rules-status');

function message(text, error = false) {
    feedback.textContent = text;
    feedback.toggleAttribute('data-error', error);
}

function showPaths(settings) {
    for (const field of fields) {
        field.value = settings.paths[field.name];
        field.readOnly = settings.overridden.includes(field.name);
    }
    showSystemRules(settings.system_routes);
    return settings.overridden.length ? ' Các ô chỉ đọc lấy đường dẫn từ môi trường chạy.' : '';
}

function showSystemRules(groups) {
    const node = (tag, text, className) => {
        const item = document.createElement(tag);
        item.textContent = text;
        if (className) item.className = className;
        return item;
    };
    const opened = new Set([...systemRules.querySelectorAll('details[open]')].map(group => group.dataset.group));
    const initialLoad = !systemRules.childElementCount;
    systemRules.replaceChildren(...groups.map((group, index) => {
        const details = node('details', '', 'system-group');
        details.dataset.group = group.title;
        details.open = opened.has(group.title) || (initialLoad && index === 0);
        const title = node('summary', group.title + ' ');
        title.append(node('small', `· ${group.operations.length} quy tắc`));
        const rows = node('ul', '', 'system-operations');
        for (const operation of group.operations) {
            const row = node('li', '', 'system-operation');
            const heading = node('div', '');
            heading.append(node('strong', operation.name), node('small', operation.action));
            const body = node('div', '');
            body.append(node('p', operation.condition));
            for (const destination of operation.destinations) body.append(node('code', destination));
            row.append(heading, body);
            rows.append(row);
        }
        details.append(title, rows);
        return details;
    }));
    systemRulesStatus.textContent = '';
    systemRulesStatus.hidden = true;
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
        systemRulesStatus.textContent = 'Không đọc được quy tắc hệ thống. Tải lại trang để thử lại.';
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
        if (loadedRules?.workspace !== settings.paths.DOCSYNC_ROOT) loadRules();
    } catch {
        message('Chưa nhận được xác nhận. Tải lại trang để kiểm tra thư mục đã lưu.', true);
    } finally {
        controls.disabled = false;
    }
});

loadPaths();

const ruleForm = document.querySelector('#rule-settings');
const ruleControls = ruleForm.querySelector('fieldset');
const ruleDocument = document.querySelector('#rule-document');
const rulesFeedback = document.querySelector('#rules-feedback');
const visualPanel = document.querySelector('#rules-visual');
const jsonPanel = document.querySelector('#rules-json');
const visualMode = document.querySelector('#rules-visual-button');
const jsonMode = document.querySelector('#rules-json-button');
const ruleCards = document.querySelector('#rule-cards');
const ruleTemplate = document.querySelector('#rule-card-template');
const exclusions = document.querySelector('#excluded-paths');
const generatedPatterns = document.querySelector('#generated-patterns');
const lines = text => text.split('\n').map(line => line.trim()).filter(Boolean);
const literalPattern = text => [...text.normalize('NFC').replaceAll('đ', 'd').replaceAll('Đ', 'D')]
    .map(char => '\\^$.*+?()[]{}|'.includes(char) ? '\\' + char : char).join('');
const managedIds = new Set(['bank_statements', 'ghtk_reports']);
let loadedRules;
let draftRules;
let editingJSON = false;

function renderCards(openRule) {
    ruleCards.replaceChildren();
    draftRules.categories.forEach((rule, index) => {
        const card = ruleTemplate.content.firstElementChild.cloneNode(true);
        const fields = new Map([...card.querySelectorAll('[data-rule]')].map(input => [input.dataset.rule, input]));
        const mode = fields.get('route_mode');
        const keywords = card.querySelector('[data-keyword]');
        const patterns = fields.get('patterns');
        const summarize = () => {
            card.querySelector('[data-summary]').textContent = rule.label || rule.id || 'Quy tắc mới';
            const label = mode.selectedOptions[0]?.textContent || 'Chọn cách xử lý';
            card.querySelector('[data-summary-detail]').textContent = rule.enabled === false
                ? '· Đã tắt' : '· ' + label + (rule.target ? ' · ' + rule.target : '');
        };
        for (const [key, input] of fields) {
            if (key === 'enabled') input.checked = rule.enabled !== false;
            else input.value = key === 'patterns' ? rule.patterns.join('\n') : rule[key] ?? (key === 'route_mode' ? 'topic_auto' : '');
            input.addEventListener('input', () => {
                if (key === 'enabled') rule[key] = input.checked;
                else if (key === 'confidence') rule[key] = input.valueAsNumber;
                else rule[key] = key === 'patterns' ? lines(input.value) : input.value;
                summarize();
            });
        }
        fields.get('id').readOnly = managedIds.has(rule.id);
        fields.get('target').readOnly = rule.id === 'ghtk_reports';
        const addKeyword = () => {
            const value = keywords.value.trim();
            if (!value) return;
            const next = literalPattern(value);
            if (!rule.patterns.includes(next)) rule.patterns = [...rule.patterns, next];
            patterns.value = rule.patterns.join('\n');
            keywords.value = '';
            keywords.focus();
        };
        card.querySelector('[data-add-keyword]').addEventListener('click', addKeyword);
        keywords.addEventListener('keydown', event => {
            if (event.key !== 'Enter') return;
            event.preventDefault();
            addKeyword();
        });
        for (const button of card.querySelectorAll('[data-step]')) {
            const offset = Number(button.dataset.step);
            const target = index + offset;
            button.disabled = target < 0 || target >= draftRules.categories.length;
            button.addEventListener('click', () => {
                draftRules.categories.splice(index, 1);
                draftRules.categories.splice(target, 0, rule);
                renderCards(rule);
                ruleCards.querySelector('details[open] summary').focus();
            });
        }
        const remove = card.querySelector('[data-remove]');
        remove.disabled = managedIds.has(rule.id);
        remove.addEventListener('click', () => {
            draftRules.categories.splice(index, 1);
            renderCards();
            document.querySelector('#add-rule').focus();
        });
        card.open = rule === openRule;
        summarize();
        ruleCards.append(card);
    });
}

function setRulesMode(useJSON) {
    editingJSON = useJSON;
    visualPanel.hidden = visualPanel.disabled = useJSON;
    jsonPanel.hidden = jsonPanel.disabled = !useJSON;
    visualMode.setAttribute('aria-pressed', String(!useJSON));
    jsonMode.setAttribute('aria-pressed', String(useJSON));
    if (useJSON) {
        ruleDocument.value = JSON.stringify(draftRules, null, 2);
    } else {
        exclusions.value = draftRules.excluded_paths.join('\n');
        generatedPatterns.value = draftRules.generated_name_patterns.join('\n');
        renderCards();
    }
}

function showRules(data) {
    loadedRules = data;
    draftRules = data.rules;
    setRulesMode(editingJSON);
}
function rulesMessage(text, error = false) {
    rulesFeedback.textContent = text;
    rulesFeedback.toggleAttribute('data-error', error);
}
async function loadRules() {
    ruleControls.disabled = true;
    rulesMessage('Đang đọc quy tắc…');
    try {
        const response = await fetch('/api/settings/rules');
        const data = await response.json();
        if (!response.ok) throw new Error(data.message);
        showRules(data);
        rulesMessage('');
    } catch (error) {
        rulesMessage(error.message, true);
    } finally {
        ruleControls.disabled = false;
    }
}

ruleForm.addEventListener('submit', async event => {
    event.preventDefault();
    if (!loadedRules) return;
    ruleControls.disabled = true;
    rulesMessage('Đang kiểm tra quy tắc…');
    try {
        const rules = editingJSON ? JSON.parse(ruleDocument.value) : draftRules;
        const response = await fetch('/api/settings/rules', {
            method: 'POST',
            headers: {'Content-Type': 'application/json', 'X-CSRF-Token': form.dataset.token},
            body: JSON.stringify({rules, workspace: loadedRules.workspace, revision: loadedRules.revision})
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.message);
        showRules(data);
        rulesMessage('Đã lưu quy tắc. Bạn có thể chạy Xem trước trong Tổng quan.');
    } catch (error) {
        rulesMessage(error instanceof SyntaxError ? 'JSON chưa đúng cú pháp.' : error.message, true);
    } finally {
        ruleControls.disabled = false;
    }
});

jsonMode.addEventListener('click', () => {
    if (draftRules && !editingJSON) setRulesMode(true);
});
visualMode.addEventListener('click', () => {
    if (!editingJSON) return;
    try {
        const rules = JSON.parse(ruleDocument.value);
        const isStringList = values => Array.isArray(values) && values.every(value => typeof value === 'string');
        if (!rules || !isStringList(rules.excluded_paths) || !isStringList(rules.generated_name_patterns)
            || !Array.isArray(rules.categories) || rules.categories.some(rule => !rule || !isStringList(rule.patterns)
                || !['review_only', 'managed_move', 'topic_auto', null, undefined].includes(rule.route_mode))) {
            throw new Error('Kiểm tra cấu trúc JSON trước khi chuyển về Biểu mẫu.');
        }
        draftRules = rules;
        setRulesMode(false);
        rulesMessage('');
    } catch (error) {
        rulesMessage(error instanceof SyntaxError ? 'JSON chưa đúng cú pháp.' : error.message, true);
    }
});
exclusions.addEventListener('input', () => draftRules.excluded_paths = lines(exclusions.value));
generatedPatterns.addEventListener('input', () => draftRules.generated_name_patterns = lines(generatedPatterns.value));
document.querySelector('#add-rule').addEventListener('click', () => {
    if (draftRules.categories.length >= 80) {
        rulesMessage('DocSync hỗ trợ tối đa 80 quy tắc.', true);
        return;
    }
    const ids = new Set(draftRules.categories.map(rule => rule.id));
    let number = 1;
    while (ids.has('rule_' + number)) number += 1;
    const rule = {id: 'rule_' + number, label: '', target: '', confidence: 0.95, route_mode: 'review_only', patterns: []};
    draftRules.categories.push(rule);
    renderCards(rule);
    ruleCards.querySelector('details[open] [data-rule=label]').focus();
});
ruleForm.addEventListener('invalid', event => {
    const card = event.target.closest('details');
    if (card) card.open = true;
}, true);
document.querySelector('#reload-rules').addEventListener('click', loadRules);
loadRules();
