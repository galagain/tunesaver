const { chromium, firefox, expect } = require('@playwright/test');

(async () => {
  const engine = process.env.MIXER_BROWSER === 'firefox' ? firefox : chromium;
  const browser = await engine.launch();
  try {
    for (const width of [1440, 390]) {
      const page = await browser.newPage({ viewport: { width, height: 900 } });
      const errors = [];
      page.on('pageerror', e => errors.push(e.message));
      let status = { active: { tiddl: true, beatport: false, qobuz: true }, counts: {}, recent: [], services: {
        tiddl: { state: 'inactive', message: 'Compte TIDAL FREE : abonnement requis pour accéder aux sons.' },
        beatport: { state: 'inactive', message: 'Moteur de téléchargement arrêté.' },
        qobuz: { state: 'active', message: 'Moteur démarré, connexion et abonnement vérifiés.' },
      } };
      await page.route('**/api/status', route => route.fulfill({ json: status }));
      await page.route('**/api/history*', route => route.fulfill({ json: { history: [] } }));
      await page.route('**/api/library', route => route.fulfill({ json: { tracks: [], playlists: [], tags: [], folders: [], imports: [] } }));
      await page.goto(process.env.MIXER_URL || 'http://127.0.0.1:8082');
      await page.waitForFunction(() => window.Mixer && publicConfig !== null);
      await page.evaluate(() => { session = { access_token: 'fixture', user: { email: 'fixture@example.com' } }; showApp(); });
      await expect(page.locator('#tiddl-service')).toHaveAttribute('data-status', 'inactive');
      await expect(page.locator('#tiddl-service')).toHaveAttribute('title', /FREE/);
      await expect(page.locator('#qobuz-service')).toHaveAttribute('data-status', 'active');
      await expect(page.locator('#beatport-service')).toHaveAttribute('data-status', 'inactive');
      await page.locator('#tiddl-service').focus();
      await expect(page.locator('#tiddl-service')).toHaveAttribute('aria-label', /abonnement requis/);
      await page.screenshot({ path: `/work/service-status-${engine.name()}-${width}.png`, fullPage: true });
      await page.waitForFunction(() => queueRefreshPromise === null);
      status.services.tiddl = { state: 'active', message: 'Abonnement vérifié.' };
      status.services.qobuz = { state: 'unknown', message: 'Connexion non vérifiable pour le moment.' };
      await page.evaluate(() => refreshQueue());
      await expect(page.locator('#tiddl-service')).toHaveAttribute('data-status', 'active');
      await expect(page.locator('#qobuz-service')).toHaveAttribute('data-status', 'unknown');
      delete status.services;
      await page.evaluate(() => refreshQueue());
      await expect(page.locator('#tiddl-service')).toHaveAttribute('data-status', 'unknown');
      await page.route('**/api/status', route => route.fulfill({ status: 503, json: { error: 'Test outage' } }));
      await page.evaluate(() => refreshQueue());
      for (const id of ['tiddl', 'qobuz', 'beatport']) await expect(page.locator(`#${id}-service`)).toHaveAttribute('data-status', 'unknown');
      await page.evaluate(() => showAuth());
      await expect(page.locator('#services')).toBeHidden();
      expect(errors).toEqual([]);
      await page.close();
      console.log(`${engine.name()} ${width}: stopped worker, FREE account, verified account, recovery and unknown status OK`);
    }
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
