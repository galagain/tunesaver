const form = document.querySelector('#transfer-form');
const button = form.querySelector('button');
const status = document.querySelector('#transfer-status');
const account = document.querySelector('#transfer-account');
const loginFields = document.querySelector('#transfer-login');
let sharedSession = null;
function storedSession() {
  try { return JSON.parse(localStorage.getItem('tunesaver.session') || 'null'); }
  catch { return null; }
}
function showLogin() {
  sharedSession = null;
  loginFields.hidden = false;
  loginFields.querySelectorAll('input').forEach(input => input.disabled = false);
  account.textContent = 'Connecte-toi avec le compte autorisé par l’administrateur.';
}
async function recognizeSession() {
  showLogin();
  let session = storedSession();
  if (!session?.access_token) return;
  button.disabled = true;
  try {
    const check = token => fetch('/api/account-transfer/session', {headers:{Authorization:`Bearer ${token}`},cache:'no-store'});
    let response = await check(session.access_token);
    if (response.status === 401 && session.refresh_token) {
      const refreshed = await fetch('/api/session/refresh', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({refresh_token:session.refresh_token}),cache:'no-store'});
      const value = await refreshed.json();
      if (!refreshed.ok) throw new Error(value.error || 'Reconnecte-toi pour continuer.');
      if (storedSession()?.access_token !== session.access_token) return;
      localStorage.setItem('tunesaver.session', JSON.stringify(value));
      session = value;
      response = await check(session.access_token);
    }
    const value = await response.json();
    if (!response.ok) throw new Error(value.error || 'Reconnecte-toi pour continuer.');
    if (storedSession()?.access_token !== session.access_token) return;
    sharedSession = session;
    loginFields.hidden = true;
    loginFields.querySelectorAll('input').forEach(input => input.disabled = true);
    account.textContent = `Connecté avec ${value.email}`;
    status.textContent = '';
  } catch (error) { status.textContent = error.message; }
  finally { button.disabled = false; }
}
const ready = recognizeSession();
window.addEventListener('storage', event => {
  if (event.key === 'tunesaver.session') recognizeSession();
});
form.addEventListener('submit', async event => {
  event.preventDefault();
  await ready;
  button.disabled = true; status.textContent = 'Préparation…';
  let session = sharedSession, temporary = false;
  try {
    const fields = new FormData(form);
    const providers = fields.getAll('provider');
    if (!providers.length) throw new Error('Choisis au moins une plateforme.');
    if (session && storedSession()?.access_token !== session.access_token) {
      showLogin(); throw new Error('La session a changé. Recharge la page.');
    }
    if (!session) {
      const login = await fetch('/api/account-transfer/login', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({email:fields.get('email'),password:fields.get('password')}), cache:'no-store'});
      session = await login.json();
      form.elements.password.value = '';
      if (!login.ok) throw new Error(session.error || 'Connexion impossible.');
      temporary = true;
    }
    const response = await fetch('/api/account-export', {method:'POST',headers:{'Content-Type':'application/json','Authorization':`Bearer ${session.access_token}`},body:JSON.stringify({providers}),cache:'no-store'});
    if (!response.ok) {
      if (response.status === 401 || response.status === 403) showLogin();
      throw new Error((await response.json()).error || 'Export impossible.');
    }
    const url = URL.createObjectURL(await response.blob());
    const link = document.createElement('a'); link.href = url; link.download = 'tunesaver-accounts.json'; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    status.textContent = 'Fichier téléchargé. Importe-le dans Mes plateformes sur ton Mac.';
  } catch (error) { status.textContent = error.message; }
  finally {
    // Never log out the main TuneSaver session after an export.
    if (temporary && session?.access_token) await fetch('/api/logout', {method:'POST',headers:{'Content-Type':'application/json','Authorization':`Bearer ${session.access_token}`},body:'{}'}).catch(()=>{});
    button.disabled = false;
  }
});
