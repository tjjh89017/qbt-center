#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import eventlet
eventlet.monkey_patch()

import pprint
pp = pprint.PrettyPrinter(indent=2)

import logging
log = logging.getLogger(__name__)

from qbittorrent import Client
#import subprocess
import time
import os
import sys
import re
import glob
import bencode
import hashlib
import base64
import select

class QBTCenter(object):

    def __init__(self, config=None, ui=False):
        # define all we need before `configure`
        self.magnet_pattern = re.compile(r'magnet:\?.*xt=urn:(?:btih|sha1):([A-Za-z0-9]*)&?.*')

        # for get host
        self.host_num = 0
        self.hosts = []
        self.host_lock = eventlet.semaphore.Semaphore()

        self.down_speed = 0
        self.up_speed = 0
        self.log_queue = DummyQueue()
        
        if ui:
            self.log_queue = eventlet.queue.Queue()

        self.settings = {}
        self.target = ''
        self.basepath = ''
        self.watch = ''
        self.interval = 60
        self.speed_interval = 60
        self.mtime = 0
        self.pool = eventlet.GreenPool(20)
        self.copy_pool = eventlet.GreenPool(1)

        '''
        {
            torrent: '<path to torrent>',
            magnet: '<some magnet>',
        }
        '''
        self.torrent_pending = eventlet.queue.Queue()
        self.move_backend = FastCopy()
        #self.move_backend = TestBackend()

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
        self.interval = int(self.settings.get('interval', 60))
        self.speed_interval = int(self.settings.get('speed_interval', 60))

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
        with self.host_lock:
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
                self.log_queue.put("{} finish at {}.".format(infohash, host.hostname))
                torrents.append(infohash)

        if torrents:
            return {'host': host, 'torrents': torrents}
        return None

    def add_pending_torrent(self):
        while True:
            while not self.torrent_pending.empty():
                torrent = self.torrent_pending.get()
                #log.warning(torrent)
                try:
                    if torrent['torrent']:
                        # check this torrent exist or not
                        infohash = ''
                        with open(torrent['torrent'], 'rb') as f:
                            info = bencode.bread(f)
                            infohash = hashlib.sha1(bencode.encode(info['info'])).hexdigest()
                        # search all host
                        is_exist = False
                        for host in self.hosts:
                            if host.torrents(hashes=infohash):
                                is_exist = True
                                break
                        if is_exist:
                            log.warning('{} exist.'.format(torrent['torrent']))
                            self.log_queue.put('exist {}.'.format(torrent['torrent']))
                            os.remove(torrent['torrent'])
                            continue
                        self.add_torrent(torrent)
                    else:
                        # magnet
                        magnet = torrent['magnet']
                        infohash = self.magnet_pattern.search(magnet).group(1)
                        if len(infohash) == 32:
                            infohash = base64.b32decode(infohash).hex()
                        # search all host
                        is_exist = False
                        for host in self.hosts:
                            if host.torrents(hashes=infohash):
                                is_exist = True
                                break
                        if is_exist:
                            log.warning('{} exist.'.format(infohash))
                            self.log_queue.put('exist {}.'.format(infohash))
                            continue
                        self.add_magnet(magnet)

                except KeyboardInterrupt:
                    break
                except OSError:
                    log.warning('OSError {}'.format(torrent['torrent']))
                except:
                    log.warning('except')
                    continue
            time.sleep(self.interval)

    def add_torrent(self, torrent):
        # assume always use torrent file rather than magnet
        host = self.get_host()
        log.warning('{} add {}.'.format(torrent['torrent'], host.hostname))
        self.log_queue.put('{} add {}.'.format(host.hostname, torrent['torrent']))
        self.log_queue.put(host.addTorrent(torrent['torrent']))

        # delete the torrent file
        os.remove(torrent['torrent'])

    def add_magnet(self, magnet):
        # assume always use torrent file rather than magnet
        host = self.get_host()
        log.warning('add {} to {}.'.format(magnet, host.hostname))
        self.log_queue.put('{} add {}.'.format(host.hostname, magnet))
        self.log_queue.put(host.addMagnet(magnet))

    def setup_file_watcher(self, path, interval):
        # check directory accessable
        if not os.access(path, os.R_OK):
            log.warning("File watcher failed. Check the permission")
            return False
        self.pool.spawn_n(self.file_watcher, path, interval)

    def file_watcher(self, path, interval):
        try:
            stat = os.stat(path)
            if stat.st_mtime > self.mtime:
                # directory has changed
                torrents = glob.iglob(os.path.join(path, '*.torrent'))
                for torrent in torrents:
                    log.warning('{} found.'.format(torrent))
                    self.log_queue.put('found {}.'.format(torrent))
                    self.torrent_pending.put({
                        'torrent': torrent,
                        'magnet': None,
                    })

                self.mtime = stat.st_mtime
        except KeyboardInterrupt:
            return
        finally:
            time.sleep(interval)
            self.pool.spawn_n(self.file_watcher, path, interval)

    def setup_speed_watcher(self, interval):
        self.pool.spawn_n(self.speed_watcher, interval)

    def speed_watcher(self, interval):
        down_speed = 0
        up_speed = 0

        for host in self.hosts:
            info = host.global_transfer_info
            down_speed += info['dl_info_speed']
            up_speed += info['up_info_speed']

        down_speed /= (1024 * 1024)
        up_speed /= (1024 * 1024)

        self.down_speed = down_speed
        self.up_speed = up_speed
        #log.warning('[D: {:.1f}MB/s][U: {:.1f}MB/s]'.format(self.down_speed, self.up_speed))

        time.sleep(interval)
        self.pool.spawn_n(self.speed_watcher, interval)

    def setup_input_watcher(self):
        server = eventlet.listen(('127.0.0.1', 9999))
        while True:
            try:
                sock, address = server.accept()
                self.pool.spawn_n(self.input_watcher, sock.makefile('r'))
            except (SystemExit, KeyboardInterrupt):
                break
            except:
                continue

    def input_watcher(self, fd):
        while True:
            uri = fd.readline()
            if not uri:
                break
            log.warning(uri)
            self.log_queue.put(uri)
            self.torrent_pending.put({
                'torrent': None,
                'magnet': uri,
            })

    def loop(self):
        # register jobs first:
        # time-based polling update (every 30 min or it will hang)
        # fs watcher and add torrent in queue
        self.pool.spawn_n(self.check_if_torrent_finish_all)
        self.pool.spawn_n(self.setup_file_watcher, self.watch, self.interval)
        self.pool.spawn_n(self.add_pending_torrent)
        self.pool.spawn_n(self.setup_speed_watcher, self.speed_interval)
        self.pool.spawn_n(self.setup_input_watcher)

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

    def addTorrent(self, torrent):
        r = ''
        with self.lock:
            with open(torrent, 'rb') as t:
                r = self.download_from_file(('some shit', t.read()))
                log.warning(r)
        
        self.updateTorrents()
        return r

    def addMagnet(self, magnet):
        r = ''
        with self.lock:
            r = self.download_from_link(magnet)
            log.warning(r)

        self.updateTorrents()
        return r

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

