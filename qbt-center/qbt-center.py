#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import eventlet
from eventlet.green import threading
eventlet.monkey_patch(thread=False)

import pprint
pp = pprint.PrettyPrinter(indent=2)

import logging
log = logging.getLogger(__name__)

from qbittorrent import Client
#import subprocess
import time
import os
#from watchdog.observers import Observer
from watchdog.events import PatternMatchingEventHandler
from watchdog.observers import Observer

class QBTCenter(object):

    def __init__(self, config=None):
        # define all we need before `configure`

        self.hosts = []
        self.host_num = 0
        self.settings = {}
        self.target = ''
        self.basepath = ''
        self.watch = ''
        self.pool = eventlet.GreenPool(20)
        self.copy_pool = eventlet.GreenPool(1)

        self.move_backend = FastCopy()
        #self.move_backend = TestBackend()
        '''
        {
            torrent: <open file>,
            magnet: '<some magnet>',
            path: '<path to torrent>'
        }
        '''
        self.torrent_pending = eventlet.queue.Queue()

        self.configure(config)
        self.connectAll()

    def __del__(self):
        for host in self.hosts:
            host.logout()

    def configure(self, config):
        if not config:
            raise RuntimeError('Need Config File')

        # [DEFAULT]
        self.settings = {k: config['settings'][k] for k in config['settings']}
        pp.pprint(self.settings)

        self.target = self.settings['target']
        self.basepath = self.settings.get('basepath', '')
        self.watch = self.settings.get('watch', '.')

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

    def get_host(self):
        # TODO find the host who has less job
        host = self.hosts[self.host_num]
        self.host_num += 1
        if self.host_num == len(self.hosts):
            self.host_num = 0
        return host

    def get_file_path(self, infohash, qb):

        # directory
        path = ''
        if self.basepath:
            path = self.basepath
        else:
            path = qb.get_torrent(infohash).get('save_path', '')

        # sub directory or filename
        files = qb.get_torrent_files(infohash)
        path += os.path.split(files[0]['name'])[0]

        # file
        if path.endswith(os.sep):
            path += files[0]['name']
        
        return path

    def move_storage(self, torrents):

        # create a list contain all finished torrents
        paths = []
        for x in torrents:
            if x:
                host = x['host']
                paths.extend([self.get_file_path(y, host) for y in x['torrents']])
        self.move_backend.move(paths, self.target)

        # delete torrent
        for x in torrents:
            self.pool.spawn_n(x['host'].delTorrent, x['torrents'])

    def check_if_torrent_finish_all(self):
        while True:
            log.warning(time.asctime(time.localtime()))

            try:
                torrents = []
                for host in self.hosts:
                    tmp = self.check_if_torrent_finish(host)
                    if tmp:
                        torrents.append(tmp)
                
                if torrents:
                    self.copy_pool.spawn_n(self.move_storage, torrents)
            except KeyboardInterrupt:
                break
            except:
                self.connectAll()
                continue

            time.sleep(10 * 60)
        
    def check_if_torrent_finish(self, host):
        tmp = host.sync()
        if tmp is None:
            return None
        torrents = []
        for infohash, torrent in tmp.items():
            if 'pausedUP' == torrent.get('state'):
                log.warning("{} finish at {}.".format(infohash, host.hostname))
                torrents.append(infohash)

        if torrents:
            return {'host': host, 'torrents': torrents}
        return None

    def find_new_torrent(self, src_path):
        self.torrent_pending.put({
            'torrent': open(src_path, 'rb'),
            'magnet': None,
            'path': src_path,
        })

    def add_pending_torrent(self):
        while not self.torrent_pending.empty():
            torrent = self.torrent_pending.pop()
            self.pool.spawn_n(self.add_torrent, torrent)

    def add_torrent(self, torrent):
        host = self.get_host()
        host.addTorrentList([torrent])
        torrent['torrent'].close()

        # delete the torrent file
        if 'path' in torrent:
            os.remove(torrent['path'])

    def loop(self):
        # register jobs first:
        # time-based polling update (every 30 min or it will hang)
        # fs watcher and add torrent in queue
        self.pool.spawn_n(self.check_if_torrent_finish_all)
        self.pool.spawn_n(self.add_pending_torrent)

        observer = Observer()
        observer.schedule(TorrentHandler(self), path=self.watch)
        self.pool.spawn_n(observer.start)

        self.pool.waitall()
        self.copy_pool.waitall()


class QBTHost(Client):

    def __init__(self, hostname, url, username, password):
        super().__init__(url)

        self.hostname = hostname
        self.username = username
        self.password = password
        self._torrents = []
        self.rid = 0

        # Only one thread can write to this host
        self.lock = eventlet.semaphore.Semaphore()

    def login(self, *args, **kwargs):
        super().login(self.username, self.password)
        self.rid = 0
        self.updateTorrents()

    def updateTorrents(self):
        with self.lock:
            self._torrents = self.torrents()
    
    def getAllTorrents(self):
        return self._torrents[:]

    def jobs(self):
        return len(self._torrents)

    def addTorrentList(self, torrents):
        with self.lock:
            f = []
            l = []
            for x in torrents:
                t = x.get('torrent', None)
                m = x.get('magnet', None)
                if t:
                    f.append(t)
                elif m:
                    l.append(m)
            self.download_from_file(f)
            self.download_from_link(l)
        
        self.updateTorrents()

    def delTorrent(self, infohash_list):
        with self.lock:
            self.delete(infohash_list)
            self._torrents = list([x for x in self._torrents if x['hash'] not in infohash_list])

    def pauseTorrent(self, infohash):
        with self.lock:
            pass

    def sync(self):
        data = super().sync(self.rid)
        with self.lock:
            self.rid = data['rid']
        return data.get('torrents')

class TorrentHandler(PatternMatchingEventHandler):
    patterns = ['*.torrent']

    def __init__(self, center, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.center = center

    def on_created(self, event):
        log.warning("New Torrent ({}) Found.".format(event.src_path))
        self.center.find_new_torrent(event.src_path)

class MoveBackend(object):

    def move(self, src_list, dst):
        pass

class TestBackend(object):

    def move(self, src_list, dst):
        pp.pprint(src_list)
        pp.pprint(dst)

class FastCopy(MoveBackend):

    def __init__(self):

        self.fastcopy_cmd = [
            'fastcopy.exe',
            '/cmd=move',
            '/estimate',
            '/acl=FALSE',
            '/stream=FALSE',
            '/reparse=FALSE',
            '/verify=FALSE',
            '/recreate',
            '/error_stop=FALSE',
            '/no_ui',
            '/balloon=FALSE',
            #'"somefile"',
            #'/to="target"',
        ]

        # import module here
        self.subprocess = __import__('subprocess')
        self.psutil = __import__('psutil')

    def move(self, src_list, dst):
        self.subprocess.call(self.fastcopy_cmd + src_list + ['/to={}'.format(dst)])
        pids = [p.info['pid'] for p in self.psutil.process_iter(attrs=['pid', 'name']) if 'FastCopy' in p.info['name']]
        # wait for FastCopy finish
        for pid in pids:
            try:
                process = self.psutil.Process(pid)
            except self.psutil.NoSuchProcess:
                continue

            while True:
                try:
                    time.sleep(10)
                    process.wait(0)
                except self.psutil.TimeoutExpired:
                    continue
                except:
                    break
                else:
                    break

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
    basepath =
    target =
    watch = 

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
