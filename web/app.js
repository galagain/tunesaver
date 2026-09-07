"use strict";

const providers = ["tiddl", "beatport", "qobuz"];
const providerLabels = { tiddl: "TIDAL", beatport: "Beatport", qobuz: "Qobuz" };
const stateLabels = {
  pending: "En attente",
  running: "En cours",
  done: "Terminé",
  failed: "Échec",
};
const kindLabels = {
  album: "l’album complet",
  playlist: "la playlist complète",
  mix: "le mix complet",
  release: "la release complète",
  chart: "le chart complet",
};
const downloadButtonByJob = new Map();

const authView = document.querySelector("#auth-view");
const authForm = document.querySelector("#auth-form");
const authEmail = document.querySelector("#auth-email");
const authPassword = document.querySelector("#auth-password");
const authCode = document.querySelector("#auth-code");
const signupVerification = document.querySelector("#signup-verification");
const sendVerificationCode = document.querySelector("#send-verification-code");
const authSubmit = document.querySelector("#auth-submit");
const authToggle = document.querySelector("#auth-toggle");
const forgotPasswordButton = document.querySelector("#forgot-password-button");
const authIntro = document.querySelector("#auth-intro");
const authMessage = document.querySelector("#auth-message");
const appView = document.querySelector("#app-view");
const adminView = document.querySelector("#admin-view");
const services = document.querySelector("#services");
const logoutButton = document.querySelector("#logout-button");
const historyToggle = document.querySelector("#history-toggle");
const userEmail = document.querySelector("#user-email");
const form = document.querySelector("#search-form");
const input = document.querySelector("#search-input");
const searchButton = document.querySelector("#search-button");
const importPlaylistButton = document.querySelector("#import-playlist-button");
const importPanel = document.querySelector("#import-panel");
const importLinks = document.querySelector("#import-links");
const importCancel = document.querySelector("#import-cancel");
const importSubmit = document.querySelector("#import-submit");
const searchMessage = document.querySelector("#search-message");
const resultsSection = document.querySelector("#results-section");
const resultsQuery = document.querySelector("#results-query");
const queueSection = document.querySelector("#queue-section");
const refreshStatusButton = document.querySelector("#refresh-status");
const refreshLibraryButton = document.querySelector("#refresh-library");
const createPlaylistButton = document.querySelector("#create-playlist");
const createTagButton = document.querySelector("#create-tag");
const playlistList = document.querySelector("#playlist-list");
const tagList = document.querySelector("#tag-list");
const libraryTracks = document.querySelector("#library-tracks");
const libraryCount = document.querySelector("#library-count");
const playerBar = document.querySelector("#player-bar");
const playerCover = document.querySelector("#player-cover");
const playerTitle = document.querySelector("#player-title");
const playerArtist = document.querySelector("#player-artist");
const audioPlayer = document.querySelector("#audio-player");
const adminRefreshButton = document.querySelector("#admin-refresh");
const adminMessage = document.querySelector("#admin-message");
const adminUsers = document.querySelector("#admin-users");
const adminUsersCount = document.querySelector("#admin-users-count");
const adminHistory = document.querySelector("#admin-history");
const adminHistoryCount = document.querySelector("#admin-history-count");
const adminChart = document.querySelector("#admin-chart");
const adminChartTotal = document.querySelector("#admin-chart-total");
const adminChartTracks = document.querySelector("#admin-chart-tracks");
const adminChartSavings = document.querySelector("#admin-chart-savings");
const savingsTotal = document.querySelector("#savings-total");
const toast = document.querySelector("#toast");

let publicConfig = null;
let session = null;
let adminSession = null;
let authMode = "login";
let toastTimer = null;
let queueTimer = null;
let queueRefreshPromise = null;
let selectedAdminUserId = "";
let adminChartMode = "tracks";
let adminDailyStats = [];
let selectedPlaylistId = "";
let selectedTagId = "";
let onlyUnassigned = false;
let draggedPlaylist = false;
let libraryState = { tracks: [], playlists: [], tags: [] };
let liveQueueTracks = [];
let renderedTracks = [];
let currentTrackId = "";
let draggedTrackTag = null;

document.addEventListener("dragover", (event) => {
  if (!draggedTrackTag) return;
  event.preventDefault();
  event.dataTransfer.dropEffect = "move";
});

document.addEventListener("drop", async (event) => {
  if (!draggedTrackTag) return;
  event.preventDefault();
  const { tagId, jobId } = draggedTrackTag;
  draggedTrackTag = null;
  if (event.target.closest(".library-track")?.dataset.jobId === jobId) return;
  await removeTrackTag(tagId, jobId);
});

async function removeTrackTag(tagId, jobId) {
  try {
    await requestJson(`/api/tags/${tagId}/tracks/${jobId}`, { method: "DELETE" });
    await refreshLibrary();
  } catch (error) { showToast(error.message, "error"); }
}
const coverUrlCache = new Map();
let historyWarningShown = false;
let lastHistoryFingerprint = "";
const activeProviders = { tiddl: null, beatport: null, qobuz: null };

function platformElements(provider) {
  return {
    count: document.querySelector(`#${provider}-count`),
    state: document.querySelector(`#${provider}-state`),
    results: document.querySelector(`#${provider}-results`),
  };
}

function setServiceStatus(provider, active) {
  const service = document.querySelector(`#${provider}-service`);
  const badge = document.querySelector(`#${provider}-health`);
  if (!service || !badge) return;
  const label = providerLabels[provider];
  service.dataset.status = active ? "active" : "inactive";
  service.title = active ? `${label} fonctionne` : `${label} est arrêté`;
  badge.textContent = active ? "✓" : "×";
  badge.setAttribute("aria-label", service.title);
}

function showToast(message, kind = "success") {
  window.clearTimeout(toastTimer);
  toast.textContent = message;
  toast.dataset.kind = kind;
  toast.hidden = false;
  toastTimer = window.setTimeout(() => { toast.hidden = true; }, 4200);
}

async function parseResponse(response) {
  let payload = {};
  try { payload = await response.json(); } catch (_error) { payload = {}; }
  if (!response.ok) {
    throw new Error(payload.msg || payload.message || payload.error_description || payload.error || `Erreur HTTP ${response.status}`);
  }
  return payload;
}

async function loadConfig() {
  const response = await fetch("/api/config", { headers: { Accept: "application/json" } });
  publicConfig = await parseResponse(response);
}

async function localAuth(path, body = {}, accessToken = "") {
  const response = await fetch(path, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}),
    },
    body: JSON.stringify(body),
  });
  return parseResponse(response);
}

async function publicJson(path, body = {}) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return parseResponse(response);
}

function saveSession(value) {
  session = value;
  if (value) {
    localStorage.setItem("tunesaver.session", JSON.stringify(value));
  } else {
    onlyUnassigned = false;
    localStorage.removeItem("tunesaver.session");
  }
}

function saveAdminSession(value) {
  adminSession = value;
  if (value) {
    localStorage.setItem("tunesaver.adminSession", JSON.stringify(value));
  } else {
    localStorage.removeItem("tunesaver.adminSession");
  }
}

async function refreshSession() {
  if (!session?.refresh_token) throw new Error("Session expirée");
  const refreshed = await localAuth(
    "/api/session/refresh",
    { refresh_token: session.refresh_token }
  );
  saveSession(refreshed);
  return refreshed;
}

async function adminFetch(url, options = {}) {
  if (!adminSession?.access_token) throw new Error("Connexion admin requise");
  const response = await fetch(url, {
    ...options,
    headers: {
      ...(options.headers || {}),
      Authorization: `Bearer ${adminSession.access_token}`,
    },
  });
  if (response.status === 401) {
    saveAdminSession(null);
    showAuth("Session admin expirée.");
  }
  return response;
}

async function adminJson(path, options = {}) {
  const response = await adminFetch(path, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
  });
  return parseResponse(response);
}

async function authenticatedFetch(url, options = {}, retry = true) {
  if (!session?.access_token) throw new Error("Connexion requise");
  const response = await fetch(url, {
    ...options,
    headers: {
      ...(options.headers || {}),
      Authorization: `Bearer ${session.access_token}`,
    },
  });
  if (response.status === 401 && retry) {
    try {
      await refreshSession();
      return authenticatedFetch(url, options, false);
    } catch (_error) {
      showAuth();
      throw new Error("Session expirée. Reconnecte-toi.");
    }
  }
  return response;
}

async function requestJson(path, options = {}) {
  const response = await authenticatedFetch(path, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
  });
  return parseResponse(response);
}

