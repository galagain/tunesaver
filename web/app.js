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
const userEmail = document.querySelector("#user-email");
const form = document.querySelector("#search-form");
const input = document.querySelector("#search-input");
const searchButton = document.querySelector("#search-button");
const searchMessage = document.querySelector("#search-message");
const resultsSection = document.querySelector("#results-section");
const resultsQuery = document.querySelector("#results-query");
const refreshStatusButton = document.querySelector("#refresh-status");
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
  authMessage.textContent = message;
}

function showApp() {
  saveAdminSession(null);
  authView.hidden = true;
  appView.hidden = false;
  adminView.hidden = true;
  services.hidden = false;
  logoutButton.hidden = false;
  userEmail.textContent = session?.user?.email || "Mon compte";
  refreshQueue();
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

function makeResultCard(result) {
  const card = document.createElement("article");
  card.className = "result-card";
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
  details.textContent = result.details || "Disponible";
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
      download.textContent = response.duplicate ? "Déjà en file" : "Ajouté ✓";
      showToast("Le fichier sera disponible dans ton historique.");
      await refreshQueue();
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
  const payload = await requestJson("/api/downloads", {
    method: "POST",
    body: JSON.stringify({ links, confirm_complete: true }),
  });
  showToast(`${payload.jobs.length} téléchargement${payload.jobs.length > 1 ? "s" : ""} ajouté${payload.jobs.length > 1 ? "s" : ""}.`);
  resultsSection.hidden = true;
  await refreshQueue();
}

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
    if (lines.length > 1) {
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
