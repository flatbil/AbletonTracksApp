"""
Shared live state. A single instance is passed between the Ableton
bridge and the WebSocket server so both can read/write it.
"""

# Needed for `float | None` as a dataclass field annotation — that syntax
# isn't valid at runtime before Python 3.10, and the field is evaluated
# eagerly at class-definition time unlike a local-variable annotation.
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class AppState:
    songs: list[dict] = field(default_factory=list)
    tracks: list[dict] = field(default_factory=list)
    current_position: float = 0.0
    is_playing: bool = False
    current_song_index: int = -1
    current_section_index: int = -1
    tempo: float = 0.0
    time_signature_numerator: int = 4
    cue_count: int = 0
    cue_warning: bool = False
    # Whether Ableton has responded to anything within the watchdog's timeout
    # window (see AbletonBridge._watchdog). Starts True so a normal startup
    # doesn't flash a false "disconnected" warning before the first response
    # has had a chance to arrive.
    ableton_connected: bool = True
    # A jump in flight — set by server._handle_jump when a tap arrives, cleared
    # by AbletonBridge._handle_beat once the section actually lands there.
    # Only meaningful for a device reconnecting mid-quantization-window and
    # picking up full_snapshot(); every already-connected device gets this from
    # the "jump_queued" broadcast the moment the tap arrives, not from here.
    queued_song_index: int = -1
    queued_section_index: int = -1
    queued_launch_beat: float | None = None
    # Live per-track output level (0.0-1.0), keyed by track index. Updates
    # far more often than anything else in this class — broadcast on its own
    # throttled cadence (see AbletonBridge._broadcast_meters), never bundled
    # into full_snapshot/position_snapshot.
    track_meters: dict[int, float] = field(default_factory=dict)

    def position_snapshot(self) -> dict:
        """Lightweight message sent ~every beat."""
        return {
            "type": "position",
            "position": self.current_position,
            "is_playing": self.is_playing,
            "current_song_index": self.current_song_index,
            "current_section_index": self.current_section_index,
            "tempo": self.tempo,
            "time_signature_numerator": self.time_signature_numerator,
            "ableton_connected": self.ableton_connected,
        }

    def tracks_snapshot(self) -> dict:
        """Lightweight message sent when track mute state changes."""
        return {"type": "tracks", "tracks": self.tracks}

    def meters_snapshot(self) -> dict:
        """Live per-track output levels — sent on its own throttled cadence."""
        return {"type": "meters", "levels": self.track_meters}

    def full_snapshot(self) -> dict:
        """Complete state — sent on connect or when markers change."""
        return {
            "type": "state",
            "songs": self.songs,
            "tracks": self.tracks,
            "position": self.current_position,
            "is_playing": self.is_playing,
            "current_song_index": self.current_song_index,
            "current_section_index": self.current_section_index,
            "tempo": self.tempo,
            "time_signature_numerator": self.time_signature_numerator,
            "cue_count": self.cue_count,
            "cue_warning": self.cue_warning,
            "ableton_connected": self.ableton_connected,
            "queued_song_index": self.queued_song_index,
            "queued_section_index": self.queued_section_index,
            "queued_launch_beat": self.queued_launch_beat,
        }
