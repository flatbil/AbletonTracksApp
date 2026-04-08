"""
Guide track analyzer — extracts BPM and section cues from a MultiTracks Guide.wav.

MultiTracks Guide tracks contain:
  • A click track (used for BPM detection via librosa)
  • Spoken section announcements made exactly 1 measure before the section starts
    e.g. "Verse 1" is spoken 1 bar before Verse 1 actually begins

Dependencies (installed separately from main bridge):
  pip install openai-whisper librosa soundfile
"""

from __future__ import annotations

import re
import logging
import numpy as np

log = logging.getLogger(__name__)

# Band instruction fragments to strip before checking for section names.
# These appear alongside section names in the guide cue ("Chorus, all in").
_STRIP_PATTERNS = [
    r'\ball\s+in\b',
    r'\bdrums?\s*(in|out|n\.?)\b',
    r'\bband\s*(in|out)\b',
    r'\beverybody\s+in\b',
    r'\bfull\s+band\b',
    r'\bcount\s*(in|off)\b',
    r'\b\d+\s*,\s*\d+\b',   # "1, 2" count patterns
]

# Maps whisper transcript fragments → canonical section names.
# Checked in order; first match wins. More specific patterns must come before general ones.
_SECTION_RULES = [
    # Intro — "entro" is a common Whisper mishear
    (r'\b(intro|entro)\b',              'Intro'),
    # Verses — numbered first so "verse 2" doesn't match plain "verse"
    (r'\bverse\s*1\b',                  'Verse 1'),
    (r'\bverse\s*2\b',                  'Verse 2'),
    (r'\bverse\s*3\b',                  'Verse 3'),
    (r'\bverse\s*4\b',                  'Verse 4'),
    (r'\bverse\b',                      'Verse'),
    # Pre-chorus
    (r'\bpre.?chorus\b',                'Pre-Chorus'),
    # Chorus
    (r'\bchorus\b',                     'Chorus'),
    # Refrain (distinct repeated section, common in worship)
    (r'\brefrain\b',                    'Refrain'),
    # Build (energy ramp before chorus)
    (r'\bbuild\b',                      'Build'),
    # Bridge
    (r'\bbridge\b',                     'Bridge'),
    # Vamp
    (r'\bvamp\b',                       'Vamp'),
    # Tag — "tack" and "task" are common Whisper mishears of "tag"
    (r'\b(tag|tack|task)\b',            'Tag'),
    # Outro variants — specific before general
    (r'\boutro\s+breakdown\b',          'Outro Breakdown'),
    (r'\boutro\s+end(ing)?\b',          'Outro Ending'),
    (r'\boutro\b',                      'Outro'),
    # Break / interlude / solo
    (r'\bbreak\b',                      'Break'),
    (r'\binterlude\b',                  'Interlude'),
    (r'\bsolo\b',                       'Solo'),
    # Turnaround / end
    (r'\bturn.?around\b',               'Turnaround'),
    (r'\bend\b',                        'End'),
]


def _normalize_section(text: str) -> str | None:
    """
    Return a canonical section name from a whisper segment, or None if not a section cue.
    Strips band instructions ("all in", "drums out") before matching.
    """
    t = text.lower().strip()
    # Remove band instruction fragments
    for pat in _STRIP_PATTERNS:
        t = re.sub(pat, '', t, flags=re.IGNORECASE)
    t = t.strip(' ,.-')
    for pattern, name in _SECTION_RULES:
        if re.search(pattern, t, re.IGNORECASE):
            return name
    return None


def detect_bpm(audio_path: str, analysis_duration: float = 20.0) -> float:
    """
    Detect BPM from the count-in at the start of a guide track.
    Only analyzes the first `analysis_duration` seconds — the click before
    any speech gives a much cleaner tempo reading than the full file.
    Returns the tempo as a float rounded to 1 decimal place.
    """
    import librosa  # deferred — only imported when actually called
    y, sr = librosa.load(audio_path, sr=None, mono=True, duration=analysis_duration)
    tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
    bpm = float(np.atleast_1d(tempo)[0])
    # Worship music is almost always 60–160 BPM.
    # librosa sometimes detects at 2x the actual tempo — halve if out of range.
    if bpm > 160:
        bpm = bpm / 2
    elif bpm < 60:
        bpm = bpm * 2
    log.info("Detected BPM: %.2f from first %.0fs of %s", bpm, analysis_duration, audio_path)
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


def analyze_guide(audio_path: str, model_size: str = "base", click_path: str = None) -> dict:
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
    # Use click track for BPM if provided — much cleaner signal than the guide mix
    bpm = detect_bpm(click_path if click_path else audio_path)
    raw_cues = transcribe_sections(audio_path, model_size)

    # Place markers exactly where the voice speaks the section name.
    # The guide track announces sections at the moment they begin
    # (the 1-bar lead-in is the musician's cue, but the beat itself is correct).
    sections = []
    for cue in raw_cues:
        beat = cue["timestamp"] * bpm / 60.0
        sections.append({
            "name": cue["name"],
            "time": round(cue["timestamp"], 3),
            "beat": round(beat, 3),
        })

    log.info(
        "Analysis complete: BPM=%.1f, %d sections: %s",
        bpm,
        len(sections),
        [s["name"] for s in sections],
    )

    return {"bpm": bpm, "sections": sections}
