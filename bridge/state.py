"""
Shared live state. A single instance is passed between the Ableton
bridge and the WebSocket server so both can read/write it.
"""

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
        }

    def tracks_snapshot(self) -> dict:
        """Lightweight message sent when track mute state changes."""
        return {"type": "tracks", "tracks": self.tracks}

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
        }
