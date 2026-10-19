"""Tests for bridge/server.py's _handle_jump — the Loop and Cue Clip branches
added alongside a normal marker jump. No live Ableton involved: _ableton is
a recording fake, so these assert on *what gets sent*, not on Live's own
behavior (which can only be verified against the real app)."""

import asyncio

import pytest

from bridge import server
from bridge.parser import parse_markers
from bridge.state import AppState


class FakeAbleton:
    def __init__(self, cue_clip_sections: set[str] = frozenset()):
        self._cue_clip_sections = cue_clip_sections
        self.calls: list[tuple] = []

    def fire_cue_clip_for_section(self, section_name: str) -> bool:
        matched = section_name.strip().lower() in self._cue_clip_sections
        self.calls.append(("fire_cue_clip_for_section", section_name, matched))
        return matched

    def schedule_downbeat_retarget(self, launch_beat: float, target_beat: float):
        self.calls.append(("schedule_downbeat_retarget", launch_beat, target_beat))

    def clear_pending_retarget(self):
        self.calls.append(("clear_pending_retarget",))

    def set_loop_region(self, start_beat: float, length_beats: float):
        self.calls.append(("set_loop_region", start_beat, length_beats))

    def disable_loop(self):
        self.calls.append(("disable_loop",))

    def jump_to_cue_index(self, index: int):
        self.calls.append(("jump_to_cue_index", index))


def cues(*pairs):
    return [{"name": n, "position": p} for n, p in pairs]


def make_state(raw_cues):
    state = AppState()
    state.songs = parse_markers(raw_cues)
    state.time_signature_numerator = 4
    state.current_position = 0.0
    return state


class TestPlainSectionJump:
    def test_jumps_normally_and_releases_any_loop(self):
        state = make_state(cues(("== Song A ==", 0), ("Verse 1", 8), ("Chorus", 16)))
        fake = FakeAbleton()
        server.init(state, fake)

        asyncio.run(server._handle_jump(0, 1))  # Verse 1, cue_index 1

        assert ("disable_loop",) in fake.calls
        assert ("jump_to_cue_index", 1) in fake.calls
        assert ("clear_pending_retarget",) in fake.calls
        assert not any(c[0] == "schedule_downbeat_retarget" for c in fake.calls)
        assert not any(c[0] == "set_loop_region" for c in fake.calls)


class TestLoopSectionJump:
    def test_binds_loop_region_and_still_jumps(self):
        state = make_state(cues(
            ("== Song A ==", 0), ("Verse 1", 8), ("Loop", 16), ("== Song B ==", 24),
        ))
        fake = FakeAbleton()
        server.init(state, fake)

        asyncio.run(server._handle_jump(0, 2))  # the Loop section, length 8

        assert ("set_loop_region", 16.0, 8.0) in fake.calls
        assert not any(c[0] == "disable_loop" for c in fake.calls)
        assert any(c[0] == "jump_to_cue_index" for c in fake.calls)


class TestCueClipSectionJump:
    def test_fires_clip_and_retargets_instead_of_normal_jump(self):
        state = make_state(cues(("== Song A ==", 0), ("Verse 1", 8), ("Chorus", 16)))
        state.current_position = 0.0  # next bar boundary (4/4) is beat 4.0
        fake = FakeAbleton(cue_clip_sections={"chorus"})
        server.init(state, fake)

        asyncio.run(server._handle_jump(0, 2))  # Chorus, position 16.0

        assert ("fire_cue_clip_for_section", "Chorus", True) in fake.calls
        # True downbeat = marker position + 1 bar (4 beats at 4/4)
        assert ("schedule_downbeat_retarget", 4.0, 20.0) in fake.calls
        assert not any(c[0] == "jump_to_cue_index" for c in fake.calls)
        assert not any(c[0] == "clear_pending_retarget" for c in fake.calls)

    def test_no_matching_clip_falls_back_to_normal_jump(self):
        state = make_state(cues(("== Song A ==", 0), ("Verse 1", 8), ("Chorus", 16)))
        fake = FakeAbleton(cue_clip_sections=set())  # nothing opted in
        server.init(state, fake)

        asyncio.run(server._handle_jump(0, 2))  # Chorus, cue_index 2

        assert ("fire_cue_clip_for_section", "Chorus", False) in fake.calls
        assert ("jump_to_cue_index", 2) in fake.calls
        assert not any(c[0] == "schedule_downbeat_retarget" for c in fake.calls)
