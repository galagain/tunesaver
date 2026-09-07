/* Discovery stays independent of the library renderer and its polling cycle. */
window.Dig = (() => {
  const $ = (id) => document.getElementById(id);
  let active = false, current = null, references = [], selection = new Map();
  let timer = null, loading = false, generation = 0, listSignature = "", resultSignature = "";
  const queued = new Map();
  const kinds = { track: "Son", playlist: "Playlist", tag: "Tag", folder: "Dossier", suggestion: "Suggestion" };
  const el = (tag, cls, text) => {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined) node.textContent = text;
    return node;
  };
  const icon = (name) => document.getElementById(`icon-${name}`).content.cloneNode(true);
  function command(name, glyph, action) {
    const button = el("button", "collection-rename");
    button.type = "button";
    button.title = name;
    button.setAttribute("aria-label", name);
    button.append(icon(glyph));
    button.addEventListener("click", async () => {
      button.disabled = true;
      try { await action(); } catch (error) { showToast(error.message, "error"); }
      finally { button.disabled = false; }
    });
    return button;
  }
  function entries(kind) {
    if (kind === "track") return libraryState.tracks.map(t => ({ id: t.job_id, name: `${t.artist} · ${t.title}`, cover: t.cover_url }));
    return (libraryState[{ playlist: "playlists", tag: "tags", folder: "folders" }[kind]] || []).map(t => ({ ...t }));
  }
  function paintReferences() {
    $("dig-references").replaceChildren();
    if (!references.length) $("dig-references").append(el("p", "empty-copy", "Aucune référence sélectionnée."));
    for (const ref of references) {
      const row = el("div", "dig-reference");
      row.append(el("span", "", `${kinds[ref.kind]} · ${ref.name}`), command(`Retirer ${ref.name}`, "close", () => {
        references = references.filter(r => !(r.kind === ref.kind && r.id === ref.id));
        paintReferences();
      }));
      $("dig-references").append(row);
    }
    $("dig-generate").disabled = !references.length;
  }
  function paintPicker() {
    const kind = $("dig-picker-kind").value;
    const query = $("dig-picker-query").value.toLocaleLowerCase();
    const rows = entries(kind).filter(e => e.name.toLocaleLowerCase().includes(query));
    $("dig-picker-list").replaceChildren();
    for (const entry of rows) {
      const key = `${kind}:${entry.id}`;
      const label = el("label", "dig-picker-row");
      const input = document.createElement("input");
      input.type = "checkbox";
      input.checked = selection.has(key);
      input.addEventListener("change", () => {
        if (input.checked && selection.size >= 100) { input.checked = false; showToast("100 références maximum.", "error"); return; }
        if (input.checked) selection.set(key, { kind, id: entry.id, name: entry.name });
        else selection.delete(key);
        $("dig-picker-count").textContent = `${selection.size} sélectionnée(s)`;
      });
      label.append(input);
      if (entry.cover) {
        const image = document.createElement("img"); image.alt = "";
        loadPrivateImage(image, entry.cover);
        label.append(image);
      }
      label.append(el("span", "", entry.name));
      $("dig-picker-list").append(label);
    }
    if (!rows.length) $("dig-picker-list").append(el("p", "empty-copy", "Aucun résultat."));
    $("dig-picker-count").textContent = `${selection.size} sélectionnée(s)`;
  }
  function sync() {
    if (!active) return;
    $("library-title").textContent = "Dig";
    $("library-count").textContent = current?.name || "Nouvelle recherche";
    $("dig-nav").dataset.selected = "true";
    document.querySelectorAll('.library-sidebar [data-selected="true"]').forEach(node => {
      if (node.id !== "dig-nav") node.dataset.selected = "false";
    });
    const signature = JSON.stringify(libraryState.playlists.map(p => [p.id, p.name]));
    if (signature !== listSignature) {
      listSignature = signature;
      const selected = $("dig-destination").value;
      $("dig-destination").replaceChildren(new Option("Mes sons", ""), ...libraryState.playlists.map(p => new Option(p.name, p.id)));
      $("dig-destination").value = libraryState.playlists.some(p => p.id === selected) ? selected : "";
    }
    if (current) paintResults();
  }
  async function refresh() {
    if (!active || !session || loading) return;
    loading = true;
    const version = generation;
    try {
      const list = await requestJson("/api/dig", { method: "GET" });
      const detail = current?.id ? await requestJson(`/api/dig/${current.id}`, { method: "GET" }) : null;
      if (!active || version !== generation) return;
      $("dig-sessions").replaceChildren(new Option("Nouvelle recherche", ""), ...list.sessions.map(s => new Option(`${s.name}${["running", "pending"].includes(s.state) ? " · En cours" : ""}`, s.id)));
      if (detail) current = detail;
      $("dig-sessions").value = current?.id || "";
      paint();
    } catch (error) { if (version === generation) showToast(error.message, "error"); }
    finally { loading = false; }
  }
  function paint() {
    $("dig-form").hidden = Boolean(current);
    for (const id of ["dig-rename", "dig-delete", "dig-edit", "dig-results-toolbar", "dig-details"]) $(id).hidden = !current;
    const running = current && ["running", "pending"].includes(current.state);
    $("dig-cancel").hidden = !running;
    $("dig-retry").hidden = !current || running;
    $("dig-more").hidden = !current || running || current.target >= current.available;
    $("dig-status").textContent = current ? current.progress || "En attente" : "";
    $("dig-status").classList.toggle("dig-running", Boolean(running));
    $("dig-detail-content").replaceChildren();
    if (current?.seeds) {
      $("dig-detail-content").append(el("p", "", `${current.seeds.length} morceaux de référence · ${current.config.mode === "explore" ? "Exploration" : "Proche"}`));
      for (const seed of current.seeds) $("dig-detail-content").append(el("p", "", `${seed.artist} · ${seed.title}`));
      for (const message of current.warnings) $("dig-detail-content").append(el("p", "dig-warning", message));
      if (current.warnings.length) $("dig-details").open = true;
    }
    sync();
    paintResults();
  }
  function downloadState(item) {
    const urls = new Set(item.matches.map(m => m.download_url));
    const same = t => urls.has(t.source_url) || (t.title === item.title && t.artist === item.artist);
    const owned = libraryState.tracks.find(same);
    if (owned) return { job: owned.job_id, state: "done", track: owned };
    const job = liveQueueTracks.find(t => same(t) || t.id === queued.get(item.id));
    return { job: job?.id || queued.get(item.id), state: job?.state || (queued.has(item.id) ? "pending" : "") };
  }
  async function itemAction(item, action, provider) {
    const id = current.id;
    const result = await requestJson(`/api/dig/${id}/items/${item.id}`, { method: "POST", body: JSON.stringify({ action, provider, playlist_id: $("dig-destination").value || null }) });
    if (action === "download") {
      queued.set(item.id, result.job_id);
      showToast("Morceau ajouté.");
      await refreshQueue();
      await refreshLibrary();
    } else if (current?.id === id) {
      item.feedback = action === "reset" ? "" : action;
    }
    resultSignature = "";
    paintResults();
  }
  function paintResults() {
    const signature = JSON.stringify([current?.results, $("dig-filter").value, $("dig-text-filter").value, $("dig-available").checked,
      $("dig-destination").value, libraryState.tracks.map(t => t.job_id), liveQueueTracks.map(t => [t.id, t.state]), [...queued]]);
    if (signature === resultSignature) return;
    resultSignature = signature;
    const area = $("dig-results");
    area.replaceChildren();
    if (!current?.results) return;
    const filter = $("dig-filter").value, query = $("dig-text-filter").value.toLocaleLowerCase();
    const rows = current.results.filter(t => (filter === "all" ? t.feedback !== "rejected" : t.feedback === filter)
      && (!$("dig-available").checked || t.matches.length) && `${t.title} ${t.artist}`.toLocaleLowerCase().includes(query));
    for (const item of rows) {
      const row = el("article", "dig-result");
      const cover = el("div", "dig-cover");
      cover.append(el("span", "", item.title.slice(0, 1)));
      if (item.cover_url?.startsWith("https://")) {
        const image = document.createElement("img"); image.alt = ""; image.loading = "lazy"; image.src = item.cover_url;
        image.addEventListener("error", () => image.remove(), { once: true });
        cover.append(image);
      }
      const info = el("div", "dig-result-info");
      info.append(el("strong", "", item.title), el("span", "dig-artist", item.artist));
      const match = item.matches[0];
      const metadata = match ? [match.album, match.label, match.bpm && `${match.bpm} BPM`, match.key, match.duration].filter(Boolean).join(" · ") : item.album;
      if (metadata) info.append(el("span", "dig-meta", metadata));
      const why = document.createElement("details");
      why.append(el("summary", "", item.reasons.join(" · ")));
      why.append(el("p", "", item.engines.join(" + ")));
      for (const ref of item.references) why.append(el("p", "", ref));
      info.append(why);
      const actions = el("div", "dig-result-actions");
      const state = downloadState(item);
      if (state.track?.streamable) {
        const play = command(`Lire ${item.title}`, "play", () => streamTrack(state.track));
        play.className = "play-button";
        actions.append(play);
      }
      const keep = command(item.feedback === "kept" ? "Retirer des favoris" : "Garder", "heart", () => itemAction(item, item.feedback === "kept" ? "reset" : "kept"));
      keep.dataset.active = String(item.feedback === "kept");
      keep.setAttribute("aria-pressed", String(item.feedback === "kept"));
      actions.append(keep, command(item.feedback === "rejected" ? "Rétablir" : "Ignorer", "close", () => itemAction(item, item.feedback === "rejected" ? "reset" : "rejected")));
      for (const m of item.matches) {
        const link = makeSourceBadge(m);
        if (link) actions.append(link);
      }
      const download = el("button", "primary-button dig-download");
      download.type = "button";
      download.textContent = state.state === "done" ? ($("dig-destination").value ? "Ajouter à la playlist" : "Dans Mes sons") :
        state.state === "running" ? "En cours" : state.state === "pending" ? "En attente" : state.state === "failed" ? "Réessayer" :
        item.matches.length ? "Télécharger" : item.checked ? "Indisponible" : "Vérification…";
      download.disabled = !item.matches.length || ["pending", "running"].includes(state.state) || (state.state === "done" && !$("dig-destination").value);
      download.addEventListener("click", async () => {
        download.disabled = true;
        try { await itemAction(item, "download", item.matches[0].provider); }
        catch (error) { showToast(error.message, "error"); download.disabled = false; }
      });
      actions.append(download);
      const deeper = el("button", "secondary-button dig-deeper", "Creuser");
      deeper.type = "button";
      deeper.addEventListener("click", () => {
        references = [{ kind: "suggestion", id: `${current.id}:${item.id}`, name: `${item.artist} · ${item.title}` }];
        $("dig-name").value = item.title.slice(0, 80);
        current = null; generation++; paintReferences(); paint(); refresh();
      });
      actions.append(deeper);
      if (!item.matches.length && item.source_url?.startsWith("https://musicbrainz.org/recording/")) {
        const link = el("a", "dig-meta", "MusicBrainz"); link.href = item.source_url; link.target = "_blank"; link.rel = "noopener noreferrer"; actions.append(link);
      }
      row.append(cover, info, actions);
      area.append(row);
    }
    if (!rows.length) area.append(el("p", "empty-copy", ["running", "pending"].includes(current.state) ? "Recherche en cours…" : "Aucune suggestion dans cette vue."));
  }
  function close() {
    active = false; generation++;
    clearInterval(timer); timer = null;
    $("dig-panel").hidden = true;
    $("library-content").hidden = false;
    document.querySelector(".search-section").hidden = false;
    $("dig-nav").dataset.selected = "false";
  }
  function open() {
    if (active) return;
    active = true;
    $("dig-panel").hidden = false;
    $("library-content").hidden = true;
    document.querySelector(".search-section").hidden = true;
    $("playlist-imports").hidden = true;
    sync(); paintReferences(); refresh();
    timer = setInterval(refresh, 3500);
  }
  $("dig-nav").addEventListener("click", open);
  document.querySelector(".library-sidebar").addEventListener("click", event => {
    const button = event.target.closest(".playlist-filter, .tag-filter");
    if (button && button.id !== "dig-nav" && !button.classList.contains("folder-toggle") && active) close();
  }, true);
  $("dig-new").addEventListener("click", () => { current = null; generation++; references = []; paintReferences(); paint(); refresh(); });
  $("dig-edit").addEventListener("click", () => {
    references = [...(current.config?.references || [])];
    $("dig-name").value = current.name;
    document.querySelector(`[name="dig-mode"][value="${current.config.mode}"]`).checked = true;
    current = null; generation++; paintReferences(); paint(); refresh();
  });
  $("dig-sessions").addEventListener("change", () => { generation++; current = $("dig-sessions").value ? { id: $("dig-sessions").value } : null; refresh(); });
  $("dig-add").addEventListener("click", () => { selection = new Map(references.map(r => [`${r.kind}:${r.id}`, r])); paintPicker(); $("dig-picker").showModal(); });
  $("dig-picker-close").append(icon("close"));
  $("dig-picker-close").addEventListener("click", () => $("dig-picker").close());
  $("dig-picker-done").addEventListener("click", () => { references = [...selection.values()]; paintReferences(); $("dig-picker").close(); });
  $("dig-picker-kind").addEventListener("change", paintPicker);
  $("dig-picker-query").addEventListener("input", paintPicker);
  $("dig-form").addEventListener("submit", async event => {
    event.preventDefault();
    $("dig-generate").disabled = true;
    try {
      current = await requestJson("/api/dig", { method: "POST", body: JSON.stringify({ name: $("dig-name").value,
        references, mode: document.querySelector('[name="dig-mode"]:checked').value }) });
      await refresh();
    } catch (error) { showToast(error.message, "error"); }
    finally { $("dig-generate").disabled = !references.length; }
  });
  $("dig-rename").append(icon("pencil"));
  $("dig-delete").append(icon("trash"));
  async function update(payload, method = "PATCH") {
    try { await requestJson(`/api/dig/${current.id}`, { method, body: JSON.stringify(payload) });
      if (method === "DELETE") { current = null; generation++; }
      await refresh();
    } catch (error) { showToast(error.message, "error"); }
  }
  $("dig-rename").addEventListener("click", () => { const name = prompt("Nom de la recherche", current.name); if (name) update({ name }); });
  $("dig-delete").addEventListener("click", () => { if (confirm("Supprimer cette recherche ? Les sons téléchargés seront conservés.")) update({}, "DELETE"); });
  for (const action of ["more", "retry", "cancel"]) $("dig-" + action).addEventListener("click", () => update({ action }));
  for (const id of ["dig-filter", "dig-available", "dig-destination"]) $(id).addEventListener("change", paintResults);
  $("dig-text-filter").addEventListener("input", paintResults);
  $("dig-create-playlist").addEventListener("click", async () => {
    const name = prompt("Nom de la playlist", current?.name || ""); if (!name) return;
    try { const result = await requestJson("/api/playlists", { method: "POST", body: JSON.stringify({ name }) });
      await refreshLibrary(); sync(); $("dig-destination").value = result.playlist.id; paintResults();
    } catch (error) { showToast(error.message, "error"); }
  });
  return { get active() { return active; }, open, sync, reset() { close(); current = null; references = []; queued.clear(); $("dig-picker").close(); } };
})();