class MoveBackend(object):

    def move(self, src_list, dst):
        pass

class TestBackend(object):

    def move(self, src_list, dst):
        pp.pprint(src_list)
        pp.pprint(dst)

class FastCopy(MoveBackend):

    def __init__(self):

        self.env = os.environ.copy()
        self.env['QBT_CENTER'] = 'using'

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
            '/force_start',
            '/force_close',
            #'"somefile"',
            #'/to="target"',
        ]

        # import module here
        self.subprocess = __import__('subprocess')
        self.psutil = __import__('psutil')

    def move(self, src_list, dst):
        self.subprocess.call(self.fastcopy_cmd + src_list + ['/to={}'.format(dst)], env=self.env.copy())
        pids = [p.info['pid'] for p in self.psutil.process_iter(attrs=['pid', 'name']) if 'FastCopy' in p.info['name']]
        # wait for FastCopy finish
        for pid in pids:
            try:
                process = self.psutil.Process(pid)
                if 'QBT_CENTER' not in process.environ():
                    continue
                log.warning('Found FastCopy. Waiting for it.')
                self.log_queue.put('Found FastCopy. Waiting for it.')
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

class DummyQueue(object):
    def __init__(self):
        pass
    def put(self, *args, **kwargs):
        pass
    def get(self, *args, **kwargs):
        pass

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
    center.loop()

if __name__ == '__main__':
    import sys
    main(sys.argv)
