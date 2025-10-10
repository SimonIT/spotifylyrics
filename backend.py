# -*- coding: utf-8 -*-
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import webbrowser  # to open link on browser
from collections import namedtuple
from typing import Tuple
from urllib import request

import requests
from diskcache import Cache

import services as s

cache = Cache(os.path.join(s.Config.SETTINGS_DIR, 'cache'))

if sys.platform == "win32":
    import win32process
    import psutil
    import win32gui
elif sys.platform == "linux":
    import dbus
elif sys.platform == "darwin":
    import applescript


class Song:
    name = ""
    artist = ""
    album = "UNKNOWN"
    year = -1
    genre = "UNKNOWN"

    cycles_per_minute = -1
    beats_per_minute = -1
    dances = []

    def __init__(self, artist, name):
        self.artist = artist
        self.name = name
        self.dances = []

    @classmethod
    def get_from_string(cls, songstring: str):
        song_name_parts = songstring.split(" - ")
        artist = ""
        if len(song_name_parts) > 2:
            artist = song_name_parts[0]
            name = " - ".join(song_name_parts[1:-1])
        elif len(song_name_parts) == 2:
            artist = song_name_parts[0]
            name = song_name_parts[1]
        else:
            name = song_name_parts[0]
        name = re.sub(r' \(.*?\)', '', name, flags=re.DOTALL)
        name = re.sub(r' \[.*?\]', '', name, flags=re.DOTALL)
        return cls(artist, name)

    def __str__(self):
        return "%s: %s (%d) \nGenre: %s\nAlbum: %s\n" \
               "Cycles per minute: %d\nBeats per minute: %d\nDances: %s\n" \
               % (
                   self.artist, self.name, self.year, self.genre, self.album,
                   self.cycles_per_minute, self.beats_per_minute, self.dances,
               )


class StreamingService:
    def get_windows_executable_name(self) -> str:
        raise NotImplementedError

    def get_apple_script(self) -> str:
        raise NotImplementedError

    def get_linux_session_object_name(self) -> str:
        raise NotImplementedError

    def get_windows_exe_path(self) -> str:
        raise NotImplementedError

    def get_linux_open_command(self) -> str:
        raise NotImplementedError

    def get_apple_open_command(self) -> str:
        raise NotImplementedError

    def get_not_playing_windows_title(self) -> Tuple:
        raise NotImplementedError


class SpotifyStreamingService(StreamingService):
    def get_windows_executable_name(self) -> str:
        return 'Spotify.exe'

    def get_apple_script(self) -> str:
        return """set currentArtist to artist of current track as string
    set currentTrack to name of current track as string
    return currentArtist & " - " & currentTrack"""

    def get_linux_session_object_name(self) -> str:
        return "spotify"

    def get_windows_exe_path(self) -> str:
        return os.getenv("APPDATA") + '\\Spotify\\Spotify.exe'

    def get_linux_open_command(self) -> str:
        return "spotify"

    def get_apple_open_command(self) -> str:
        return "Spotify"

    def get_not_playing_windows_title(self) -> Tuple:
        return 'Spotify', 'Spotify Free', 'Spotify Premium', 'Drag', 'Advertisement', ''

    def __str__(self):
        return "Spotify"


class TidalStreamingService(StreamingService):
    def get_windows_executable_name(self) -> str:
        return 'TIDAL.exe'

    def get_apple_script(self) -> str:
        return ""  # TODO

    def get_linux_session_object_name(self) -> str:
        return ""  # Not supported on linux

    def get_windows_exe_path(self) -> str:
        return os.getenv("LOCALAPPDATA") + '\\TIDAL\\TIDAL.exe'

    def get_linux_open_command(self) -> str:
        return ""  # Not supported on linux

    def get_apple_open_command(self) -> str:
        return "Tidal"  # TODO

    def get_not_playing_windows_title(self) -> Tuple:
        return 'TIDAL', ''

    def __str__(self):
        return "Tidal"