async function loadPrivateImage(image, url) {
  if (coverUrlCache.has(url)) {
    image.src = coverUrlCache.get(url);
    return;
  }
  try {
    const response = await authenticatedFetch(url, {
      headers: { Accept: "image/avif,image/webp,image/png,image/jpeg,*/*;q=0.8" },
    });
    if (!response.ok) return;
    const blob = await response.blob();
    const objectUrl = URL.createObjectURL(blob);
    coverUrlCache.set(url, objectUrl);
    image.src = objectUrl;
  } catch (_error) {
    image.remove();
  }
}

function resetPlayer() {
  audioPlayer.pause();
  audioPlayer.removeAttribute("src");
  audioPlayer.load();
  currentTrackId = "";
  playerBar.hidden = true;
}

function clearCoverCache() {
  for (const objectUrl of coverUrlCache.values()) URL.revokeObjectURL(objectUrl);
  coverUrlCache.clear();
}

function showAuth(message = "") {
  window.clearInterval(queueTimer);
  queueTimer = null;
  saveSession(null);
  saveAdminSession(null);
  authView.hidden = false;
  appView.hidden = true;
  adminView.hidden = true;
  services.hidden = true;
  logoutButton.hidden = true;
  historyToggle.hidden = true;
  queueSection.hidden = true;
  resetPlayer();
  clearCoverCache();
  authMessage.textContent = message;
}

function showApp() {
  saveAdminSession(null);
  authView.hidden = true;
  appView.hidden = false;
  adminView.hidden = true;
  services.hidden = false;
  logoutButton.hidden = false;
  historyToggle.hidden = false;
  queueSection.hidden = true;
  historyToggle.dataset.open = "false";
  historyToggle.setAttribute("aria-expanded", "false");
  historyToggle.textContent = "Historique";
  userEmail.textContent = session?.user?.email || "Mon compte";
  refreshQueue();
  refreshLibrary();
  window.clearInterval(queueTimer);
  queueTimer = window.setInterval(refreshQueue, 5000);
}

function showAdmin() {
  window.clearInterval(queueTimer);
  queueTimer = null;
  saveSession(null);
  authView.hidden = true;
  appView.hidden = true;
  adminView.hidden = false;
  services.hidden = true;
  logoutButton.hidden = false;
  historyToggle.hidden = true;
  queueSection.hidden = true;
  resetPlayer();
  clearCoverCache();
  userEmail.textContent = "Admin";
  refreshAdmin();
}

authToggle.addEventListener("click", () => {
  authMode = authMode === "login" ? "signup" : "login";
  const signup = authMode === "signup";
  authSubmit.textContent = signup ? "Créer mon compte" : "Se connecter";
  authToggle.textContent = signup ? "J’ai déjà un compte" : "Créer un compte";
  authIntro.textContent = signup
    ? "Crée ton espace personnel sécurisé."
    : "Connecte-toi pour accéder à ton espace.";
  authPassword.autocomplete = signup ? "new-password" : "current-password";
  signupVerification.hidden = !signup;
  authCode.required = signup;
  authCode.value = "";
  authMessage.textContent = "";
});

async function sendSignupVerificationCode() {
  const email = authEmail.value.trim();
  if (!email) {
    authMessage.textContent = "Entre ton e-mail avant de demander le code.";
    authEmail.focus();
    return;
  }
  if (!authPassword.value) {
    authMessage.textContent = "Entre un mot de passe avant de demander le code.";
    authPassword.focus();
    return;
  }
  try {
    const result = await publicJson("/api/signup", { email, password: authPassword.value });
    authMessage.textContent = result.message || "Code envoyé par e-mail.";
  } catch (error) {
    if (!/existe/i.test(error.message)) throw error;
    const result = await publicJson("/api/email/resend", { email });
    authMessage.textContent = result.message || "Nouveau code envoyé par e-mail.";
  }
  authCode.focus();
}

async function forgotPasswordFlow() {
  const email = window.prompt("E-mail du compte", authEmail.value.trim());
  if (!email) return;
  const request = await publicJson("/api/password-reset/request", { email });
  authMessage.textContent = request.message;
  const code = window.prompt("Code reçu par e-mail");
  if (!code) return;
  const password = window.prompt("Nouveau mot de passe");
  if (!password) return;
  const result = await publicJson("/api/password-reset/confirm", { email, code, password });
  authMessage.textContent = result.message || "Mot de passe mis à jour.";
}

forgotPasswordButton.addEventListener("click", async () => {
  forgotPasswordButton.disabled = true;
  try {
    await forgotPasswordFlow();
  } catch (error) {
    authMessage.textContent = error.message;
  } finally {
    forgotPasswordButton.disabled = false;
  }
});

sendVerificationCode.addEventListener("click", async () => {
  sendVerificationCode.disabled = true;
  authMessage.textContent = "";
  try {
    await sendSignupVerificationCode();
  } catch (error) {
    authMessage.textContent = error.message;
  } finally {
    sendVerificationCode.disabled = false;
  }
});

authForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  authSubmit.disabled = true;
  authMessage.textContent = "";
  try {
    const email = authEmail.value.trim();
    if (authMode === "login" && email === "admin") {
      const response = await fetch("/api/admin/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password: authPassword.value }),
      });
      const result = await parseResponse(response);
      saveAdminSession(result);
      authPassword.value = "";
      showAdmin();
      return;
    }
    if (authMode === "signup") {
      const code = authCode.value.trim();
      if (!code) {
        authMessage.textContent = "Clique sur Vérification d’e-mail, puis entre le code reçu.";
        authCode.focus();
        return;
      }
      const result = await publicJson("/api/email/verify", { email, code });
      authMessage.textContent = result.message || "Compte créé. Il est en attente d'approbation.";
      authPassword.value = "";
      authCode.value = "";
      return;
    }
    const result = await localAuth("/api/login", {
      email,
      password: authPassword.value,
    });
    if (!result.access_token) {
      authMessage.textContent = result.message || "Compte créé. Il est en attente d'approbation.";
      authPassword.value = "";
      return;
    }
    saveSession(result);
    const accessCheck = await authenticatedFetch("/api/status", { method: "GET" });
    if (!accessCheck.ok) await parseResponse(accessCheck);
    authPassword.value = "";
    showApp();
  } catch (error) {
    saveSession(null);
    authMessage.textContent = error.message;
  } finally {
    authSubmit.disabled = false;
  }
});

logoutButton.addEventListener("click", async () => {
  if (adminSession?.access_token) {
    showAuth("Tu es déconnecté.");
    return;
  }
  if (session?.access_token) {
    try { await localAuth("/api/logout", {}, session.access_token); } catch (_error) { /* local logout still applies */ }
  }
  showAuth("Tu es déconnecté.");
});

function setPlatformLoading(provider) {
  const elements = platformElements(provider);
  elements.results.replaceChildren();
  elements.count.textContent = "";
  elements.state.className = "panel-state loading-state";
  elements.state.textContent = `Recherche sur ${providerLabels[provider]}…`;
}

function setPlatformError(provider, message) {
  const elements = platformElements(provider);
  elements.results.replaceChildren();
  elements.count.textContent = "";
  elements.state.className = "panel-state error-state";
  elements.state.textContent = message;
}

function resultCover(result) {
  const cover = document.createElement("div");
  cover.className = "result-cover";
  const fallback = document.createElement("span");
  fallback.textContent = (result.title || result.platform || "?").trim().slice(0, 1).toUpperCase() || "?";
  cover.append(fallback);
  if (result.cover_url) {
    const image = document.createElement("img");
    image.alt = "";
    image.loading = "lazy";
    image.src = result.cover_url;
    image.addEventListener("load", () => { cover.dataset.loaded = "true"; });
    cover.append(image);
  }
  return cover;
}

function resultInfo(label, value) {
  const item = document.createElement("span");
  item.className = "result-info";
  item.textContent = value ? `${label}: ${value}` : `${label}: -`;
  return item;
}

