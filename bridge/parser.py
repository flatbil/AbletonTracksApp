"""
Parse raw Ableton cue points into a song/section tree.

Naming convention:
  Song header  →  == Amazing Grace ==
  Section      →  Verse 1, Chorus, Bridge, ...
"""

import re

SONG_HEADER = re.compile(r"^==\s*(.+?)\s*==$")


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
                "sections": [{"name": "Start", "position": position, "cue_index": cue_index}],
            }
            songs.append(current_song)
        elif current_song is not None:
            current_song["sections"].append({"name": name, "position": position, "cue_index": cue_index})

    return songs


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