class VlcMediaPlayer(StreamingService):
    def get_windows_executable_name(self) -> str:
        return 'vlc.exe'

    def get_apple_script(self) -> str:
        return "return get name of current item"

    def get_linux_session_object_name(self) -> str:
        return "vlc"

    def get_windows_exe_path(self) -> str:
        return os.getenv("PROGRAMFILES") + "\\VideoLAN\\VLC\\vlc.exe"

    def get_linux_open_command(self) -> str:
        return "vlc"

    def get_apple_open_command(self) -> str:
        return "VLC"  # TODO

    def get_not_playing_windows_title(self) -> Tuple:
        return 'VLC media player', ''

    def __str__(self):
        return "VLC"


# With Sync. Not working: s._minilyrics, s._qq, s._rentanadviser (broken), s._syair (broken)
SERVICES_LIST1 = [s._megalobiz]

# Without Sync. - Ordered by reliability and speed (Local is always first to check user's own lyrics)
# Multiple sources to maximize coverage - if one doesn't have it, another will!
SERVICES_LIST2 = [
    s._local,               # User's own lyrics files
    s._lyricsovh,           # Primary API - fast and reliable ✅
    s._genius,              # Annotated lyrics, 100% success ✅
    s._letras,              # Brazilian site, 100% success ✅
    s._tekstowo,            # Polish site, 100% success ✅
    s._songlyrics,          # Good scraper, 33% success ✅
    s._geniusromaji,        # Genius romanized (Japanese/Korean songs)
    s._lyricalnonsense,     # Excellent for Japanese songs with romaji
    s._musixmatch,          # Sometimes works, backup
    # REMOVED: Dead/broken services
    # s._chartlyrics,       # API no longer returns lyrics (Lyric: None)
    # s._lyricscom,         # 403 Forbidden (anti-bot)
    # s._azlyrics,          # Blocked by anti-bot protection
    # s._azapi,             # Search engines blocking the library
    # s._versuri,           # Timeout/scraper outdated
    # s._songmeanings       # HTML structure changed, broken
]

# Accords
SERVICES_LIST3 = [s._ultimateguitar, s._cifraclub, s._songsterr]

'''
current_service is used to store the current index of the list.
Useful to change the lyrics with the button "Next Lyric" if
the service returned a wrong song
'''
CURRENT_SERVICE = -1
LAST_SERVICE_USED = -1  # Track which service was last used for "Change Lyrics" button
SECONDS_IN_WEEK = 604800
LyricsMetadata = namedtuple("LyricsMetadata", ["lyrics", "url", "service_name", "timed"])


def cache_lyrics(func):
    def recreate_cache():
        global cache
        cache_dir = cache.directory
        cache.close()
        shutil.rmtree(cache_dir)
        cache = Cache(cache_dir)
        print("Cache recreated")

    def wrapper(*args, **kwargs):
        song = args[0]
        sync = kwargs.get("sync", False)
        ignore_cache = kwargs.get("ignore_cache", False)

        clean_song_name = '{}-{}'.format(song.artist, song.name)
        if not ignore_cache:
            try:
                lyrics_metadata = cache.get(clean_song_name)
            except ValueError:
                recreate_cache()
                lyrics_metadata = None
            if not lyrics_metadata or lyrics_metadata.lyrics == s.Config.ERROR:
                lyrics_metadata = func(*args, **kwargs)
                try:
                    cache.set(clean_song_name, lyrics_metadata, expire=SECONDS_IN_WEEK)
                except ValueError:
                    recreate_cache()
            return lyrics_metadata
        else:
            lyrics_metadata = func(*args, **kwargs)
            cache.set(clean_song_name, lyrics_metadata, expire=SECONDS_IN_WEEK)
            return lyrics_metadata

    return wrapper


