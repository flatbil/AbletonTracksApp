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
                    {"name": "Verse 1", "position": 0.0},
                    {"name": "Chorus",  "position": 8.0},
                ]
            },
            ...
        ]
    """
    songs = []
    current_song = None

    for cue in sorted(cue_points, key=lambda c: c["position"]):
        name = cue["name"].strip()
        position = float(cue["position"])

        match = SONG_HEADER.match(name)
        if match:
            current_song = {
                "name": match.group(1),
                "position": position,
                "sections": [],
            }
            songs.append(current_song)
        elif current_song is not None:
            current_song["sections"].append({"name": name, "position": position})

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
