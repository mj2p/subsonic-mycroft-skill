from adapt.intent import IntentBuilder
from mycroft import MycroftSkill, intent_file_handler


class Subsonic(MycroftSkill):
    def __init__(self):
        MycroftSkill.__init__(self)

    @intent_file_handler('subsonic.intent')
    def handle_subsonic(self, message):
        self.speak_dialog('subsonic')


def create_skill():
    return Subsonic()

