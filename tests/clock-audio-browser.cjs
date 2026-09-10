const { chromium, firefox, expect } = require('@playwright/test');
const fs = require('node:fs');

function fixture(tone = false, bpm = 0) {
  const rate = 44100, frames = rate * 12, data = Buffer.alloc(44 + frames * 2);
  data.write('RIFF'); data.writeUInt32LE(data.length - 8, 4); data.write('WAVEfmt ', 8);
  data.writeUInt32LE(16, 16); data.writeUInt16LE(1, 20); data.writeUInt16LE(1, 22);
  data.writeUInt32LE(rate, 24); data.writeUInt32LE(rate * 2, 28); data.writeUInt16LE(2, 32); data.writeUInt16LE(16, 34);
  data.write('data', 36); data.writeUInt32LE(frames * 2, 40);
  let seed = 13;
  for (let i = 0; i < frames; i++) {
    seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0;
    const t = i / rate, beat = bpm ? t % (60 / bpm) : 0;
    const value = bpm ? Math.sin(beat * Math.PI * 880) * .5 * Math.exp(-beat * 90)
      : tone ? Math.sin(t * Math.PI * 880) * .3 : (seed / 2 ** 32 - .5) * .6;
    data.writeInt16LE(Math.round(value * 32767), 44 + i * 2);
  }
  return data;
}

