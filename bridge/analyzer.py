"""
Guide track analyzer — extracts BPM and section cues from a MultiTracks Guide.wav.

MultiTracks Guide tracks contain:
  • A click track (used for BPM detection via librosa)
  • Spoken section announcements made exactly 1 measure before the section starts
    e.g. "Verse 1" is spoken 1 bar before Verse 1 actually begins

Dependencies (installed separately from main bridge):
  pip install openai-whisper librosa soundfile
"""

import re
import logging
import numpy as np

log = logging.getLogger(__name__)

# Maps whisper transcript fragments → canonical section names.
# Checked in order; first match wins.
_SECTION_RULES = [
    (r'\bintro\b',                  'Intro'),
    (r'\bverse\s*1\b',              'Verse 1'),
    (r'\bverse\s*2\b',              'Verse 2'),
    (r'\bverse\s*3\b',              'Verse 3'),
    (r'\bverse\b',                  'Verse'),
    (r'\bpre.?chorus\b',            'Pre-Chorus'),
    (r'\bchorus\b',                 'Chorus'),
    (r'\bbridge\b',                 'Bridge'),
    (r'\boutro\b',                  'Outro'),
    (r'\btag\b',                    'Tag'),
    (r'\bvamp\b',                   'Vamp'),
    (r'\bbreak\b',                  'Break'),
    (r'\binterlude\b',              'Interlude'),
    (r'\bsolo\b',                   'Solo'),
    (r'\bend\b',                    'End'),
    (r'\bturn.?around\b',           'Turnaround'),
]


def _normalize_section(text: str) -> str | None:
    """Return a canonical section name from a whisper segment, or None if not a section cue."""
    t = text.lower()
    for pattern, name in _SECTION_RULES:
        if re.search(pattern, t):
            return name
    return None


def detect_bpm(audio_path: str) -> float:
    """
    Detect BPM from an audio file using librosa's beat tracker.
    Returns the tempo as a float rounded to 1 decimal place.
    """
    import librosa  # deferred — only imported when actually called
    y, sr = librosa.load(audio_path, sr=None, mono=True)
    tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
    # beat_track returns ndarray in newer librosa versions
    bpm = float(np.atleast_1d(tempo)[0])
    log.info("Detected BPM: %.2f from %s", bpm, audio_path)
    return round(bpm, 1)


def transcribe_sections(audio_path: str, model_size: str = "base") -> list[dict]:
    """
    Run Whisper on the guide track and return a list of section announcements.
    Each entry: {"name": str, "timestamp": float}  (timestamp in seconds)

    The first call downloads the Whisper model if not already cached (~74MB for 'base').
    """
    import whisper  # deferred import
    log.info("Loading Whisper model '%s'...", model_size)
    model = whisper.load_model(model_size)
    log.info("Transcribing %s ...", audio_path)
    result = model.transcribe(audio_path, verbose=False)

    sections = []
    seen_names: set[str] = set()

    for seg in result.get("segments", []):
        name = _normalize_section(seg["text"])
        if name is None:
            continue
        # De-duplicate: allow a name to repeat (Chorus appears multiple times)
        # but skip if this is within 2 seconds of a same-name segment
        ts = float(seg["start"])
        duplicate = any(
            s["name"] == name and abs(s["timestamp"] - ts) < 2.0
            for s in sections
        )
        if not duplicate:
            sections.append({"name": name, "timestamp": ts})
            log.info("  Found cue '%s' at %.2fs", name, ts)

    return sections


def analyze_guide(audio_path: str, model_size: str = "base") -> dict:
    """
    Full analysis of a Guide.wav file.

    Returns:
    {
        "bpm": float,
        "sections": [
            {"name": str, "beat": float, "time": float},
            ...
        ]
    }

    beat positions are absolute from the start of the audio file.
    The 1-measure offset (announcement leads the section by one bar) is removed.
    """
    bpm = detect_bpm(audio_path)
    raw_cues = transcribe_sections(audio_path, model_size)

    # 1 measure = 4 beats * (60s / BPM)
    measure_seconds = 4.0 * 60.0 / bpm

    sections = []
    for cue in raw_cues:
        actual_time = cue["timestamp"] + measure_seconds
        beat = actual_time * bpm / 60.0
        sections.append({
            "name": cue["name"],
            "time": round(actual_time, 3),
            "beat": round(beat, 3),
        })

    log.info(
        "Analysis complete: BPM=%.1f, %d sections: %s",
        bpm,
        len(sections),
        [s["name"] for s in sections],
    )

    return {"bpm": bpm, "sections": sections}
