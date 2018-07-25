from adapt.intent import IntentBuilder
from mycroft import MycroftSkill, intent_handler
from mycroft.util.log import getLogger

__author__ = 'MJ2P'


LOGGER = getLogger(__name__)

class Subsonic(MycroftSkill):
    def __init__(self):
        MycroftSkill.__init__(self)

    @intent_handler(IntentBuilder("").require("Play").optionally("Music").optionally("Artist"))
    def handle_artist_play(self, message):
	artist = message.data.get("Artist")
        self.speak_dialog('artist', {"artist": artist})


def create_skill():
    return Subsonic()

