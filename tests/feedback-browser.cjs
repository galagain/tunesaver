const {chromium, expect} = require('@playwright/test');

(async () => {
  const browser = await chromium.launch();
  try {
    for (const width of [1440, 390]) {
      const page = await browser.newPage({viewport: {width, height: 900}});
      const errors = [], entries = [];
      let detail, unread = false, screenshot;
      page.on('pageerror', e => errors.push(e.message));
      await page.route('**/api/**', async route => {
        const path = new URL(route.request().url()).pathname;
        if (path === '/api/config') return route.continue();
        if (path.includes('/feedback')) {
          const action = path.split('/feedback')[1];
          const data = route.request().method() === 'POST' ? route.request().postDataJSON() : null;
          if (!action && data) {
            screenshot = data.screenshot;
            entries.push({id: 'one', title: data.title, kind: data.kind, status: 'new', email: 'alice@example.com', updated_at: new Date().toISOString()});
            detail = {id: 'one', title: data.title, kind: data.kind, status: 'new', revision: 1, context: data.context, has_screenshot: Boolean(screenshot),
              messages: [{author: 'user', body: data.body, created_at: new Date().toISOString()}]};
            return route.fulfill({status: 201, json: {id: 'one'}});
          }
          if (!action) return route.fulfill({json: {threads: entries.map(t => ({...t, unread}))}});
          if (action.endsWith('/screenshot')) return route.fulfill({contentType: 'image/png', body: Buffer.from(screenshot, 'base64')});
          if (action.endsWith('/comment')) {
            detail.messages.push({author: path.includes('/admin/') ? 'admin' : 'user', body: data.body, created_at: new Date().toISOString()});
            detail.revision++; unread = true;
          } else if (action.endsWith('/status')) {
            detail.status = data.status; entries[0].status = data.status; detail.revision++; unread = true;
          } else if (action.endsWith('/read')) unread = false;
          if (data) return route.fulfill({json: {saved: true}});
          return route.fulfill({json: detail});
        }
        return route.fulfill({json: {tracks: [], playlists: [], imports: [], tags: [], orders: {}, active: {}, counts: {}, recent: [], history: [], profiles: [], daily: []}});
      });
      await page.goto(process.env.MIXER_URL || 'http://127.0.0.1:8095');
      await page.waitForFunction(() => publicConfig !== null);
      await page.evaluate(() => {session = {access_token: 'fixture', user: {email: 'alice@example.com'}}; showApp(); clearInterval(queueTimer);});
      await page.locator('#feedback-open').click();
      await expect(page.locator('#feedback-create')).toBeVisible();
      await page.locator('#feedback-subject').fill('Lecture interrompue');
      await page.locator('#feedback-body').fill('Le lecteur s’arrête après quelques secondes. <img src=x onerror=alert(1)>');
      const png = await page.evaluate(() => {const c = document.createElement('canvas'); c.width = c.height = 10; return c.toDataURL().split(',')[1];});
      await page.locator('#feedback-file').setInputFiles({name: 'capture.png', mimeType: 'image/png', buffer: Buffer.from(png, 'base64')});
      await page.locator('#feedback-create button[type=submit]').click();
      await expect(page.locator('#feedback-thread-title')).toHaveText('Lecture interrompue');
      await expect(page.locator('#feedback-image')).toBeVisible();
      expect(await page.locator('#feedback-image').evaluate(img => img.complete && img.naturalWidth > 0)).toBe(true);
      expect(detail.context.section).toBe('library');
      await expect(page.locator('#feedback-messages img')).toHaveCount(0);
      await page.locator('#feedback-comment').fill('Cela arrive aussi sur ma seconde playlist.');
      await page.locator('#feedback-reply button').click();
      await expect(page.locator('.feedback-message')).toHaveCount(2);
      await page.screenshot({path: `/work/feedback-user-${width}.png`});
      await page.locator('#feedback-close').click();
      await page.evaluate(() => {showAuth(); adminSession = {access_token: 'admin'}; showAdmin();});
      await page.locator('#feedback-admin-open').click();
      await page.locator('#feedback-list button').click();
      await expect(page.locator('#feedback-new')).toBeHidden();
      await page.locator('#feedback-comment').fill('Merci, le correctif est en ligne.');
      await page.locator('#feedback-reply button').click();
      await expect(page.locator('.feedback-message-admin')).toHaveCount(1);
      await page.locator('#feedback-status').selectOption('resolved');
      await expect(page.locator('#feedback-status-label')).toHaveText('Résolu');
      await page.screenshot({path: `/work/feedback-admin-${width}.png`});
      await page.locator('#feedback-filter').selectOption('bugs');
      await expect(page.locator('#feedback-list button')).toHaveCount(0);
      await page.keyboard.press('Escape');
      await expect(page.locator('#feedback-dialog')).toBeHidden();
      unread = true;
      await page.evaluate(() => {showAuth(); session = {access_token: 'fixture'}; showApp(); clearInterval(queueTimer);});
      await expect(page.locator('#feedback-open .feedback-badge')).toHaveText('1');
      await page.locator('#feedback-open').click();
      await page.locator('#feedback-list button').click();
      await expect(page.locator('#feedback-open .feedback-badge')).toBeHidden();
      expect(errors).toEqual([]);
      await page.close();
      console.log(`${width}: create, comment, admin reply/status, filters, XSS-safe text and close OK`);
    }
  } finally {await browser.close();}
})().catch(error => {console.error(error); process.exitCode = 1;});
