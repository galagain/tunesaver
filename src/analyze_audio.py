"""Isolated, bounded audio analysis. No network, credentials or database access."""
import json
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
    import librosa
    import numpy as np
    import soundfile as sf

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
        sr, hop, fft = 22050, 256, 2048
        onsets, bands = [], []
        previous = None
        mel = librosa.filters.mel(sr=sr, n_fft=fft, n_mels=64)
        frequencies = librosa.fft_frequencies(sr=sr, n_fft=fft)
        masks = [frequencies < 250, (frequencies >= 250) & (frequencies < 2500), frequencies >= 2500]
        # Streaming overlapping FFT blocks keep memory independent of track length.
        for block in sf.blocks(decoded, blocksize=fft + hop * 511, overlap=fft - hop, dtype="float32"):
            if len(block) < fft:
                break
            spectrum = np.abs(librosa.stft(block, n_fft=fft, hop_length=hop, center=False))
            power = spectrum ** 2
            log_mel = librosa.power_to_db(mel @ power, ref=1.0, top_db=None)
            padded = np.concatenate([previous, log_mel], axis=1) if previous is not None else log_mel
            flux = librosa.onset.onset_strength(S=padded, sr=sr, hop_length=hop, center=False)
            onsets.append(flux[-log_mel.shape[1]:])
            previous = log_mel[:, -1:]
            bands.append(np.stack([np.sqrt(power[m].sum(axis=0)) for m in masks]))
        if not onsets:
            raise ValueError("Signal audio trop court.")
        onset = np.concatenate(onsets)
        band_values = np.concatenate(bands, axis=1)
        tempo, beats = librosa.beat.beat_track(onset_envelope=onset, sr=sr, hop_length=hop, units="time", trim=False)
        beats = np.asarray(beats) + fft / (2 * sr)
        beats = beats[(beats >= 0) & (beats < duration)]
        bpm = float(np.asarray(tempo).reshape(-1)[0])
        variation = float(np.std(np.diff(beats)) / np.mean(np.diff(beats))) if len(beats) > 4 else 1.0
        reliable = len(beats) >= 8 and float(np.max(onset)) > 0.1 and 40 <= bpm <= 250
        offset = None
        if reliable:
            # Fit the full set of detected beats, not the quantized tempo bin alone.
            slope, intercept = np.polyfit(np.arange(len(beats)), beats, 1)
            bpm = round(60 / slope, 3)
            offset = round(float(intercept % slope), 5)
        else:
            bpm = None
        rate = min(40, 16384 / duration)
        count = math.ceil(duration * rate)
        peaks = np.zeros((3, count), dtype=np.float32)
        indices = np.minimum(count - 1, ((np.arange(band_values.shape[1]) * hop + fft / 2) / sr * rate).astype(int))
        for i in range(3):
            np.maximum.at(peaks[i], indices, band_values[i])
        scale = max(float(np.quantile(peaks.sum(axis=0), 0.995)), 1e-6)
        peaks = np.clip(np.sqrt(peaks / scale) * 255, 0, 255).astype(np.uint8)
        key = command(["keyfinder-cli", "-n", "standard", str(decoded)]).stdout.decode().strip()
        if key not in KEYS:
            key = None
        return {
            "version": 1, "duration": round(duration, 4), "bpm": bpm, "grid_offset": offset,
            "beats": np.round(beats, 4).tolist() if reliable else [],
            "grid_quality": "stable" if reliable and variation < 0.04 else "variable" if reliable else "uncertain",
            "key": key, "camelot": KEYS.get(key), "key_estimated": True,
            "sample_rate": int(stream.get("sample_rate") or 0), "channels": int(stream.get("channels") or 0),
            "codec": str(stream.get("codec_name") or ""), "bit_rate": int(stream.get("bit_rate") or probe.get("format", {}).get("bit_rate") or 0),
            "waveform": {"rate": rate, "bands": peaks.tolist()},
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
