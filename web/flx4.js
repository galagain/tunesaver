// FLX4 MIDI addresses: Pioneer DDJ-FLX4 MIDI Message List E1.
// Cross-checked against Mixxx's Pioneer-DDJ-FLX4.midi.xml.
export const clamp = (value, min, max) => Math.min(max, Math.max(min, value));
export const crossfade = (value) => [Math.cos(value * Math.PI / 2), Math.sin(value * Math.PI / 2)];

export class FLX4Decoder {
  constructor() { this.msb = new Map(); }

  decode(bytes) {
    if (bytes.length !== 3 || bytes.some((v, i) => !Number.isInteger(v) || v < 0 || v > (i ? 127 : 255))) return null;
    const [status, key, value] = bytes;
    const type = status & 0xf0;
    const channel = status & 0x0f;
    const pressed = type === 0x90 && value > 0;
    if (type === 0x90 || type === 0x80) {
      if (channel === 6 && pressed && [0x41, 0x42].includes(key)) return { action: "browseFocus", back: key === 0x42 };
      if (channel === 6 && pressed && [0x46, 0x47].includes(key)) return { action: "load", deck: key - 0x46 };
      if ([7, 8, 9, 10].includes(channel) && key < 8 && pressed) {
        return { action: "hotcue", deck: channel < 9 ? 0 : 1, pad: key, clear: channel === 8 || channel === 10 };
      }
      if (channel > 1) return null;
      if (key === 0x3f) return { action: "shift", deck: channel, value: pressed };
      if ([0x36, 0x67].includes(key)) return { action: "jogTouch", deck: channel, value: pressed };
      if (!pressed) return null;
      const action = { 11: "play", 12: "cue", 72: "start", 84: "headphone", 16: "loopIn", 17: "loopOut", 77: "loop", 88: "matchTempo" }[key];
      return action ? { action, deck: channel } : null;
    }
    if (type !== 0xb0) return null;
    if (channel === 6 && key === 0x40) return { action: "browse", value: value < 64 ? value : value - 128 };
    if (channel < 2 && [0x21, 0x22, 0x23, 0x29].includes(key)) {
      return { action: key === 0x29 ? "seek" : key === 0x21 ? "jog" : "platter", deck: channel, value: value - 64 };
    }
    const base = key >= 32 ? key - 32 : key;
    const controls = channel === 6
      ? { 31: "cross", 12: "headMix", 23: "filterA", 24: "filterB" }
      : channel < 2 ? { 0: "tempo", 4: "trim", 7: "high", 11: "mid", 15: "low", 19: "volume" } : {};
    const action = controls[base];
    if (!action) return null;
    const id = `${channel}:${base}`;
    if (key < 32) this.msb.set(id, value);
    else if (!this.msb.has(id)) return null;
    const full = this.msb.get(id) * 128 + (key < 32 ? 0 : value);
    return { action, deck: channel < 2 ? channel : undefined, value: full / 16383, raw: full };
  }
}
export function isFLX4Port(port) {
  return /FLX4/.test(`${port.name || ''} ${port.manufacturer || ''}`.toUpperCase().replace(/[^A-Z0-9]/g, ''));
}
