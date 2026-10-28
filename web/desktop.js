/* Account settings for the native Mac application. Never persist secrets in Web Storage. */
(() => {
  const names = {tiddl: 'TIDAL', beatport: 'Beatport', qobuz: 'Qobuz'};
  let timer, latest, inFlight = false;
  const button = document.createElement('button');
  button.id = 'desktop-accounts-open';
  button.type = 'button';
  button.className = 'desktop-settings-button';
  button.textContent = 'Mes plateformes';
  const dialog = document.createElement('dialog');
  dialog.id = 'desktop-accounts';
  dialog.setAttribute('aria-labelledby', 'desktop-accounts-title');
  dialog.innerHTML = `<header><div><span class="desktop-eyebrow">TUNESAVER POUR MAC</span>
    <h2 id="desktop-accounts-title">Ta musique. Tes comptes.</h2></div>
    <button type="button" id="desktop-close" aria-label="Fermer">×</button></header>
    <p class="desktop-intro">Connecte les plateformes que tu utilises. Ta bibliothèque reste sur ce Mac et tes connexions sont mémorisées dans le Trousseau macOS.</p>
    <div id="desktop-account-cards"></div>
    <p id="desktop-error" role="alert"></p>
    <footer><div><strong>Une bibliothèque qui reste à toi</strong>
    <p>Les morceaux sont conservés jusqu’à leur suppression. Les recherches et imports nécessitent Internet et les droits d’accès de ton abonnement.</p></div>
    <button type="button" id="desktop-open-music">Afficher dans le Finder</button></footer>`;
  document.body.append(dialog);
  const cards = dialog.querySelector('#desktop-account-cards');
  for (const [provider, name] of Object.entries(names)) {
    const card = document.createElement('section');
    card.className = 'desktop-account-card';
    card.dataset.provider = provider;
    card.innerHTML = `<div class="desktop-account-heading"><h3>${name}</h3><span class="desktop-account-state">Non connecté</span></div>
      <p class="desktop-account-message" role="status"></p>
      <form autocomplete="off"></form>
      <a class="desktop-authorize" target="_blank" rel="noopener noreferrer" hidden>Continuer sur TIDAL</a>
      <button type="button" class="desktop-disconnect" hidden>Déconnecter ce compte</button>`;
    const form = card.querySelector('form');
    if (provider === 'beatport') {
      form.innerHTML = `<label>Identifiant Beatport<input name="username" autocomplete="username" required maxlength="320"></label>
        <label>Mot de passe<input name="password" type="password" autocomplete="current-password" required maxlength="1024"></label>
        <button type="submit">Connecter Beatport</button>`;
    } else if (provider === 'qobuz') {
      form.innerHTML = `<button type="button" class="desktop-qobuz-login">Se connecter sur Qobuz</button>
        <details><summary>Connexion avec un jeton de session</summary>
        <p>Depuis ta session sur play.qobuz.com : copie les champs id et user_auth_token de localuser dans le stockage local du navigateur.</p>
        <label>Identifiant du compte<input name="user_id" inputmode="numeric" required pattern="[0-9]+" maxlength="40"></label>
        <label>Jeton de session<input name="token" type="password" required maxlength="8192"></label>
        <button type="submit">Connecter avec ce jeton</button></details>
        <p class="desktop-notice">Le téléchargement Qobuz est actuellement affecté par un changement de leur API. La connexion et la recherche restent disponibles.</p>`;
      form.querySelector('.desktop-qobuz-login').onclick = () => {
        if (window.webkit?.messageHandlers?.desktop) window.webkit.messageHandlers.desktop.postMessage({action: 'qobuzLogin'});
        else { form.querySelector('details').open = true; }
      };
    } else {
      form.innerHTML = `<p>Autorise la connexion directement sur le site de TIDAL.</p><button type="submit">Connecter TIDAL</button>`;
    }
    form.addEventListener('submit', async event => {
      event.preventDefault();
      const payload = Object.fromEntries(new FormData(form));
      await connect(provider, payload);
      form.reset();
    });
    card.querySelector('.desktop-disconnect').onclick = async () => {
      try {
        await requestJson(`/api/desktop/accounts/${provider}`, {method: 'DELETE', body: '{}'});
        await refresh();
      } catch (error) { showError(error.message); }
    };
    cards.append(card);
  }
  function showError(message) { dialog.querySelector('#desktop-error').textContent = message || ''; }
  async function connect(provider, payload) {
    try {
      showError('');
      const card = cards.querySelector(`[data-provider="${provider}"]`);
      card.querySelectorAll('input, button').forEach(el => {el.disabled = true;});
      await requestJson(`/api/desktop/accounts/${provider}`, {method: 'POST', body: JSON.stringify(payload)});
    } catch (error) { showError(error.message); }
    finally { await refresh(); }
  }
  window.tunesaverConnectQobuz = payload => connect('qobuz', payload);
  async function refresh() {
    if (inFlight) return;
    inFlight = true;
    try {
      latest = await requestJson('/api/desktop/accounts');
      if (latest.storage_error) showError(latest.storage_error);
      for (const [provider, account] of Object.entries(latest.accounts)) {
        const card = cards.querySelector(`[data-provider="${provider}"]`);
        const pending = account.state === 'pending';
        card.dataset.connected = String(Boolean(account.connected));
        card.querySelector('.desktop-account-state').textContent = pending ? 'Connexion…' : account.connected ? 'Connecté' : 'Non connecté';
        card.querySelector('.desktop-account-message').textContent = account.message || '';
        card.querySelector('form').hidden = Boolean(account.connected);
        card.querySelectorAll('input, button').forEach(el => {el.disabled = pending;});
        const disconnect = card.querySelector('.desktop-disconnect');
        disconnect.hidden = !account.connected && !(pending && provider === 'tiddl');
        disconnect.disabled = false;
        disconnect.textContent = pending ? 'Annuler la connexion' : 'Déconnecter ce compte';
        const link = card.querySelector('.desktop-authorize');
        link.hidden = !account.authorization_url || !pending;
        if (!link.hidden) {
          link.href = account.authorization_url;
          link.textContent = account.code ? `Continuer sur TIDAL · ${account.code}` : 'Continuer sur TIDAL';
        } else link.removeAttribute('href');
      }
      button.textContent = `Mes plateformes · ${Object.values(latest.accounts).filter(a => a.connected).length}/3`;
    } catch (error) { showError(error.message); }
    finally { inFlight = false; }
  }
  function open() {
    if (!dialog.open) dialog.showModal();
    refresh();
    clearInterval(timer);
    timer = setInterval(refresh, 2000);
  }
  button.onclick = open;
  dialog.querySelector('#desktop-close').onclick = () => dialog.close();
  dialog.addEventListener('close', () => clearInterval(timer));
  dialog.querySelector('#desktop-open-music').onclick = () => {
    if (window.webkit?.messageHandlers?.desktop) window.webkit.messageHandlers.desktop.postMessage({action: 'openMusic'});
    else showError(latest?.music_directory || 'Dossier local indisponible.');
  };
  window.addEventListener('desktop-ready', async () => {
    document.querySelector('.sidebar-footer').prepend(button);
    await refresh();
    if (latest && !Object.values(latest.accounts).some(account => account.connected)) open();
  }, {once: true});
})();