function makeResultCard(result) {
  const card = document.createElement("article");
  card.className = "result-card";
  card.append(resultCover(result));
  const body = document.createElement("div");
  body.className = "result-body";
  const meta = document.createElement("div");
  meta.className = "result-meta";
  const kind = document.createElement("span");
  kind.className = "kind-chip";
  kind.textContent = result.kind === "track" ? "Morceau" : result.kind;
  meta.append(kind);
  const title = document.createElement("h4");
  title.textContent = result.title || "Sans titre";
  const artist = document.createElement("p");
  artist.className = "result-artist";
  artist.textContent = result.artist || result.platform;
  const details = document.createElement("p");
  details.className = "result-details";
  details.append(
    resultInfo("Album", result.album),
    resultInfo("Label", result.label),
    resultInfo("Mix", result.mix),
    resultInfo("Qualité", result.quality),
    resultInfo("BPM", result.bpm),
    resultInfo("Clé", result.key),
    resultInfo("Durée", result.duration)
  );
  body.append(meta, title, artist, details);

  const actions = document.createElement("div");
  actions.className = "result-actions";
  const source = document.createElement("a");
  source.className = "source-link";
  source.href = result.source_url;
  source.target = "_blank";
  source.rel = "noopener noreferrer";
  source.textContent = "Ouvrir";
  const download = document.createElement("button");
  download.className = "download-button";
  download.type = "button";
  download.dataset.provider = result.provider;
  const completeLabel = kindLabels[result.kind] || "la ressource complète";
  const isBlockedBatch = ["artist", "label"].includes(result.kind);
  const defaultLabel = result.requires_confirmation ? `Télécharger ${completeLabel}` : "Télécharger";
  download.dataset.defaultLabel = defaultLabel;
  download.textContent = defaultLabel;
  if (isBlockedBatch) {
    download.disabled = true;
    download.textContent = "Non disponible";
  } else if (activeProviders[result.provider] === false) {
    download.disabled = true;
    download.textContent = "Service arrêté";
  }
  download.addEventListener("click", async () => {
    if (result.requires_confirmation && !window.confirm(`Confirmer le téléchargement de ${completeLabel} ?`)) return;
    download.disabled = true;
    download.textContent = "Préparation…";
    try {
      const response = await requestJson("/api/download", {
        method: "POST",
        body: JSON.stringify({
          provider: result.provider,
          url: result.download_url,
          title: result.title,
          artist: result.artist,
          confirm_complete: result.requires_confirmation === true,
        }),
      });
      download.dataset.jobId = response.job_id;
      downloadButtonByJob.set(response.job_id, download);
      download.textContent = response.duplicate ? "Déjà en file" : "En attente";
      showToast("Le son est ajouté dans Mes sons.");
      await refreshQueue();
      selectedPlaylistId = "";
      selectedTagId = "";
      renderLibrary();
    } catch (error) {
      download.disabled = false;
      download.textContent = "Réessayer";
      showToast(error.message, "error");
    }
  });
  actions.append(source, download);
  card.append(body, actions);
  return card;
}

function renderPlatform(provider, results) {
  const elements = platformElements(provider);
  elements.results.replaceChildren();
  elements.count.textContent = results.length ? `${results.length} résultat${results.length > 1 ? "s" : ""}` : "";
  if (!results.length) {
    elements.state.className = "panel-state";
    elements.state.textContent = "Aucun morceau trouvé.";
    return;
  }
  elements.state.textContent = "";
  elements.state.className = "panel-state hidden-state";
  const fragment = document.createDocumentFragment();
  for (const result of results) fragment.append(makeResultCard(result));
  elements.results.append(fragment);
}

function looksLikeDirectLink(value) {
  return /^https:\/\//i.test(value)
    || /^(track|video|album|artist)\/[0-9]+$/i.test(value)
    || /^(playlist|mix)\/[A-Za-z0-9-]+$/i.test(value);
}

async function searchProvider(query, provider) {
  try {
    const payload = await requestJson("/api/search", {
      method: "POST",
      body: JSON.stringify({ query, provider }),
    });
    renderPlatform(provider, payload.results || []);
  } catch (error) {
    setPlatformError(provider, error.message);
  }
}

async function searchDirect(query) {
  for (const provider of providers) setPlatformLoading(provider);
  try {
    const payload = await requestJson("/api/search", {
      method: "POST",
      body: JSON.stringify({ query }),
    });
    for (const provider of providers) {
      renderPlatform(provider, (payload.results || []).filter((result) => result.provider === provider));
    }
  } catch (error) {
    for (const provider of providers) setPlatformError(provider, error.message);
  }
}

async function enqueueList(links) {
  if (!window.confirm(`Ajouter ${links.length} liens à la file de téléchargement ?`)) return;
  const playlists = links.filter(isImportPlaylist);
  const direct = links.filter((link) => !isImportPlaylist(link));
  for (const url of playlists) {
    await requestJson("/api/playlist-imports", { method: "POST", body: JSON.stringify({ url }) });
  }
  if (direct.length) {
    await requestJson("/api/downloads", {
      method: "POST",
      body: JSON.stringify({ links: direct, confirm_complete: true }),
    });
  }
  showToast(playlists.length ? "Import de playlist lancé." : "Téléchargements ajoutés.");
  resultsSection.hidden = true;
  await refreshQueue();
  await refreshLibrary();
}

function playlistLinksFromText(value) {
  return value.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
}

function isImportPlaylist(value) {
  try {
    const url = new URL(value);
    return (url.hostname === "open.spotify.com" && url.pathname.includes("/playlist/"))
      || (["soundcloud.com", "www.soundcloud.com"].includes(url.hostname) && url.pathname.includes("/sets/"))
      || (["beatport.com", "www.beatport.com"].includes(url.hostname) && /\/(playlists?|charts?)\//.test(url.pathname));
  } catch (_) { return false; }
}

importPlaylistButton.addEventListener("click", () => {
  importPanel.hidden = !importPanel.hidden;
  if (!importPanel.hidden) importLinks.focus();
});

importCancel.addEventListener("click", () => {
  importPanel.hidden = true;
  importLinks.value = "";
});

importSubmit.addEventListener("click", async () => {
  const links = playlistLinksFromText(importLinks.value);
  if (!links.length) {
    searchMessage.textContent = "Colle au moins un lien à importer.";
    importLinks.focus();
    return;
  }
  importSubmit.disabled = true;
  searchMessage.textContent = "";
  try {
    await enqueueList(links);
    importPanel.hidden = true;
    importLinks.value = "";
  } catch (error) {
    searchMessage.textContent = error.message;
  } finally {
    importSubmit.disabled = false;
  }
});

input.addEventListener("keydown", (event) => {
  if (event.key !== "Enter" || event.shiftKey) return;
  event.preventDefault();
  form.requestSubmit();
});

document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape" || appView.hidden || resultsSection.hidden) return;
  resultsSection.hidden = true;
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const query = input.value.trim();
  const lines = query.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
  if (!query) {
    searchMessage.textContent = "Saisis un titre, un artiste ou un lien.";
    input.focus();
    return;
  }
  searchMessage.textContent = "";
  searchButton.disabled = true;
  searchButton.textContent = "Patiente…";
  try {
    if (lines.length > 1 || isImportPlaylist(query)) {
      await enqueueList(lines);
      return;
    }
    resultsSection.hidden = false;
    resultsQuery.textContent = `“${query.length > 64 ? `${query.slice(0, 61)}…` : query}”`;
    if (looksLikeDirectLink(query)) {
      await searchDirect(query);
    } else {
      for (const provider of providers) setPlatformLoading(provider);
      await Promise.allSettled(providers.map((provider) => searchProvider(query, provider)));
    }
  } catch (error) {
    searchMessage.textContent = error.message;
  } finally {
    searchButton.disabled = false;
    searchButton.textContent = "Rechercher";
  }
});

function totalForState(counts, state) {
  return providers.reduce((total, provider) => total + (counts[provider]?.[state] || 0), 0);
}

async function syncHistory(items) {
  if (!items.length) return;
  const rows = items.map((job) => ({
    job_id: job.id,
    provider: job.provider,
    source_url: job.source_url || "",
    title: job.title || "",
    artist: job.artist || "",
    resource_kind: job.kind || "track",
    state: job.state,
    track_count: job.track_count || 0,
    estimated_savings_cents: job.estimated_savings_cents || 0,
    created_at: job.created_at || new Date().toISOString(),
    completed_at: job.completed_at || null,
    delivered_at: job.delivered_at || null,
  }));
  const fingerprint = JSON.stringify(rows);
  if (fingerprint === lastHistoryFingerprint) return;
  await requestJson("/api/history", {
    method: "POST",
    body: JSON.stringify({ history: rows }),
  });
  lastHistoryFingerprint = fingerprint;
}

function updateDownloadButtons(jobs) {
  const activeJobIds = new Set();
  for (const job of jobs) {
    if (!job?.id) continue;
    activeJobIds.add(job.id);
    const button = downloadButtonByJob.get(job.id);
    if (!button) continue;
    button.textContent = stateLabels[job.state] || job.state || "En file";
    button.disabled = job.state !== "failed";
    if (job.state === "failed") button.textContent = "Réessayer";
  }
  for (const [jobId, button] of downloadButtonByJob.entries()) {
    if (activeJobIds.has(jobId)) continue;
    if (button.textContent !== "Terminé") button.textContent = "Terminé";
    button.disabled = true;
  }
}

