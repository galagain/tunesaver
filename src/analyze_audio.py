"""Isolated, bounded audio analysis. No network, credentials or database access."""
import json
import base64
import math
import os
from pathlib import Path
import resource
import subprocess
import sys
import tempfile
import zipfile

os.environ.setdefault("NUMBA_CACHE_DIR", "/tmp/tunesaver-numba")
for variable in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "NUMBA_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ[variable] = "1"

NOTES = ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"]
MAJOR = ["8B", "3B", "10B", "5B", "12B", "7B", "2B", "9B", "4B", "11B", "6B", "1B"]
MINOR = ["5A", "12A", "7A", "2A", "9A", "4A", "11A", "6A", "1A", "8A", "3A", "10A"]
KEYS = dict(zip(NOTES + [n + "m" for n in NOTES], MAJOR + MINOR))
MAX_DURATION = 3600


def command(args, timeout=120):
    return subprocess.run(args, check=True, capture_output=True, timeout=timeout)


def tempo_onsets(peaks, rate):
    import numpy as np
    from scipy import signal

    width = max(1, round(rate * .02))
    envelope = np.convolve(peaks[:, 0] + .25 * peaks[:, 1], np.ones(width) / width, mode='same')
    attacks = np.maximum(0, envelope - np.roll(envelope, width))
    attacks[:width] = 0
    positions, _ = signal.find_peaks(attacks, distance=max(1, int(rate * .18)),
                                     prominence=float(np.quantile(attacks, .8)))
    return positions / rate, attacks[positions] ** 2


def fixed_tempo_grid(beat_times, onset_times=None, onset_weights=None):
    import numpy as np
    from scipy import optimize

    beats = np.unique(np.asarray(beat_times, dtype=float))
    beats = beats[np.isfinite(beats) & (beats >= 0)]
    if len(beats) < 8 or beats[-1] - beats[0] < 2:
        return None
    intervals = np.diff(beats)
    intervals = intervals[(intervals >= .2) & (intervals <= 1.5)]
    if not len(intervals):
        return None
    typical = float(60 / np.median(intervals))
    span = float(beats[-1] - beats[0])
    times, weights = beats, np.ones(len(beats))
    if onset_times is not None and onset_weights is not None:
        times = np.asarray(onset_times, dtype=float)
        weights = np.asarray(onset_weights, dtype=float)
        valid = np.isfinite(times) & (times >= 0) & np.isfinite(weights) & (weights > 0)
        times, weights = times[valid], weights[valid]
        if len(times) < 8:
            times, weights = beats, np.ones(len(beats))
    indices = np.linspace(0, len(times) - 1, min(len(times), 4096), dtype=int)
    sample, weights = times[indices], weights[indices]
    weights = weights / weights.sum()
    sample = sample - sample[0]
    spacing = min(.1, 15 / span)
    candidates = np.unique(np.concatenate((np.arange(40, 301),
        np.arange(max(40, typical * .75), min(300, typical * 1.25), spacing))))

    # Periodic phase fitting tolerates skipped beats and breaks: a missing beat
    # does not renumber every subsequent beat, unlike regression on array indices.
    def coherence(bpm):
        phase = np.exp(2j * np.pi * sample * bpm / 60)
        # The second harmonic keeps offbeat percussion from cancelling the pulse.
        return float(((abs(np.sum(phase * weights)) ** 2 + .5 * abs(np.sum(phase ** 2 * weights)) ** 2) / 1.5) ** .5)

    scores = []
    for start in range(0, len(candidates), 128):
        group = candidates[start:start + 128]
        phase = np.exp(2j * np.pi * group[:, None] * sample / 60)
        strength = (np.abs(np.sum(phase * weights, axis=1)) ** 2
                    + .5 * np.abs(np.sum(phase ** 2 * weights, axis=1)) ** 2) ** .5
        scores.extend(strength * np.exp(-.5 * (np.log2(group / typical) / .35) ** 2))
    seed = float(candidates[int(np.argmax(scores))])
    optimum = optimize.minimize_scalar(lambda bpm: -coherence(bpm),
        bounds=(max(40, seed - spacing), min(300, seed + spacing)), method='bounded', options={'xatol': 1e-7})
    bpm = float(optimum.x)
    integer = round(bpm)
    snapped = (40 <= integer <= 300 and abs(bpm - integer) <= .15
               and span * abs(bpm - integer) / bpm <= .08
               and coherence(integer) >= coherence(bpm) * .95)
    bpm = float(integer) if snapped else round(bpm, 5)
    step = 60 / bpm
    reference = float(np.angle(np.mean(np.exp(2j * np.pi * beats / step))) / (2 * np.pi) * step) % step
    half_phase = float(np.angle(np.sum(np.exp(4j * np.pi * times[indices] / step) * weights)) / (4 * np.pi) * step) % (step / 2)
    phases = [half_phase, half_phase + step / 2]
    phase = min(phases, key=lambda value: abs((value - reference + step / 2) % step - step / 2))
    residuals = (times[indices] - phase + step / 2) % step - step / 2
    inliers = np.abs(residuals) <= min(.08, step * .2)
    offset = phase
    if np.any(inliers):
        order = np.argsort(residuals[inliers])
        cumulative = np.cumsum(weights[inliers][order])
        median = residuals[inliers][order][np.searchsorted(cumulative, cumulative[-1] / 2)]
        offset = (phase + float(median)) % step
    residuals = (beats - offset + step / 2) % step - step / 2
    return {'bpm': bpm, 'grid_offset': round(offset, 6),
            'grid_error_ms': round(float(np.quantile(np.abs(residuals), .9)) * 1000, 1),
            'bpm_integer': bool(snapped), 'grid_confidence': round(coherence(bpm), 4)}


