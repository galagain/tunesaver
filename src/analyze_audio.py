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


def analyze(path: Path) -> dict:
    import numpy as np
    import soundfile as sf
    from scipy import signal, optimize

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
        quality = 'uncertain'
        if len(beats) >= 8 and float(peaks[:, 3:].max()) > .005:
            # Refine QM beat positions against local attacks, then fit with an
            # outlier-resistant regression so breaks do not drag the entire grid.
            attacks = np.maximum(0, np.diff(peaks[:, 0] + peaks[:, 1], prepend=0))
            positions, _ = signal.find_peaks(attacks, distance=int(rate * .08), prominence=float(np.quantile(attacks, .8)))
            for i, beat in enumerate(beats):
                near = positions[np.abs(positions / rate - beat) < .065]
                if len(near):
                    best = near[np.argmax(attacks[near])]
                    beats[i] = best / rate
            indices = np.arange(len(beats))
            fit = optimize.least_squares(lambda p: p[0] * indices + p[1] - beats,
                                         np.polyfit(indices, beats, 1), loss='soft_l1', f_scale=.012)
            slope, intercept = fit.x
            if slope > 0 and 40 <= 60 / slope <= 300:
                bpm = round(float(60 / slope), 3)
                offset = round(float(intercept % slope), 6)
                residual_ms = round(float(np.quantile(np.abs(fit.fun), .9)) * 1000, 1)
                quality = 'stable' if residual_ms <= 30 else 'variable'
        packed = np.clip(peaks * 255, 0, 255).astype(np.uint8)
        key = command(["keyfinder-cli", "-n", "standard", str(decoded)]).stdout.decode().strip()
        if key not in KEYS:
            key = None
        return {
            "version": 2, "engine": "Queen Mary DSP", "duration": round(duration, 4), "bpm": bpm, "grid_offset": offset,
            "beats": np.round(beats, 5).tolist() if bpm else [],
            "grid_quality": quality, "grid_error_ms": residual_ms,
            "key": key, "camelot": KEYS.get(key), "key_estimated": True,
            "sample_rate": int(stream.get("sample_rate") or 0), "channels": int(stream.get("channels") or 0),
            "codec": str(stream.get("codec_name") or ""), "bit_rate": int(stream.get("bit_rate") or probe.get("format", {}).get("bit_rate") or 0),
            "waveform": {"rate": rate, "frames": len(packed), "encoding": "u8-lmhpn", "data": base64.b64encode(packed.tobytes()).decode('ascii')},
        }


if __name__ == "__main__":
    os.nice(10)
    resource.setrlimit(resource.RLIMIT_AS, (2 * 1024 ** 3, 2 * 1024 ** 3))
    resource.setrlimit(resource.RLIMIT_CPU, (240, 240))
    try:
        print(json.dumps(analyze(Path(sys.argv[1]).resolve()), allow_nan=False))
    except Exception as exc:
        print(json.dumps({"error": str(exc)[:240]}))
        sys.exit(1)