(async () => {
  const isFirefox = process.env.MIXER_BROWSER === 'firefox', engine = isFirefox ? firefox : chromium;
  const folder = fs.readdirSync('/ms-playwright').find(n => n.startsWith(isFirefox ? 'firefox-' : 'chromium-'));
  const browser = await engine.launch({ executablePath: `/ms-playwright/${folder}/${isFirefox ? 'firefox/firefox' : 'chrome-linux64/chrome'}`,
    ...(isFirefox ? { firefoxUserPrefs: { 'media.autoplay.default': 0 } } : { args: ['--no-sandbox', '--autoplay-policy=no-user-gesture-required'] }) });
  try {
    const page = await browser.newPage({ bypassCSP: true }), errors = [];
    page.on('pageerror', e => errors.push(e.message));
    page.on('console', m => { if (m.type() === 'error') console.error(m.text()); });
    await page.route('**/fixture/clock*.wav', r => r.fulfill({ contentType: 'audio/wav', body: fixture(r.request().url().includes('tone'), Number(/clock-(120|128)/.exec(r.request().url())?.[1]) || 0) }));
    const recorder = `
      class Recorder extends AudioWorkletProcessor {
        constructor(){super();this.recording=false;this.port.onmessage=()=>{this.left=[];this.right=[];this.recording=true;};}
        process(inputs){
          if(this.recording){for(let i=0;i<128;i++){this.left.push(inputs[0][0]?.[i]||0);this.right.push(inputs[0][1]?.[i]||0);}
            if(this.left.length>sampleRate*.5){this.recording=false;this.port.postMessage([this.left,this.right]);}}
          return true;
        }
      }
      registerProcessor('clock-recorder',Recorder);
    `;
    await page.goto(process.env.MIXER_URL || 'http://127.0.0.1:8082');
    await page.evaluate(source => { window.__recorderSource = source; }, recorder);
    await page.evaluate(async () => {
      const { ClockMedia, createStretch, audioDeadline } = await import('/mix-clock.js');
      window.__ctx = new AudioContext({ latencyHint: 'interactive' });
      await __ctx.resume();
      window.__at = () => audioDeadline(__ctx);
      window.__make = async url => {
        const m = new ClockMedia(__ctx, () => createStretch(__ctx));
        m.src = url;
        await m.prepare(url, new AbortController().signal).catch(e => { throw Error('Prepare: ' + e.message); });
        return m;
      };
      window.__players = await Promise.all([__make('/fixture/clock.wav'), __make('/fixture/clock.wav')]);
      const recorderURL = URL.createObjectURL(new Blob([__recorderSource], { type: 'text/javascript' }));
      await __ctx.audioWorklet.addModule(recorderURL);
      URL.revokeObjectURL(recorderURL);
      window.__capture = new AudioWorkletNode(__ctx, 'clock-recorder', { channelCount: 2, channelCountMode: 'explicit', channelInterpretation: 'discrete' });
      window.__merger = __ctx.createChannelMerger(2);
      __players.forEach((p, i) => p.output.connect(__merger, 0, i));
      __merger.connect(__capture); __capture.connect(__ctx.destination);
      window.__record = () => new Promise(resolve => { __capture.port.onmessage = e => resolve(e.data); __capture.port.postMessage('record'); });
      window.__measure = async () => {
        const [a, b] = await __record(), start = Math.floor(__ctx.sampleRate * .3), size = 2048;
        let diff = 0, energy = 0, best = -Infinity, lag = 0;
        for(let i=start;i<start+size;i++){diff+=(a[i]-b[i])**2;energy+=a[i]**2+b[i]**2;}
        for(let shift=-2205;shift<=2205;shift++){
          let dot=0,aa=0,bb=0;
          for(let i=start;i<start+size;i++){dot+=a[i]*b[i+shift];aa+=a[i]**2;bb+=b[i+shift]**2;}
          const score=dot/Math.sqrt(aa*bb);if(score>best){best=score;lag=shift;}
        }
        return { lagMs:lag*1000/__ctx.sampleRate, correlation:best, residual:diff/energy, energy };
      };
    });
    const measurements = [];
    for (let i = 0; i < 5; i++) {
      await page.evaluate(() => { const at = __at(); __players.forEach(p => p.seekAt(.4, at, true)); });
      const result = await page.evaluate(() => __measure());
      measurements.push(result);
      expect(result.energy).toBeGreaterThan(1);
      expect(result.lagMs).toBe(0);
      expect(result.residual).toBeLessThan(1e-10);
      await page.evaluate(() => { __players[0].currentTime += .08; });
      await page.waitForTimeout(120);
      await page.evaluate(() => { const at = __at(); __players[0].seekAt(__players[1].positionAt(at), at); });
      const aligned = await page.evaluate(() => __measure());
      measurements.push(aligned);
      expect(aligned.lagMs).toBe(0);
      expect(aligned.residual).toBeLessThan(1e-10);
    }
    // Master tempo uses the real WASM processor, with no external CDN or relaxed JS eval.
    await page.evaluate(async () => {
      __players.forEach(p => p.dispose());
      __players = await Promise.all([__make('/fixture/clock-tone.wav'), __make('/fixture/clock-tone.wav')]);
      __players.forEach((p, i) => { p.output.connect(__merger, 0, i); p.preservesPitch = i === 0; p.playbackRate = 1.08; });
      const at = __at(); __players.forEach(p => p.seekAt(.5, at, true));
    });
    const pitches = await page.evaluate(async () => {
      const samples = await __record();
      return samples.map(data => {
        const crossings = [];
        for(let i=Math.floor(__ctx.sampleRate*.25);i<data.length-1;i++) if(data[i]<=0&&data[i+1]>0) crossings.push(i);
        return (crossings.length-1)*__ctx.sampleRate/(crossings.at(-1)-crossings[0]);
      });
    });
    expect(Math.abs(pitches[0] - 440)).toBeLessThan(8);
    expect(Math.abs(pitches[1] - 475.2)).toBeLessThan(3);
    await page.evaluate(async () => {
      __players.forEach(p => p.dispose());
      __players = await Promise.all([__make('/fixture/clock-120.wav'), __make('/fixture/clock-128.wav')]);
      __players.forEach((p, i) => { p.output.connect(__merger, 0, i); p.playbackRate = i ? 1 : 128 / 120; });
      const at = __at(); __players.forEach(p => p.seekAt(0, at, true));
    });
    const beatLags = [];
    for (let trial = 0; trial < 4; trial++) {
      await page.waitForTimeout(300);
      const lag = await page.evaluate(async () => {
        const samples = await __record();
        const peak = data => {
          let best = -1, index = 0;
          for (let i = 64; i < data.length - 64; i++) {
            let energy = 0;
            for (let j = -64; j < 64; j++) energy += data[i + j] ** 2;
            if (energy > best) { best = energy; index = i; }
          }
          return index;
        };
        return (peak(samples[0]) - peak(samples[1])) * 1000 / __ctx.sampleRate;
      });
      beatLags.push(lag);
      expect(Math.abs(lag)).toBeLessThan(6);
    }
    await page.evaluate(() => { __players.forEach(p => p.dispose()); return __ctx.close(); });
    expect(errors).toEqual([]);
    console.log(JSON.stringify({ browser: engine.name(), measurements, pitches, beatLags }));
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
