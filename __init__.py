from adapt.intent import IntentBuilder
from mycroft import MycroftSkill, intent_handler
from mycroft.util.log import getLogger

__author__ = 'MJ2P'


LOGGER = getLogger(__name__)


class Subsonic(MycroftSkill):
    def __init__(self):
        MycroftSkill.__init__(self)

    @intent_handler(IntentBuilder('PlayArtistIntent').require('Play').require('Music').require('Artist'))
    def handle_play_artist_intent(self, message):
        artist = message.data.get('Artist')
        self.speak_dialog('artist', {'artist': artist})


def create_skill():
    return Subsonic()

