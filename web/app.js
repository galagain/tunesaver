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
const accountArea = document.querySelector('#account-area');
const logoutButton = document.querySelector("#logout-button");
const historyToggle = document.querySelector("#history-toggle");
const userEmail = document.querySelector("#user-email");
const form = document.querySelector("#search-form");
const input = document.querySelector("#search-input");
const searchButton = document.querySelector("#search-button");
const searchPlaylistButton = document.querySelector('#search-playlist-button');
const searchResultsByProvider = new Map();
let lastTextSearch = '';
const searchMessage = document.querySelector("#search-message");
const resultsSection = document.querySelector("#results-section");
const queueSection = document.querySelector("#queue-section");
const refreshStatusButton = document.querySelector("#refresh-status");
const refreshLibraryButton = document.querySelector("#refresh-library");
const createPlaylistButton = document.querySelector("#create-playlist");
const playlistList = document.querySelector("#playlist-list");
const libraryTracks = document.querySelector("#library-tracks");
const libraryCount = document.querySelector("#library-count");
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
const searchView = document.querySelector('#search-view');
const searchNav = document.querySelector('#search-nav');

function updateWorkspaceNavigation() {
  document.querySelectorAll('.library-nav-content button').forEach(button => {
    const current = button === searchNav ? !searchView.hidden : button === historyToggle ? !queueSection.hidden
      : searchView.hidden && queueSection.hidden && button.dataset.selected === 'true';
    if (current) button.setAttribute('aria-current', 'page');
    else button.removeAttribute('aria-current');
  });
}

function setWorkspaceView(view) {
  const search = view === 'search';
  searchView.hidden = !search;
  queueSection.hidden = view !== 'history';
  document.querySelector('.library-section').hidden = view !== 'library';
  document.querySelector('.workspace').dataset.view = view;
  searchNav.dataset.selected = String(search);
  historyToggle.dataset.selected = String(view === 'history');
  updateWorkspaceNavigation();
}

searchNav.addEventListener('click', () => { setWorkspaceView('search'); input.focus({ preventScroll: true }); });
document.querySelector('.sidebar-brand').addEventListener('click', event => {
  event.preventDefault();
  document.querySelector('.library-nav-content [data-collection=""]').click();
});
document.querySelector('.library-nav-content').addEventListener('click', event => {
  if (event.target.closest('[data-collection]')) {
    librarySearchScope = null;
    libraryTracks.renderKey = null;
    setWorkspaceView('library');
  }
}, true);
const headerObserver = new ResizeObserver(() => {
  document.documentElement.style.setProperty('--header-height', `${document.querySelector('.site-header').getBoundingClientRect().height}px`);
});
headerObserver.observe(document.querySelector('.site-header'));

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
let librarySearchScope = null;
let selectedLibraryTrackId = '';
let trackMove = null;
let draggedLibraryTrack = false;
let savingTrackOrder = false;
const librarySorts = new Map();
const trackCollator = new Intl.Collator('fr', { numeric: true, sensitivity: 'base' });
let libraryState = { tracks: [], playlists: [], tags: [] };
let liveQueueTracks = [];
const deletedJobIds = new Set();
const uiIcons = Promise.all([import('/vendor/mixer-icons.js'), import('/vendor/player-icons.js?v=20260909-30')])
  .then(([mixer, player]) => ({ ...mixer, ...player }));
function appendUIIcon(element, name) {
  uiIcons.then(icons => { element.append(icons.createElement(icons[name], { 'aria-hidden': 'true' })); });
}
document.querySelectorAll('[data-ui-icon]').forEach(element => appendUIIcon(element, element.dataset.uiIcon));
providers.forEach(provider => {
  const heading = document.querySelector(`#${provider}-results`).closest('.platform-panel').querySelector('h3');
  const mark = document.createElement('span'); mark.className = 'platform-mark'; mark.setAttribute('aria-hidden', 'true');
  mark.append(document.querySelector(`#icon-${provider}`).content.cloneNode(true));
  heading.prepend(mark);
});
document.addEventListener('click', event => {
  document.querySelectorAll('.workspace-menu[open]').forEach(menu => {
    if (!menu.contains(event.target) || event.target.closest('button')) menu.open = false;
  });
});
document.addEventListener('toggle', event => {
  const menu = event.target;
  if (!menu.matches?.('.workspace-menu[open]')) return;
  const panel = menu.querySelector('.workspace-menu-items');
  const anchor = menu.querySelector('summary').getBoundingClientRect();
  const top = document.querySelector('.site-header').getBoundingClientRect().bottom + 4;
  const bottom = document.querySelector('#playback-dock').getBoundingClientRect().top - 4;
  panel.style.maxHeight = `${Math.max(40, bottom - top)}px`;
  const box = panel.getBoundingClientRect();
  panel.style.left = `${Math.max(8, Math.min(anchor.right - box.width, innerWidth - box.width - 8))}px`;
  panel.style.top = `${Math.max(top, Math.min(anchor.bottom + 4, bottom - box.height))}px`;
}, true);
document.querySelectorAll('#library-tracks, .library-nav-content').forEach(pane => {
  pane.addEventListener('scroll', () => {
    document.querySelectorAll('.workspace-menu[open]').forEach(menu => { menu.open = false; });
  });
});
document.addEventListener('keydown', event => {
  if (event.key !== 'Escape') return;
  document.querySelectorAll('.workspace-menu[open]').forEach(menu => { menu.open = false; menu.querySelector('summary').focus(); });
});
let renderedTracks = [];
let currentTrackId = "";
const coverUrlCache = new Map();
let historyWarningShown = false;
let lastHistoryFingerprint = "";
const activeProviders = { tiddl: null, beatport: null, qobuz: null };

const siteDialogs = window.TuneSaverDialogs = (() => {
  const dialog = document.querySelector("#action-dialog");
  const form = document.querySelector("#action-dialog-form");
  const input = document.querySelector("#action-dialog-input");
  const cancelButton = document.querySelector("#action-dialog-cancel");
  const submitButton = document.querySelector("#action-dialog-submit");
  let resolve = null, result = null, cancelled = null, returnFocus = null;
  const cancel = () => {
    if (!resolve) return;
    result = cancelled;
    dialog.close();
  };
  dialog.addEventListener("cancel", event => { event.preventDefault(); cancel(); });
  dialog.addEventListener("keydown", event => event.stopPropagation());
  cancelButton.addEventListener("click", cancel);
  form.addEventListener("submit", event => {
    event.preventDefault();
    result = input.hidden ? true : input.value;
    dialog.close();
  });
  dialog.addEventListener("close", () => {
    const complete = resolve;
    resolve = null;
    input.value = "";
    returnFocus?.focus();
    complete?.(result);
  });
  function show(title, value, options, prompt) {
    if (resolve) return Promise.resolve(prompt ? null : false);
    returnFocus = document.activeElement?.closest('.workspace-menu')?.querySelector('summary') || document.activeElement;
    document.querySelector("#action-dialog-title").textContent = title;
    input.hidden = !prompt;
    input.disabled = !prompt;
    input.type = options.type || "text";
    input.autocomplete = input.type === "password" ? "new-password" : "off";
    input.maxLength = options.maxLength || 1024;
    input.value = value;
    submitButton.textContent = options.confirmText || (prompt ? "Valider" : "Confirmer");
    cancelled = prompt ? null : false;
    result = cancelled;
    return new Promise(complete => {
      resolve = complete;
      dialog.showModal();
      (prompt ? input : cancelButton).focus();
      if (prompt && input.type === "text") input.select();
    });
  }
  return {
    prompt: (title, value = "", options = {}) => show(title, value, options, true),
    confirm: (title, options = {}) => show(title, "", options, false),
    cancel,
  };
})();

function platformElements(provider) {
  return {
    count: document.querySelector(`#${provider}-count`),
    state: document.querySelector(`#${provider}-state`),
    results: document.querySelector(`#${provider}-results`),
  };
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
    importPreviews.clear();
    selectedLibraryTrackId = '';
    document.querySelector('#library-search').value = '';
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
  playerVersion++;
  playerAudio.pause();
  playerAudio.removeAttribute('src');
  playerAudio.load();
  playerTitle.textContent = 'Aucun son en lecture';
  playerArtist.textContent = '';
  playerCover.replaceChildren();
  currentTrackId = "";
}