def analyze(path: Path) -> dict:
    import numpy as np
    import soundfile as sf
    from scipy import signal

    with tempfile.TemporaryDirectory(prefix="tunesaver-analysis-") as folder:
        root = Path(folder)
        if path.suffix.lower() == ".zip":
            with zipfile.ZipFile(path) as archive:
                members = [m for m in archive.infolist() if Path(m.filename).suffix.lower() in {".mp3", ".flac", ".wav", ".m4a", ".aac", ".aiff", ".ogg", ".opus"} and not m.is_dir()]
                if len(members) != 1 or members[0].file_size > 512 * 1024 * 1024:
                    raise ValueError("Archive non analysable (un seul son, 512 Mo maximum).")
                target = root / ("source" + Path(members[0].filename).suffix.lower())
                with archive.open(members[0]) as source, target.open("wb") as dest:
                    total = 0
                    while block := source.read(1024 * 1024):
                        total += len(block)
                        if total > 512 * 1024 * 1024:
                            raise ValueError("Archive audio trop volumineuse.")
                        dest.write(block)
                path = target
        probe = json.loads(command(["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)]).stdout)
        stream = next((s for s in probe.get("streams", []) if s.get("codec_type") == "audio"), {})
        duration = float(probe.get("format", {}).get("duration") or stream.get("duration") or 0)
        if not math.isfinite(duration) or not 2 <= duration <= MAX_DURATION:
            raise ValueError("Analyse disponible pour les sons de 2 secondes à 60 minutes.")
        decoded = root / "audio.wav"
        command(["ffmpeg", "-nostdin", "-v", "error", "-threads", "1", "-i", str(path), "-map", "0:a:0", "-t", str(MAX_DURATION), "-ac", "1", "-ar", "22050", "-c:a", "pcm_s16le", str(decoded)])
        sr, bin_size = 22050, 50
        rate = sr / bin_size
        filters = [signal.butter(2, 250, fs=sr, output='sos'),
                   signal.butter(2, [250, 2500], btype='bandpass', fs=sr, output='sos'),
                   signal.butter(2, 2500, btype='highpass', fs=sr, output='sos')]
        states = [np.zeros((len(sos), 2)) for sos in filters]
        chunks = []
        # Preserve positive/negative sample extrema, with continuous band filters.
        # 441 bins/s retain transients at 2.27 ms instead of smoothed FFT energy.
        for block in sf.blocks(decoded, blocksize=sr * 10, dtype='float32'):
            padded = np.pad(block, (0, (-len(block)) % bin_size))
            bins = padded.reshape(-1, bin_size)
            values = []
            for i, sos in enumerate(filters):
                band, states[i] = signal.sosfilt(sos, padded, zi=states[i])
                values.append(np.max(np.abs(band.reshape(-1, bin_size)), axis=1))
            values.extend([np.maximum(0, bins.max(axis=1)), np.maximum(0, -bins.min(axis=1))])
            chunks.append(np.stack(values, axis=1))
        peaks = np.concatenate(chunks)
        rhythm = json.loads(command(['qm-beats', str(decoded)]).stdout)
        beats = np.asarray(rhythm['beats'], dtype=float)
        beats = beats[(beats >= 0) & (beats < duration)]
        bpm, offset, residual_ms = None, None, None
        fixed = None
        quality = 'uncertain'
        if len(beats) >= 8 and float(peaks[:, 3:].max()) > .005:
            # Refine QM beat positions against local attacks before fitting.
            attacks = np.maximum(0, np.diff(peaks[:, 0] + peaks[:, 1], prepend=0))
            positions, _ = signal.find_peaks(attacks, distance=int(rate * .08), prominence=float(np.quantile(attacks, .8)))
            for i, beat in enumerate(beats):
                near = positions[np.abs(positions / rate - beat) < .065]
                if len(near):
                    best = near[np.argmax(attacks[near])]
                    beats[i] = best / rate
            beats = np.unique(beats)
            fixed = fixed_tempo_grid(beats, *tempo_onsets(peaks, rate))
            if fixed:
                bpm, offset, residual_ms = fixed['bpm'], fixed['grid_offset'], fixed['grid_error_ms']
                quality = 'stable' if residual_ms <= 30 else 'variable'
        packed = np.clip(peaks * 255, 0, 255).astype(np.uint8)
        key = command(["keyfinder-cli", "-n", "standard", str(decoded)]).stdout.decode().strip()
        if key not in KEYS:
            key = None
        return {
            "version": 3, "engine": "Queen Mary DSP", "duration": round(duration, 4), "bpm": bpm, "grid_offset": offset,
            "tempo_strategy": "fixed-periodic", "bpm_integer": bool(fixed and fixed['bpm_integer']),
            "grid_confidence": fixed['grid_confidence'] if fixed else None,
            "beats": np.round(beats, 5).tolist() if bpm else [],
            "grid_quality": quality, "grid_error_ms": residual_ms,
            "key": key, "camelot": KEYS.get(key), "key_estimated": True,
            "sample_rate": int(stream.get("sample_rate") or 0), "channels": int(stream.get("channels") or 0),
            "codec": str(stream.get("codec_name") or ""), "bit_rate": int(stream.get("bit_rate") or probe.get("format", {}).get("bit_rate") or 0),
            "waveform": {"rate": rate, "frames": len(packed), "encoding": "u8-lmhpn", "data": base64.b64encode(packed.tobytes()).decode('ascii')},
        }


if __name__ == "__main__":
    os.nice(10)
    if sys.platform != "darwin":
        resource.setrlimit(resource.RLIMIT_AS, (2 * 1024 ** 3, 2 * 1024 ** 3))
    resource.setrlimit(resource.RLIMIT_CPU, (240, 240))
    try:
        print(json.dumps(analyze(Path(sys.argv[1]).resolve()), allow_nan=False))
    except Exception as exc:
        print(json.dumps({"error": str(exc)[:240]}))
        sys.exit(1)
