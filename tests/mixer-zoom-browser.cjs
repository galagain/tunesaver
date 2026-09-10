const { chromium, firefox, expect } = require('@playwright/test');

(async () => {
  const engine = process.env.MIXER_BROWSER === 'firefox' ? firefox : chromium;
  const browser = await engine.launch();
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.route('**/api/status', r => r.fulfill({ json: { active: {}, counts: {}, recent: [] } }));
    await page.route('**/api/history*', r => r.fulfill({ json: { history: [] } }));
    await page.route('**/api/library', r => r.fulfill({ json: { tracks: [], playlists: [], tags: [], folders: [], imports: [] } }));
    await page.goto(process.env.MIXER_URL || 'http://127.0.0.1:8082');
    await page.waitForFunction(() => window.Mixer && publicConfig !== null);
    await page.evaluate(() => { session = { access_token: 'fixture', user: { email: 'fixture@example.com' } }; showApp(); });
    await page.locator('#mixer-nav').click();
    const canvas = page.locator('.mix-timeline-lane canvas').first(), zoom = page.locator('#mix-wave-zoom');
    await expect(zoom).toHaveValue('12');
    const wheel = (options, selector = '#mix-timelines canvas') => page.evaluate(({ options, selector }) => {
      const event = new WheelEvent('wheel', { bubbles: true, cancelable: true, ...options });
      document.querySelector(selector).dispatchEvent(event);
      return event.defaultPrevented;
    }, { options, selector });
    await canvas.hover();
    await page.keyboard.down('Shift');
    const scroll = await page.evaluate(() => scrollY);
    await page.mouse.wheel(0, -100);
    await expect(zoom).toHaveValue('13');
    await page.mouse.wheel(0, 100);
    await expect(zoom).toHaveValue('12');
    await page.keyboard.up('Shift');
    expect(await page.evaluate(() => scrollY)).toBe(scroll);
    expect(await wheel({ deltaY: -100 })).toBe(false);
    expect(await wheel({ deltaY: -100, shiftKey: true, ctrlKey: true })).toBe(false);
    expect(await wheel({ deltaY: -100, shiftKey: true, metaKey: true })).toBe(false);
    expect(await wheel({ deltaY: -100, shiftKey: true }, '#library-search')).toBe(false);
    await expect(zoom).toHaveValue('12');
    expect(await wheel({ deltaX: -100, shiftKey: true })).toBe(true);
    await expect(zoom).toHaveValue('13');
    await wheel({ deltaY: 3, deltaMode: 1, shiftKey: true });
    await expect(zoom).toHaveValue('12');
    await wheel({ deltaY: -1, deltaMode: 2, shiftKey: true });
    await expect(zoom).toHaveValue('13');
    await page.evaluate(() => {
      for (let i = 0; i < 5; i++) document.querySelector('#mix-timelines canvas').dispatchEvent(
        new WheelEvent('wheel', { bubbles: true, cancelable: true, shiftKey: true, deltaY: 20 }));
    });
    await expect(zoom).toHaveValue('12');
    await wheel({ deltaY: -100000, shiftKey: true });
    await expect(zoom).toHaveValue('24');
    await wheel({ deltaY: 100000, shiftKey: true });
    await expect(zoom).toHaveValue('1');
    await wheel({ deltaY: -100, shiftKey: true });
    await expect(zoom).toHaveValue('2');
    await zoom.evaluate(input => { input.value = ''; });
    await wheel({ deltaY: -100, shiftKey: true });
    await expect(zoom).toHaveValue('13');
    expect(errors).toEqual([]);
    console.log(`${engine.name()}: Shift+wheel zoom, native scroll, modifiers, delta units, touchpad and bounds OK`);
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
