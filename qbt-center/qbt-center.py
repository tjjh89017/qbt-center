#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import eventlet
eventlet.monkey_patch()

import pprint
pp = pprint.PrettyPrinter(indent=2)

import logging
log = logging.getLogger(__name__)

from qbittorrent import Client
import subprocess
import time

class QBTCenter(object):

    __DEFAULT = {
        'username': 'admin',
        'password': 'adminadmin',
    }

    def __init__(self, config=None):
        # define all we need before `configure`

        self.hosts = []
        self.settings = {}
        self.pool = eventlet.GreenPool(20)

        self.configure(config)
        self.connectAll()
        pass

    def __del__(self):
        for host in self.hosts:
            host.logout()

    def configure(self, config):
        if not config:
            raise RuntimeError('Need Config File')

        # [DEFAULT]
        self.settings = {k: config['settings'][k] for k in config['settings']}
        pp.pprint(self.settings)

        # [Host]
        for k, v in config['hosts'].items():
            log.warning('{} {}'.format(k, v.get('url')))
            self.hosts.append(QBTHost(k, v.get('url'), v.get('username', ''), v.get('password', '')))

    def connect(self, host):
        # if return is None, success
        # or return error msg
        return host.login()

    def connectAll(self):
        for host in self.hosts:
            log.warning("connect to {}".format(host.hostname))
            ret = host.login()
            if ret:
                log.warning(ret)

    def reconnect(self, host, tries=5):
        # maybe we need some exception handle to this
        # but for now just connect it again
        for i in range(tries):
            ret = self.connect(host)
            if not ret:
                return True
            pp.pprint(ret)
        return False

    def loop(self):
        self.connectAll()


class QBTHost(Client):

    def __init__(self, hostname, url, username, password):
        super().__init__(url)

        self.hostname = hostname
        self.username = username
        self.password = password
        self._torrents = []

        # Only one thread can write to this host
        self.lock = eventlet.semaphore.Semaphore()

    def login(self, *args, **kwargs):
        super().login(self.username, self.password)
        self._torrents[:] = self.torrents()
        pass
    
    def getAllTorrents(self):
        return self._torrents

    def jobs(self):
        return len(self._torrents)

    def addTorrent(self, infohash):
        with self.lock:
            pass

    def delTorrent(self, infohash):
        with self.lock:
            pass

    def pauseTorrent(self, infohash):
        with self.lock:
            pass

def main(argv):

    # TODO parse args and support args to config
    # config file is high priority
    import argparse
    from configparser import ConfigParser

    parser = argparse.ArgumentParser(description='qBT Center')
    parser.add_argument('-c', '--config', type=argparse.FileType('r'), required=True)
    result = parser.parse_args()

    '''
    [DEFAULT]
    some global setting

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
    config_parser.read_string(result.config.read())

    config = {
        'hosts': {x: config_parser[x] for x in config_parser.sections()},
        'settings': config_parser['DEFAULT'],
    }

    center = QBTCenter(config)
    center.loop()

if __name__ == '__main__':
    import sys
    main(sys.argv)
