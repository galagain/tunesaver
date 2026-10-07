(() => {
  const $ = id => document.getElementById(id);
  const dialog = $('feedback-dialog');
  const labels = {new: 'Nouveau', in_progress: 'En cours', resolved: 'Résolu', closed: 'Classé'};
  let items = [], selected = '', admin = false, epoch = 0, imageUrl = '', busy = false;
  const prefix = () => admin ? '/api/admin/feedback' : '/api/feedback';
  const api = (path = '', options) => (admin ? adminJson : requestJson)(prefix() + path, options);
  const post = (path, payload) => api(path, {method: 'POST', body: JSON.stringify(payload)});
  function error(e) { $('feedback-error').textContent = e?.message || ''; }
  function revokeImage() {
    $('feedback-image').removeAttribute('src');
    $('feedback-image-link').hidden = true;
    $('feedback-image-link').removeAttribute('href');
    if (imageUrl) URL.revokeObjectURL(imageUrl);
    imageUrl = '';
  }
  function reset() {
    epoch++; selected = ''; items = []; busy = false; dialog.close(); revokeImage();
    $('feedback-create').reset(); $('feedback-reply').reset();
    $('feedback-list').replaceChildren(); $('feedback-messages').replaceChildren();
    document.querySelectorAll('.feedback-badge').forEach(el => {el.hidden = true; el.textContent = '';});
  }
  function badges() {
    const count = items.filter(item => item.unread).length;
    document.querySelectorAll('.feedback-badge').forEach(el => {
      el.hidden = !count; el.textContent = String(count); el.title = `${count} retour(s) non lu(s)`;
    });
  }
  function renderList() {
    const filter = $('feedback-filter').value;
    $('feedback-list').replaceChildren();
    const filtered = items.filter(item => filter === 'all' || (filter === 'unread' ? item.unread
      : filter === 'bugs' ? item.kind === 'bug' && ['new', 'in_progress'].includes(item.status)
      : filter === 'suggestion' ? item.kind === 'suggestion' : item.status === filter));
    for (const item of filtered) {
      const button = document.createElement('button');
      button.type = 'button'; button.dataset.selected = String(item.id === selected);
      const title = document.createElement('strong'); title.textContent = item.title;
      const meta = document.createElement('span');
      meta.textContent = `${item.unread ? 'Non lu · ' : ''}${item.kind === 'bug' ? 'Bug' : 'Suggestion'} · ${labels[item.status]}`;
      const author = document.createElement('small');
      author.textContent = `${admin ? `${item.email} · ` : ''}${new Date(item.updated_at).toLocaleDateString('fr-FR')}`;
      button.append(title, meta, author); button.onclick = () => openThread(item.id).catch(error);
      $('feedback-list').append(button);
    }
    if (!filtered.length) $('feedback-list').textContent = 'Aucun retour.';
    badges();
  }
  async function refresh() {
    const version = epoch;
    const payload = await api();
    if (version !== epoch) return;
    items = payload.threads || []; renderList();
  }
  async function notificationCheck() {
    if (!session?.access_token && !adminSession?.access_token) return;
    if (dialog.open || busy) return;
    admin = Boolean(adminSession?.access_token);
    try {await refresh();} catch (_) { /* Keep background polling quiet. */ }
  }
  function newThread() {
    epoch++; selected = ''; revokeImage(); error(null);
    $('feedback-create').hidden = false; $('feedback-thread').hidden = true; $('feedback-empty').hidden = true;
    renderList(); $('feedback-subject').focus();
  }
  async function openThread(id) {
    if (busy) return;
    const version = ++epoch;
    selected = id; revokeImage(); error(null);
    $('feedback-create').hidden = true; $('feedback-thread').hidden = true;
    $('feedback-empty').hidden = false; $('feedback-empty').textContent = 'Chargement…';
    renderList();
    const data = await api(`/${id}`);
    if (version !== epoch || !dialog.open) return;
    $('feedback-thread-title').textContent = data.title;
    $('feedback-status-label').textContent = labels[data.status];
    $('feedback-status').hidden = !admin; $('feedback-status').value = data.status;
    $('feedback-context').textContent = Object.values(data.context || {}).filter(Boolean).join(' · ');
    $('feedback-messages').replaceChildren();
    for (const message of data.messages) {
      const entry = document.createElement('article');
      entry.className = `feedback-message feedback-message-${message.author}`;
      const heading = document.createElement('small');
      heading.textContent = `${message.author === 'admin' ? 'Administration' : message.author === 'status' ? 'Statut' : 'Utilisateur'} · ${new Date(message.created_at).toLocaleString('fr-FR')}`;
      const body = document.createElement('p'); body.textContent = message.body;
      entry.append(heading, body); $('feedback-messages').append(entry);
    }
    $('feedback-reply').reset(); $('feedback-thread').hidden = false; $('feedback-empty').hidden = true;
    await post(`/${id}/read`, {revision: data.revision});
    if (version !== epoch) return;
    await refresh();
    if (data.has_screenshot) {
      const response = await (admin ? adminFetch : authenticatedFetch)(`${prefix()}/${id}/screenshot`);
      if (!response.ok) throw new Error('Impossible de charger la capture.');
      const blob = await response.blob();
      if (version !== epoch || !dialog.open) return;
      imageUrl = URL.createObjectURL(blob);
      $('feedback-image').src = imageUrl; $('feedback-image-link').href = imageUrl;
      $('feedback-image-link').hidden = false;
    }
  }
  async function open(isAdmin) {
    reset(); admin = isAdmin; error(null); $('feedback-filter').value = 'all';
    $('feedback-new').hidden = admin;
    $('feedback-create').hidden = true; $('feedback-thread').hidden = true;
    $('feedback-empty').hidden = false; $('feedback-empty').textContent = 'Sélectionne un retour.';
    dialog.showModal();
    await refresh();
    if (!admin && !items.length && dialog.open) newThread();
  }
  async function imageData(file) {
    if (!file) return '';
    if (!['image/png', 'image/jpeg', 'image/webp'].includes(file.type) || file.size > 10 * 1024 * 1024)
      throw new Error('Choisis une image PNG, JPEG ou WebP de moins de 10 Mo.');
    const bitmap = await createImageBitmap(file);
    try {
      const scale = Math.min(1, 1200 / Math.max(bitmap.width, bitmap.height));
      const canvas = document.createElement('canvas');
      canvas.width = Math.max(1, Math.round(bitmap.width * scale));
      canvas.height = Math.max(1, Math.round(bitmap.height * scale));
      canvas.getContext('2d').drawImage(bitmap, 0, 0, canvas.width, canvas.height);
      const data = canvas.toDataURL('image/png').split(',')[1];
      if (data.length > 1398104) throw new Error('Capture trop lourde. Réduis sa taille avant de l’envoyer.');
      return data;
    } finally {bitmap.close();}
  }
  function context() {
    const browser = [/Edg\/[\d.]+/, /Firefox\/[\d.]+/, /Chrome\/[\d.]+/, /Version\/[\d.]+/]
      .map(pattern => navigator.userAgent.match(pattern)?.[0]).find(Boolean)?.replace('Version/', 'Safari/') || 'Autre navigateur';
    return {version: '20261008-feedback1', browser,
      section: document.querySelector('.workspace').dataset.view === 'library' && selectedPlaylistId
        ? 'Playlist' : document.querySelector('.workspace').dataset.view || 'library'};
  }
  async function saving(button, action) {
    if (busy) return;
    busy = true; button.disabled = true; error(null);
    try {await action();} catch (e) {error(e);} finally {busy = false; button.disabled = false;}
  }
  $('feedback-create').onsubmit = event => {
    event.preventDefault();
    const version = epoch;
    saving(event.submitter, async () => {
      const screenshot = await imageData($('feedback-file').files[0]);
      if (version !== epoch) return;
      const result = await post('', {kind: $('feedback-kind').value, title: $('feedback-subject').value,
        body: $('feedback-body').value, screenshot, context: context()});
      if (version !== epoch) return;
      $('feedback-create').reset(); busy = false;
      await openThread(result.id);
    });
  };
  $('feedback-reply').onsubmit = event => {
    event.preventDefault(); const id = selected, version = epoch;
    saving(event.submitter, async () => {
      await post(`/${id}/comment`, {body: $('feedback-comment').value});
      if (version !== epoch) return;
      busy = false; await openThread(id);
    });
  };
  $('feedback-status').onchange = event => {
    const id = selected, version = epoch;
    saving(event.target, async () => {
      await post(`/${id}/status`, {status: event.target.value});
      if (version !== epoch) return;
      busy = false; await openThread(id);
    });
  };
  $('feedback-open').onclick = () => open(false).catch(error);
  $('feedback-admin-open').onclick = () => open(true).catch(error);
  $('feedback-close').onclick = () => {epoch++; dialog.close(); revokeImage();};
  dialog.addEventListener('cancel', () => {epoch++; revokeImage();});
  dialog.addEventListener('close', () => {if (!dialog.open) revokeImage();});
  $('feedback-new').onclick = () => {if (!busy) newThread();};
  $('feedback-filter').onchange = renderList;
  $('feedback-refresh').onclick = () => (selected ? openThread(selected) : refresh()).catch(error);
  window.addEventListener('feedback-reset', reset);
  window.addEventListener('feedback-session', notificationCheck);
  setInterval(notificationCheck, 30000);
  notificationCheck();
})();