async function fetchHistory() {
  const history = [];
  const pageSize = 500;
  for (let offset = 0; ; offset += pageSize) {
    const payload = await requestJson(`/api/history?limit=${pageSize}&offset=${offset}`, {
      method: "GET",
    });
    const page = payload.history || [];
    history.push(...page);
    if (page.length < pageSize) return history;
  }
}

async function downloadReadyJob(jobId) {
  const response = await requestJson("/api/download-ticket", {
    method: "POST",
    body: JSON.stringify({ job_id: jobId }),
  });
  window.location.assign(response.url);
  window.setTimeout(refreshQueue, 2500);
}

function setPlayerCover(track) {
  playerCover.replaceChildren();
  const fallback = document.createElement("span");
  fallback.textContent = (track.title || track.file_name || "?").trim().slice(0, 1).toUpperCase() || "?";
  playerCover.append(fallback);
  playerCover.dataset.loaded = "false";
  if (!track.cover_url) return;
  const image = document.createElement("img");
  image.alt = "";
  if (coverUrlCache.has(track.cover_url)) playerCover.dataset.loaded = "true";
  image.addEventListener("load", () => { playerCover.dataset.loaded = "true"; });
  playerCover.append(image);
  loadPrivateImage(image, track.cover_url);
}

function setPlayerTrack(track) {
  currentTrackId = track.job_id;
  playerTitle.textContent = track.title || track.file_name || track.job_id;
  playerArtist.textContent = track.artist || providerLabels[track.provider] || "TuneSaver";
  setPlayerCover(track);
  playerBar.hidden = false;
}

async function streamTrack(track) {
  const jobId = typeof track === "string" ? track : track.job_id;
  const fullTrack = typeof track === "string"
    ? renderedTracks.find((item) => item.job_id === track) || { job_id: track }
    : track;
  const response = await requestJson("/api/stream-ticket", {
    method: "POST",
    body: JSON.stringify({ job_id: jobId }),
  });
  setPlayerTrack(fullTrack);
  audioPlayer.src = response.url;
  await audioPlayer.play();
}

async function playNextTrack() {
  const currentIndex = renderedTracks.findIndex((track) => track.job_id === currentTrackId);
  const next = renderedTracks
    .slice(Math.max(0, currentIndex + 1))
    .find((track) => track.streamable);
  if (!next) return;
  try {
    await streamTrack(next);
  } catch (error) {
    showToast(error.message, "error");
  }
}

async function fetchLibrary() {
  return requestJson("/api/library", { method: "GET" });
}

function trackInPlaylist(track, playlistId) {
  if (!playlistId) return true;
  const playlist = libraryState.playlists.find((item) => item.id === playlistId);
  return playlist ? playlist.track_ids.includes(track.job_id) : true;
}

function trackInTag(track, tagId) {
  if (!tagId) return true;
  const tag = libraryState.tags.find((item) => item.id === tagId);
  return tag ? tag.track_ids.includes(track.job_id) : true;
}

function currentLibraryTitle() {
  if (onlyUnassigned) return "Sons sans playlist";
  if (selectedPlaylistId) {
    const playlist = libraryState.playlists.find((item) => item.id === selectedPlaylistId);
    return playlist ? playlist.name : "Mes playlists";
  }
  if (selectedTagId) {
    const tag = libraryState.tags.find((item) => item.id === selectedTagId);
    return tag ? `# ${tag.name}` : "Mes tags";
  }
  return "Mes sons";
}

function liveTracksForLibrary() {
  if (selectedTagId) return [];
  const existing = new Set(libraryState.tracks.map((track) => track.job_id));
  return liveQueueTracks
    .filter((job) => !existing.has(job.id) && ["pending", "running", "failed"].includes(job.state))
    .filter((job) => !selectedPlaylistId || trackInPlaylist({ job_id: job.id }, selectedPlaylistId))
    .map((job) => ({
      job_id: job.id,
      provider: job.provider,
      title: job.title || job.source_url || job.id,
      artist: job.artist || "",
      source_url: job.source_url || "",
      created_at: job.created_at || "",
      state: job.state,
      tags: [],
      streamable: false,
      download_ready: false,
      is_live: true,
    }));
}

function applyTagStyle(element, tag) {
  element.style.setProperty("--tag-color", tag.color || "#665cff");
}

function providerShortLabel(provider) {
  if (provider === "beatport") return "b";
  if (provider === "qobuz") return "Q";
  return "T";
}

function makeSourceBadge(track) {
  const label = providerLabels[track.provider] || track.provider || "Source";
  const element = track.source_url ? document.createElement("a") : document.createElement("span");
  element.className = `source-badge ${track.provider || ""}`;
  const template = document.getElementById(`icon-${track.provider}`);
  if (template) element.append(template.content.cloneNode(true));
  else element.textContent = providerShortLabel(track.provider);
  element.title = track.source_url ? `Ouvrir sur ${label}` : label;
  element.setAttribute("aria-label", element.title);
  if (track.source_url) {
    element.href = track.source_url;
    element.target = "_blank";
    element.rel = "noopener noreferrer";
  }
  return element;
}

async function addTagToTrack(tagId, jobId) {
  if (!tagId || !jobId) return;
  await requestJson(`/api/tags/${tagId}/tracks`, {
    method: "POST",
    body: JSON.stringify({ job_id: jobId }),
  });
  await refreshLibrary();
}

function makeRenameButton(item, collection) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "collection-rename";
  button.title = collection === "playlists" ? "Renommer la playlist" : collection === "playlist-folders" ? "Renommer le dossier" : "Renommer le tag";
  button.setAttribute("aria-label", `${button.title} : ${item.name}`);
  button.append(document.getElementById("icon-pencil").content.cloneNode(true));
  button.addEventListener("click", async () => {
    const value = window.prompt(button.title, item.name);
    if (value === null) return;
    const name = value.trim().replace(/\s+/g, " ");
    const limit = collection === "tags" ? 40 : 80;
    if (!name || [...name].length > limit) {
      showToast(`Le nom doit contenir entre 1 et ${limit} caractères.`, "error");
      return;
    }
    if (name === item.name) return;
    button.disabled = true;
    try {
      await requestJson(`/api/${collection}/${item.id}`, { method: "PATCH", body: JSON.stringify({ name }) });
      await refreshLibrary();
    } catch (error) { showToast(error.message, "error"); }
    finally { button.disabled = false; }
  });
  return button;
}

async function movePlaylist(playlistId, folderId) {
  try {
    await requestJson(`/api/playlists/${playlistId}/folder`, { method: "PATCH", body: JSON.stringify({ folder_id: folderId || null }) });
    await refreshLibrary();
  } catch (error) { showToast(error.message, "error"); }
}

function folderDropTarget(element, folderId) {
  element.addEventListener("dragover", (event) => {
    if (!Array.from(event.dataTransfer.types).includes("application/x-tunesaver-playlist")) return;
    event.preventDefault();
    element.dataset.drop = "true";
  });
  element.addEventListener("dragleave", () => { element.dataset.drop = "false"; });
  element.addEventListener("drop", (event) => {
    element.dataset.drop = "false";
    const id = event.dataTransfer.getData("application/x-tunesaver-playlist");
    if (!id) return;
    event.preventDefault();
    event.stopPropagation();
    movePlaylist(id, folderId);
  });
}

