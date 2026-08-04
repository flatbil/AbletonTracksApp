"""Tests for bridge/parser.py — song/section detection logic."""

import pytest
from bridge.parser import parse_markers, find_current_indices


# ── helpers ───────────────────────────────────────────────────────────────────

def cues(*pairs):
    """Build a cue list from (name, position) pairs."""
    return [{"name": n, "position": p} for n, p in pairs]


# ── parse_markers ─────────────────────────────────────────────────────────────

class TestNamedSongs:
    def test_single_named_song(self):
        songs = parse_markers(cues(
            ("== Amazing Grace ==", 0),
            ("Verse 1", 8),
            ("Chorus", 16),
        ))
        assert len(songs) == 1
        assert songs[0]["name"] == "Amazing Grace"
        assert [s["name"] for s in songs[0]["sections"]] == ["Start", "Verse 1", "Chorus"]

    def test_multiple_named_songs(self):
        songs = parse_markers(cues(
            ("== Song A ==", 0),
            ("Verse 1", 8),
            ("== Song B ==", 32),
            ("Chorus", 40),
        ))
        assert len(songs) == 2
        assert songs[0]["name"] == "Song A"
        assert songs[1]["name"] == "Song B"
        assert songs[1]["sections"][0]["name"] == "Start"

    def test_song_header_position_is_song_position(self):
        songs = parse_markers(cues(("== My Song ==", 16), ("Bridge", 24)))
        assert songs[0]["position"] == 16.0
        assert songs[0]["sections"][0]["position"] == 16.0


class TestStartKeywords:
    def test_english_start(self):
        songs = parse_markers(cues(
            ("Start", 0),
            ("Verse 1", 8),
            ("Chorus", 16),
        ))
        assert len(songs) == 1
        assert songs[0]["name"] == "Song 1"
        assert songs[0]["sections"][0]["name"] == "Start"
        assert [s["name"] for s in songs[0]["sections"]] == ["Start", "Verse 1", "Chorus"]

    def test_multiple_songs_via_start_keyword(self):
        songs = parse_markers(cues(
            ("Start", 0),
            ("Verse 1", 8),
            ("Start", 32),
            ("Verse 1", 40),
        ))
        assert len(songs) == 2
        assert songs[0]["name"] == "Song 1"
        assert songs[1]["name"] == "Song 2"

    def test_spanish_inicio(self):
        songs = parse_markers(cues(("Inicio", 0), ("Verso 1", 8)))
        assert len(songs) == 1
        assert songs[0]["name"] == "Song 1"
        assert songs[0]["sections"][0]["name"] == "Inicio"

    def test_french_debut_accented(self):
        songs = parse_markers(cues(("Début", 0), ("Refrain", 8)))
        assert len(songs) == 1
        assert songs[0]["name"] == "Song 1"

    def test_french_debut_plain(self):
        songs = parse_markers(cues(("debut", 0), ("Refrain", 8)))
        assert len(songs) == 1

    def test_keyword_case_insensitive(self):
        songs = parse_markers(cues(("START", 0), ("Verse 1", 8)))
        assert len(songs) == 1
        assert songs[0]["name"] == "Song 1"

    def test_intro_not_a_song_boundary(self):
        # "Intro" is a section name, not a song start.
        songs = parse_markers(cues(
            ("== My Song ==", 0),
            ("Intro", 4),
            ("Verse 1", 8),
        ))
        assert len(songs) == 1
        assert [s["name"] for s in songs[0]["sections"]] == ["Start", "Intro", "Verse 1"]

    def test_intro_without_header_is_dropped(self):
        # "Intro" with no header and no song open → no song created (not a start keyword).
        songs = parse_markers(cues(("Intro", 0), ("Verse 1", 8)))
        assert len(songs) == 0

    def test_mixed_named_and_unnamed(self):
        songs = parse_markers(cues(
            ("== Amazing Grace ==", 0),
            ("Verse 1", 8),
            ("Start", 32),
            ("Verse 1", 40),
        ))
        assert len(songs) == 2
        assert songs[0]["name"] == "Amazing Grace"
        assert songs[1]["name"] == "Song 2"


class TestCueIndex:
    def test_cue_index_matches_original_order(self):
        # cue_index must reflect Ableton's list order, not our sort order.
        raw = cues(("Chorus", 16), ("== Song ==", 0), ("Verse 1", 8))
        songs = parse_markers(raw)
        # After sort, == Song == (pos 0) comes first, then Verse 1, then Chorus.
        assert songs[0]["sections"][0]["cue_index"] == 1  # "== Song ==" is index 1 in raw
        assert songs[0]["sections"][1]["cue_index"] == 2  # "Verse 1" is index 2 in raw
        assert songs[0]["sections"][2]["cue_index"] == 0  # "Chorus" is index 0 in raw

    def test_empty_input(self):
        assert parse_markers([]) == []

    def test_sections_before_any_header_are_dropped(self):
        songs = parse_markers(cues(("Verse 1", 0), ("== Song ==", 8), ("Chorus", 16)))
        assert len(songs) == 1
        assert [s["name"] for s in songs[0]["sections"]] == ["Start", "Chorus"]


# ── find_current_indices ──────────────────────────────────────────────────────

class TestFindCurrentIndices:
    def setup_method(self):
        self.songs = parse_markers(cues(
            ("== Song A ==", 0),
            ("Verse 1", 8),
            ("Chorus", 16),
            ("== Song B ==", 32),
            ("Verse 1", 40),
        ))

    def test_before_any_marker(self):
        assert find_current_indices(self.songs, -1) == (-1, -1)

    def test_at_song_start(self):
        assert find_current_indices(self.songs, 0) == (0, 0)

    def test_mid_section(self):
        assert find_current_indices(self.songs, 10) == (0, 1)

    def test_at_chorus(self):
        assert find_current_indices(self.songs, 16) == (0, 2)

    def test_second_song(self):
        assert find_current_indices(self.songs, 32) == (1, 0)

    def test_second_song_verse(self):
        assert find_current_indices(self.songs, 45) == (1, 1)
