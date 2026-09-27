// Bookmarklet template for fb-album-ebook.py — install with `fb-album-ebook.py bookmarklet`.
//
// Open the first photo of an album (URL has fbid=…&set=a.…), click the bookmark.
// It walks the photo viewer with the "next" button like a person would, reads the
// image URL + caption, and downloads fb-album-<id>.json for
// `fb-album-ebook.py fetch <file> --source bookmarklet`.
//
// Anti-block rules: one photo every DELAY_MIN–DELAY_MAX seconds (random), at most
// MAX_PER_RUN photos per run, no background requests, and a hard stop on any
// checkpoint / login / "temporarily blocked" sign. Progress lives in localStorage,
// so clicking the bookmark again continues where the last run stopped.
(() => {
  const DELAY_MIN = 20, DELAY_MAX = 60;      // seconds between photos
  const MAX_PER_RUN = 150;
  const MAX_FAILURES = 3;                     // consecutive unreadable photos
  const LOAD_TIMEOUT = 25;                    // seconds to wait for the next photo
  const FORMAT = 'fb-album-bookmarklet/1';
  const readPhoto = __PHOTO_DETAIL_JS__;

  if (window.__fbAlbumEbook) { window.__fbAlbumEbook.panel.style.display = 'block'; return; }

  const params = () => new URLSearchParams(location.search);
  const currentFbid = () => params().get('fbid') || '';
  const albumId = ((params().get('set') || '').match(/^a\.(\d+)/) || [])[1];
  if (!albumId || !currentFbid()) {
    alert('Mở ảnh đầu tiên của album (URL có fbid=…&set=a.…) rồi bấm lại bookmarklet.');
    return;
  }

  const KEY = 'fbAlbumEbook:' + albumId;
  const load = () => { try { return JSON.parse(localStorage.getItem(KEY)) || null; } catch (e) { return null; } };
  const state = load() || {
    format: FORMAT,
    album: {id: albumId, title: '', source_url: `https://www.facebook.com/media/set/?set=a.${albumId}&type=3`},
    last: '', complete: false, photos: [],
  };
  const save = () => { try { localStorage.setItem(KEY, JSON.stringify(state)); } catch (e) {} };
  const seen = new Set(state.photos.map(p => p.id));
  if (!state.album.title) {
    state.album.title = document.title.replace(/^\(\d+\)\s*/, '').replace(/\s*\|\s*Facebook\s*$/, '');
  }

  // ---- panel (DOM API only: Facebook enforces Trusted Types, so no innerHTML)
  const el = (tag, style, text) => {
    const node = document.createElement(tag);
    if (style) node.style.cssText = style;
    if (text) node.textContent = text;
    return node;
  };
  const panel = el('div', 'position:fixed;z-index:2147483647;right:16px;bottom:16px;width:300px;padding:12px;' +
    'background:#fff;color:#111;border:1px solid #ccc;border-radius:8px;box-shadow:0 4px 16px rgba(0,0,0,.25);' +
    'font:13px/1.4 system-ui,sans-serif');
  const heading = el('div', 'font-weight:600;margin-bottom:6px', 'FB album → Ebook');
  const titleInput = el('input', 'width:100%;box-sizing:border-box;margin-bottom:6px;padding:4px');
  titleInput.value = state.album.title;
  titleInput.placeholder = 'Tên album';
  titleInput.oninput = () => { state.album.title = titleInput.value.trim(); save(); };
  const status = el('div', 'margin-bottom:8px;white-space:pre-wrap');
  const row = el('div', 'display:flex;gap:6px;flex-wrap:wrap');
  const button = (label, onclick) => { const b = el('button', 'padding:4px 8px;cursor:pointer', label); b.onclick = onclick; row.appendChild(b); return b; };
  panel.append(heading, titleInput, status, row);
  document.body.appendChild(panel);

  const say = text => { status.textContent = `${state.photos.length} ảnh đã lấy\n${text}`; };
  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
  let stopped = false, running = false;

  const download = () => {
    const blob = new Blob([JSON.stringify(state, null, 2)], {type: 'application/json'});
    const link = el('a');
    link.href = URL.createObjectURL(blob);
    link.download = `fb-album-${albumId}.json`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(link.href), 10000);
  };

  // Stop signals: we never retry automatically after one of these.
  const BLOCK_RE = /temporarily blocked|tạm thời bị chặn|going too fast|quá nhanh|checkpoint|captcha|security check|kiểm tra bảo mật|xác nhận danh tính|confirm your identity/i;
  const blockSign = () => {
    if (/\/(checkpoint|login)/.test(location.pathname) || document.querySelector('input[name="pass"]')) {
      return 'Facebook yêu cầu đăng nhập / checkpoint';
    }
    for (const dialog of document.querySelectorAll('[role="dialog"], [role="alertdialog"]')) {
      const text = dialog.innerText || '';
      if (BLOCK_RE.test(text)) return 'Facebook cảnh báo: ' + text.trim().split('\n')[0].slice(0, 120);
    }
    return '';
  };

  const NEXT_RE = /^(next photo|next|ảnh tiếp theo|tiếp theo|xem ảnh tiếp theo)$/i;
  const nextButton = () => Array.from(document.querySelectorAll('[aria-label]'))
    .find(node => NEXT_RE.test(node.getAttribute('aria-label').trim()) && node.offsetParent !== null);

  const expandCaption = async () => {
    const pane = document.querySelector('[role="complementary"]');
    if (!pane) return;
    const more = Array.from(pane.querySelectorAll('[role="button"]'))
      .filter(node => /^\s*(See more|Xem thêm)\s*$/.test(node.innerText || '')).slice(0, 3);
    for (const node of more) { node.click(); await sleep(400 + Math.random() * 600); }
  };

  const capture = async () => {
    await expandCaption();
    const detail = readPhoto();
    if (!detail.src) return false;
    const id = currentFbid();
    if (!seen.has(id)) {
      seen.add(id);
      state.photos.push({id, caption: detail.caption || '', created_time: detail.created || '', image_url: detail.src});
    }
    state.last = id;
    save();
    return true;
  };

  const waitForNext = async previous => {
    const deadline = Date.now() + LOAD_TIMEOUT * 1000;
    while (Date.now() < deadline) {
      if (stopped) return false;
      await sleep(500);
      if (currentFbid() !== previous && document.querySelector('img[data-visualcompletion="media-vc-image"]')) {
        await sleep(1500 + Math.random() * 1500);   // let the caption pane render
        return true;
      }
    }
    return false;
  };

  const finish = message => {
    stopped = true;
    save();
    say(message);
    if (state.photos.length) download();
  };

  const run = async () => {
    stopped = false;
    let taken = 0, failures = 0;
    if (state.last && currentFbid() !== state.last && seen.size) {
      say('Đang ở ảnh khác với lần dừng trước. Bấm "Tới ảnh đã dừng" rồi bấm bookmarklet lại, ' +
          'hoặc "Làm lại" để bắt đầu từ ảnh này.');
      return;
    }
    while (!stopped) {
      const sign = blockSign();
      if (sign) return finish(`⛔ DỪNG: ${sign}\nNghỉ ít nhất 24 giờ rồi mới chạy lại.`);
      const id = currentFbid();
      if (!seen.has(id)) {
        if (await capture()) { taken += 1; failures = 0; }
        else if (++failures >= MAX_FAILURES) return finish('⛔ DỪNG: không đọc được ảnh 3 lần liên tiếp (Facebook đổi giao diện hoặc đang giới hạn).');
      }
      if (taken >= MAX_PER_RUN) return finish(`Đã lấy ${MAX_PER_RUN} ảnh trong lần này. Nghỉ vài giờ rồi bấm bookmarklet để chạy tiếp.`);
      const next = nextButton();
      if (!next) { state.complete = true; return finish('✅ Hết album (không còn nút ảnh tiếp theo).'); }
      const wait = (DELAY_MIN + Math.random() * (DELAY_MAX - DELAY_MIN)) * 1000;
      for (let left = wait; left > 0 && !stopped; left -= 1000) {
        say(`Ảnh tiếp theo sau ${Math.ceil(left / 1000)} giây…`);
        await sleep(Math.min(1000, left));
      }
      if (stopped) return;
      next.click();
      if (!(await waitForNext(id))) {
        if (stopped) return;
        return finish('⛔ DỪNG: ảnh tiếp theo không tải được. Kiểm tra trang rồi thử lại sau.');
      }
      if (seen.has(currentFbid())) {
        state.complete = true;
        return finish('✅ Đã quay lại ảnh đầu — hết album.');
      }
    }
  };

  button('Bắt đầu / Tiếp tục', () => {
    if (running) return;
    running = true;
    run().finally(() => { running = false; });
  });
  button('Dừng', () => { stopped = true; save(); say('Đã dừng. Bấm "Tải JSON" để lưu.'); });
  button('Tải JSON', download);
  button('Tới ảnh đã dừng', () => {
    if (state.last) location.href = `https://www.facebook.com/photo/?fbid=${state.last}&set=a.${albumId}`;
  });
  button('Làm lại', () => {
    if (!confirm('Xoá tiến độ đã lưu của album này?')) return;
    localStorage.removeItem(KEY);
    state.photos = []; state.last = ''; state.complete = false;
    seen.clear();
    say('Đã xoá tiến độ.');
  });
  button('Ẩn', () => { panel.style.display = 'none'; });

  window.__fbAlbumEbook = {panel, state};
  say(state.photos.length
    ? `Lần trước dừng ở ảnh ${state.last}. Bấm "Bắt đầu / Tiếp tục".`
    : `Chờ ${DELAY_MIN}–${DELAY_MAX}s mỗi ảnh, tối đa ${MAX_PER_RUN} ảnh/lần. Bấm "Bắt đầu".`);
})();