function renderPlaylists() {
  playlistList.replaceChildren();
  const containers = new Map();
  for (const folder of libraryState.folders || []) {
    const group = document.createElement("div");
    group.className = "playlist-folder";
    const header = document.createElement("div");
    header.className = "playlist-row folder-heading";
    const toggle = document.createElement("button");
    toggle.className = "playlist-filter folder-toggle";
    toggle.type = "button";
    toggle.setAttribute("aria-expanded", String(!folder.collapsed));
    const count = libraryState.playlists.filter((p) => p.folder_id === folder.id).length;
    toggle.textContent = `${folder.collapsed ? "▸" : "▾"} ${folder.name} (${count})`;
    const children = document.createElement("div");
    children.className = "folder-playlists";
    children.hidden = Boolean(folder.collapsed);
    children.id = `folder-${folder.id}`;
    toggle.setAttribute("aria-controls", children.id);
    toggle.addEventListener("click", async () => {
      toggle.disabled = true;
      try {
        await requestJson(`/api/playlist-folders/${folder.id}`, { method: "PATCH", body: JSON.stringify({ collapsed: !folder.collapsed }) });
        await refreshLibrary();
      } catch (error) { showToast(error.message, "error"); }
      finally { toggle.disabled = false; }
    });
    folderDropTarget(toggle, folder.id);
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "playlist-delete";
    remove.textContent = "×";
    remove.title = "Supprimer le dossier";
    remove.setAttribute("aria-label", `Supprimer le dossier ${folder.name}`);
    remove.addEventListener("click", async () => {
      if (!window.confirm(`Supprimer le dossier ${folder.name} ? Les playlists seront conservées.`)) return;
      try {
        await requestJson(`/api/playlist-folders/${folder.id}`, { method: "DELETE" });
        await refreshLibrary();
      } catch (error) { showToast(error.message, "error"); }
    });
    header.append(toggle, makeRenameButton(folder, "playlist-folders"), remove);
    group.append(header, children);
    playlistList.append(group);
    containers.set(folder.id, children);
  }
  const root = document.createElement("div");
  root.className = "root-playlists";
  folderDropTarget(root, null);
  playlistList.append(root);
  for (const playlist of libraryState.playlists) {
    const row = document.createElement("div");
    row.className = "playlist-row";
    const button = document.createElement("button");
    button.className = "playlist-filter";
    button.type = "button";
    button.dataset.playlistId = playlist.id;
    button.dataset.selected = playlist.id === selectedPlaylistId ? "true" : "false";
    button.textContent = `${playlist.name} (${playlist.track_ids.length})`;
    button.draggable = true;
    button.addEventListener("dragstart", (event) => {
      draggedPlaylist = true;
      event.dataTransfer.setData("application/x-tunesaver-playlist", playlist.id);
      event.dataTransfer.effectAllowed = "move";
    });
    button.addEventListener("dragend", () => { draggedPlaylist = false; renderLibrary(); });
    button.addEventListener("dragover", (event) => {
      if (!Array.from(event.dataTransfer.types).includes("application/x-tunesaver-job")) return;
      event.preventDefault();
      button.dataset.drop = "true";
    });
    button.addEventListener("dragleave", () => {
      button.dataset.drop = "false";
    });
    button.addEventListener("drop", async (event) => {
      event.preventDefault();
      button.dataset.drop = "false";
      const jobId = event.dataTransfer.getData("application/x-tunesaver-job");
      if (!jobId || playlist.track_ids.includes(jobId)) return;
      try {
        await requestJson(`/api/playlists/${playlist.id}/tracks`, {
          method: "POST",
          body: JSON.stringify({ job_id: jobId }),
        });
        selectedPlaylistId = playlist.id;
        selectedTagId = "";
        await refreshLibrary();
      } catch (error) {
        showToast(error.message, "error");
      }
    });
    button.addEventListener("click", () => {
      selectedPlaylistId = playlist.id;
      selectedTagId = "";
      renderLibrary();
    });
    const remove = document.createElement("button");
    remove.className = "playlist-delete";
    remove.type = "button";
    remove.textContent = "×";
    remove.addEventListener("click", async () => {
      if (!window.confirm(`Supprimer la playlist ${playlist.name} ?`)) return;
      await requestJson(`/api/playlists/${playlist.id}`, { method: "DELETE" });
      if (selectedPlaylistId === playlist.id) selectedPlaylistId = "";
      await refreshLibrary();
    });
    row.append(button, makeRenameButton(playlist, "playlists"), remove);
    (containers.get(playlist.folder_id) || root).append(row);
  }
  const all = document.querySelector(".playlist-filter[data-playlist-id='']");
  if (all) all.dataset.selected = selectedPlaylistId || selectedTagId || onlyUnassigned ? "false" : "true";
  document.querySelector("#unassigned-tracks").dataset.selected = String(onlyUnassigned);
}

function renderTags() {
  tagList.replaceChildren();
  for (const tag of libraryState.tags) {
    const row = document.createElement("div");
    row.className = "tag-row";
    const button = document.createElement("button");
    button.className = "tag-filter";
    button.type = "button";
    button.draggable = true;
    applyTagStyle(button, tag);
    button.dataset.tagId = tag.id;
    button.dataset.selected = tag.id === selectedTagId ? "true" : "false";
    button.textContent = `${tag.name} (${tag.track_ids.length})`;
    button.addEventListener("dragstart", (event) => {
      event.dataTransfer.effectAllowed = "copy";
      event.dataTransfer.setData("application/x-tunesaver-tag", tag.id);
      button.dataset.dragging = "true";
    });
    button.addEventListener("dragend", () => {
      button.dataset.dragging = "false";
    });
    button.addEventListener("dragover", (event) => {
      if (!Array.from(event.dataTransfer.types).includes("application/x-tunesaver-job")) return;
      event.preventDefault();
      button.dataset.drop = "true";
    });
    button.addEventListener("dragleave", () => {
      button.dataset.drop = "false";
    });
    button.addEventListener("drop", async (event) => {
      event.preventDefault();
      button.dataset.drop = "false";
      const jobId = event.dataTransfer.getData("application/x-tunesaver-job");
      if (!jobId || tag.track_ids.includes(jobId)) return;
      try {
        await addTagToTrack(tag.id, jobId);
        selectedTagId = tag.id;
        selectedPlaylistId = "";
      } catch (error) {
        showToast(error.message, "error");
      }
    });
    button.addEventListener("click", () => {
      selectedTagId = tag.id;
      selectedPlaylistId = "";
      renderLibrary();
    });
    const remove = document.createElement("button");
    remove.className = "tag-delete";
    remove.type = "button";
    remove.textContent = "×";
    remove.addEventListener("click", async () => {
      if (!window.confirm(`Supprimer le tag ${tag.name} ?`)) return;
      await requestJson(`/api/tags/${tag.id}`, { method: "DELETE" });
      if (selectedTagId === tag.id) selectedTagId = "";
      await refreshLibrary();
    });
    row.append(button, makeRenameButton(tag, "tags"), remove);
    tagList.append(row);
  }
}