@cache_lyrics
def load_lyrics(song: Song, **kwargs):
    sync = kwargs.get("sync", False)
    global CURRENT_SERVICE

    # Note: _local is now permanently in SERVICES_LIST2, no need to insert dynamically
    
    timed = False
    lyrics = s.Config.ERROR
    
    # When at the end of services, wrap around to beginning
    if CURRENT_SERVICE >= (len(SERVICES_LIST1) + len(SERVICES_LIST2) - 1):
        CURRENT_SERVICE = -1

    if sync and CURRENT_SERVICE + 1 < len(SERVICES_LIST1):
        temp_lyrics = []
        for i in range(CURRENT_SERVICE + 1, len(SERVICES_LIST1)):
            lyrics, url, service_name, timed = SERVICES_LIST1[i](song)
            if lyrics != s.Config.ERROR:
                CURRENT_SERVICE = i
                if timed:
                    break
                else:
                    temp_lyrics = lyrics, url, service_name, timed
        if not timed and temp_lyrics and temp_lyrics[0] != s.Config.ERROR:
            lyrics, url, service_name, timed = temp_lyrics

    current_not_synced_service = CURRENT_SERVICE - len(SERVICES_LIST1)
    current_not_synced_service = -1 if current_not_synced_service < -1 else current_not_synced_service
    start_index = current_not_synced_service + 1
    
    # If start_index is beyond the list, wrap around to beginning
    if start_index >= len(SERVICES_LIST2):
        start_index = 0
        CURRENT_SERVICE = -1  # Reset to start from beginning
    
    if sync and lyrics == s.Config.ERROR or not sync or CURRENT_SERVICE > (len(SERVICES_LIST1) - 1):
        for i in range(start_index, len(SERVICES_LIST2)):
            result = SERVICES_LIST2[i](song)
            lyrics, url, service_name = result[0], result[1], result[2]
            if lyrics != s.Config.ERROR:
                lyrics = lyrics.replace("&amp;", "&").replace("`", "'").strip()
                CURRENT_SERVICE = i + len(SERVICES_LIST1)
                global LAST_SERVICE_USED
                LAST_SERVICE_USED = i  # Remember which service index found the lyrics
                break
        # If we tried all services from start_index to end and found nothing,
        # wrap around to beginning for next attempt
        if lyrics == s.Config.ERROR and start_index > 0:
            # We tried from start_index to end, next time try from beginning
            CURRENT_SERVICE = len(SERVICES_LIST1) + len(SERVICES_LIST2) - 1
            LAST_SERVICE_USED = -1  # Reset so next_lyrics will wrap properly
        elif lyrics == s.Config.ERROR and start_index == 0:
            # We already wrapped and tried everything, keep at -1 to try again from start
            CURRENT_SERVICE = -1
            LAST_SERVICE_USED = -1  # Reset so next_lyrics will wrap properly
    
    if lyrics == s.Config.ERROR:
        service_name = "---"

    # return "Error: Could not find lyrics."  if the for loop doesn't find any lyrics
    return LyricsMetadata(lyrics, url, service_name, timed)


def load_info(window, song: Song):
    def complete(function):
        function(song)
        window.refresh_info()

    threading.Thread(target=complete, args=(s._tanzmusikonline,)).start()
    threading.Thread(target=complete, args=(s._welchertanz,)).start()


def get_lyrics(song: Song, sync=False):
    global CURRENT_SERVICE, LAST_SERVICE_USED
    CURRENT_SERVICE = -1
    LAST_SERVICE_USED = -1  # Reset when getting fresh lyrics

    return load_lyrics(song, sync=sync)


def next_lyrics(song: Song, sync=False):
    global CURRENT_SERVICE, LAST_SERVICE_USED
    # When clicking "Change Lyrics", start searching from the service AFTER the one that last found lyrics
    # This ensures we cycle through different services even if the same service would find lyrics again
    if LAST_SERVICE_USED >= 0:
        # Set CURRENT_SERVICE so that the next search starts AFTER the last successful service
        CURRENT_SERVICE = LAST_SERVICE_USED + len(SERVICES_LIST1)
    else:
        # If no service has found lyrics yet, or we wrapped around, advance by 1
        # to try the next service instead of staying stuck
        CURRENT_SERVICE = (CURRENT_SERVICE + 1) % (len(SERVICES_LIST1) + len(SERVICES_LIST2))
    return load_lyrics(song, sync=sync, ignore_cache=True)


