import hashlib
import string
from random import SystemRandom, shuffle

import requests
from adapt.intent import IntentBuilder
from mycroft import MycroftSkill, intent_handler
from mycroft.util.log import getLogger
from mycroft.util.parse import fuzzy_match, match_one
from mycroft.skills.audioservice import AudioService

__author__ = 'MJ2P'


LOGGER = getLogger(__name__)


class Subsonic(MycroftSkill):
    def __init__(self):
        MycroftSkill.__init__(self)
        self.results = None
        self.audio_service = None

    def initialize(self):
        self.audio_service = AudioService(self.emitter)

    def hash_password(self):
        """
        return random salted md5 hash of password
        """
        characters = string.ascii_uppercase + string.ascii_lowercase + string.digits
        salt = ''.join(SystemRandom().choice(characters) for i in range(9))
        salted_password = self.settings.get('password') + salt
        token = hashlib.md5(salted_password.encode('utf-8')).hexdigest()
        return token, salt

    def create_url(self, endpoint):
        """
        build the standard url for interfacing with the Subsonic REST API
        :param endpoint: REST endpoint to incorporate in the url
        """
        token, salt = self.hash_password()
        url = '{}/rest/{}?u={}&t={}&s={}&v=1.16.0&c=pSub&f=json'.format(
            self.settings.get('server_url'),
            endpoint,
            self.settings.get('username'),
            token,
            salt
        )
        return url

    @staticmethod
    def make_request(url):
        """
        GET the supplied url and resturn the response as json.
        Handle any errors present.
        :param url: full url. see create_url method for details
        :return: Subsonic response or None on failure
        """
        r = requests.get(url=url)

        try:
            response = r.json()
        except ValueError:
            response = {
                'subsonic-response': {
                    'error': {
                        'code': 100,
                        'message': r.text
                    },
                    'status': 'failed'
                }
            }

        subsonic_response = response.get('subsonic-response', {})
        status = subsonic_response.get('status', 'failed')

        if status == 'failed':
            error = subsonic_response.get('error', {})
            print(
                'Command Failed! {}: {}'.format(
                    error.get('code', ''),
                    error.get('message', '')
                )
            )
            return None

        return response

    def scrobble(self, song_id):
        """
        notify the Subsonic server that a track is being played within pSub
        :param song_id:
        :return:
        """
        self.make_request(
            url='{}&id={}'.format(
                self.create_url('scrobble'),
                song_id
            )
        )

    def search(self, query):
        """
        search using query and return the result
        :return:
        :param query: search term string
        """
        results = self.make_request(
            url='{}&query={}'.format(self.create_url('search3'), query)
        )
        if results:
            return results['subsonic-response']['searchResult3']
        return {}

    def get_artists(self):
        """
        Gather list of Artists from the Subsonic server
        :return: list
        """
        artists = self.make_request(url=self.create_url('getArtists'))
        if artists:
            return artists['subsonic-response']['artists']['index']
        return []

    def get_playlists(self):
        """
        Get a list of available playlists from the server
        :return:
        """
        playlists = self.make_request(url=self.create_url('getPlaylists'))
        if playlists:
            return playlists['subsonic-response']['playlists']['playlist']
        return []

    def get_music_folders(self):
        """
        Gather list of Music Folders from the Subsonic server
        :return: list
        """
        music_folders = self.make_request(url=self.create_url('getMusicFolders'))
        if music_folders:
            return music_folders['subsonic-response']['musicFolders']['musicFolder']
        return []

    def get_album_tracks(self, album_id):
        """
        return a list of album track ids for the given album id
        :param album_id: id of the album
        :return: list
        """
        album_info = self.make_request('{}&id={}'.format(self.create_url('getAlbum'), album_id))
        songs = []

        for song in album_info['subsonic-response']['album']['song']:
            songs.append(song)

        return songs

    @intent_handler(
        IntentBuilder(
            'PlayArtistIntent'
        ).require(
            'Play'
        ).optionally(
            'Music'
        ).optionally(
            'ArtistKeyWord'
        ).require(
            'Artist'
        )
    )
    def handle_play_artist_intent(self, message):
        """
        Handle playing tracks by a chosen artist
        Phrases like:
            Play some Aphex Twin
            Play some tracks by Tune-yards
            Play some noise by Atari Teenage Riot
        """
        artist = message.data.get('Artist')
        available_artists = self.search(artist).get('artist', [])

        if not available_artists:
            self.speak_dialog('no.artists', {'artist': artist})
            return

        self.speak_dialog('artist', {'artist': artist})

        songs = []

        for artist in available_artists:
            artist_info = self.make_request('{}&id={}'.format(self.create_url('getArtist'), artist.get('id')))

            for album in artist_info['subsonic-response']['artist']['album']:
                songs += self.get_album_tracks(album.get('id'))

        shuffle(songs)

        self.audio_service.play(
            [
                '{}&id={}'.format(self.create_url('download'), song['id'])
                for song in songs
            ],
            'vlc'
        )

    @intent_handler(
        IntentBuilder(
            'PlayMusicIntent'
        ).require(
            'Play'
        ).optionally(
            'AlbumKeyWord'
        ).require(
            'MusicTarget'
        ).require(
            'ArtistKeyword'
        ).require(
            'Artist'
        )
    )
    def handle_play_music_intent(self, message):
        """
        Handle playing of an Album or a Single Track, with an Artist specified
        Phrases like:
            Play the album Syro by Aphex Twin
            Play Tundra by Squarepusher (single track)
        """
        # album keyword lets us determine that the user has definitely asked for an album
        album_keyword = message.data.get('AlbumKeyword')
        # target can be a single track or an album
        target = message.data.get('MusicTarget')
        artist = message.data.get('Artist')

        search_results = self.search(target)

        if album_keyword:
            # user gave tan Album keyword so we can skip all other search results
            available_targets = search_results.get('album', [])
        else:
            available_targets = search_results.get('song', []) + search_results.get('album')

        # make sure the targets returned from the search have the correct artist
        matching_targets = []

        for found_target in available_targets:
            if 'artist' not in found_target:
                # videos may show up in search results but will have no artist
                continue
            # use fuzzy matching on the artist name and only accept those that match the
            if fuzzy_match(found_target.get('artist').lower().strip(), artist.lower().strip()) > 0.8:
                matching_targets.append(found_target)

        # if no targets have a nearly matching artist we report the failure to the user
        if not matching_targets:
            self.speak_dialog('no.targets', {'target': target, 'artist': artist})
            return

        # otherwise we 'match_one' target based on the target name.
        # build a dict matching target name to target id (needed for playing0
        final_targets = dict()

        for matching_target in matching_targets:
            if 'name' in matching_target:
                # name only exists in an album
                final_targets[matching_target['name']] = {'id': matching_target['id'], 'type': 'album'}
            else:
                # we only chose albums and songs from the search results so can safely assume we have a song
                # songs are identified by 'title'
                final_targets[matching_target['title']] = {'id': matching_target['id'], 'type': 'song'}

        chosen_target = dict(match_one(target, final_targets)[0])
        chose_target_name = next(t for t in final_targets if final_targets[t]['id'] == chosen_target['id'])

        self.speak_dialog(
            'target', {'target': chose_target_name, 'artist': artist}
        )

        if chosen_target['type'] == 'song':
            songs = [chosen_target]
        else:
            songs = self.get_album_tracks(chosen_target['id'])
            shuffle(songs)

        self.audio_service.play(
            [
                '{}&id={}'.format(self.create_url('download'), song['id'])
                for song in songs
            ],
            'vlc'
        )


def create_skill():
    return Subsonic()