function renderLibrary() {
  if (draggedTrackTag || draggedPlaylist) return;
  if (selectedPlaylistId || selectedTagId) onlyUnassigned = false;
  renderImports();
  renderPlaylists();
  renderTags();
  libraryTracks.replaceChildren();
  const storedTracks = libraryState.tracks
    .filter((track) => trackInPlaylist(track, selectedPlaylistId))
    .filter((track) => trackInTag(track, selectedTagId));
  const assigned = new Set(libraryState.playlists.flatMap((p) => p.track_ids));
  const tracks = [...liveTracksForLibrary(), ...storedTracks]
    .filter((track) => !onlyUnassigned || !assigned.has(track.job_id));
  renderedTracks = tracks;
  document.querySelector("#library-title").textContent = currentLibraryTitle();
  libraryCount.textContent = `${tracks.length} son${tracks.length > 1 ? "s" : ""}`;
  if (!tracks.length) {
    const empty = document.createElement("p");
    empty.className = "empty-copy";
    empty.textContent = selectedPlaylistId
      ? "Aucun son dans cette playlist."
      : selectedTagId
        ? "Aucun son avec ce tag."
        : "Aucun son disponible.";
    libraryTracks.append(empty);
    return;
  }
  tracks.forEach((track, index) => {
    const row = document.createElement("article");
    row.className = "library-track";
    row.draggable = !track.is_live && track.state !== "failed";
    row.dataset.jobId = track.job_id;
    row.dataset.state = track.state || "done";
    row.addEventListener("dragstart", (event) => {
      if (track.is_live || track.state === "failed") {
        event.preventDefault();
        return;
      }
      event.dataTransfer.effectAllowed = "copy";
      event.dataTransfer.setData("application/x-tunesaver-job", track.job_id);
      event.dataTransfer.setData("text/plain", track.job_id);
      row.dataset.dragging = "true";
    });
    row.addEventListener("dragend", () => {
      row.dataset.dragging = "false";
    });
    row.addEventListener("dragover", (event) => {
      if (track.is_live || track.state === "failed") return;
      if (!Array.from(event.dataTransfer.types).includes("application/x-tunesaver-tag")) return;
      event.preventDefault();
      row.dataset.drop = "true";
    });
    row.addEventListener("dragleave", () => {
      row.dataset.drop = "false";
    });
    row.addEventListener("drop", async (event) => {
      row.dataset.drop = "false";
      if (track.is_live || track.state === "failed") return;
      const tagId = event.dataTransfer.getData("application/x-tunesaver-tag");
      if (!tagId) return;
      event.preventDefault();
      try {
        await addTagToTrack(tagId, track.job_id);
      } catch (error) {
        showToast(error.message, "error");
      }
    });
    const cover = document.createElement("div");
    cover.className = "library-cover";
    const coverText = document.createElement("span");
    coverText.textContent = (track.title || track.file_name || "?").trim().slice(0, 1).toUpperCase() || "?";
    cover.append(coverText);
    if (track.cover_url) {
      const image = document.createElement("img");
      image.alt = "";
      image.loading = "lazy";
      if (coverUrlCache.has(track.cover_url)) cover.dataset.loaded = "true";
      image.addEventListener("load", () => cover.dataset.loaded = "true");
      cover.append(image);
      loadPrivateImage(image, track.cover_url);
    }
    const rank = document.createElement("span");
    rank.className = "track-rank";
    rank.textContent = String(index + 1);
    const body = document.createElement("div");
    body.className = "library-track-body";
    const title = document.createElement("strong");
    title.textContent = track.title || track.file_name || track.job_id;
    const meta = document.createElement("span");
    meta.textContent = `${track.artist || "Artiste inconnu"}${track.created_at ? ` · ${formatAdminDate(track.created_at)}` : ""}`;
    const badges = document.createElement("div");
    badges.className = "track-tags";
    for (const tag of track.tags || []) {
      const badge = document.createElement("button");
      badge.type = "button";
      badge.className = "track-tag";
      badge.draggable = true;
      badge.title = `Retirer le tag ${tag.name}`;
      badge.setAttribute("aria-label", badge.title);
      badge.addEventListener("click", () => removeTrackTag(tag.id, track.job_id));
      badge.addEventListener("dragstart", (event) => {
        event.stopPropagation();
        draggedTrackTag = { tagId: tag.id, jobId: track.job_id };
        event.dataTransfer.effectAllowed = "move";
        event.dataTransfer.setData("application/x-tunesaver-remove-tag", tag.id);
        badge.dataset.dragging = "true";
      });
      badge.addEventListener("dragend", () => {
        draggedTrackTag = null;
        badge.dataset.dragging = "false";
      });
      applyTagStyle(badge, tag);
      badge.textContent = tag.name;
      badges.append(badge);
    }
    body.append(title, meta, badges);
    const actions = document.createElement("div");
    actions.className = "library-track-actions";
    if (track.streamable) {
      const play = document.createElement("button");
      play.className = "play-button";
      play.type = "button";
      play.append(document.getElementById("icon-play").content.cloneNode(true));
      play.setAttribute("aria-label", `Lire ${track.title || track.file_name || "ce son"}`);
      play.title = "Lire";
      play.addEventListener("click", async () => {
        play.disabled = true;
        try { await streamTrack(track); }
        catch (error) { showToast(error.message, "error"); }
        finally { play.disabled = false; }
      });
      actions.append(play);
    }
    actions.append(makeSourceBadge(track));
    if (track.is_live || track.state === "failed") {
      const statusButton = document.createElement("button");
      statusButton.className = `download-status-button ${track.state || "pending"}`;
      statusButton.type = "button";
      statusButton.disabled = true;
      statusButton.textContent = stateLabels[track.state || "pending"] || "En file";
      actions.append(statusButton);
    } else if (!selectedPlaylistId && !selectedTagId) {
      const download = document.createElement("button");
      download.className = "history-download";
      download.type = "button";
      download.textContent = "Télécharger";
      download.addEventListener("click", async () => {
        download.disabled = true;
        try { await downloadReadyJob(track.job_id); }
        catch (error) { showToast(error.message, "error"); download.disabled = false; }
      });
      actions.append(download);
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "track-delete";
      remove.title = "Supprimer de Mes sons";
      remove.setAttribute("aria-label", `${remove.title} : ${track.title || track.file_name || "ce son"}`);
      remove.append(document.getElementById("icon-trash").content.cloneNode(true));
      remove.addEventListener("click", async () => {
        if (!window.confirm("Supprimer ce morceau de Mes sons et de ses playlists ?")) return;
        remove.disabled = true;
        try {
          await requestJson(`/api/library/${track.job_id}`, { method: "DELETE" });
          if (currentTrackId === track.job_id) resetPlayer();
          await refreshLibrary();
        } catch (error) { showToast(error.message, "error"); }
        finally { remove.disabled = false; }
      });
      actions.append(remove);
    }
    if (selectedPlaylistId || selectedTagId) {
      const remove = document.createElement("button");
      remove.className = "playlist-remove-track";
      remove.type = "button";
      remove.textContent = "Retirer";
      remove.addEventListener("click", async () => {
        const url = selectedPlaylistId
          ? `/api/playlists/${selectedPlaylistId}/tracks/${track.job_id}`
          : `/api/tags/${selectedTagId}/tracks/${track.job_id}`;
        await requestJson(url, { method: "DELETE" });
        await refreshLibrary();
      });
      actions.append(remove);
    }
    row.append(rank, cover, body, actions);
    libraryTracks.append(row);
  });
}

const dismissedImports = new Set((() => {
  try {
    const stored = JSON.parse(localStorage.getItem("tunesaver.dismissedImports") || "[]");
    return Array.isArray(stored) ? stored.filter(id => typeof id === "string").slice(-100) : [];
  } catch (_) { return []; }
})());

function renderImports() {
  const area = document.querySelector("#playlist-imports");
  const imports = (libraryState.imports || []).filter(item => !dismissedImports.has(item.id)
    && (!selectedPlaylistId || item.playlist_id === selectedPlaylistId));
  area.hidden = !imports.length;
  const expanded = new Set(Array.from(area.querySelectorAll("details[open]")).map(el => el.dataset.id));
  area.replaceChildren();
  const labels = { pending: "Lecture de la playlist", running: "Recherche des titres", done: "Titres mis en téléchargement", partial: "Import partiel", failed: "Import impossible" };
  for (const item of imports) {
    const row = document.createElement("details");
    row.className = "playlist-import";
    row.dataset.id = item.id;
    row.open = expanded.has(item.id);
    const summary = document.createElement("summary");
    const name = document.createElement("strong");
    name.textContent = item.name;
    const status = document.createElement("span");
    status.textContent = `${labels[item.state] || item.state}${item.total ? ` · ${item.processed}/${item.total} · ${item.queued} en file` : ""}`;
    summary.append(name, status);
    if (["done", "partial", "failed"].includes(item.state)) {
      const close = document.createElement("button");
      close.type = "button";
      close.className = "import-dismiss";
      close.title = "Masquer ce bilan";
      close.setAttribute("aria-label", `Masquer le bilan de ${item.name}`);
      close.append(document.getElementById("icon-close").content.cloneNode(true));
      close.addEventListener("click", (event) => {
        event.preventDefault();
        event.stopPropagation();
        dismissedImports.add(item.id);
        try {
          localStorage.setItem("tunesaver.dismissedImports", JSON.stringify([...dismissedImports].slice(-100)));
        } catch (_) { /* Keep dismissal for this session when storage is unavailable. */ }
        renderImports();
        document.querySelector(".import-dismiss")?.focus();
        if (area.hidden) importPlaylistButton.focus();
      });
      summary.append(close);
    }
    const progress = document.createElement("progress");
    progress.max = item.total || 1;
    if (item.total || !["pending", "running"].includes(item.state)) progress.value = item.processed;
    progress.setAttribute("aria-label", `Import ${item.name}`);
    row.append(summary, progress);
    if (item.error) {
      const error = document.createElement("p");
      error.textContent = item.error;
      row.append(error);
    }
    for (const failed of item.failures || []) {
      const line = document.createElement("p");
      line.textContent = `${failed.artist} · ${failed.title} : ${failed.reason}`;
      row.append(line);
    }
    area.append(row);
  }
}

async function refreshLibrary() {
  if (!session) return;
  refreshLibraryButton.disabled = true;
  try {
    libraryState = await fetchLibrary();
    if (selectedPlaylistId && !libraryState.playlists.some((playlist) => playlist.id === selectedPlaylistId)) {
      selectedPlaylistId = "";
    }
    if (selectedTagId && !libraryState.tags.some((tag) => tag.id === selectedTagId)) {
      selectedTagId = "";
    }
    renderLibrary();
  } catch (error) {
    showToast(error.message, "error");
  } finally {
    refreshLibraryButton.disabled = false;
  }
}

document.querySelector(".playlist-filter[data-playlist-id='']").addEventListener("click", () => {
  onlyUnassigned = false;
  selectedPlaylistId = "";
  selectedTagId = "";
  renderLibrary();
});

document.querySelector("#unassigned-tracks").addEventListener("click", () => {
  selectedPlaylistId = "";
  selectedTagId = "";
  onlyUnassigned = true;
  renderLibrary();
});