function clearCoverCache() {
  for (const objectUrl of coverUrlCache.values()) URL.revokeObjectURL(objectUrl);
  coverUrlCache.clear();
}

function showAuth(message = "") {
  searchResultsByProvider.clear(); lastTextSearch = '';
  cancelTrackMove();
  librarySorts.clear();
  siteDialogs.cancel();
  resetPlayer();
  window.clearInterval(queueTimer);
  queueTimer = null;
  saveSession(null);
  saveAdminSession(null);
  authView.hidden = false;
  appView.hidden = true;
  adminView.hidden = true;
  accountArea.hidden = true;
  logoutButton.hidden = true;
  historyToggle.hidden = true;
  queueSection.hidden = true;
  resetPlayer();
  clearCoverCache();
  authMessage.textContent = message;
  document.querySelector('#history-search').value = '';
  document.querySelector('#history-state').value = '';
  renderHistory([], []);
}

function showApp() {
  saveAdminSession(null);
  authView.hidden = true;
  appView.hidden = false;
  adminView.hidden = true;
  document.querySelector('.sidebar-footer').append(accountArea);
  accountArea.hidden = false;
  logoutButton.hidden = false;
  historyToggle.hidden = false;
  queueSection.hidden = true;
  userEmail.textContent = session?.user?.email || "Mon compte";
  userEmail.title = userEmail.textContent;
  setWorkspaceView('library');
  refreshQueue();
  refreshLibrary();
  window.clearInterval(queueTimer);
  queueTimer = window.setInterval(refreshQueue, 5000);
}

function showAdmin() {
  resetPlayer();
  window.clearInterval(queueTimer);
  queueTimer = null;
  saveSession(null);
  authView.hidden = true;
  appView.hidden = true;
  adminView.hidden = false;
  document.querySelector('.header-actions').append(accountArea);
  accountArea.hidden = false;
  logoutButton.hidden = false;
  historyToggle.hidden = true;
  queueSection.hidden = true;
  resetPlayer();
  clearCoverCache();
  userEmail.textContent = "Admin";
  userEmail.title = 'Admin';
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
  const email = await siteDialogs.prompt("E-mail du compte", authEmail.value.trim(), { type: "email" });
  if (!email) return;
  const request = await publicJson("/api/password-reset/request", { email });
  authMessage.textContent = request.message;
  const code = await siteDialogs.prompt("Code reçu par e-mail");
  if (!code) return;
  const password = await siteDialogs.prompt("Nouveau mot de passe", "", { type: "password" });
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


function setDownloadLabel(button, label) {
  button.dataset.idle = String(label === 'Télécharger');
  let text = button.querySelector('.download-label');
  if (!text) {
    const icon = document.createElement('span'); icon.setAttribute('aria-hidden', 'true');
    text = document.createElement('span'); text.className = 'download-label';
    button.replaceChildren(icon, text);
    appendUIIcon(icon, 'Download');
  }
  text.textContent = label;
  button.title = label;
  button.setAttribute('aria-label', label);
}

function makeResultCard(result, index = 0) {
  const card = document.createElement("article");
  card.className = "library-track result-card";
  const number = document.createElement('span');
  number.className = 'track-number';
  number.textContent = String(index + 1);
  const cover = makeLibraryCover(result); cover.classList.add('result-cover');
  card.append(number, cover);
  const body = makeTrackBody(result); body.classList.add('result-body');
  const album = document.createElement('p'); album.className = 'result-album';
  album.textContent = result.album || 'Album non renseigné'; album.title = album.textContent;
  const context = document.createElement('p'); context.className = 'result-context';
  context.textContent = [result.label, result.mix, result.quality].filter(Boolean).join(' · ') || '—';
  context.title = ['Label', 'Mix', 'Qualité'].map((label, i) => `${label} : ${[result.label, result.mix, result.quality][i] || 'Non renseigné'}`).join(' · ');
  const details = makeTrackMetrics({duration: result.duration})[2];
  details.classList.add('result-details');
  body.title = [album.textContent, context.textContent].filter(Boolean).join(' · ');

  const actions = document.createElement("div");
  actions.className = "library-track-actions result-actions";
  const source = makeSourceBadge(result);
  const download = document.createElement("button");
  download.className = "download-button";
  download.type = "button";
  download.dataset.provider = result.provider;
  const completeLabel = kindLabels[result.kind] || "la ressource complète";
  const isBlockedBatch = ["artist", "label"].includes(result.kind);
  const defaultLabel = "Télécharger";
  download.dataset.defaultLabel = defaultLabel;
  setDownloadLabel(download, defaultLabel);
  if (isBlockedBatch) {
    download.disabled = true;
    setDownloadLabel(download, "Non disponible");
  } else if (activeProviders[result.provider] === false) {
    download.disabled = true;
    setDownloadLabel(download, "Service arrêté");
  }
  download.addEventListener("click", async () => {
    if (result.requires_confirmation && !await siteDialogs.confirm(`Confirmer le téléchargement de ${completeLabel} ?`)) return;
    download.disabled = true;
    setDownloadLabel(download, "Préparation…");
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
      setDownloadLabel(download, response.duplicate ? "Déjà en file" : "En attente");
      showToast("Le son est ajouté dans Mes sons.");
      await refreshQueue();
      selectedPlaylistId = "";
      selectedTagId = "";
      renderLibrary();
    } catch (error) {
      download.disabled = false;
      setDownloadLabel(download, "Réessayer");
      showToast(error.message, "error");
    }
  });
  actions.append(source, download);
  card.append(body, details, actions);
  return card;
}

function renderPlatform(provider, results) {
  searchResultsByProvider.set(provider, results);
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
  results.forEach((result, index) => fragment.append(makeResultCard(result, index)));
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
  for (const url of links) {
    const imported = await requestJson("/api/playlist-imports", { method: "POST", body: JSON.stringify({ url, auto_download: true }) });
    selectedPlaylistId = imported.playlist_id || '';
    selectedTagId = "";
  }
  showToast(selectedPlaylistId ? 'Lecture des titres lancée.' : 'Morceau ajouté dans Tous mes sons.');
  resultsSection.hidden = true;
  await refreshQueue();
  await refreshLibrary();
  setWorkspaceView('library');
}

async function groupSearchInPlaylist(lines) {
  if (!lines.length || !lines.every(isMusicURL)) throw new Error('Colle des liens pour créer une playlist.');
  const links = [...new Set(lines)];
  if (!links.length) throw new Error('Aucun morceau à regrouper.');
  const name = await siteDialogs.prompt('Nom de la playlist', 'Nouvelle playlist', { maxLength: 80 });
  if (name === null) return;
  if (!name.trim()) throw new Error('Donne un nom à la playlist.');
  const imported = await requestJson('/api/playlist-imports', { method: 'POST', body: JSON.stringify({ urls: links, name: name.trim(), auto_download: true }) });
  selectedPlaylistId = imported.playlist_id; selectedTagId = '';
  resultsSection.hidden = true;
  await refreshLibrary(); setWorkspaceView('library');
  showToast('Les titres seront regroupés dans cette playlist.');
}

function isMusicURL(value) {
  return /^[a-z][a-z0-9+.-]*:\/\//i.test(value);
}

function updateSearchPlaylistButton() {
  const lines = input.value.trim().split(/\r?\n/).map(line => line.trim()).filter(Boolean);
  searchPlaylistButton.disabled = searchButton.disabled || !lines.length || !lines.every(isMusicURL);
  searchPlaylistButton.title = 'Créer une playlist à partir des liens';
}
input.addEventListener('input', updateSearchPlaylistButton);
updateSearchPlaylistButton();

function hasDroppedFiles(event) {
  return Array.from(event.dataTransfer?.types || []).includes('Files');
}

