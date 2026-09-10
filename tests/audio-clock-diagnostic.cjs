// Isolated signal-level comparison; this does not measure the user's hardware latency.
const { chromium, firefox } = require('@playwright/test');
const fs = require('node:fs');

(async () => {
  const name = process.env.MIXER_BROWSER || 'chromium', isFirefox = name === 'firefox';
  const folder = fs.readdirSync('/ms-playwright').find(n => n.startsWith(name + '-'));
  const browser = await (isFirefox ? firefox : chromium).launch({
    executablePath: `/ms-playwright/${folder}/${isFirefox ? 'firefox/firefox' : 'chrome-linux64/chrome'}`,
    ...(isFirefox ? { firefoxUserPrefs: { 'media.autoplay.default': 0 } } : { args: ['--no-sandbox', '--autoplay-policy=no-user-gesture-required'] }),
  });
  try {
    const page = await browser.newPage();
    await page.route('http://localhost:9099/**', r => r.fulfill({ contentType: 'text/html', body: '<title>Audio clock diagnostic</title>' }));
    await page.goto('http://localhost:9099/');
    const results = await page.evaluate(async () => {
      const context = new AudioContext({ latencyHint: 'interactive' });
      await context.resume();
      const rate = context.sampleRate, frames = rate * 2;
      const pcm = new ArrayBuffer(44 + frames * 2), view = new DataView(pcm);
      const str = (offset, s) => [...s].forEach((c, i) => view.setUint8(offset + i, c.charCodeAt(0)));
      str(0, 'RIFF'); view.setUint32(4, pcm.byteLength - 8, true); str(8, 'WAVEfmt ');
      view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
      view.setUint32(24, rate, true); view.setUint32(28, rate * 2, true);
      view.setUint16(32, 2, true); view.setUint16(34, 16, true); str(36, 'data'); view.setUint32(40, frames * 2, true);
      let state = 13;
      for (let i = 0; i < frames; i++) {
        state = (Math.imul(state, 1664525) + 1013904223) >>> 0;
        view.setInt16(44 + i * 2, Math.round((state / 2 ** 32 - .5) * 20000), true);
      }
      const buffer = await context.decodeAudioData(pcm.slice(0));
      const url = URL.createObjectURL(new Blob([pcm], { type: 'audio/wav' }));
      const media = [new Audio(url), new Audio(url)];
      await Promise.all(media.map(m => new Promise((resolve, reject) => { m.oncanplaythrough = resolve; m.onerror = reject; m.load(); })));
      const moduleURL = URL.createObjectURL(new Blob([`
        class Capture extends AudioWorkletProcessor {
          constructor() { super(); this.left=[]; this.right=[]; this.active=false;
            this.port.onmessage=()=>{this.left=[];this.right=[];this.active=true;}; }
          process(inputs) {
            if(this.active) {
              const channels=inputs[0];
              for(let i=0;i<128;i++){this.left.push(channels[0]?.[i]||0);this.right.push(channels[1]?.[i]||0);}
              if(this.left.length>=sampleRate*.4){this.port.postMessage([this.left,this.right]);this.active=false;}
            }
            return true;
          }
        }
        registerProcessor('capture', Capture);
      `], { type: 'application/javascript' }));
      await context.audioWorklet.addModule(moduleURL);
      const capture = new AudioWorkletNode(context, 'capture', { channelCount: 2, channelCountMode: 'explicit', channelInterpretation: 'discrete' });
      const merger = context.createChannelMerger(2);
      merger.connect(capture); capture.connect(context.destination);
      const sources = media.map(m => context.createMediaElementSource(m));
      const startCapture = () => new Promise(resolve => { capture.port.onmessage = e => resolve(e.data); capture.port.postMessage('capture'); });
      const summarize = ([left, right]) => {
        const a = left.findIndex(v => Math.abs(v) > .02), b = right.findIndex(v => Math.abs(v) > .02);
        if (a < 0 || b < 0) throw new Error('Silent capture');
        let difference = 0, energy = 0;
        for (let i = Math.max(a, b) + 4096; i < left.length; i++) {
          difference += (left[i] - right[i]) ** 2; energy += left[i] ** 2 + right[i] ** 2;
        }
        return { onsetDifferenceMs: (b - a) * 1000 / rate, residual: difference / energy };
      };
      const results = { sampleRate: rate, media: [], scheduled: [] };
      for (let trial = 0; trial < 6; trial++) {
        sources.forEach((s, i) => s.connect(merger, 0, i));
        media.forEach(m => { m.currentTime = 0; });
        await Promise.all(media.map(m => m.seeking ? new Promise(resolve => m.addEventListener('seeked', resolve, { once: true })) : Promise.resolve()));
        const measured = startCapture();
        await new Promise(resolve => setTimeout(resolve, 30));
        await Promise.all(media.map(m => m.play()));
        results.media.push(summarize(await measured));
        media.forEach(m => m.pause()); sources.forEach(s => s.disconnect());
        const pair = [0, 1].map(i => { const s = context.createBufferSource(); s.buffer = buffer; s.connect(merger, 0, i); return s; });
        const reference = startCapture(), when = context.currentTime + .05;
        pair.forEach(s => s.start(when));
        results.scheduled.push(summarize(await reference));
        pair.forEach(s => { s.stop(); s.disconnect(); });
      }
      results.mediaSeekSync = [];
      sources.forEach((s, i) => s.connect(merger, 0, i));
      for (let trial = 0; trial < 5; trial++) {
        media.forEach((m, i) => { m.pause(); m.currentTime = .1 + i * .08; });
        await Promise.all(media.map(m => m.seeking ? new Promise(resolve => m.addEventListener('seeked', resolve, { once: true })) : Promise.resolve()));
        await Promise.all(media.map(m => m.play()));
        await new Promise(resolve => setTimeout(resolve, 100));
        media[0].currentTime = media[1].currentTime;
        const [left, right] = await startCapture();
        let best = -Infinity, lag = 0;
        for (let shift = -4096; shift <= 4096; shift++) {
          let dot = 0, aa = 0, bb = 0;
          for (let i = 9000; i < 10024; i++) {
            const a = left[i], b = right[i + shift];
            dot += a * b; aa += a * a; bb += b * b;
          }
          const score = dot / Math.sqrt(aa * bb);
          if (score > best) { best = score; lag = shift; }
        }
        results.mediaSeekSync.push({ signalLagMs: lag * 1000 / rate, correlation: best,
          reportedTimeDifferenceMs: (media[1].currentTime - media[0].currentTime) * 1000 });
      }
      media.forEach(m => m.pause());
      URL.revokeObjectURL(url); URL.revokeObjectURL(moduleURL); await context.close();
      return results;
    });
    console.log(JSON.stringify({ browser: name, ...results }, null, 2));
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
