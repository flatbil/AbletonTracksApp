"""
Parse raw Ableton cue points into a song/section tree.

Naming convention (either works):
  Named song   →  == Amazing Grace ==   (song header marker)
  Unnamed song →  first marker is a recognized "song start" keyword
                  → auto-named "Song 1", "Song 2", etc.

  Section      →  Verse 1, Chorus, Bridge, ...
"""

import re

SONG_HEADER = re.compile(r"^==\s*(.+?)\s*==$")

# Cue names that signal the start of a new song when the == convention isn't used.
# Deliberately excludes "Intro" — it's commonly a section name within a song.
SONG_START_KEYWORDS = {
    "start",                    # English
    "inicio", "início",         # Spanish / Portuguese
    "começo", "comienzo",       # Portuguese / Spanish
    "début", "debut",           # French (accented and plain)
    "anfang",                   # German
    "inizio",                   # Italian
}

# A section named exactly "Loop" (any case) vamps in place — see
# AbletonBridge.set_loop_region. Kept to one literal keyword, same style as
# SONG_START_KEYWORDS, rather than a prefix match, so a section legitimately
# named "Loop Pedal" or similar in someone's existing set isn't misread.
LOOP_SECTION_KEYWORD = "loop"


def _is_song_start(name: str) -> bool:
    return name.strip().lower() in SONG_START_KEYWORDS


def _is_loop_section(name: str) -> bool:
    return name.strip().lower() == LOOP_SECTION_KEYWORD


def parse_markers(cue_points: list[dict]) -> list[dict]:
    """
    Args:
        cue_points: list of {"name": str, "position": float} sorted by position

    Returns:
        list of songs:
        [
            {
                "name": "Amazing Grace",
                "position": 0.0,
                "sections": [
                    {"name": "Start",  "position": 0.0, "cue_index": 0},
                    {"name": "Verse 1","position": 8.0, "cue_index": 1},
                    {"name": "Chorus", "position": 16.0,"cue_index": 2},
                ]
            },
            ...
        ]

    cue_index is the flat index into Ableton's cue_points list, used for
    unambiguous index-based jumping (respects launch quantization).
    """
    songs = []
    current_song = None

    # Sort by position, with song headers before sections at the same position
    def sort_key(c):
        is_header = 0 if SONG_HEADER.match(c["name"].strip()) else 1
        return (float(c["position"]), is_header)

    sorted_cues = sorted(cue_points, key=sort_key)

    # cue_index must match AbletonOSC's song.cue_points order (the ORIGINAL
    # unsorted order Ableton sends), NOT our display-sorted order.
    # Sorting only affects display/detection — jumping uses Ableton's own list.
    original_index = {(c["name"].strip(), float(c["position"])): i for i, c in enumerate(cue_points)}

    for cue in sorted_cues:
        name = cue["name"].strip()
        position = float(cue["position"])
        cue_index = original_index[(name, position)]

        match = SONG_HEADER.match(name)
        if match:
            current_song = {
                "name": match.group(1),
                "position": position,
                "sections": [{"name": "Start", "position": position, "cue_index": cue_index, "is_loop": False}],
            }
            songs.append(current_song)
        elif _is_song_start(name):
            # Keyword-only convention: "Start", "Inicio", "Début", etc. each open
            # a new unnamed song. Song name auto-increments ("Song 1", "Song 2", …).
            song_number = len(songs) + 1
            current_song = {
                "name": f"Song {song_number}",
                "position": position,
                "sections": [{"name": name, "position": position, "cue_index": cue_index, "is_loop": False}],
            }
            songs.append(current_song)
        elif current_song is not None:
            current_song["sections"].append({
                "name": name,
                "position": position,
                "cue_index": cue_index,
                "is_loop": _is_loop_section(name),
            })

    _annotate_section_lengths(songs)
    return songs


def _annotate_section_lengths(songs: list[dict]) -> None:
    """Fill in each section's "length" (beats until the next marker anywhere
    in the set, song boundaries included) — needed to bound a Loop section's
    Ableton Arrangement Loop region. The very last section in the whole set
    has no next marker to measure to, so it's left as None and can't loop
    (nothing to bound the region against)."""
    flat: list[dict] = [section for song in songs for section in song["sections"]]
    for this_section, next_section in zip(flat, flat[1:]):
        this_section["length"] = next_section["position"] - this_section["position"]
    if flat:
        flat[-1]["length"] = None


def find_current_indices(songs: list[dict], position: float) -> tuple[int, int]:
    """
    Given the current playback position (in beats), return
    (song_index, section_index) for the active section.
    Returns (-1, -1) if position is before any known marker.
    """
    song_idx = -1
    section_idx = -1

    for s_i, song in enumerate(songs):
        if position >= song["position"]:
            song_idx = s_i
            section_idx = -1
            for sc_i, section in enumerate(song["sections"]):
                if position >= section["position"]:
                    section_idx = sc_i
                else:
                    break

    return song_idx, section_idx