window.addEventListener('dragover', event => {
  if (!hasDroppedFiles(event)) return;
  event.preventDefault();
  event.dataTransfer.dropEffect = appView.hidden ? 'none' : 'copy';
  document.body.classList.toggle('file-drop-active', !appView.hidden);
}, true);
window.addEventListener('dragleave', event => {
  if (!event.relatedTarget) document.body.classList.remove('file-drop-active');
});
window.addEventListener('drop', async event => {
  if (!hasDroppedFiles(event)) return;
  event.preventDefault(); event.stopPropagation();
  document.body.classList.remove('file-drop-active');
  if (appView.hidden || !session?.access_token) { showToast('Connecte-toi pour importer un fichier.', 'error'); return; }
  if (searchButton.disabled) { showToast('Un import est déjà en cours.', 'error'); return; }
  searchButton.disabled = searchPlaylistButton.disabled = true;
  input.readOnly = true;
  try {
    const files = Array.from(event.dataTransfer.files);
    const documents = [];
    for (const file of files) {
      if (!file.name.toLowerCase().endsWith('.txt') || file.size > 1024 * 1024) throw new Error('Dépose un fichier .txt UTF-8 de 1 Mo maximum.');
      const text = new TextDecoder('utf-8', { fatal: true }).decode(await file.arrayBuffer()).trim();
      if (text.startsWith('{')) documents.push({ document: JSON.parse(text) });
      else {
        const links = text.split(/\r?\n/).map(line => line.trim()).filter(Boolean);
        if (!links.length || !links.every(isMusicURL)) throw new Error(`Le fichier ${file.name} doit contenir un lien par ligne ou une playlist TuneSaver.`);
        documents.push({ links });
      }
    }
    for (const item of documents) {
      if (item.links) await enqueueList(item.links);
      else {
        const imported = await requestJson('/api/playlist-imports', { method: 'POST', body: JSON.stringify({ document: item.document, auto_download: true }) });
        selectedPlaylistId = imported.playlist_id; selectedTagId = '';
        await refreshLibrary(); setWorkspaceView('library');
      }
    }
    showToast('Import lancé. Les fichiers déjà disponibles seront réutilisés.');
  } catch (error) { showToast(error.message || 'Fichier texte illisible.', 'error'); }
  finally { searchButton.disabled = false; updateSearchPlaylistButton(); input.readOnly = false; }
}, true);

input.addEventListener('paste', event => {
  if (searchButton.disabled) { event.preventDefault(); return; }
  const pasted = event.clipboardData?.getData('text/plain').trim();
  const lines = pasted?.split(/\r?\n/).map(line => line.trim()).filter(Boolean);
  if (!lines?.length || !lines.every(isMusicURL)) return;
  event.preventDefault();
  input.value = lines.join('\n');
  updateSearchPlaylistButton();
});

input.addEventListener("keydown", (event) => {
  if (event.key !== "Enter" || event.shiftKey) return;
  event.preventDefault();
  form.requestSubmit();
});