document.querySelector("#create-folder").addEventListener("click", async () => {
  const name = window.prompt("Nom du dossier");
  if (!name) return;
  try {
    await requestJson("/api/playlist-folders", { method: "POST", body: JSON.stringify({ name }) });
    await refreshLibrary();
  } catch (error) { showToast(error.message, "error"); }
});

folderDropTarget(document.querySelector("#playlist-title"), null);

createPlaylistButton.addEventListener("click", async () => {
  const name = window.prompt("Nom de la playlist");
  if (!name) return;
  createPlaylistButton.disabled = true;
  try {
    const result = await requestJson("/api/playlists", {
      method: "POST",
      body: JSON.stringify({ name }),
    });
    selectedPlaylistId = result.playlist.id;
    await refreshLibrary();
  } catch (error) {
    showToast(error.message, "error");
  } finally {
    createPlaylistButton.disabled = false;
  }
});

createTagButton.addEventListener("click", async () => {
  const name = window.prompt("Nom du tag");
  if (!name) return;
  createTagButton.disabled = true;
  try {
    const result = await requestJson("/api/tags", {
      method: "POST",
      body: JSON.stringify({ name }),
    });
    selectedTagId = result.tag.id;
    selectedPlaylistId = "";
    await refreshLibrary();
  } catch (error) {
    showToast(error.message, "error");
  } finally {
    createTagButton.disabled = false;
  }
});

audioPlayer.addEventListener("ended", playNextTrack);

function renderHistory(history, localJobs) {
  const localById = new Map(localJobs.map((job) => [job.id, job]));
  const recent = document.querySelector("#recent-jobs");
  recent.replaceChildren();
  const savings = history
    .filter((job) => job.state === "done")
    .reduce((total, job) => total + Number(job.estimated_savings_cents || 0), 0);
  savingsTotal.textContent = new Intl.NumberFormat("fr-FR", {
    style: "currency",
    currency: "EUR",
  }).format(savings / 100);
  if (!history.length) {
    const empty = document.createElement("p");
    empty.className = "empty-copy";
    empty.textContent = "Aucun téléchargement dans ton historique.";
    recent.append(empty);
    return;
  }
  for (const job of history) {
    const local = localById.get(job.job_id);
    const row = document.createElement("div");
    row.className = "recent-job";
    const provider = document.createElement("span");
    provider.className = `mini-provider ${job.provider}`;
    provider.textContent = providerLabels[job.provider] || job.provider;
    const label = document.createElement("span");
    label.className = "job-label";
    label.textContent = job.title
      ? `${job.title}${job.artist ? ` — ${job.artist}` : ""}`
      : job.job_id;
    let action;
    if (local?.download_ready) {
      action = document.createElement("button");
      action.className = "history-download";
      action.type = "button";
      action.textContent = "Récupérer";
      action.addEventListener("click", async () => {
        action.disabled = true;
        try { await downloadReadyJob(job.job_id); }
        catch (error) { action.disabled = false; showToast(error.message, "error"); }
      });
    } else {
      action = document.createElement("span");
      action.className = `job-state ${job.state}`;
      action.textContent = stateLabels[job.state] || job.state;
    }
    row.append(provider, label, action);
    recent.append(row);
  }
}

async function refreshQueue() {
  if (!session || queueRefreshPromise) return queueRefreshPromise;
  queueRefreshPromise = refreshQueueOnce().finally(() => { queueRefreshPromise = null; });
  return queueRefreshPromise;
}

async function refreshQueueOnce() {
  refreshStatusButton.disabled = true;
  try {
    const payload = await requestJson("/api/status", { method: "GET" });
    const counts = payload.counts || {};
    for (const provider of providers) {
      activeProviders[provider] = payload.active?.[provider] !== false;
      setServiceStatus(provider, activeProviders[provider]);
      for (const button of document.querySelectorAll(`.download-button[data-provider="${provider}"]`)) {
        if (button.textContent === "Ajouté ✓" || button.textContent === "Déjà en file") continue;
        if (!activeProviders[provider]) {
          button.disabled = true;
          button.textContent = "Service arrêté";
        } else if (button.textContent === "Service arrêté") {
          button.disabled = false;
          button.textContent = button.dataset.defaultLabel || "Télécharger";
        }
      }
    }
    for (const state of ["pending", "running", "done", "failed"]) {
      document.querySelector(`#${state}-count`).textContent = String(totalForState(counts, state));
    }
    liveQueueTracks = payload.recent || [];
    updateDownloadButtons(liveQueueTracks);
    let history = (payload.recent || []).map((job) => ({ ...job, job_id: job.id }));
    try {
      await syncHistory(payload.recent || []);
      history = await fetchHistory();
    } catch (error) {
      if (!historyWarningShown) {
        showToast(`Historique local indisponible : ${error.message}`, "error");
        historyWarningShown = true;
      }
    }
    renderHistory(history, payload.recent || []);
    await refreshLibrary();
  } catch (error) {
    for (const provider of providers) {
      activeProviders[provider] = false;
      setServiceStatus(provider, false);
    }
    showToast(error.message || "Impossible de lire la file.", "error");
  } finally {
    refreshStatusButton.disabled = false;
  }
}

refreshStatusButton.addEventListener("click", refreshQueue);

historyToggle.addEventListener("click", async () => {
  const open = queueSection.hidden;
  queueSection.hidden = !open;
  historyToggle.dataset.open = open ? "true" : "false";
  historyToggle.setAttribute("aria-expanded", String(open));
  if (open) await refreshQueue();
});