def load_chords(song: Song):
    for i in SERVICES_LIST3:
        urls = i(song)
        for url in urls:
            webbrowser.open(url)


spids = []


# windows only
def update_spids(windows_executable: str):
    global spids
    for proc in psutil.process_iter():
        try:
            if proc.name() == windows_executable:
                spids.append(proc.pid)
        except psutil.NoSuchProcess:
            print("Process does not exist anymore")
        except psutil.AccessDenied:
            print("Cannot access the name of the process")


def get_window_title(service: StreamingService) -> str:
    window_name = ''
    if sys.platform == "win32":
        global spids
        if len(spids) == 0:
            update_spids(service.get_windows_executable_name())

        windows = []

        def enum_window_callback(hwnd, pid):
            nonlocal windows
            tid, current_pid = win32process.GetWindowThreadProcessId(hwnd)
            if pid == current_pid and win32gui.IsWindowVisible(hwnd):
                windows.append(hwnd)

        def get_title():
            global spids
            nonlocal windows
            nonlocal window_name
            windows = []

            try:
                for pid in spids:
                    win32gui.EnumWindows(enum_window_callback, pid)
                    for item in windows:
                        if win32gui.GetWindowText(item):
                            window_name = win32gui.GetWindowText(item)
                            raise StopIteration
            except StopIteration:
                pass

        get_title()

        if not window_name:
            update_spids(service.get_windows_executable_name())
            get_title()

    elif sys.platform == "darwin":
        try:
            r = applescript.tell.app(service.get_apple_open_command(), service.get_apple_script())
            window_name = r.out
        except Exception as error:
            print(error)
    else:
        try:
            session = dbus.SessionBus()
            spotify_dbus = session.get_object("org.mpris.MediaPlayer2.%s" % service.get_linux_session_object_name(),
                                              "/org/mpris/MediaPlayer2")
            spotify_interface = dbus.Interface(spotify_dbus, "org.freedesktop.DBus.Properties")
            metadata = spotify_interface.Get("org.mpris.MediaPlayer2.Player", "Metadata")
            window_name = "%s - %s" % (metadata['xesam:artist'][0], metadata['xesam:title'])
        except Exception as error:
            print(error)
        if not window_name:
            try:
                command = "xwininfo -tree -root"
                windows = subprocess.check_output(["/bin/bash", "-c", command]).decode("utf-8")
                for line in windows.splitlines():
                    if '("' + service.get_linux_open_command() + '" "' + service.get_linux_open_command() + '")' in line.lower():
                        if " - " in line:
                            window_name = line.split('"')[1]
                            break
            except Exception as error:
                print(error)
    if "—" in window_name:
        window_name = window_name.replace("—", "-")
    return window_name


def check_version() -> bool:
    proxy = request.getproxies()
    try:
        return get_version() >= \
               float(requests.get("https://api.github.com/repos/SimonIT/spotifylyrics/tags", timeout=5, proxies=proxy)
                     .json()[0]["name"])
    except Exception:
        return True


def get_version() -> float:
    return 1.55


def open_spotify(service: StreamingService) -> bool:
    if sys.platform == "win32":
        if not get_window_title(service):
            try:
                subprocess.Popen(service.get_windows_exe_path())
            except FileNotFoundError:
                return False
    elif sys.platform == "linux":
        if not get_window_title(service):
            subprocess.Popen(service.get_linux_open_command())
    elif sys.platform == "darwin":
        if not get_window_title(service):
            subprocess.call(["open", "-a", service.get_apple_open_command()])
    return True


def main():
    if os.name == "nt":
        os.system("chcp 65001")

    def clear():
        if os.name == "nt":
            os.system("cls")
        else:
            os.system("clear")

    clear()
    old_song_name = ""
    service = SpotifyStreamingService()
    while True:
        song_name = get_window_title(service)
        if old_song_name != song_name:
            if song_name not in service.get_not_playing_windows_title():
                old_song_name = song_name
                clear()
        time.sleep(1)


if __name__ == '__main__':
    main()