document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape" || appView.hidden || searchView.hidden || resultsSection.hidden) return;
  resultsSection.hidden = true;
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (searchButton.disabled) return;
  const query = input.value.trim();
  const lines = query.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
  if (!query) {
    searchMessage.textContent = "Saisis un titre, un artiste ou un lien.";
    input.focus();
    return;
  }
  searchMessage.textContent = "";
  searchButton.disabled = true;
  searchPlaylistButton.disabled = true;
  input.readOnly = true;
  searchButton.setAttribute('aria-busy', 'true');
  try {
    if (event.submitter === searchPlaylistButton) {
      await groupSearchInPlaylist(lines);
      return;
    }
    if (lines.length > 1 || isMusicURL(query)) {
      await enqueueList(lines);
      return;
    }
    resultsSection.hidden = false;
    if (looksLikeDirectLink(query)) {
      searchResultsByProvider.clear(); lastTextSearch = '';
      await searchDirect(query);
    } else {
      searchResultsByProvider.clear(); lastTextSearch = query;
      for (const provider of providers) setPlatformLoading(provider);
      await Promise.allSettled(providers.map((provider) => searchProvider(query, provider)));
    }
  } catch (error) {
    searchMessage.textContent = error.message;
  } finally {
    searchButton.disabled = false;
    updateSearchPlaylistButton();
    input.readOnly = false;
    searchButton.setAttribute('aria-busy', 'false');
  }
});

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
    setDownloadLabel(button, stateLabels[job.state] || job.state || "En file");
    button.disabled = job.state !== "failed";
    if (job.state === "failed") setDownloadLabel(button, "Réessayer");
  }
  for (const [jobId, button] of downloadButtonByJob.entries()) {
    if (activeJobIds.has(jobId)) continue;
    if (button.textContent !== "Terminé") setDownloadLabel(button, "Terminé");
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

async function streamTrack(track) {
  const fullTrack = typeof track === "string"
    ? renderedTracks.find((item) => item.job_id === track) || { job_id: track }
    : track;
  const version = ++playerVersion;
  if (fullTrack.job_id === currentTrackId && playerAudio.src && !playerAudio.error) return playerAudio.play();
  const { url } = await requestJson('/api/stream-ticket', {
    method: 'POST', body: JSON.stringify({ job_id: fullTrack.job_id }),
  });
  if (version !== playerVersion) return;
  playerAudio.src = url;
  currentTrackId = fullTrack.job_id;
  playerTitle.textContent = fullTrack.title || fullTrack.file_name || 'Sans titre';
  playerArtist.textContent = fullTrack.artist || 'Artiste inconnu';
  playerCover.replaceChildren();
  if (fullTrack.cover_url) {
    const image = document.createElement('img'); image.alt = '';
    playerCover.append(image);
    loadPrivateImage(image, fullTrack.cover_url);
  }
  await playerAudio.play();
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

async function playPreviousTrack() {
  if (playerAudio.currentTime > 3) { playerAudio.currentTime = 0; return; }
  const index = renderedTracks.findIndex(track => track.job_id === currentTrackId);
  const previous = renderedTracks.slice(0, Math.max(0, index)).reverse().find(track => track.streamable);
  if (previous) await streamTrack(previous);
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

function makeTrackDeleteButton(track) {
  const jobId = track.job_id || track.id;
  const remove = document.createElement("button");
  remove.type = "button";
  remove.className = "track-delete";
  remove.title = "Supprimer définitivement";
  remove.setAttribute("aria-label", `${remove.title} : ${track.title || track.file_name || "ce son"}`);
  remove.append(document.getElementById("icon-trash").content.cloneNode(true));
  remove.addEventListener("click", async () => {
    const name = track.title || track.file_name || "ce morceau";
    if (!await siteDialogs.confirm(`Supprimer définitivement « ${name} » ? Le fichier et son lien seront effacés. Le son sera retiré des playlists. Tout téléchargement en cours sera annulé.`, { confirmText: "Supprimer" })) return;
    remove.disabled = true;
    try {
      await requestJson(`/api/library/${jobId}`, { method: "DELETE" });
      deletedJobIds.add(jobId);
      if (currentTrackId === jobId) resetPlayer();
      const download = downloadButtonByJob.get(jobId);
      if (download) {
        download.disabled = !activeProviders[track.provider];
        setDownloadLabel(download, download.dataset.defaultLabel || "Télécharger");
        delete download.dataset.jobId;
        downloadButtonByJob.delete(jobId);
      }
      const coverUrl = `/api/covers/${jobId}`;
      if (coverUrlCache.has(coverUrl)) URL.revokeObjectURL(coverUrlCache.get(coverUrl));
      coverUrlCache.delete(coverUrl);
      liveQueueTracks = liveQueueTracks.filter(job => job.id !== jobId);
      libraryState.tracks = libraryState.tracks.filter(item => item.job_id !== jobId);
      renderLibrary();
      await refreshQueue();
      showToast("Morceau supprimé.");
    } catch (error) { showToast(error.message, "error"); }
    finally { remove.disabled = false; }
  });
  return remove;
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

function makeLibraryCover(track) {
  const cover = document.createElement('div'); cover.className = 'library-cover';
  const initial = document.createElement('span'); initial.textContent = (track.title || '?').trim().slice(0, 1).toUpperCase();
  cover.append(initial);
  const url = track.cover_url;
  if (url && ((url.startsWith('/') && !url.startsWith('//')) || url.startsWith('https://'))) {
    const image = document.createElement('img'); image.alt = ''; image.loading = 'lazy';
    image.addEventListener('load', () => cover.dataset.loaded = 'true');
    if (coverUrlCache.has(url)) cover.dataset.loaded = 'true';
    cover.append(image);
    if (url.startsWith('/api/')) loadPrivateImage(image, url); else image.src = url;
  }
  return cover;
}

function makeTrackBody(track) {
  const body = document.createElement('div'); body.className = 'library-track-body';
  const title = document.createElement('strong');
  title.textContent = track.title || track.file_name || 'Sans titre'; title.title = title.textContent;
  const artist = document.createElement('span');
  artist.textContent = track.artist || 'Artiste inconnu'; artist.title = artist.textContent;
  body.append(title, artist);
  return body;
}

function makeTrackMetrics(analysis = {}) {
  const bpm = document.createElement('span'); bpm.className = 'track-bpm';
  const tempo = parseFloat(analysis.bpm);
  bpm.textContent = Number.isFinite(tempo) && tempo > 0 ? tempo.toFixed(2) : '—';
  const key = document.createElement('span'); key.className = 'track-key';
  key.textContent = analysis.camelot || analysis.key || '—';
  const duration = document.createElement('span'); duration.className = 'track-duration';
  const seconds = Number(analysis.duration);
  duration.textContent = Number.isFinite(seconds) && seconds > 0
    ? `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, '0')}`
    : /^\d+:\d{2}$/.test(String(analysis.duration || '')) ? analysis.duration : '—';
  return [bpm, key, duration];
}

function makeTrackStatus(label, detail = '') {
  const button = document.createElement('button'); button.type = 'button';
  button.className = 'download-status-button'; button.disabled = true;
  button.textContent = label; button.title = detail || label;
  return button;
}


function makeRenameButton(item, collection) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "collection-rename";
  button.title = collection === "playlists" ? "Renommer la playlist" : "Renommer le tag";
  button.setAttribute("aria-label", `${button.title} : ${item.name}`);
  button.append(document.getElementById("icon-pencil").content.cloneNode(true));
  button.addEventListener("click", async () => {
    const value = await siteDialogs.prompt(button.title, item.name, { maxLength: collection === "tags" ? 40 : 80 });
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

function makeCollectionExportButton(item, collection) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "collection-rename collection-export";
  button.title = "Exporter la playlist (.txt)";
  button.setAttribute("aria-label", `${button.title} : ${item.name}`);
  import("/vendor/mixer-icons.js").then(({ createElement, LogOut }) => {
    const icon = createElement(LogOut);
    icon.setAttribute("aria-hidden", "true");
    button.append(icon);
  });
  button.addEventListener("click", async () => {
    button.disabled = true;
    try {
      const result = await requestJson(`/api/${collection}/${item.id}/export?format=tunesaver`);
      if (!result.links.length) {
        showToast("Aucun lien de morceau téléchargé à exporter.", "error");
        return;
      }
      const blob = new Blob([result.text || result.links.join("\n") + "\n"], { type: "text/plain;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      const name = result.name.replace(/[<>:"/\\|?*\x00-\x1f\x7f]/g, "_").trim().slice(0, 80);
      link.href = url;
      link.download = `${collection === "tags" ? "tag" : "playlist"}-${name || "tunesaver"}.txt`;
      document.body.append(link);
      try { link.click(); }
      finally { link.remove(); setTimeout(() => URL.revokeObjectURL(url), 30000); }
      showToast(`${result.links.length} lien(s) exporté(s).${result.skipped ? ` ${result.skipped} morceau(x) non téléchargé(s) ou sans lien ignoré(s).` : ""}`);
    } catch (error) { showToast(error.message, "error"); }
    finally { button.disabled = false; }
  });
  return button;
}

function collectionRowLabel(button, name, count) {
  button.replaceChildren();
  button.title = name;
  const label = document.createElement('span'); label.className = 'collection-name'; label.textContent = name;
  const total = document.createElement('span'); total.className = 'collection-count'; total.textContent = count;
  button.append(label, total);
}

const downloadingCollections = new Set();

document.querySelector('#collection-download').addEventListener('click', async () => {
  const id = selectedPlaylistId;
  if (!id || downloadingCollections.has(id)) return;
  const item = (libraryState.imports || []).find(item => item.playlist_id === id && ['pending', 'ready', 'running', 'matched'].includes(item.state));
  if (item && ['pending', 'running'].includes(item.state)) return;
  downloadingCollections.add(id);
  renderCollectionActions();
  try {
    if (item) {
      const maxTracks = Number(importLimits.get(item.id) || 0);
      if (!Number.isSafeInteger(maxTracks) || maxTracks < 0) throw new Error('Nombre de titres invalide.');
      await requestJson(`/api/playlist-imports/${item.id}/start`, { method: 'POST', body: JSON.stringify({ max_tracks: maxTracks, download: true }) });
      importPreviews.delete(item.id);
      await refreshLibrary();
    } else {
      const result = await requestJson(`/api/playlists/${id}/download-ticket`, { method: 'POST', body: '{}' });
      if (result.skipped && !await siteDialogs.confirm(`${result.count} fichier(s) disponible(s). ${result.skipped} son(s) en cours, en échec ou indisponible(s) seront exclus. Télécharger les fichiers disponibles ?`)) return;
      const link = document.createElement('a');
      link.href = result.url; link.download = '';
      document.body.append(link); link.click(); link.remove();
      showToast('Préparation du ZIP. Le téléchargement va démarrer.');
    }
  } catch (error) { showToast(error.message, 'error'); }
  finally { downloadingCollections.delete(id); renderCollectionActions(); }
});

function renderCollectionActions() {
  const collection = selectedPlaylistId ? 'playlists' : selectedTagId ? 'tags' : '';
  const item = selectedPlaylistId ? libraryState.playlists.find(p => p.id === selectedPlaylistId)
    : libraryState.tags.find(t => t.id === selectedTagId);
  const menu = document.querySelector('#collection-menu');
  const download = document.querySelector('#collection-download');
  const importing = (libraryState.imports || []).find(entry => entry.playlist_id === selectedPlaylistId && ['pending', 'ready', 'running', 'matched'].includes(entry.state));
  download.hidden = collection !== 'playlists' || !item;
  download.disabled = downloadingCollections.has(selectedPlaylistId) || Boolean(importing && ['pending', 'running'].includes(importing.state));
  download.title = importing ? (download.disabled ? 'Téléchargement en préparation' : 'Télécharger') : 'Télécharger la playlist en ZIP';
  menu.hidden = !item;
  if (!item || menu.open) return;
  const actions = document.querySelector('#collection-actions');
  actions.replaceChildren();
  const rename = makeRenameButton(item, collection);
  rename.append(document.createTextNode('Renommer'));
  actions.append(rename);
  const exportButton = makeCollectionExportButton(item, collection);
  exportButton.append(document.createTextNode('Exporter la playlist (.txt)'));
  actions.append(exportButton);
  if (collection === 'playlists') {
    if (importing?.state === 'ready') {
      const label = document.createElement('label'); label.textContent = 'Titres (0 = tous)';
      const limit = document.createElement('input'); limit.type = 'number'; limit.min = '0'; limit.step = '1';
      limit.className = 'import-limit'; limit.value = importLimits.get(importing.id) || '0';
      limit.setAttribute('aria-label', 'Titres par playlist ou album, 0 pour tous');
      limit.addEventListener('input', () => importLimits.set(importing.id, limit.value));
      label.append(limit); actions.append(label);
    }
  }
  const remove = document.createElement('button'); remove.type = 'button'; remove.className = 'collection-delete';
  remove.append(document.getElementById('icon-trash').content.cloneNode(true), document.createTextNode('Supprimer'));
  remove.addEventListener('click', async () => {
    if (!await siteDialogs.confirm(`Supprimer « ${item.name} » ? Les sons seront conservés dans Mes sons.`, { confirmText: 'Supprimer' })) return;
    try {
      await requestJson(`/api/${collection}/${item.id}`, { method: 'DELETE' });
      selectedPlaylistId = selectedTagId = '';
      await refreshLibrary();
    } catch (error) { showToast(error.message, 'error'); }
  });
  actions.append(remove);
}

function renderPlaylists() {
  const key = JSON.stringify([libraryState.playlists, selectedPlaylistId, selectedTagId]);
  if (playlistList.renderKey === key) return;
  playlistList.renderKey = key;
  playlistList.replaceChildren();
  const root = document.createElement("div");
  root.className = "root-playlists";
  playlistList.append(root);
  for (const playlist of libraryState.playlists) {
    const row = document.createElement("div");
    row.className = "playlist-row playlist-entry";
    const button = document.createElement("button");
    button.className = "playlist-filter";
    button.type = "button";
    button.dataset.playlistId = playlist.id;
    button.dataset.collection = `playlists:${playlist.id}`;
    button.dataset.selected = playlist.id === selectedPlaylistId ? "true" : "false";
    const readyImport = (libraryState.imports || []).find(item => item.playlist_id === playlist.id && item.state === 'ready');
    collectionRowLabel(button, playlist.name, readyImport ? readyImport.total : playlist.track_ids.length);
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
    row.append(button);
    root.append(row);
  }
  const all = document.querySelector(".playlist-filter[data-playlist-id='']");
  if (all) all.dataset.selected = selectedPlaylistId || selectedTagId ? "false" : "true";
}


function libraryScope() {
  if (selectedPlaylistId) return `playlist:${selectedPlaylistId}`;
  if (selectedTagId) return `tag:${selectedTagId}`;
  return 'library:';
}

function currentLibrarySort() {
  return librarySorts.get(libraryScope()) || { by: 'number', direction: 1 };
}

function manualLibraryTracks() {
  const stored = libraryState.tracks
    .filter(track => trackInPlaylist(track, selectedPlaylistId) && trackInTag(track, selectedTagId));
  const tracks = [...liveTracksForLibrary(), ...stored];
  const collection = selectedPlaylistId ? libraryState.playlists.find(p => p.id === selectedPlaylistId)
    : selectedTagId ? libraryState.tags.find(t => t.id === selectedTagId) : null;
  const order = [...new Set([
    ...(libraryState.orders?.[libraryScope()] || []),
    ...(collection?.track_ids || []),
  ])];
  const ranks = new Map(order.map((id, index) => [id, index]));
  return tracks.sort((a, b) => (ranks.get(a.job_id) ?? ranks.size) - (ranks.get(b.job_id) ?? ranks.size));
}

function sortedLibraryTracks(tracks) {
  const { by, direction } = currentLibrarySort();
  if (by === 'number') return direction === 1 ? [...tracks] : [...tracks].reverse();
  const value = track => {
    if (by === 'bpm') return Number(track.mix_analysis?.bpm) > 0 ? Number(track.mix_analysis.bpm) : null;
    if (by === 'key') return track.mix_analysis?.camelot || track.mix_analysis?.key || null;
    return (by === 'title' ? track.title || track.file_name : track.artist)?.trim() || null;
  };
  return [...tracks].sort((a, b) => {
    const av = value(a), bv = value(b);
    if (av === null || bv === null) return av === bv ? 0 : av === null ? 1 : -1;
    return direction * (by === 'bpm' ? av - bv : trackCollator.compare(av, bv));
  });
}

function changeLibrarySort(by, toggle = false) {
  const previous = currentLibrarySort();
  librarySorts.set(libraryScope(), { by, direction: toggle && previous.by === by ? -previous.direction : 1 });
  renderLibrary();
}
document.querySelectorAll('[data-track-sort]').forEach(button => button.addEventListener('click', () => changeLibrarySort(button.dataset.trackSort, true)));

async function moveLibraryTrack(id, targetId, after) {
  if (savingTrackOrder || id === targetId) return;
  const scope = libraryScope(), account = session;
  const ids = sortedLibraryTracks(manualLibraryTracks()).map(track => track.job_id);
  if (!ids.includes(id) || !ids.includes(targetId)) return;
  ids.splice(ids.indexOf(id), 1);
  ids.splice(ids.indexOf(targetId) + Number(after), 0, id);
  savingTrackOrder = true;
  libraryTracks.setAttribute('aria-busy', 'true');
  try {
    await requestJson('/api/library/order', { method: 'PUT', body: JSON.stringify({ scope, track_ids: ids }) });
    if (session !== account) return;
    libraryState.orders = { ...libraryState.orders, [scope]: ids };
    librarySorts.set(scope, { by: 'number', direction: 1 });
  } catch (error) { if (session === account) showToast(error.message, 'error'); }
  finally {
    savingTrackOrder = false;
    libraryTracks.removeAttribute('aria-busy');
    if (session === account) {
      renderLibrary();
      if (libraryScope() === scope) libraryTracks.querySelector(`[data-job-id="${CSS.escape(id)}"] .track-number`)?.focus({ preventScroll: true });
    }
  }
}

function cancelTrackMove() {
  if (trackMove) cancelAnimationFrame(trackMove.frame);
  trackMove = null;
  libraryTracks.querySelectorAll('[data-reorder], [data-moving]').forEach(row => {
    delete row.dataset.reorder; delete row.dataset.moving;
  });
}

function updateTrackMove() {
  const move = trackMove;
  if (!move?.active) return;
  libraryTracks.querySelectorAll('[data-reorder]').forEach(row => delete row.dataset.reorder);
  const row = document.elementFromPoint(move.x, move.y)?.closest('.library-track');
  move.target = row?.dataset.jobId;
  if (row && move.target !== move.id) {
    const box = row.getBoundingClientRect();
    move.after = move.y > box.y + box.height / 2;
    row.dataset.reorder = move.after ? 'after' : 'before';
  }
}

function beginTrackMove(event, id) {
  if (event.button !== 0 || savingTrackOrder || trackMove) return;
  event.preventDefault(); event.stopPropagation();
  selectLibraryTrack(id);
  event.currentTarget.setPointerCapture(event.pointerId);
  trackMove = { id, pointer: event.pointerId, scope: libraryScope(), startX: event.clientX, startY: event.clientY, x: event.clientX, y: event.clientY, active: false };
  const scroll = () => {
    if (!trackMove) return;
    if (trackMove.active) {
      const bounds = libraryTracks.getBoundingClientRect();
      const delta = trackMove.y < bounds.top + 35 ? -12 : trackMove.y > bounds.bottom - 35 ? 12 : 0;
      if (delta) libraryTracks.scrollTop += delta;
      updateTrackMove();
    }
    trackMove.frame = requestAnimationFrame(scroll);
  };
  trackMove.frame = requestAnimationFrame(scroll);
}
document.addEventListener('pointermove', event => {
  if (trackMove?.pointer !== event.pointerId) return;
  trackMove.x = event.clientX; trackMove.y = event.clientY;
  if (Math.hypot(trackMove.x - trackMove.startX, trackMove.y - trackMove.startY) > 5) {
    trackMove.active = true;
    libraryTracks.querySelector(`[data-job-id="${CSS.escape(trackMove.id)}"]`).dataset.moving = 'true';
  }
  updateTrackMove();
});
document.addEventListener('pointerup', event => {
  if (trackMove?.pointer !== event.pointerId) return;
  const move = trackMove;
  cancelTrackMove();
  if (move.active && move.target && move.scope === libraryScope()) moveLibraryTrack(move.id, move.target, move.after);
  else renderLibrary();
});
document.addEventListener('pointercancel', event => { if (trackMove?.pointer === event.pointerId) { cancelTrackMove(); renderLibrary(); } });
document.addEventListener('keydown', event => { if (event.key === 'Escape' && trackMove) { cancelTrackMove(); renderLibrary(); } });
window.addEventListener('blur', cancelTrackMove);

function renderLibrary() {
  if (draggedLibraryTrack || trackMove || savingTrackOrder) return;
  const scope = libraryScope();
  if (librarySearchScope !== scope) {
    document.querySelector('#library-search').value = '';
    librarySearchScope = scope;
    selectedLibraryTrackId = '';
    libraryTracks.scrollTop = 0;
  }
  renderImports();
  renderPlaylists();
  updateWorkspaceNavigation();
  renderCollectionActions();
  const preview = (libraryState.imports || []).find(item => item.playlist_id === selectedPlaylistId
    && ["pending", "ready", "running", "matched", "cancelled"].includes(item.state));
  if (preview) {
    renderImportPreview(preview);
    return;
  }
  const manualTracks = manualLibraryTracks();
  const numbers = new Map(manualTracks.map((track, index) => [track.job_id, index + 1]));
  const query = document.querySelector('#library-search').value.trim().toLocaleLowerCase();
  const tracks = sortedLibraryTracks(manualTracks)
    .filter(track => `${track.title || track.file_name || ''} ${track.artist || ''}`.toLocaleLowerCase().includes(query));
  const sort = currentLibrarySort();
  document.querySelectorAll('[data-track-sort]').forEach(button => {
    button.setAttribute('aria-pressed', String(button.dataset.trackSort === sort.by));
    button.dataset.direction = button.dataset.trackSort === sort.by ? String(sort.direction) : '';
  });
  renderedTracks = tracks;
  if (!tracks.some(track => track.job_id === selectedLibraryTrackId)) selectedLibraryTrackId = tracks[0]?.job_id || '';
  document.querySelector("#library-title").textContent = currentLibraryTitle();
  libraryCount.textContent = `${tracks.length} son${tracks.length > 1 ? "s" : ""}`;
  const renderKey = JSON.stringify([tracks, [...numbers], sort, selectedPlaylistId, selectedTagId]);
  if (libraryTracks.renderKey === renderKey) return;
  libraryTracks.renderKey = renderKey;
  libraryTracks.replaceChildren();
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
  tracks.forEach((track) => {
    const row = document.createElement("article");
    row.className = "library-track";
    row.draggable = !track.is_live && track.state !== "failed";
    row.dataset.jobId = track.job_id;
    row.dataset.state = track.state || "done";
    row.dataset.selected = String(track.job_id === selectedLibraryTrackId);
    row.tabIndex = 0;
    row.addEventListener('click', () => selectLibraryTrack(track.job_id));
    row.addEventListener('keydown', event => {
      if (event.target === row && event.key === 'Enter') { event.preventDefault(); selectLibraryTrack(track.job_id); }
    });
    row.addEventListener("dragstart", (event) => {
      if (track.is_live || track.state === "failed") {
        event.preventDefault();
        return;
      }
      event.dataTransfer.effectAllowed = "copy";
      event.dataTransfer.setData("application/x-tunesaver-job", track.job_id);
      event.dataTransfer.setData("text/plain", track.job_id);
      row.dataset.dragging = "true";
      draggedLibraryTrack = true;
    });
    row.addEventListener("dragend", () => {
      row.dataset.dragging = "false";
      draggedLibraryTrack = false;
      renderLibrary();
    });
    const cover = makeLibraryCover(track);
    const body = makeTrackBody(track);
    const actions = document.createElement("div");
    actions.className = "library-track-actions";
    const menu = document.createElement('details'); menu.className = 'workspace-menu track-menu';
    const summary = document.createElement('summary');
    summary.title = 'Actions du morceau'; summary.setAttribute('aria-label', `Actions : ${track.title || 'ce son'}`);
    appendUIIcon(summary, 'Settings2');
    const extraActions = document.createElement('div'); extraActions.className = 'workspace-menu-items';
    menu.append(summary, extraActions);
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
      const statusButton = makeTrackStatus(stateLabels[track.state || 'pending'] || 'En file', track.error || '');
      statusButton.classList.add(track.state || 'pending');
      actions.append(statusButton);
    } else if (track.expired) {
      const restore = document.createElement('button'); restore.type = 'button'; restore.className = 'restore-audio';
      restore.title = 'Retélécharger depuis la plateforme'; restore.setAttribute('aria-label', `Retélécharger ${track.title || 'ce son'}`);
      appendUIIcon(restore, 'Download');
      restore.addEventListener('click', async () => {
        restore.disabled = true;
        try { await requestJson(`/api/library/${track.job_id}/redownload`, {method: 'POST', body: '{}'}); await refreshQueue(); }
        catch (error) { showToast(error.message, 'error'); restore.disabled = false; }
      });
      actions.append(restore);
    } else {
      const download = document.createElement("button");
      download.className = "history-download";
      download.type = "button";
      download.title = "Télécharger le fichier";
      download.setAttribute('aria-label', `Télécharger ${track.title || 'ce son'}`);
      appendUIIcon(download, selectedPlaylistId ? 'Download' : 'LogOut');
      download.addEventListener("click", async () => {
        download.disabled = true;
        try { await downloadReadyJob(track.job_id); }
        catch (error) { showToast(error.message, "error"); download.disabled = false; }
      });
      if (selectedPlaylistId) actions.append(download);
      else {
        download.append(document.createTextNode('Télécharger le fichier'));
        extraActions.append(download);
      }
    }
    if (!selectedPlaylistId) actions.append(makeTrackDeleteButton(track));
    if (selectedPlaylistId || selectedTagId) {
      const remove = document.createElement("button");
      remove.className = "playlist-remove-track";
      remove.type = "button";
      remove.title = selectedPlaylistId ? 'Retirer de cette playlist' : 'Retirer ce tag';
      remove.setAttribute('aria-label', `${remove.title} : ${track.title || 'ce son'}`);
      appendUIIcon(remove, 'Minus');
      remove.addEventListener("click", async () => {
        remove.disabled = true;
        const url = selectedPlaylistId
          ? `/api/playlists/${selectedPlaylistId}/tracks/${track.job_id}`
          : `/api/tags/${selectedTagId}/tracks/${track.job_id}`;
        try {
          await requestJson(url, { method: "DELETE" });
          await refreshLibrary();
        } catch (error) {
          showToast(error.message, 'error');
          remove.disabled = false;
        }
      });
      if (selectedPlaylistId) actions.append(remove);
      else {
        remove.append(document.createTextNode(remove.title));
        extraActions.append(remove);
      }
    }
    const [bpm, key, duration] = makeTrackMetrics(track.mix_analysis);
    if (extraActions.childElementCount) actions.insertBefore(menu, actions.querySelector('.track-delete'));
    const number = document.createElement('button');
    number.type = 'button'; number.className = 'track-number';
    number.textContent = numbers.get(track.job_id);
    number.title = `Déplacer ${track.title || track.file_name || 'ce son'}`;
    number.setAttribute('aria-label', `${number.title}, position ${number.textContent}`);
    number.addEventListener('pointerdown', event => beginTrackMove(event, track.job_id));
    number.addEventListener('dragstart', event => { event.preventDefault(); event.stopPropagation(); });
    number.addEventListener('keydown', event => {
      if (!event.altKey || !['ArrowUp', 'ArrowDown'].includes(event.key)) return;
      event.preventDefault();
      const index = renderedTracks.findIndex(item => item.job_id === track.job_id);
      const target = renderedTracks[index + (event.key === 'ArrowDown' ? 1 : -1)];
      if (target) moveLibraryTrack(track.job_id, target.job_id, event.key === 'ArrowDown');
    });
    row.append(number, cover, body, bpm, key, duration, actions);
    libraryTracks.append(row);
  });
}

function selectLibraryTrack(id) {
  selectedLibraryTrackId = id;
  libraryTracks.querySelectorAll('.library-track').forEach(row => { row.dataset.selected = String(row.dataset.jobId === id); });
}

document.querySelector('#library-search').addEventListener('input', renderLibrary);

const dismissedImports = new Set((() => {
  try {
    const stored = JSON.parse(localStorage.getItem("tunesaver.dismissedImports") || "[]");
    return Array.isArray(stored) ? stored.filter(id => typeof id === "string").slice(-100) : [];
  } catch (_) { return []; }
})());

const importPreviews = new Map();
const loadingImportPreviews = new Set();
const importLimits = new Map();

function renderImportPreview(item) {
  renderedTracks = [];
  const jobs = new Map([...liveTracksForLibrary(), ...libraryState.tracks].map(track => [track.job_id, track]));
  const query = document.querySelector('#library-search').value.trim().toLocaleLowerCase();
  const sort = currentLibrarySort();
  document.querySelectorAll('[data-track-sort]').forEach(button => {
    button.setAttribute('aria-pressed', String(button.dataset.trackSort === sort.by));
    button.dataset.direction = button.dataset.trackSort === sort.by ? String(sort.direction) : '';
  });
  const renderKey = JSON.stringify(['preview', item.id, item.state, item.processed, item.activity, importPreviews.has(item.id), query, sort, [...jobs.values()]]);
  if (libraryTracks.renderKey === renderKey) return;
  libraryTracks.renderKey = renderKey;
  document.querySelector("#library-title").textContent = currentLibraryTitle();
  libraryCount.textContent = `${item.total} sons`;
  libraryTracks.replaceChildren();
  if (item.state === "pending") {
    const message = document.createElement("p");
    message.className = "empty-copy";
    message.textContent = "Lecture de la playlist…";
    libraryTracks.append(message);
    return;
  }
  const tracks = importPreviews.get(item.id);
  if (!tracks) {
    if (!loadingImportPreviews.has(item.id)) {
      loadingImportPreviews.add(item.id);
      const account = session;
      requestJson(`/api/playlist-imports/${item.id}`).then(payload => {
        if (session !== account) return;
        importPreviews.set(item.id, payload.tracks);
        if (selectedPlaylistId === item.playlist_id) renderLibrary();
      }).catch(error => {
        libraryTracks.renderKey = null;
        showToast(error.message);
      }).finally(() => loadingImportPreviews.delete(item.id));
    }
    return;
  }
  const rows = tracks.map((track, index) => ({ ...track, number: index + 1 }))
    .filter(track => `${track.title} ${track.artist}`.toLocaleLowerCase().includes(query));
  const orderedRows = sortedLibraryTracks(rows.map(track => ({ ...track, mix_analysis: track.matching?.candidate || track })));
  const fragment = document.createDocumentFragment();
  for (const track of orderedRows) {
    const row = document.createElement("article");
    row.className = "library-track import-preview-track";
    const candidate = track.matching?.candidate || track;
    const number = document.createElement('span'); number.className = 'track-number'; number.textContent = track.number;
    const cover = makeLibraryCover({...track, cover_url: candidate.cover_url || track.cover_url});
    const body = makeTrackBody(track);
    const [bpm, key, duration] = makeTrackMetrics({...candidate, duration: track.duration || candidate.duration});
    const actions = document.createElement('div'); actions.className = 'library-track-actions';
    row.append(number, cover, body, bpm, key, duration, actions);
    const matching = track.matching;
    if (matching) {
      if (matching.target?.url?.startsWith('https://')) {
        actions.append(makeSourceBadge({provider: matching.target.provider === 'tidal' ? 'tiddl' : matching.target.provider, source_url: matching.target.url}));
      }
      const job = jobs.get(matching.job_id);
      const states = { searching: 'Recherche', not_found: 'Introuvable', ambiguous: 'À vérifier', unavailable: 'Indisponible', error: 'Erreur' };
      const label = matching.job_id ? (job?.state === 'done' ? '' : stateLabels[job?.state || 'pending']) : states[matching.status];
      if (label) actions.append(makeTrackStatus(label, matching.error));
    } else if (item.state !== 'ready' && track.number > item.total) {
      actions.append(makeTrackStatus('Non sélectionné', 'Hors limite sélectionnée'));
    }
    fragment.append(row);
  }
  libraryTracks.append(fragment);
}

function renderImports() {
  const area = document.querySelector("#playlist-imports");
  if (area.contains(document.activeElement) && document.activeElement.classList.contains('import-limit')) return;
  const imports = (libraryState.imports || []).filter(item => !dismissedImports.has(item.id) && !['ready', 'matched'].includes(item.state)
    && (!selectedPlaylistId || item.playlist_id === selectedPlaylistId));
  area.hidden = !imports.length;
  const expanded = new Set(Array.from(area.querySelectorAll("details[open]")).map(el => el.dataset.id));
  area.replaceChildren();
  const labels = { pending: "Lecture des titres", ready: "Prête", running: "Recherche des titres", matched: "Recherche terminée", cancelled: "Arrêté", done: "Titres mis en téléchargement", partial: "Import partiel", failed: "Import impossible" };
  for (const item of imports) {
    const row = document.createElement("details");
    row.className = "playlist-import";
    row.dataset.id = item.id;
    row.open = expanded.has(item.id);
    const summary = document.createElement("summary");
    const name = document.createElement("strong");
    name.textContent = item.name;
    const status = document.createElement("span");
    status.textContent = item.state === "ready" ? `${item.total} titres · Aucun téléchargement lancé`
      : `${labels[item.state] || item.state}${item.total ? ` · ${item.processed}/${item.total} · ${item.matched || 0} trouvés · ${item.queued} en file` : ""}${item.activity ? ' · ' + item.activity : ''}`;
    summary.append(name, status);
    if (["done", "partial", "failed", "cancelled"].includes(item.state)) {
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
        if (area.hidden) searchNav.focus({ preventScroll: true });
      });
      summary.append(close);
    }
    const progress = document.createElement("progress");
    progress.max = item.total || 1;
    if (item.total || !["pending", "running"].includes(item.state)) progress.value = item.processed;
    progress.setAttribute("aria-label", `Import ${item.name}`);
    row.append(summary);
    if (item.state !== "ready") row.append(progress);
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
    for (const item of libraryState.imports || []) {
      if (['running', 'matched', 'cancelled'].includes(item.state)) importPreviews.delete(item.id);
    }
    libraryState.tracks = libraryState.tracks.filter(track => !deletedJobIds.has(track.job_id));
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
  selectedPlaylistId = "";
  selectedTagId = "";
  renderLibrary();
});


createPlaylistButton.addEventListener("click", async () => {
  const name = await siteDialogs.prompt("Nom de la playlist", "", { maxLength: 80 });
  if (!name) return;
  createPlaylistButton.disabled = true;
  try {
    const result = await requestJson("/api/playlists", {
      method: "POST",
      body: JSON.stringify({ name }),
    });
    selectedPlaylistId = result.playlist.id;
    selectedTagId = '';
    setWorkspaceView('library');
    await refreshLibrary();
  } catch (error) {
    showToast(error.message, "error");
  } finally {
    createPlaylistButton.disabled = false;
  }
});



let historyItems = [], historyLocalJobs = [], historyFingerprint = '';
function renderHistory(history, localJobs) {
  historyItems = history;
  historyLocalJobs = localJobs;
  const localById = new Map(localJobs.map((job) => [job.id, job]));
  const recent = document.querySelector("#recent-jobs");
  for (const state of Object.keys(stateLabels)) {
    document.querySelector(`#${state}-count`).textContent = String(history.filter(job => job.state === state).length);
  }
  const savings = history
    .filter((job) => job.state === "done")
    .reduce((total, job) => total + Number(job.estimated_savings_cents || 0), 0);
  savingsTotal.textContent = new Intl.NumberFormat("fr-FR", {
    style: "currency",
    currency: "EUR",
  }).format(savings / 100);
  const query = document.querySelector('#history-search').value.trim().toLocaleLowerCase('fr');
  const state = document.querySelector('#history-state').value;
  const filtered = history.filter(job => (!state || job.state === state)
    && (!query || `${job.title || ''} ${job.artist || ''} ${job.job_id}`.toLocaleLowerCase('fr').includes(query)));
  document.querySelector('#history-count').textContent = `${filtered.length} téléchargement${filtered.length > 1 ? 's' : ''}`;
  const fingerprint = JSON.stringify([history.length, filtered, localJobs.map(job => [job.id, job.download_ready])]);
  if (fingerprint === historyFingerprint) return;
  historyFingerprint = fingerprint;
  const scrollTop = recent.scrollTop;
  const focused = document.activeElement?.closest('.recent-job');
  const focusedAction = document.activeElement?.classList.contains('track-delete') ? '.track-delete' : '.history-download';
  recent.replaceChildren();
  if (!filtered.length) {
    const empty = document.createElement("p");
    empty.className = "empty-copy";
    empty.textContent = history.length ? 'Aucun téléchargement ne correspond.' : "Aucun téléchargement dans ton historique.";
    recent.append(empty);
    return;
  }
  for (const job of filtered) {
    const local = localById.get(job.job_id);
    const row = document.createElement("div");
    row.className = "recent-job";
    row.dataset.jobId = job.job_id;
    const provider = document.createElement("span");
    provider.className = `mini-provider ${job.provider}`;
    provider.textContent = providerLabels[job.provider] || job.provider;
    const label = document.createElement("span");
    label.className = "job-label";
    const title = document.createElement('strong'); title.textContent = job.title || job.job_id;
    const artist = document.createElement('span'); artist.textContent = job.artist || '';
    label.append(title, artist); label.title = [title.textContent, job.artist].filter(Boolean).join(' · ');
    const date = document.createElement('time'); date.className = 'history-date';
    const timestamp = job.created_at || job.completed_at;
    date.textContent = formatAdminDate(timestamp) || '—';
    if (timestamp && Number.isFinite(Date.parse(timestamp))) date.dateTime = new Date(timestamp).toISOString();
    const status = document.createElement('span'); status.className = `job-state ${job.state}`;
    status.textContent = stateLabels[job.state] || job.state;
    const actions = document.createElement('div'); actions.className = 'history-row-actions';
    if (local?.download_ready) {
      const action = document.createElement("button");
      action.className = "history-download";
      action.type = "button";
      action.title = 'Récupérer le fichier'; action.setAttribute('aria-label', `Récupérer : ${job.title || job.job_id}`);
      appendUIIcon(action, 'Download');
      action.addEventListener("click", async () => {
        action.disabled = true;
        try { await downloadReadyJob(job.job_id); }
        catch (error) { showToast(error.message, "error"); }
        finally { action.disabled = false; }
      });
      actions.append(action);
    }
    actions.append(makeTrackDeleteButton(job));
    row.append(provider, label, date, status, actions);
    recent.append(row);
  }
  recent.scrollTop = scrollTop;
  if (focused) recent.querySelector(`[data-job-id="${CSS.escape(focused.dataset.jobId)}"] ${focusedAction}`)?.focus({ preventScroll: true });
}

async function refreshQueue() {
  if (!session || queueRefreshPromise) return queueRefreshPromise;
  queueRefreshPromise = refreshQueueOnce().finally(() => { queueRefreshPromise = null; });
  return queueRefreshPromise;
}

async function refreshQueueOnce() {
  const account = session;
  refreshStatusButton.disabled = true;
  try {
    const payload = await requestJson("/api/status", { method: "GET" });
    if (session !== account) return;
    payload.recent = (payload.recent || []).filter(job => !deletedJobIds.has(job.id));
    for (const provider of providers) {
      activeProviders[provider] = payload.active?.[provider] === true;
      for (const button of document.querySelectorAll(`.download-button[data-provider="${provider}"]`)) {
        if (button.textContent === "Ajouté ✓" || button.textContent === "Déjà en file") continue;
        if (!activeProviders[provider]) {
          button.disabled = true;
          setDownloadLabel(button, "Service arrêté");
        } else if (button.textContent === "Service arrêté") {
          button.disabled = false;
          setDownloadLabel(button, button.dataset.defaultLabel || "Télécharger");
        }
      }
    }
    liveQueueTracks = payload.recent || [];
    updateDownloadButtons(liveQueueTracks);
    let history = (payload.recent || []).map((job) => ({ ...job, job_id: job.id }));
    try {
      await syncHistory(payload.recent || []);
      history = await fetchHistory();
    } catch (error) {
      if (session !== account) return;
      if (!historyWarningShown) {
        showToast(`Historique local indisponible : ${error.message}`, "error");
        historyWarningShown = true;
      }
    }
    if (session !== account) return;
    renderHistory(history.filter(job => !deletedJobIds.has(job.job_id)), payload.recent || []);
    await refreshLibrary();
  } catch (error) {
    if (session !== account) return;
    for (const provider of providers) {
      activeProviders[provider] = false;
    }
    showToast(error.message || "Impossible de lire la file.", "error");
  } finally {
    refreshStatusButton.disabled = false;
  }
}

refreshStatusButton.addEventListener("click", refreshQueue);

historyToggle.addEventListener("click", async () => {
  setWorkspaceView('history');
  await refreshQueue();
});
document.querySelector('#history-search').addEventListener('input', () => renderHistory(historyItems, historyLocalJobs));
document.querySelector('#history-state').addEventListener('change', () => renderHistory(historyItems, historyLocalJobs));

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
    details.textContent = profile.last_download_at
      ? `Dernier téléchargement : ${formatAdminDate(profile.last_download_at)}`
      : 'Aucun téléchargement';
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
      const password = await siteDialogs.prompt(`Nouveau mot de passe pour ${label}`, "", { type: "password" });
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
      if (!await siteDialogs.confirm(`Supprimer définitivement ${label} ?`, { confirmText: "Supprimer" })) return;
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
    role: "group",
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
  adminDailyStats.forEach((item, index) => {
    const hit = chartSvgElement('rect', {
      class: 'chart-day-hit', x: margin.left + step * index, y: margin.top,
      width: step, height: height - margin.top, fill: 'transparent',
      tabindex: 0, role: 'button',
      'aria-label': `${item.date} : ${item.tracks} sons, voir les utilisateurs`,
    });
    hit.addEventListener('click', () => openAdminDay(item));
    hit.addEventListener('keydown', event => {
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        openAdminDay(item);
      }
    });
    svg.append(hit);
  });
}

