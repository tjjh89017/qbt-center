#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import eventlet
eventlet.monkey_patch()

from qbtcenter import QBTCenter

import sys
import time
from collections import deque
from asciimatics.widgets import Frame, ListBox, Layout, Divider, Text, \
        Button, TextBox, Widget, Label
from asciimatics.scene import Scene
from asciimatics.screen import Screen
from asciimatics.exceptions import ResizeScreenError, NextScene, StopApplication
from asciimatics.event import KeyboardEvent

class MainView(Frame):
    def __init__(self, screen, model):
        super().__init__(
            screen,
            screen.height,
            screen.width,
            hover_focus=True,
            title='qBT Center',
            reduce_cpu=True
        )

        self._model = model
        self._height = screen.height
        self._width = screen.width
        self.log_height = self._height - 4
        self.log_width = self._width - 2


        self.speed = Label('a')
        self.log = Label('a', height=self.log_height)
        self.log_text = deque()

        layout = Layout([100])
        self.add_layout(layout)
        layout.add_widget(self.speed)

        log_layout = Layout([100], fill_frame=True)
        self.add_layout(log_layout)
        log_layout.add_widget(self.log)

        button_layout = Layout([1, 1])
        self.add_layout(button_layout)
        button_layout.add_widget(Button("Quit", self._quit), 0)
        button_layout.add_widget(Button("OK", self._ok), 1)

        self.fix()

        eventlet.spawn_n(self.loop)

    def reset(self):
        super().reset()
        self.data = None

    @staticmethod
    def _quit():
        raise StopApplication('Quit')

    @staticmethod
    def _ok():
        pass

    def get_log(self):

        while not self._model.log_queue.empty():
            self.log_text.append(self._model.log_queue.get())

        l = len(self.log_text) - self.log_height
        for _ in range(l):
            self.log_text.popleft()


    def loop(self):
        while True:
            try:
                self.screen.force_update()
                time.sleep(1)
            except:
                eventlet.spawn_n(self.loop)

    def _update(self, frame_no):

        down_speed = self._model.down_speed
        up_speed = self._model.up_speed
        self.speed.text = '[D: {:.1f}MB/s][U: {:.1f}MB/s]'.format(down_speed, up_speed)

        self.get_log() 
        self.log.text = '\n'.join(self.log_text)

        super()._update(frame_no)

def keyboard_handler(event):
    if isinstance(event, KeyboardEvent):
        c = event.key_code
        if c in (3, 17):
            raise StopApplication("User terminated app")

def view(screen, scene, model):
    scenes = [
        Scene([MainView(screen, model)], -1, name='Main')
    ]

    screen.play(scenes, stop_on_resize=True, start_scene=scene, unhandled_input=keyboard_handler)

def ui_loop(model):
    last_scene = None
    while True:
        try:
            Screen.wrapper(view, catch_interrupt=True, arguments=[last_scene, model])
            sys.exit(0)
        except ResizeScreenError as e:
            last_scene = e.scene


def main(argv):

    # TODO parse args and support args to config
    # config file is high priority
    import argparse
    from configparser import ConfigParser

    parser = argparse.ArgumentParser(description='qBT Center')
    parser.add_argument('-c', '--config', action='store', required=True)
    result = parser.parse_args()

    '''
    [DEFAULT]
    some global setting
    basepath = X:\\done\\
    target = F:\\test\\
    watch = X:\\watch_\\
    interval = 10
    speed_interval = 10

    [Idenity]
    url = <hostname or ip>
    username = <username> # default to 'admin'
    password = <password> # default to 'adminadmin'

    [Idenity2]
    url = 
    username = 
    password = 
    '''
    config_parser = ConfigParser()
    config_parser.read(result.config, encoding='utf8')

    config = {
        'hosts': {x: config_parser[x] for x in config_parser.sections()},
        'settings': config_parser['DEFAULT'],
    }

    center = QBTCenter(config)
    #center.loop()

    eventlet.spawn_n(center.loop)
    ui_loop(center)

if __name__ == '__main__':
    main(sys.argv)