function formatAdminDate(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return new Intl.DateTimeFormat("fr-FR", {
    day: "2-digit",
    month: "2-digit",
    year: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function renderAdminUsers(profiles) {
  adminUsers.replaceChildren();
  adminUsersCount.textContent = `${profiles.length} compte${profiles.length > 1 ? "s" : ""}`;
  if (!profiles.length) {
    const empty = document.createElement("p");
    empty.className = "empty-copy";
    empty.textContent = "Aucun compte.";
    adminUsers.append(empty);
    return;
  }
  if (!selectedAdminUserId || !profiles.some((profile) => profile.user_id === selectedAdminUserId)) {
    selectedAdminUserId = profiles[0].user_id;
  }
  for (const profile of profiles) {
    const row = document.createElement("article");
    row.className = "admin-user";
    row.dataset.selected = profile.user_id === selectedAdminUserId ? "true" : "false";
    const button = document.createElement("button");
    button.className = "admin-user-main";
    button.type = "button";
    button.addEventListener("click", async () => {
      selectedAdminUserId = profile.user_id;
      renderAdminUsers(profiles);
      await refreshAdminHistory();
    });
    const email = document.createElement("strong");
    email.textContent = profile.email || "Sans e-mail";
    const details = document.createElement("span");
    details.textContent = formatAdminDate(profile.created_at) || profile.user_id;
    button.append(email, details);

    const status = document.createElement("span");
    status.className = `admin-status ${profile.blocked ? "denied" : profile.approved ? "approved" : "pending"}`;
    status.textContent = profile.blocked ? "Refusé" : profile.approved ? "Accepté" : "En attente";

    const emailStatus = document.createElement("span");
    emailStatus.className = `admin-email-status ${profile.email_verified ? "confirmed" : "unconfirmed"}`;
    emailStatus.textContent = profile.email_verified ? "Mail vérifié" : "Mail non vérifié";

    const action = document.createElement("button");
    action.className = profile.approved ? "admin-deny" : "admin-approve";
    action.type = "button";
    action.textContent = profile.approved ? "Refuser" : "Accepter";
    action.addEventListener("click", async () => {
      action.disabled = true;
      try {
        await adminJson(`/api/admin/profiles/${profile.user_id}`, {
          method: "PATCH",
          body: JSON.stringify({ approved: !profile.approved }),
        });
        await refreshAdmin();
      } catch (error) {
        showToast(error.message, "error");
      } finally {
        action.disabled = false;
      }
    });

    const passwordButton = document.createElement("button");
    passwordButton.className = "admin-password";
    passwordButton.type = "button";
    passwordButton.textContent = "Mot de passe";
    passwordButton.addEventListener("click", async () => {
      const label = profile.email || "ce compte";
      const password = window.prompt(`Nouveau mot de passe pour ${label}`);
      if (password === null) return;
      if (password.length < 6) {
        showToast("Le mot de passe doit faire au moins 6 caractères.", "error");
        return;
      }
      passwordButton.disabled = true;
      try {
        await adminJson(`/api/admin/profiles/${profile.user_id}/password`, {
          method: "POST",
          body: JSON.stringify({ password }),
        });
        showToast("Mot de passe mis à jour.");
      } catch (error) {
        showToast(error.message, "error");
      } finally {
        passwordButton.disabled = false;
      }
    });

    const deleteButton = document.createElement("button");
    deleteButton.className = "admin-delete";
    deleteButton.type = "button";
    deleteButton.textContent = "Supprimer";
    deleteButton.addEventListener("click", async () => {
      const label = profile.email || "ce compte";
      if (!window.confirm(`Supprimer définitivement ${label} ?`)) return;
      deleteButton.disabled = true;
      try {
        await adminJson(`/api/admin/profiles/${profile.user_id}`, {
          method: "DELETE",
          body: JSON.stringify({}),
        });
        if (selectedAdminUserId === profile.user_id) selectedAdminUserId = "";
        await refreshAdmin();
        showToast("Compte supprimé.");
      } catch (error) {
        showToast(error.message, "error");
      } finally {
        deleteButton.disabled = false;
      }
    });

    const actions = document.createElement("div");
    actions.className = "admin-user-actions";
    actions.append(emailStatus, status, action, passwordButton, deleteButton);

    row.append(button, actions);
    adminUsers.append(row);
  }
}

function formatChartDay(value) {
  const date = new Date(`${value}T12:00:00`);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("fr-FR", {
    day: "2-digit",
    month: "2-digit",
  }).format(date);
}

function formatChartValue(value, mode = adminChartMode) {
  if (mode === "savings") {
    return new Intl.NumberFormat("fr-FR", {
      style: "currency",
      currency: "EUR",
      maximumFractionDigits: 0,
    }).format(value / 100);
  }
  return new Intl.NumberFormat("fr-FR").format(value);
}

function chartSvgElement(name, attrs = {}) {
  const element = document.createElementNS("http://www.w3.org/2000/svg", name);
  for (const [key, value] of Object.entries(attrs)) {
    element.setAttribute(key, String(value));
  }
  return element;
}

function renderAdminChart() {
  adminChart.replaceChildren();
  adminChartTracks.dataset.active = adminChartMode === "tracks" ? "true" : "false";
  adminChartSavings.dataset.active = adminChartMode === "savings" ? "true" : "false";
  const valueKey = adminChartMode === "savings" ? "savings_cents" : "tracks";
  const total = adminDailyStats.reduce((sum, item) => sum + Number(item[valueKey] || 0), 0);
  adminChartTotal.textContent = adminChartMode === "savings"
    ? `${formatChartValue(total, "savings")} économisés`
    : `${formatChartValue(total, "tracks")} sons`;
  if (!adminDailyStats.length) {
    const empty = document.createElement("p");
    empty.className = "empty-copy";
    empty.textContent = "Aucune donnée.";
    adminChart.append(empty);
    return;
  }

  const width = 720;
  const height = 260;
  const margin = { top: 20, right: 22, bottom: 42, left: 54 };
  const chartWidth = width - margin.left - margin.right;
  const chartHeight = height - margin.top - margin.bottom;
  const values = adminDailyStats.map((item) => Number(item[valueKey] || 0));
  const maxValue = Math.max(1, ...values);
  const step = chartWidth / adminDailyStats.length;
  const barWidth = Math.max(6, Math.min(18, step * 0.56));
  const yFor = (value) => margin.top + chartHeight - (Number(value || 0) / maxValue) * chartHeight;
  const xCenter = (index) => margin.left + step * index + step / 2;

  const svg = chartSvgElement("svg", {
    viewBox: `0 0 ${width} ${height}`,
    role: "img",
    "aria-label": adminChartMode === "savings"
      ? "Économies par jour"
      : "Sons téléchargés par jour",
  });

  for (let tick = 0; tick <= 4; tick += 1) {
    const value = Math.round((maxValue / 4) * tick);
    const y = yFor(value);
    svg.append(chartSvgElement("line", {
      class: "chart-grid",
      x1: margin.left,
      x2: width - margin.right,
      y1: y,
      y2: y,
    }));
    const label = chartSvgElement("text", {
      class: "chart-axis-label",
      x: margin.left - 10,
      y: y + 4,
      "text-anchor": "end",
    });
    label.textContent = formatChartValue(value);
    svg.append(label);
  }

  const points = [];
  adminDailyStats.forEach((item, index) => {
    const value = Number(item[valueKey] || 0);
    const x = xCenter(index);
    const y = yFor(value);
    points.push([x, y]);
    svg.append(chartSvgElement("rect", {
      class: "chart-bar",
      x: x - barWidth / 2,
      y,
      width: barWidth,
      height: margin.top + chartHeight - y,
      rx: 3,
    }));
    const shouldLabel = index === 0
      || index === adminDailyStats.length - 1
      || index % 5 === 0;
    if (shouldLabel) {
      const label = chartSvgElement("text", {
        class: "chart-axis-label",
        x,
        y: height - 14,
        "text-anchor": "middle",
      });
      label.textContent = formatChartDay(item.date);
      svg.append(label);
    }
  });

  if (points.length > 1) {
    svg.append(chartSvgElement("path", {
      class: "chart-line",
      d: points.map(([x, y], index) => `${index === 0 ? "M" : "L"} ${x.toFixed(2)} ${y.toFixed(2)}`).join(" "),
    }));
  }
  for (const [x, y] of points) {
    svg.append(chartSvgElement("circle", {
      class: "chart-point",
      cx: x,
      cy: y,
      r: 3,
    }));
  }
  adminChart.append(svg);
}

function renderAdminHistory(items) {
  adminHistory.replaceChildren();
  adminHistoryCount.textContent = `${items.length} son${items.length > 1 ? "s" : ""}`;
  if (!items.length) {
    const empty = document.createElement("p");
    empty.className = "empty-copy";
    empty.textContent = "Aucun son pour ce compte.";
    adminHistory.append(empty);
    return;
  }
  for (const item of items) {
    const row = document.createElement("article");
    row.className = "admin-track";
    const provider = document.createElement("span");
    provider.className = `mini-provider ${item.provider}`;
    provider.textContent = providerLabels[item.provider] || item.provider;
    const body = document.createElement("div");
    body.className = "admin-track-body";
    const title = document.createElement("strong");
    title.textContent = item.title || item.source_url || item.job_id;
    const meta = document.createElement("span");
    const artist = item.artist ? `${item.artist} · ` : "";
    meta.textContent = `${artist}${item.resource_kind || "track"} · ${formatAdminDate(item.created_at)}`;
    body.append(title, meta);
    const state = document.createElement("span");
    state.className = `job-state ${item.state}`;
    state.textContent = stateLabels[item.state] || item.state;
    row.append(provider, body, state);
    adminHistory.append(row);
  }
}

async function refreshAdminHistory() {
  if (!selectedAdminUserId) return;
  const payload = await adminJson(`/api/admin/history?user_id=${encodeURIComponent(selectedAdminUserId)}`);
  renderAdminHistory(payload.history || []);
}

async function refreshAdminStats() {
  const payload = await adminJson("/api/admin/stats");
  adminDailyStats = payload.daily || [];
  renderAdminChart();
}

async function refreshAdmin() {
  adminRefreshButton.disabled = true;
  adminMessage.textContent = "";
  try {
    const payload = await adminJson("/api/admin/profiles");
    const profiles = payload.profiles || [];
    renderAdminUsers(profiles);
    await refreshAdminStats();
    await refreshAdminHistory();
  } catch (error) {
    adminMessage.textContent = error.message;
  } finally {
    adminRefreshButton.disabled = false;
  }
}

adminRefreshButton.addEventListener("click", refreshAdmin);
adminChartTracks.addEventListener("click", () => {
  adminChartMode = "tracks";
  renderAdminChart();
});
adminChartSavings.addEventListener("click", () => {
  adminChartMode = "savings";
  renderAdminChart();
});

async function start() {
  try {
    await loadConfig();
    const storedAdmin = JSON.parse(localStorage.getItem("tunesaver.adminSession") || "null");
    if (storedAdmin?.access_token) {
      adminSession = storedAdmin;
      try {
        await adminJson("/api/admin/profiles");
        showAdmin();
        return;
      } catch (_error) {
        saveAdminSession(null);
      }
    }
    const stored = JSON.parse(localStorage.getItem("tunesaver.session") || "null");
    if (stored?.access_token && stored?.refresh_token) {
      session = stored;
      try {
        const response = await authenticatedFetch("/api/status", { method: "GET" });
        if (!response.ok) await parseResponse(response);
        showApp();
        return;
      } catch (error) {
        showAuth(error.message);
        return;
      }
    }
    showAuth();
  } catch (error) {
    showAuth(`Configuration impossible : ${error.message}`);
  }
}

start();