const adminDayDialog = document.querySelector('#admin-day-dialog');
document.querySelector('#admin-day-close').addEventListener('click', () => adminDayDialog.close());
function openAdminDay(item) {
  document.querySelector('#admin-day-title').textContent = new Intl.DateTimeFormat('fr-FR', {dateStyle: 'long'})
    .format(new Date(`${item.date}T12:00:00`));
  document.querySelector('#admin-day-total').textContent = `${item.tracks} son${item.tracks > 1 ? 's' : ''} téléchargé${item.tracks > 1 ? 's' : ''}`;
  const pie = document.querySelector('#admin-day-pie');
  const list = document.querySelector('#admin-day-users');
  list.replaceChildren();
  const colors = ['#5ee6ad', '#62b9f6', '#f7be62', '#ed829f', '#b39af5', '#e5df72', '#66d9da', '#ef9867'];
  const stops = [];
  let position = 0;
  for (const [index, user] of (item.users || []).entries()) {
    const percent = item.tracks ? user.tracks / item.tracks * 100 : 0;
    const color = colors[index % colors.length];
    stops.push(`${color} ${position}% ${position + percent}%`);
    position += percent;
    const row = document.createElement('li');
    const swatch = document.createElement('span');
    swatch.className = 'admin-day-swatch';
    swatch.style.background = color;
    const name = document.createElement('span');
    name.textContent = user.email;
    const count = document.createElement('strong');
    count.textContent = `${user.tracks} · ${percent.toLocaleString('fr-FR', {maximumFractionDigits: 1})} %`;
    row.append(swatch, name, count);
    list.append(row);
  }
  pie.style.background = stops.length ? `conic-gradient(${stops.join(',')})` : '#363636';
  pie.setAttribute('aria-label', item.tracks ? 'Répartition des sons par utilisateur, détaillée dans la liste' : 'Aucun téléchargement ce jour');
  if (!item.tracks) list.textContent = 'Aucun téléchargement ce jour.';
  adminDayDialog.showModal();
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

const playerAudio = document.querySelector('#player-audio');
const playerTitle = document.querySelector('#player-title');
const playerArtist = document.querySelector('#player-artist');
const playerCover = document.querySelector('#player-cover');
let playerVersion = 0;
playerAudio.addEventListener('ended', playNextTrack);
playerAudio.addEventListener('error', () => {
  if (playerAudio.hasAttribute('src')) showToast('Lecture impossible. Réessaie de lancer le morceau.', 'error');
});
for (const [id, icon, action] of [
  ['player-previous', 'SkipBack', playPreviousTrack],
  ['player-next', 'SkipForward', playNextTrack],
]) {
  const button = document.getElementById(id);
  appendUIIcon(button, icon);
  button.addEventListener('click', () => action().catch(error => showToast(error.message, 'error')));
}

start();
