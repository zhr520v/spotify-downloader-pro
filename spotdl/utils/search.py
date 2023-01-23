"""
Module for creating Song objects by interacting with Spotify API
or by parsing a query.

To use this module you must first initialize the SpotifyClient.
"""

import concurrent.futures
import json
import logging
from pathlib import Path
from typing import Dict, List, Optional

from spotdl.types.album import Album
from spotdl.types.artist import Artist
from spotdl.types.playlist import Playlist
from spotdl.types.saved import Saved
from spotdl.types.song import Song, SongList
from spotdl.utils.metadata import get_file_metadata

__all__ = [
    "QueryError",
    "get_search_results",
    "parse_query",
    "get_simple_songs",
    "reinit_song",
    "get_song_from_file_metadata",
    "gather_known_songs",
]

logger = logging.getLogger(__name__)


class QueryError(Exception):
    """
    Base class for all exceptions related to query.
    """


def get_search_results(search_term: str) -> List[Song]:
    """
    Creates a list of Song objects from a search term.

    ### Arguments
    - search_term: the search term to use

    ### Returns
    - a list of Song objects
    """

    return Song.list_from_search_term(search_term)


def parse_query(
    query: List[str],
    threads: int = 1,
) -> List[Song]:
    """
    Parse query and return list containing song object

    ### Arguments
    - query: List of strings containing query
    - threads: Number of threads to use

    ### Returns
    - List of song objects
    """

    songs: List[Song] = get_simple_songs(query)

    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=threads) as executor:
        for song in executor.map(reinit_song, songs):
            results.append(song)

    return results


def get_simple_songs(
    query: List[str],
) -> List[Song]:
    """
    Parse query and return list containing simple song objects

    ### Arguments
    - query: List of strings containing query

    ### Returns
    - List of simple song objects
    """

    songs: List[Song] = []
    lists: List[SongList] = []
    for request in query:
        if (
            "youtube.com/watch?v=" in request
            or "youtu.be/" in request
            and "open.spotify.com" in request
            and "track" in request
            and "|" in request
        ):
            split_urls = request.split("|")
            if (
                len(split_urls) <= 1
                or "youtube" not in split_urls[0]
                and "youtu.be" not in split_urls[0]
                or "spotify" not in split_urls[1]
            ):
                raise QueryError(
                    'Incorrect format used, please use "YouTubeURL|SpotifyURL"'
                )

            songs.append(
                Song.from_missing_data(url=split_urls[1], download_url=split_urls[0])
            )
        elif "open.spotify.com" in request and "track" in request:
            songs.append(Song.from_url(url=request))
        elif "open.spotify.com" in request and "playlist" in request:
            lists.append(Playlist.from_url(request, fetch_songs=False))
        elif "open.spotify.com" in request and "album" in request:
            lists.append(Album.from_url(request, fetch_songs=False))
        elif "open.spotify.com" in request and "artist" in request:
            lists.append(Artist.from_url(request, fetch_songs=False))
        elif "album:" in request:
            lists.append(Album.from_search_term(request))
        elif request == "saved":
            lists.append(Saved.from_url(request, fetch_songs=False))
        elif request.endswith(".spotdl"):
            with open(request, "r", encoding="utf-8") as save_file:
                for track in json.load(save_file):
                    # Append to songs
                    songs.append(Song.from_dict(track))
        else:
            songs.append(Song.from_search_term(request))

    for song_list in lists:
        logger.info(
            "Found %s songs in %s (%s)",
            len(song_list.urls),
            song_list.name,
            song_list.__class__.__name__,
        )

        for song in song_list.songs:
            if song.song_list:
                songs.append(Song.from_missing_data(**song.json))
            else:
                song_data = song.json
                song_data["song_list"] = song_list
                songs.append(Song.from_missing_data(**song_data))

    return songs


def songs_from_albums(alubms: List[str]):
    """
    Get all songs from albums ids/urls/etc.

    ### Arguments
    - albums: List of albums ids

    ### Returns
    - List of songs
    """

    songs = []
    for album_id in alubms:
        album = Album.from_url(album_id, fetch_songs=False)

        for song in album.songs:
            if song.song_list:
                songs.append(Song.from_missing_data(**song.json))
            else:
                song_data = song.json
                song_data["song_list"] = album
                songs.append(Song.from_missing_data(**song_data))

    return songs


def reinit_song(song: Song, playlist_numbering: bool = False) -> Song:
    """
    Update song object with new data
    from Spotify

    ### Arguments
    - song: Song object
    - playlist_numbering: bool, default value is False

    ### Returns
    - Updated song object
    """

    data = song.json
    new_data = Song.from_url(data["url"]).json
    data.update((k, v) for k, v in new_data.items() if v is not None)

    if data.get("song_list"):
        # Reinitialize the correct song list object
        if song.song_list:
            song_list = song.song_list.__class__(**data["song_list"])
            data["song_list"] = song_list
            data["list_position"] = song_list.urls.index(song.url)
            if playlist_numbering:
                data["track_number"] = data["list_position"] + 1
                data["tracks_count"] = len(song_list.urls)
                data["album_name"] = song_list.name
                if isinstance(song_list, Playlist):
                    data["album_artist"] = song_list.author_name
                    data["cover_url"] = song_list.cover_url
                data["disc_number"] = 1
                data["disc_count"] = 1

    # return reinitialized song object
    return Song(**data)


def get_song_from_file_metadata(file: Path) -> Optional[Song]:
    """
    Get song based on the file metadata or file name

    ### Arguments
    - file: Path to file

    ### Returns
    - Song object
    """

    file_metadata = get_file_metadata(file)

    if file_metadata is None:
        return None

    return Song.from_missing_data(**file_metadata)


def gather_known_songs(output: str, output_format: str) -> Dict[str, List[Path]]:
    """
    Gather all known songs from the output directory

    ### Arguments
    - output: Output path template
    - output_format: Output format

    ### Returns
    - Dictionary containing all known songs and their paths
    """

    # Get the base directory from the path template
    # Path("/Music/test/{artist}/{artists} - {title}.{output-ext}") -> "/Music/test"
    base_dir = output.split("{", 1)[0]
    paths = Path(base_dir).glob(f"**/*.{output_format}")

    known_songs: Dict[str, List[Path]] = {}
    for path in paths:
        # Try to get the song from the metadata
        song = get_song_from_file_metadata(path)

        # If the songs doesn't have metadata, try to get it from the filename
        if song is None or song.url is None:
            search_results = get_search_results(path.stem)
            if len(search_results) == 0:
                continue

            song = search_results[0]

        known_paths = known_songs.get(song.url)
        if known_paths is None:
            known_songs[song.url] = [path]
        else:
            known_songs[song.url].append(path)

    return known_songs
