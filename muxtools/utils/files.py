import os
import re
import binascii
from pathlib import Path
from shutil import rmtree
from copy import deepcopy
from typing import Callable
from pymediainfo import Track, MediaInfo

from .log import *
from .glob import GlobSearch
from .types import PathLike, TrackType
from .env import get_temp_workdir, get_workdir

__all__ = [
    "ensure_path",
    "uniquify_path",
    "get_crc32",
    "make_output",
    "ensure_path_exists",
    "clean_temp_files",
    "get_absolute_track",
    "get_track_list",
    "find_tracks",
]


def ensure_path(pathIn: PathLike, caller: any) -> Path:
    """
    Utility function for other functions to make sure a path was passed to them.

    :param pathIn:      Supposed passed Path
    :param caller:      Caller name used for the exception and error message
    """
    if pathIn is None:
        raise crit("Path cannot be None.", caller)
    else:
        return Path(pathIn).resolve()


def ensure_path_exists(pathIn: PathLike | list[PathLike] | GlobSearch, caller: any, allow_dir: bool = False) -> Path:
    """
    Utility function for other functions to make sure a path was passed to them and that it exists.

    :param pathIn:      Supposed passed Path
    :param caller:      Caller name used for the exception and error message
    """
    from ..muxing.muxfiles import MuxingFile

    if isinstance(pathIn, MuxingFile):
        return ensure_path_exists(pathIn.file, caller)
    if isinstance(pathIn, GlobSearch):
        pathIn = pathIn.paths
    if isinstance(pathIn, list):
        pathIn = pathIn[0]
    path = ensure_path(pathIn, caller)
    if not path.exists():
        raise crit(f"Path target '{path}' does not exist.", caller)
    if not allow_dir and path.is_dir():
        raise crit(f"Path cannot be a directory.", caller)
    return path


def uniquify_path(path: PathLike) -> str:
    """
    Extends path to not conflict with existing files

    :param file:        Input file

    :return:            Unique path
    """

    if isinstance(path, Path):
        path = str(path.resolve())

    filename, extension = os.path.splitext(path)
    counter = 1

    while os.path.exists(path):
        path = filename + " (" + str(counter) + ")" + extension
        counter += 1

    return path


def get_crc32(file: PathLike) -> str:
    """
    Generates crc32 checksum for file

    :param file:        Input file

    :return:            Checksum for file
    """
    buf = open(file, "rb").read()
    buf = binascii.crc32(buf) & 0xFFFFFFFF
    return "%08X" % buf


def clean_temp_files():
    rmtree(get_temp_workdir())


def make_output(source: PathLike, ext: str, suffix: str = "", user_passed: PathLike | None = None, temp: bool = False) -> Path:
    workdir = get_temp_workdir() if temp else get_workdir()
    source_stem = Path(source).stem

    if user_passed:
        user_passed = Path(user_passed)
        if user_passed.exists() and user_passed.is_dir():
            return Path(user_passed, f"{source_stem}.{ext}").resolve()
        else:
            return user_passed.with_suffix(f".{ext}").resolve()
    else:
        return Path(uniquify_path(os.path.join(workdir, f"{source_stem}{f'_{suffix}' if suffix else ''}.{ext}"))).resolve()


def get_track_list(file: PathLike, caller: any = None) -> list[Track]:
    """Makes a sanitized mediainfo track list"""
    caller = caller if caller else get_track_list
    file = ensure_path_exists(file, caller)
    mediainfo = MediaInfo.parse(file)
    current = 0
    sanitized_list = []
    # Weird mediainfo quirks
    for t in mediainfo.tracks:
        if t.track_type.lower() not in ["video", "audio", "text"]:
            continue
        sanitized_list.append(t)

        t.track_id = current
        if "truehd" in (getattr(t, "commercial_name", "") or "").lower() and "extension" in (getattr(t, "muxing_mode", "") or "").lower():
            current += 1
            identifier = getattr(t, "format_identifier", "AC-3") or "AC-3"
            compat_track = deepcopy(t)
            compat_track.format = identifier
            compat_track.codec_id = f"A_{identifier.replace('-', '')}"
            compat_track.commercial_name = ""
            compat_track.compression_mode = "Lossy"
            compat_track.track_id = current
            sanitized_list.append(compat_track)

        current += 1
    return sanitized_list


