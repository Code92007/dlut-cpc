(() => {
  const esc = value => String(value ?? '').replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;').replaceAll("'", '&#39;');
  const imageUrl = (id, thumb = false) => `/api/photos/image/${encodeURIComponent(id)}${thumb ? '?thumb=1' : ''}`;
  let selectedAdmin = '', adminPage = 1;
  async function request(url, options = {}) {
    const response = await fetch(url, {cache: 'no-store', ...options});
    const body = await response.json();
    if (!response.ok) {
      const error = new Error(body.error || '照片加载失败');
      error.status = response.status;
      throw error;
    }
    return body;
  }
  const heading = '<div class="page-heading"><div><span class="eyebrow">Competition Photos</span><h1>比赛照片</h1><p class="contest-caption">留住赛场内外的共同记忆</p></div></div>';
  const grid = items => `<div class="photo-grid">${items.map((item, index) => `<button class="photo-tile" type="button" data-photo-index="${index}" aria-label="查看照片 ${index + 1}${item.caption ? '：' + esc(item.caption) : ''}"><img src="${imageUrl(item.id, true)}" alt="${esc(item.caption || '比赛照片')}" loading="lazy" referrerpolicy="no-referrer">${item.caption ? `<span>${esc(item.caption)}</span>` : ''}</button>`).join('')}</div>`;
  const pager = data => data.pages > 1 ? `<div class="review-pagination"><span>第 ${data.page} / ${data.pages} 页 · ${data.count} 张</span>${data.page > 1 ? '<button class="admin-button secondary" data-photo-page="-1">上一页</button>' : ''}${data.page < data.pages ? '<button class="admin-button secondary" data-photo-page="1">下一页</button>' : ''}</div>` : '';
  function bindViewer(root, items) {
    root.querySelectorAll('[data-photo-index]').forEach(button => button.addEventListener('click', () => {
      let index = Number(button.dataset.photoIndex);
      const dialog = document.createElement('dialog');
      dialog.className = 'photo-viewer';
      dialog.innerHTML = '<div class="photo-viewer-bar"><button type="button" data-prev aria-label="上一张">←</button><span data-position></span><button type="button" data-next aria-label="下一张">→</button><button type="button" data-close aria-label="关闭照片">关闭 ×</button></div><img alt=""><p data-caption></p>';
      const show = () => {
        dialog.querySelector('img').src = imageUrl(items[index].id);
        dialog.querySelector('img').alt = items[index].caption || '比赛照片';
        dialog.querySelector('[data-caption]').textContent = items[index].caption;
        dialog.querySelector('[data-position]').textContent = `${index + 1} / ${items.length}`;
        dialog.querySelector('[data-prev]').disabled = index === 0;
        dialog.querySelector('[data-next]').disabled = index === items.length - 1;
      };
      const move = delta => { index = Math.max(0, Math.min(items.length - 1, index + delta)); show(); };
      dialog.querySelector('[data-prev]').onclick = () => move(-1);
      dialog.querySelector('[data-next]').onclick = () => move(1);
      dialog.querySelector('[data-close]').onclick = () => dialog.close();
      dialog.querySelector('img').onerror = () => { dialog.querySelector('[data-caption]').textContent = '照片暂不可用，登录可能已过期，请关闭后重新登录。'; };
      dialog.addEventListener('keydown', event => { if (event.key === 'ArrowLeft') move(-1); if (event.key === 'ArrowRight') move(1); });
      dialog.addEventListener('click', event => { if (event.target === dialog) dialog.close(); });
      dialog.addEventListener('close', () => { dialog.remove(); button.focus(); });
      root.append(dialog); show(); dialog.showModal();
    }));
  }
  async function mount(root) {
    root.innerHTML = `${heading}<div class="empty-state">正在验证查看权限…</div>`;
    let session;
    const fail = error => { if (root.isConnected) root.innerHTML = `${heading}<div class="empty-state">${esc(error.message)}<p><a href="/api/photos/login">使用 OJ Wall 重新登录</a></p></div>`; };
    try {
      session = await request('/api/photos/session');
      if (!root.isConnected) return;
      if (!session.authenticated && !session.admin) {
        const reason = new URLSearchParams(location.search).get('auth');
        const message = reason === 'unverified' ? '该 OJ Wall 账号尚未通过成员认证。请先在区域赛页面申请，等待管理员审核。' : reason === 'expired' ? '本次登录已过期，请重新进入。' : '';
        root.innerHTML = `${heading}<section class="photo-gate"><span class="eyebrow">Members Only</span><h2>认证成员专属相册</h2><p>比赛照片仅向通过成员认证的 OJ Wall 用户开放。</p>${message ? `<p class="admin-message error">${message}</p>` : ''}${session.configured ? `<a class="admin-button" href="/api/photos/login">使用 OJ Wall 登录</a><a class="photo-help" href="${esc(session.wallUrl)}/regionals" target="_blank" rel="noopener noreferrer">前往申请成员认证 ↗</a>` : '<p>照片功能正在准备中，请稍后再来。</p>'}</section>`;
        return;
      }
      const account = `<div class="photo-session"><span>${session.authenticated ? esc(session.account) + ' · 已通过成员认证' : '管理员预览'}</span>${session.authenticated ? '<button class="admin-button secondary" id="photoLogout">退出照片登录</button>' : ''}</div>`;
      const bindLogout = () => root.querySelector('#photoLogout')?.addEventListener('click', async () => {
        try { await request('/api/photos/logout', {method: 'POST', headers: {'X-CSRF-Token': session.csrf}}); mount(root); } catch (error) { fail(error); }
      });
      const list = async () => {
        const data = await request('/api/photos/albums');
        if (!root.isConnected) return;
        root.innerHTML = `${heading}${account}<label class="photo-search">查找比赛<input id="photoSearch" type="search" placeholder="输入比赛名称或年份"></label><div id="photoAlbumList"></div>`;
        const render = value => {
          const albums = data.albums.filter(item => `${item.title} ${item.date} ${item.description}`.toLowerCase().includes(value.toLowerCase()));
          root.querySelector('#photoAlbumList').innerHTML = albums.length ? `<div class="photo-albums">${albums.map(item => `<button type="button" class="photo-album" data-album="${item.id}">${item.cover ? `<img src="${imageUrl(item.cover, true)}" alt="" loading="lazy">` : '<div class="photo-cover-empty">比赛记忆</div>'}<div><span class="eyebrow">${esc(item.date)} · ${item.count} 张照片</span><h2>${esc(item.title)}</h2><p>${esc(item.description)}</p></div></button>`).join('')}</div>` : `<div class="empty-state">${data.albums.length ? '没有匹配的相册' : '比赛照片整理中，上传后将在这里展示。'}</div>`;
          root.querySelectorAll('[data-album]').forEach(button => button.onclick = () => album(button.dataset.album).catch(fail));
        };
        root.querySelector('#photoSearch').oninput = event => render(event.target.value);
        render(''); bindLogout();
      };
      const album = async (id, page = 1) => {
        const data = await request(`/api/photos/album?id=${encodeURIComponent(id)}&page=${page}`);
        if (!root.isConnected) return;
        root.innerHTML = `${heading}${account}<button class="admin-button secondary" id="photoBack">← 全部相册</button><div class="photo-album-heading"><span class="eyebrow">${esc(data.album.date)} · ${data.count} 张照片</span><h2>${esc(data.album.title)}</h2><p>${esc(data.album.description)}</p></div>${data.items.length ? grid(data.items) : '<div class="empty-state">这个相册的照片正在整理中。</div>'}${pager(data)}`;
        root.querySelector('#photoBack').onclick = () => list().catch(fail);
        root.querySelectorAll('[data-photo-page]').forEach(button => button.onclick = () => album(id, data.page + Number(button.dataset.photoPage)).catch(fail));
        bindViewer(root, data.items); bindLogout();
      };
      await list();
    } catch (error) { fail(error); }
  }
  async function mountAdmin(root, csrf) {
    let message = '', busy = false, removeId = '';
    const post = (path, body) => request(`/api/admin/photos/${path}`, {method: 'POST', headers: {'X-CSRF-Token': csrf, 'Content-Type': 'application/json'}, body: JSON.stringify(body)});
    const displayError = error => { message = error.message; const box = root.querySelector('#photoAdminMessage'); if (box) { box.textContent = message; box.className = 'admin-message error'; } };
    const render = async () => {
      try {
        const data = await request('/api/admin/photos/albums');
        const selected = data.albums.find(item => item.id === selectedAdmin);
        if (!selected) selectedAdmin = '';
        const details = selected ? await request(`/api/admin/photos/albums?album=${selected.id}&page=${adminPage}`) : null;
        if (!root.isConnected) return;
        root.innerHTML = `<p class="contest-caption">只有通过 OJ Wall 成员认证的用户可以查看。新相册默认隐藏，整理后勾选“向认证成员开放”。</p><p id="photoAdminMessage" class="admin-message" role="status">${esc(message)}</p>
          <div class="photo-admin-toolbar"><label>选择相册<select id="photoAdminAlbum"><option value="">新建相册</option>${data.albums.map(item => `<option value="${item.id}" ${item.id === selectedAdmin ? 'selected' : ''}>${esc(item.title)} · ${item.count} 张 · ${item.published ? '已开放' : '隐藏'}</option>`).join('')}</select></label><button type="button" class="admin-button secondary" id="photoNew">新建相册</button></div>
          <form id="photoAlbumForm" class="admin-form"><h2>${selected ? '编辑相册' : '新建比赛相册'}</h2><div class="admin-fields"><label>比赛名称<input name="title" required maxlength="200" value="${esc(selected?.title || '')}" placeholder="例如：2026 ICPC 西安站"></label><label>比赛日期<input name="date" type="date" required value="${esc(selected?.date || '')}"></label><label class="wide">相册简介<textarea name="description" maxlength="2000" rows="3">${esc(selected?.description || '')}</textarea></label><label class="admin-checkbox wide"><input name="published" type="checkbox" ${selected?.published ? 'checked' : ''}>向认证成员开放</label></div><button class="admin-button">保存相册</button></form>
          ${selected ? `<form id="photoUploadForm" class="admin-form"><h2>上传照片</h2><p class="contest-caption">支持多选 JPEG、PNG、WebP，每张不超过 12 MB。上传后会清除照片中的定位等元数据。</p><div class="admin-fields"><label class="wide">选择照片<input name="files" type="file" accept="image/jpeg,image/png,image/webp" required multiple></label><label class="wide">照片说明（可选）<input name="caption" maxlength="300" placeholder="本次上传的照片共用此说明"></label></div><button class="admin-button" id="photoUploadButton">开始上传</button><p id="photoUploadProgress" role="status"></p></form><section class="admin-recent"><h2>相册照片 · ${details.count} 张</h2>${details.items.length ? grid(details.items) : '<div class="empty-state">还没有照片，请在上方上传。</div>'}${pager(details)}<div class="photo-remove-list">${details.items.map((item, index) => `<div><span>照片 ${index + 1 + (details.page - 1) * 60}${item.caption ? ' · ' + esc(item.caption) : ''}</span><button type="button" class="admin-button secondary" data-photo-remove="${item.id}">${removeId === item.id ? '确认删除此照片' : '删除'}</button></div>`).join('')}</div></section>` : ''}`;
        root.querySelector('#photoAdminAlbum').onchange = event => { selectedAdmin = event.target.value; adminPage = 1; message = ''; render(); };
        root.querySelector('#photoNew').onclick = () => { selectedAdmin = ''; adminPage = 1; message = ''; render(); };
        root.querySelector('#photoAlbumForm').onsubmit = async event => {
          event.preventDefault(); if (busy) return; busy = true;
          const form = event.target, values = new FormData(form);
          form.querySelector('button').disabled = true;
          try { const saved = await post('album', {...Object.fromEntries(values), published: values.has('published'), ...(selected ? {id: selected.id} : {})}); selectedAdmin = saved.id; message = '相册已保存'; await render(); } catch (error) { displayError(error); } finally { busy = false; form.querySelector('button').disabled = false; }
        };
        root.querySelector('#photoUploadForm')?.addEventListener('submit', async event => {
          event.preventDefault(); if (busy) return; busy = true;
          const form = event.target, files = [...form.elements.files.files], caption = form.elements.caption.value;
          root.querySelectorAll('input,button,select,textarea').forEach(control => { control.disabled = true; });
          let uploaded = 0; const errors = [];
          for (const file of files) {
            form.querySelector('#photoUploadProgress').textContent = `正在上传 ${uploaded + errors.length + 1} / ${files.length}：${file.name}`;
            try {
              if (file.size > 12 * 1024 * 1024) throw new Error('超过 12 MB');
              await request(`/api/admin/photos/upload?album=${selected.id}&caption=${encodeURIComponent(caption)}`, {method: 'POST', headers: {'X-CSRF-Token': csrf, 'Content-Type': file.type || 'application/octet-stream'}, body: file}); uploaded++;
            } catch (error) { errors.push(`${file.name}：${error.message}`); }
          }
          message = `已上传 ${uploaded} 张照片${errors.length ? '；上传失败：' + errors.join('；') : ''}`; busy = false; await render();
        });
        if (details) {
          bindViewer(root, details.items);
          root.querySelectorAll('[data-photo-page]').forEach(button => button.onclick = () => { adminPage = details.page + Number(button.dataset.photoPage); render(); });
          root.querySelectorAll('[data-photo-remove]').forEach(button => button.onclick = async () => {
            if (busy) return;
            if (removeId !== button.dataset.photoRemove) { removeId = button.dataset.photoRemove; render(); return; }
            button.disabled = true;
            try { await post('remove', {id: removeId}); removeId = ''; message = '照片已删除'; await render(); } catch (error) { displayError(error); button.disabled = false; }
          });
        }
      } catch (error) { if (root.isConnected) root.innerHTML = `<div class="admin-message error">${esc(error.message)}</div>`; }
    };
    await render();
  }
  window.competitionPhotos = {mount, mountAdmin};
})();
