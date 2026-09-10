const fs = require('node:fs');
const path = require('node:path');

// Observe the media facade without adding a diagnostics API to production.
exports.observeClockMedia = async page => {
  const source = fs.readFileSync(path.join(__dirname, '../web/mix-clock.js'), 'utf8');
  await page.route('**/mix-clock.js*', route => route.fulfill({ contentType: 'text/javascript',
    body: source.replace('this.timer = setInterval', 'globalThis.__observeClockMedia?.(this);\n    this.timer = setInterval') }));
};