def find_tracks(
    file: PathLike,
    name: str | None = None,
    lang: str | None = None,
    type: TrackType | None = None,
    use_regex: bool = True,
    custom_condition: Callable[[Track], bool] | None = None,
) -> list[Track]:
    """
    Convenience function to find tracks with some conditions.

    :param file:                File to parse with MediaInfo.
    :param name:                Name to match, case insensitively.
    :param lang:                Language to match. This can be any of the possible formats like English/eng/en and is case insensitive.
    :param type:                Track Type to search for.
    :param use_regex:           Use regex for the name search instead of checking for equality.
    :param custom_condition:    Here you can pass any function to create your own conditions. (They have to return a bool)
                                For example: custom_condition=lambda track: track.codec_id == "A_EAC3"
    """

    if not name and not lang and not type and not custom_condition:
        return []
    tracks = get_track_list(file)

    def name_matches(title: str) -> bool:
        if title.casefold().strip() == name.casefold().strip():
            return True
        if use_regex:
            return re.match(name, title, re.I)
        return False

    if name:
        tracks = [track for track in tracks if name_matches(getattr(track, "title", "") or "")]

    if lang:
        languages: list[str] = getattr(track, "other_language", None) or list[str]()
        tracks = [track for track in tracks if lang.casefold() in [l.casefold() for l in languages]]

    if type:
        if type not in (TrackType.VIDEO, TrackType.AUDIO, TrackType.SUB):
            raise error("You can only search for video, audio and subtitle tracks!", find_tracks)
        type_string = (str(type.name) if type != TrackType.SUB else "Text").casefold()
        tracks = [track for track in tracks if track.track_type.casefold() == type_string]

    if custom_condition:
        tracks = [track for track in tracks if custom_condition(track)]

    return tracks


def get_absolute_track(file: PathLike, track: int, type: TrackType, caller: any = None) -> Track:
    """
    Finds the absolute track for a relative track number of a specific type.

    :param file:    String or pathlib based Path
    :param track:   Relative track number
    :param type:    TrackType of the requested relative track
    """
    caller = caller if caller else get_absolute_track
    file = ensure_path_exists(file, caller)

    tracks = get_track_list(file, caller)
    videos = [track for track in tracks if track.track_type.casefold() == "Video".casefold()]
    audios = [track for track in tracks if track.track_type.casefold() == "Audio".casefold()]
    subtitles = [track for track in tracks if track.track_type.casefold() == "Text".casefold()]
    match type:
        case TrackType.VIDEO:
            if not videos:
                raise error(f"No video tracks have been found in '{file.name}'!", caller)
            try:
                return videos[track]
            except:
                raise error(f"Your requested track doesn't exist.", caller)
        case TrackType.AUDIO:
            if not audios:
                raise error(f"No audio tracks have been found in '{file.name}'!", caller)
            try:
                return audios[track]
            except:
                raise error(f"Your requested track doesn't exist.", caller)
        case TrackType.SUB:
            if not subtitles:
                raise error(f"No subtitle tracks have been found in '{file.name}'!", caller)
            try:
                return subtitles[track]
            except:
                raise error(f"Your requested track doesn't exist.", caller)
        case _:
            raise error("Not implemented for anything other than Video, Audio or Subtitles.", caller)


def get_absolute_tracknum(file: PathLike, track: int, type: TrackType, caller: any = None) -> int:
    """
    Finds the absolute track number for a relative track number of a specific type.

    :param file:    String or pathlib based Path
    :param track:   Relative track number
    :param type:    TrackType of the requested relative track
    """
    return get_absolute_track(file, track, type, caller).track_id
