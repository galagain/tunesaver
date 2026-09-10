const knobs = new Map();

export function refreshKnobs() {
  for (const [input, knob] of knobs) {
    if (!input.isConnected) { knobs.delete(input); continue; }
    const min = Number(input.min), max = Number(input.max), value = Number(input.value);
    // Signed controls have a neutral centre, even with asymmetric EQ ranges.
    const angle = min < 0 && max > 0
      ? 135 * value / (value < 0 ? -min : max)
      : -135 + (value - min) / (max - min) * 270;
    knob.style.setProperty('--angle', `${angle}deg`);
    knob.dataset.disabled = String(input.disabled);
    const text = input.dataset.format === 'percent' ? `${Math.round(value * 100)} %`
      : input.dataset.control === 'tempo' ? `${value > 0 ? '+' : ''}${value.toFixed(2)}%` : value.toFixed(max <= 2 ? 2 : 1);
    knob.querySelector('output').textContent = text;
    if (input.dataset.format === 'percent') input.setAttribute('aria-valuetext', text);
  }
}

export function makeKnob(input) {
  if (knobs.has(input)) return;
  const knob = document.createElement('span'); knob.className = 'mix-knob';
  const face = document.createElement('span'); face.className = 'mix-knob-face'; face.setAttribute('aria-hidden', 'true');
  face.append(document.createElement('i'));
  const output = document.createElement('output'); output.setAttribute('aria-hidden', 'true');
  const initial = input.value;
  input.replaceWith(knob); knob.append(input, face, output); knobs.set(input, knob);
  let drag = null;
  const change = value => {
    const step = Number(input.step) || .01;
    input.value = Math.max(Number(input.min), Math.min(Number(input.max), Math.round(value / step) * step));
    input.dispatchEvent(new Event('input', { bubbles: true })); refreshKnobs();
  };
  knob.addEventListener('pointerdown', event => {
    if (input.disabled || event.button !== 0) return;
    event.preventDefault(); input.focus(); knob.setPointerCapture(event.pointerId);
    drag = { y: event.clientY, value: Number(input.value) };
  });
  knob.addEventListener('pointermove', event => {
    if (drag) change(drag.value + (drag.y - event.clientY) * (Number(input.max) - Number(input.min)) / (event.shiftKey ? 1500 : 150));
  });
  const end = () => { drag = null; };
  knob.addEventListener('pointerup', end); knob.addEventListener('pointercancel', end); knob.addEventListener('lostpointercapture', end);
  knob.addEventListener('dblclick', () => { if (!input.disabled) change(Number(initial)); });
  knob.addEventListener('wheel', event => {
    if (input.disabled || document.activeElement !== input) return;
    event.preventDefault(); change(Number(input.value) - Math.sign(event.deltaY) * Number(input.step) * (event.shiftKey ? 1 : 5));
  }, { passive: false });
  input.addEventListener('input', refreshKnobs);
  refreshKnobs();
}
