import os
import sys
import tempfile
import Live
import json
from functools import partial
from typing import Tuple, Any

from .handler import AbletonOSCHandler

class SongHandler(AbletonOSCHandler):
    def __init__(self, manager):
        super().__init__(manager)
        self.class_identifier = "song"

    def init_api(self):
        #--------------------------------------------------------------------------------
        # Callbacks for Song: methods
        #--------------------------------------------------------------------------------
        for method in [
            "capture_and_insert_scene",
            "capture_midi",
            "continue_playing",
            "create_audio_track",
            "create_midi_track",
            "create_return_track",
            "create_scene",
            "delete_return_track",
            "delete_scene",
            "delete_track",
            "duplicate_scene",
            "duplicate_track",
            "force_link_beat_time",
            "jump_by",
            "jump_to_prev_cue",
            "jump_to_next_cue",
            "redo",
            "re_enable_automation",
            "set_or_delete_cue",
            "start_playing",
            "stop_all_clips",
            "stop_playing",
            "tap_tempo",
            "trigger_session_record",
            "undo"
        ]:
            callback = partial(self._call_method, self.song, method)
            self.osc_server.add_handler("/live/song/%s" % method, callback)

        #--------------------------------------------------------------------------------
        # Callbacks for Song: properties (read/write)
        #--------------------------------------------------------------------------------
        properties_rw = [
            "arrangement_overdub",
            "back_to_arranger",
            "clip_trigger_quantization",
            "current_song_time",
            "groove_amount",
            "is_ableton_link_enabled",
            "loop",
            "loop_length",
            "loop_start",
            "metronome",
            "midi_recording_quantization",
            "nudge_down",
            "nudge_up",
            "punch_in",
            "punch_out",
            "record_mode",
            "root_note",
            "scale_name",
            "session_record",
            "signature_denominator",
            "signature_numerator",
            "tempo"
        ]

        #--------------------------------------------------------------------------------
        # Callbacks for Song: properties (read-only)
        #--------------------------------------------------------------------------------
        properties_r = [
            "can_redo",
            "can_undo",
            "is_playing",
            "song_length",
            "session_record_status"
        ]

        for prop in properties_r + properties_rw:
            self.osc_server.add_handler("/live/song/get/%s" % prop, partial(self._get_property, self.song, prop))
            self.osc_server.add_handler("/live/song/start_listen/%s" % prop, partial(self._start_listen, self.song, prop))
            self.osc_server.add_handler("/live/song/stop_listen/%s" % prop, partial(self._stop_listen, self.song, prop))
        for prop in properties_rw:
            self.osc_server.add_handler("/live/song/set/%s" % prop, partial(self._set_property, self.song, prop))

        #--------------------------------------------------------------------------------
        # Callbacks for Song: Track properties
        #--------------------------------------------------------------------------------
        self.osc_server.add_handler("/live/song/get/num_tracks", lambda _: (len(self.song.tracks),))

        def song_get_track_names(params):
            if len(params) == 0:
                track_index_min, track_index_max = 0, len(self.song.tracks)
            else:
                track_index_min, track_index_max = params
                if track_index_max == -1:
                    track_index_max = len(self.song.tracks)
            return tuple(self.song.tracks[index].name for index in range(track_index_min, track_index_max))
        self.osc_server.add_handler("/live/song/get/track_names", song_get_track_names)

        def song_get_track_data(params):
            """
            Retrieve one more properties of a block of tracks and their clips.
            Properties must be of the format track.property_name or clip.property_name.

            For example:
                /live/song/get/track_data 0 12 track.name clip.name clip.length

            Queries tracks 0..11, and returns a list of values comprising:

            [track_0_name, clip_0_0_name,   clip_0_1_name,   ... clip_0_7_name,
                           clip_1_0_length, clip_0_1_length, ... clip_0_7_length,
             track_1_name, clip_1_0_name,   clip_1_1_name,   ... clip_1_7_name, ...]
            """
            track_index_min, track_index_max, *properties = params
            track_index_min = int(track_index_min)
            track_index_max = int(track_index_max)
            self.logger.info("Getting track data: %s (tracks %d..%d)" %
                             (properties, track_index_min, track_index_max))
            if track_index_max == -1:
                track_index_max = len(self.song.tracks)
            rv = []
            for track_index in range(track_index_min, track_index_max):
                track = self.song.tracks[track_index]
                for prop in properties:
                    obj, property_name = prop.split(".")
                    if obj == "track":
                        if property_name == "num_devices":
                            value = len(track.devices)
                        else:
                            value = getattr(track, property_name)
                            if isinstance(value, Live.Track.Track):
                                #--------------------------------------------------------------------------------
                                # Map Track objects to their track_index to return via OSC
                                #--------------------------------------------------------------------------------
                                value = list(self.song.tracks).index(value)
                        rv.append(value)
                    elif obj == "clip":
                        for clip_slot in track.clip_slots:
                            if clip_slot.clip is not None:
                                rv.append(getattr(clip_slot.clip, property_name))
                            else:
                                rv.append(None)
                    elif obj == "clip_slot":
                        for clip_slot in track.clip_slots:
                            rv.append(getattr(clip_slot, property_name))
                    elif obj == "device":
                        for device in track.devices:
                            rv.append(getattr(device, property_name))
                    else:
                        self.logger.error("Unknown object identifier in get/track_data: %s" % obj)
            return tuple(rv)
        self.osc_server.add_handler("/live/song/get/track_data", song_get_track_data)


        def song_export_structure(params):
            tracks = []
            for track_index, track in enumerate(self.song.tracks):
                group_track = None
                if track.group_track is not None:
                    group_track = list(self.song.tracks).index(track.group_track)
                track_data = {
                    "index": track_index,
                    "name": track.name,
                    "is_foldable": track.is_foldable,
                    "group_track": group_track,
                    "clips": [],
                    "devices": []
                }
                for clip_index, clip_slot in enumerate(track.clip_slots):
                    if clip_slot.clip:
                        clip_data = {
                            "index": clip_index,
                            "name": clip_slot.clip.name,
                            "length": clip_slot.clip.length,
                        }
                        track_data["clips"].append(clip_data)

                for device_index, device in enumerate(track.devices):
                    device_data = {
                        "class_name": device.class_name,
                        "type": device.type,
                        "name": device.name,
                        "parameters": []
                    }
                    for parameter in device.parameters:
                        device_data["parameters"].append({
                            "name": parameter.name,
                            "value": parameter.value,
                            "min": parameter.min,
                            "max": parameter.max,
                            "is_quantized": parameter.is_quantized,
                        })
                    track_data["devices"].append(device_data)

                tracks.append(track_data)
            song = {
                "tracks": tracks
            }

            if sys.platform == "darwin":
                #--------------------------------------------------------------------------------
                # On macOS, TMPDIR by default points to a process-specific directory.
                # We want to use a global temp dir (typically, tmp) so that other processes
                # know where to find this output .json, so unset TMPDIR.
                #--------------------------------------------------------------------------------
                os.environ["TMPDIR"] = ""
            fd = open(os.path.join(tempfile.gettempdir(), "abletonosc-song-structure.json"), "w")
            json.dump(song, fd)
            fd.close()
            self.logger.warning("Exported song structure to directory %s" % tempfile.gettempdir())
            return (1,)
        self.osc_server.add_handler("/live/song/export/structure", song_export_structure)

        #--------------------------------------------------------------------------------
        # Callbacks for Song: Scene properties
        #--------------------------------------------------------------------------------
        self.osc_server.add_handler("/live/song/get/num_scenes", lambda _: (len(self.song.scenes),))

        def song_get_scene_names(params):
            if len(params) == 0:
                scene_index_min, scene_index_max = 0, len(self.song.scenes)
            else:
                scene_index_min, scene_index_max = params
            return tuple(self.song.scenes[index].name for index in range(scene_index_min, scene_index_max))
        self.osc_server.add_handler("/live/song/get/scenes/name", song_get_scene_names)

        #--------------------------------------------------------------------------------
        # Callbacks for Song: Cue generation from arrangement track
        #--------------------------------------------------------------------------------
        def song_generate_cues_from_track(song, params):
            """
            Scan arrangement clips on a named track and create/rename cue points.

            Each clip's start_time becomes a cue position; the clip's name becomes
            the cue name. Existing cues at those positions are renamed rather than
            duplicated. Existing cues at OTHER positions are left untouched.

            Params: track_name (str)
            Returns: (clips_found, cues_created_or_renamed)
            """
            track_name = str(params[0]) if params else "Cues"

            cue_track = None
            for track in song.tracks:
                if track.name == track_name:
                    cue_track = track
                    break
            if cue_track is None:
                self.logger.warning("generate_cues: track '%s' not found", track_name)
                return (0, 0)

            try:
                clips = list(cue_track.arrangement_clips)
            except AttributeError:
                self.logger.warning("generate_cues: arrangement_clips not available")
                return (0, 0)

            if not clips:
                self.logger.info("generate_cues: no arrangement clips on '%s'", track_name)
                return (0, 0)

            # Build a time → cue_point index for fast lookup
            def cue_map():
                return {round(cp.time, 3): cp for cp in song.cue_points}

            existing = cue_map()
            saved_time = song.current_song_time
            processed = 0

            for clip in clips:
                t_key = round(clip.start_time, 3)
                if t_key in existing:
                    # Just rename the existing cue
                    existing[t_key].name = clip.name
                else:
                    # Seek to position and toggle a new cue
                    song.current_song_time = clip.start_time
                    song.set_or_delete_cue()
                    existing = cue_map()  # refresh after creation
                    if t_key in existing:
                        existing[t_key].name = clip.name
                processed += 1

            # Restore playhead
            song.current_song_time = saved_time
            self.logger.info("generate_cues: processed %d clips from '%s'", processed, track_name)
            return (len(clips), processed)

        self.osc_server.add_handler("/live/song/generate_cues_from_track",
                                     partial(song_generate_cues_from_track, self.song))

        #--------------------------------------------------------------------------------
        # Callbacks for Song: Get file path of first arrangement clip on a named track
        # Used by the bridge analyzer to locate Guide.wav on disk
        #--------------------------------------------------------------------------------
        def song_get_guide_clip_path(song, params):
            """
            Params: track_name (str, default "Guide")
            Returns: (file_path_str, arrangement_start_beats) — empty string and -1 if not found.
            arrangement_start_beats is the beat position of the clip in the arrangement,
            used to offset analysis timestamps to absolute arrangement positions.
            """
            track_name = str(params[0]) if params else "Guide"
            for track in song.tracks:
                if track.name == track_name:
                    try:
                        clips = list(track.arrangement_clips)
                    except AttributeError:
                        return ("", -1.0)
                    for clip in clips:
                        try:
                            path = clip.file_path
                            start = float(clip.start_time)  # beats from arrangement start
                            if path:
                                self.logger.info("Guide clip: path=%s start_beat=%.2f", path, start)
                                return (path, start)
                        except AttributeError:
                            pass
            self.logger.warning("get_guide_clip_path: track '%s' not found or has no clips", track_name)
            return ("", -1.0)

        self.osc_server.add_handler("/live/song/get/guide_clip_path",
                                    partial(song_get_guide_clip_path, self.song))

        #--------------------------------------------------------------------------------
        # Replace ALL cue markers atomically.
        # Params: bpm (float), clip_start_beat (float),
        #         then flat pairs: name (str), time_seconds (float), ...
        # Does everything inside Ableton's Python environment — no OSC round-trips.
        #--------------------------------------------------------------------------------
        def song_replace_all_cues(song, params):
            """
            1. Sets tempo.
            2. Deletes every existing cue.
            3. Creates each new cue and immediately names it.
            4. Restores the playhead position.

            Params (flat list):
              bpm            — float, song tempo
              clip_start_beat — float, arrangement beat where the clip starts (0 for bar 1)
              name0, time0s, name1, time1s, ...  (time in SECONDS, not beats)

            Returns: (cues_created,)
            """
            args = list(params)
            if len(args) < 2:
                self.logger.warning("replace_all_cues: not enough params")
                return (0,)

            try:
                bpm = float(args[0])
                clip_start_beat = float(args[1])
                args = args[2:]
            except (ValueError, TypeError) as e:
                self.logger.error("replace_all_cues: bad bpm/offset params: %s", e)
                return (0,)

            # Parse (name, time_seconds) pairs
            pairs = []
            i = 0
            while i + 1 < len(args):
                try:
                    name = str(args[i])
                    time_s = float(args[i + 1])
                    beat = round(time_s * bpm / 60.0) + clip_start_beat  # snap to nearest beat
                    pairs.append((name, float(beat)))
                except (ValueError, TypeError):
                    pass
                i += 2

            if not pairs:
                self.logger.warning("replace_all_cues: no valid pairs")
                return (0,)

            # Set tempo
            try:
                song.tempo = bpm
                self.logger.info("replace_all_cues: set tempo=%.2f BPM", bpm)
            except Exception as e:
                self.logger.error("replace_all_cues: failed to set tempo: %s", e)

            saved_time = song.current_song_time

            # Delete all existing cues by toggling at each cue's position
            for cp in list(song.cue_points):
                try:
                    song.current_song_time = cp.time
                    song.set_or_delete_cue()  # cue exists → deletes it
                except Exception as e:
                    self.logger.warning("replace_all_cues: could not delete cue at %.2f: %s", cp.time, e)

            self.logger.info("replace_all_cues: cleared existing cues, creating %d new ones", len(pairs))

            # Create each cue and name it immediately
            created = 0
            for name, beat in pairs:
                try:
                    song.current_song_time = beat
                    song.set_or_delete_cue()  # no cue here → creates it
                    # Find the newly created cue by position and name it
                    for cp in song.cue_points:
                        if abs(cp.time - beat) < 0.5:
                            cp.name = name
                            break
                    self.logger.info("  created cue '%s' at beat %.2f", name, beat)
                    created += 1
                except Exception as e:
                    self.logger.error("  failed to create cue '%s' at beat %.2f: %s", name, beat, e)

            song.current_song_time = saved_time
            self.logger.info("replace_all_cues: done — %d cues created", created)
            return (created,)

        self.osc_server.add_handler("/live/song/replace_all_cues",
                                    partial(song_replace_all_cues, self.song))
        # Keep old handler for compatibility
        self.osc_server.add_handler("/live/song/create_cues_from_data",
                                    partial(song_replace_all_cues, self.song))

        #--------------------------------------------------------------------------------
        # Callbacks for Song: Cue point properties
        #--------------------------------------------------------------------------------
        def song_get_cue_points(song, _):
            cue_points = song.cue_points
            cue_point_pairs = [(cue_point.name, cue_point.time) for cue_point in cue_points]
            return tuple(element for pair in cue_point_pairs for element in pair)
        self.osc_server.add_handler("/live/song/get/cue_points", partial(song_get_cue_points, self.song))

        def song_jump_to_cue_point(song, params: Tuple[Any] = ()):
            cue_point_index = params[0]
            if isinstance(cue_point_index, str):
                for cue_point in song.cue_points:
                    if cue_point.name == cue_point_index:
                        cue_point.jump()
            elif isinstance(cue_point_index, int):
                cue_point = song.cue_points[cue_point_index]
                cue_point.jump()
        self.osc_server.add_handler("/live/song/cue_point/jump", partial(song_jump_to_cue_point, self.song))

        self.osc_server.add_handler("/live/song/cue_point/add_or_delete", partial(self._call_method, self.song, "set_or_delete_cue"))
        def song_cue_point_set_name(song, params: Tuple[Any] = ()):
            cue_point_index = params[0]
            new_name = params[1]
            cue_point = song.cue_points[cue_point_index]
            cue_point.name = new_name
        self.osc_server.add_handler("/live/song/cue_point/set/name", partial(song_cue_point_set_name, self.song))

        #--------------------------------------------------------------------------------
        # Listener for /live/song/get/beat
        #--------------------------------------------------------------------------------
        self.last_song_time = -1.0
        
        def stop_beat_listener(params: Tuple[Any] = ()):
            try:
                self.song.remove_current_song_time_listener(self.current_song_time_changed)
                self.logger.info("Removing beat listener")
            except:
                pass

        def start_beat_listener(params: Tuple[Any] = ()):
            stop_beat_listener()
            self.logger.info("Adding beat listener")
            self.song.add_current_song_time_listener(self.current_song_time_changed)

        self.osc_server.add_handler("/live/song/start_listen/beat", start_beat_listener)
        self.osc_server.add_handler("/live/song/stop_listen/beat", stop_beat_listener)

    def current_song_time_changed(self):
        #--------------------------------------------------------------------------------
        # If song has rewound or skipped to next beat, sent a /live/beat message
        #--------------------------------------------------------------------------------
        if (self.song.current_song_time < self.last_song_time) or \
                (int(self.song.current_song_time) > int(self.last_song_time)):
            self.osc_server.send("/live/song/get/beat", (int(self.song.current_song_time),))
        self.last_song_time = self.song.current_song_time

    def clear_api(self):
        super().clear_api()
        try:
            self.song.remove_current_song_time_listener(self.current_song_time_changed)
        except:
            pass
