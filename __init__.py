import hashlib
import string
import time
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
        self.song_ids = dict()
        self.play_list_count = 0

    def initialize(self):
        self.audio_service = AudioService(self.emitter)
        self.add_event('mycroft.audio.playing_track', self.handle_playing_track)
        self.add_event('mycroft.audio.service.next', self.handle_next_track)
        self.add_event('mycroft.audio.service.prev', self.handle_prev_track)

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
        url = '{}/rest/{}?u={}&t={}&s={}&v=1.16.0&c=mycroft&f=json'.format(
            self.settings.get('server_url'),
            endpoint,
            self.settings.get('username'),
            token,
            salt
        )
        return url

    def make_request(self, url):
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
            self.speak_dialog(
                'subsonic.error',
                {
                    'code': error.get('code'),
                    'message': error.get('message')
                }
            )
            return None

        return response

    def handle_playing_track(self, message):
        """
        notify the Subsonic server that a track is being played
        triggered by the mycroft.audio.playing_track event
        """
        # get the track name from the message
        track_name = message.data.get('track')
        # get the corresponding ID from our map
        song_id = self.song_ids.get(track_name)

        if song_id:
            # scrobble the track
            self.make_request(
                url='{}&id={}'.format(
                    self.create_url('scrobble'),
                    song_id
                )
            )

    def handle_next_track(self, message):
        """
        reduce the playlist count by 1
        triggered by the mycroft.audio.service.next event
        """
        self.play_list_count -= 1

    def handle_prev_track(self, message):
        """
        increase the playlist count by 1
        triggered by the mycroft.audio.service.prev event
        """
        self.play_list_count += 1

    def search(self, query):
        """
        search using query and return the result
        """
        results = self.make_request(
            url='{}&query={}'.format(self.create_url('search3'), query)
        )
        if results:
            return results['subsonic-response']['searchResult3']
        return {}

    def get_playlists(self):
        """
        Get a list of available playlists from the server
        """
        playlists = self.make_request(url=self.create_url('getPlaylists'))
        if playlists:
            return playlists['subsonic-response']['playlists']['playlist']
        return []

    def get_random_songs(self):
        """
        Gather random tracks from the Subsonic server
        """
        url = self.create_url('getRandomSongs')
        random_songs = self.make_request(url)

        if not random_songs:
            return []

        return random_songs['subsonic-response']['randomSongs']['song']

    def get_album_tracks(self, album_id):
        """
        return a list of album track ids for the given album id
        """
        album_info = self.make_request('{}&id={}'.format(self.create_url('getAlbum'), album_id))
        songs = []

        for song in album_info['subsonic-response']['album']['song']:
            songs.append(song)

        return songs

    def play_songs(self, songs):
        """
        play a new set of tracks.
        """
        # we use the song_ids to scrobble tracks as they are played
        # this method resets the playlist so we reset the list of sing ids too
        self.song_ids = {}

        for song in songs:
            self.song_ids[song['title']] = song['id']

        shuffle(songs)
        playlist = ['{}&id={}'.format(self.create_url('download'), song['id']) for song in songs]
        # this method resets the playlist so playlist length always starts off as long as this one
        self.play_list_count = len(playlist)
        self.audio_service.play(playlist, 'vlc')

    def queue_songs(self, songs):
        """
        add the songs to the currently playing queue
        (basically the same as play_songs above but we add to the song id map and play_list_count)
        """
        for song in songs:
            self.song_ids[song['title']] = song['id']

        shuffle(songs)
        playlist = ['{}&id={}'.format(self.create_url('download'), song['id']) for song in songs]
        # this method adds to the playlist so playlist length gets bigger as songs are added
        self.play_list_count += len(playlist)
        self.audio_service.queue(playlist)

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
        ).optionally(
            '_TestRunner'
        ).build()
    )
    def handle_play_artist_intent(self, message):
        """
        Handle playing tracks by a chosen artist
        Phrases like:
            Play some Aphex Twin
            Play some tracks by Tune-yards
            Play some noise by Atari Teenage Riot
        """
        if message.data.get('_TestRunner'):
            self.speak('You have reached the Artist Intent')
            return

        artist = message.data.get('Artist')
        available_artists = self.search(artist).get('artist', [])

        if not available_artists:
            self.speak_dialog('no.artists', {'artist': artist})
            return

        # we want to match the best search result
        # make a dict holding the details we need
        matching_artists = dict()

        for available_artist in available_artists:
            matching_artists[available_artist['name']] = available_artist['id']

        matched_artist_id = match_one(artist, matching_artists)[0]

        self.speak_dialog(
            'artist',
            {'artist': next(a for a in matching_artists if matching_artists[a] == matched_artist_id)}
        )

        songs = []
        artist_info = self.make_request('{}&id={}'.format(self.create_url('getArtist'), matched_artist_id))

        for album in artist_info['subsonic-response']['artist']['album']:
            songs += self.get_album_tracks(album.get('id'))

        self.play_songs(songs)

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
        ).build()
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
            available_targets = search_results.get('song', []) + search_results.get('album', [])

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
                final_targets[matching_target['name']] = {
                    'id': matching_target['id'],
                    'type': 'album',
                    'artist': matching_target['artist']
                }
            else:
                # we only chose albums and songs from the search results so can safely assume we have a song
                # songs are identified by 'title'
                final_targets[matching_target['title']] = {
                    'id': matching_target['id'],
                    'type': 'song',
                    'artist': matching_target['artist']
                }

        chosen_target = dict(match_one(target, final_targets)[0])
        chose_target_name = next(t for t in final_targets if final_targets[t]['id'] == chosen_target['id'])

        self.speak_dialog(
            'target',
            {'target': chose_target_name, 'artist': chosen_target['artist']}
        )

        if chosen_target['type'] == 'song':
            songs = [next(t for t in matching_targets if t['id'] == chosen_target['id'])]
        else:
            songs = self.get_album_tracks(chosen_target['id'])

        self.play_songs(songs)

    @intent_handler(
        IntentBuilder(
            'RandomIntent'
        ).require(
            'Play'
        ).optionally(
            'Music'
        ).require(
            'Random'
        ).optionally(
            'Music'
        ).build()
    )
    def handle_random_intent(self, message):
        """
        play random tunes until told to stop
        """
        has_played = message.data.get('has_played')

        if not has_played:
            self.speak_dialog('random')
            message.data['has_played'] = True
            self.play_songs(self.get_random_songs())

        else:
            # we have been here before so just add random tracks to the queue
            self.queue_songs(self.get_random_songs())

        while self.play_list_count > 1:
            time.sleep(5)
            continue

        self.handle_random_intent(message)

    @intent_handler(
        IntentBuilder(
            'RadioIntent'
        ).require(
            'Play'
        ).optionally(
            'Music'
        ).require(
            'Radio'
        ).require(
            'Artist'
        ).build()
    )
    def handle_radio_intent(self, message):
        has_played = message.data.get('has_played')

        artist = message.data.get('Artist')
        available_artists = self.search(artist).get('artist', [])

        if not available_artists:
            self.speak_dialog('no.artists', {'artist': artist})
            return

        # we want to match the best search result
        # make a dict holding the details we need
        matching_artists = dict()

        for available_artist in available_artists:
            matching_artists[available_artist['name']] = available_artist['id']

        matched_artist_id = match_one(artist, matching_artists)[0]

        similar_songs = self.make_request(
            '{}&id={}'.format(self.create_url('getSimilarSongs2'), matched_artist_id)
        )

        if not has_played:
            self.speak_dialog(
                'radio',
                {'artist': next(a for a in matching_artists if matching_artists[a] == matched_artist_id)}
            )
            message.data['has_played'] = True
            self.play_songs(similar_songs['subsonic-response']['similarSongs2']['song'])
        else:
            self.queue_songs(similar_songs['subsonic-response']['similarSongs2']['song'])

        while self.play_list_count > 1:
            time.sleep(5)
            continue

        self.handle_radio_intent(message)

    @intent_handler(
        IntentBuilder(
            'PlaylistIntent'
        ).require(
            'Play'
        ).require(
            'Playlist'
        ).require(
            'PlaylistKeyWord'
        ).build()
    )
    def handle_playlist_intent(self, message):
        playlist = message.data.get('Playlist')
        available_playlists = self.get_playlists()
        matchable_playlists = dict()

        for available_playlist in available_playlists:
            matchable_playlists[available_playlist['name']] = available_playlist['id']

        chosen_playlist_id = match_one(playlist, matchable_playlists)[0]

        self.speak_dialog(
            'playlist',
            {'playlist': next(p['name'] for p in available_playlists if p['id'] == chosen_playlist_id)}
        )

        playlist_info = self.make_request(
            url='{}&id={}'.format(self.create_url('getPlaylist'), chosen_playlist_id)
        )
        songs = playlist_info['subsonic-response']['playlist']['entry']

        self.play_songs(songs)


def create_skill():
    return Subsonic()

