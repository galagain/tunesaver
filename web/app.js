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
const authSubmit = document.querySelector("#auth-submit");
const authToggle = document.querySelector("#auth-toggle");
const authIntro = document.querySelector("#auth-intro");
const authMessage = document.querySelector("#auth-message");
const appView = document.querySelector("#app-view");
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
const savingsTotal = document.querySelector("#savings-total");
const toast = document.querySelector("#toast");

let publicConfig = null;
let session = null;
let authMode = "login";
let toastTimer = null;
let queueTimer = null;
let queueRefreshPromise = null;
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

async function supabaseAuth(path, body, accessToken = "") {
  const response = await fetch(`${publicConfig.supabase_url}${path}`, {
    method: "POST",
    headers: {
      apikey: publicConfig.supabase_publishable_key,
      "Content-Type": "application/json",
      ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}),
    },
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

async function refreshSession() {
  if (!session?.refresh_token) throw new Error("Session expirée");
  const refreshed = await supabaseAuth(
    "/auth/v1/token?grant_type=refresh_token",
    { refresh_token: session.refresh_token }
  );
  saveSession(refreshed);
  return refreshed;
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

async function supabaseRest(path, options = {}) {
  const { expectJson = true, ...fetchOptions } = options;
  const response = await authenticatedFetch(`${publicConfig.supabase_url}/rest/v1/${path}`, {
    ...fetchOptions,
    headers: {
      apikey: publicConfig.supabase_publishable_key,
      "Content-Type": "application/json",
      ...(fetchOptions.headers || {}),
    },
  });
  if (!expectJson) {
    if (!response.ok) await parseResponse(response);
    return null;
  }
  return parseResponse(response);
}

function showAuth(message = "") {
  window.clearInterval(queueTimer);
  queueTimer = null;
  saveSession(null);
  authView.hidden = false;
  appView.hidden = true;
  services.hidden = true;
  logoutButton.hidden = true;
  authMessage.textContent = message;
}

function showApp() {
  authView.hidden = true;
  appView.hidden = false;
  services.hidden = false;
  logoutButton.hidden = false;
  userEmail.textContent = session?.user?.email || "Mon compte";
  refreshQueue();
  window.clearInterval(queueTimer);
  queueTimer = window.setInterval(refreshQueue, 5000);
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
  authMessage.textContent = "";
});

authForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  authSubmit.disabled = true;
  authMessage.textContent = "";
  try {
    const path = authMode === "signup"
      ? "/auth/v1/signup"
      : "/auth/v1/token?grant_type=password";
    const result = await supabaseAuth(path, {
      email: authEmail.value.trim(),
      password: authPassword.value,
    });
    if (!result.access_token) {
      authMessage.textContent = "Compte créé. Vérifie ton e-mail avant de te connecter.";
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
  if (session?.access_token) {
    try { await supabaseAuth("/auth/v1/logout", {}, session.access_token); } catch (_error) { /* local logout still applies */ }
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
    user_id: session.user.id,
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
  await supabaseRest("download_history?on_conflict=job_id", {
    method: "POST",
    headers: { Prefer: "resolution=merge-duplicates,return=minimal" },
    body: JSON.stringify(rows),
    expectJson: false,
  });
  lastHistoryFingerprint = fingerprint;
}

async function fetchHistory() {
  const history = [];
  const pageSize = 500;
  for (let offset = 0; ; offset += pageSize) {
    const page = await supabaseRest(
      `download_history?select=*&order=created_at.desc&limit=${pageSize}&offset=${offset}`
    );
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
        showToast(`Historique Supabase indisponible : ${error.message}`, "error");
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

async function start() {
  try {
    await loadConfig();
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
