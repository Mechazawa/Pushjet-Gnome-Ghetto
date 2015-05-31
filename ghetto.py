#!/usr/bin/env python3

from socket import socket, AF_INET, SOCK_STREAM
from json import loads as json_decode
from os import path, makedirs
from sys import argv
from uuid import uuid4
import notify2
import requests



class PushjetApiException(Exception):
    pass


class LazyPushjetConnector(object):
    _MAGIC_START = b'\x02'
    _MAGIC_END = b'\x03'
    _CACHE_DIR = path.expanduser('~/.cache/pushjet/icons/')

    def __init__(self, uuid=None, server='api.pushjet.io', port=7171, verbose=False):
        self._sock = None
        self.uuid = uuid or uuid4()
        self.server = server
        self.port = port
        self.subscriptions = []
        self.verbose = verbose

    def connect(self):
        if self._sock is not None:
            return
        self._sock = socket(AF_INET, SOCK_STREAM)
        self._sock.connect((self.server, self.port))
        self._sock.send(bytes(self.uuid, 'ASCII'))

    def lazy_receiver(self):
        while True:
            assert self._sock is not None
            c, s = b'', b''
            while c != self._MAGIC_START:
                c = self._sock.recv(1)

            while c != self._MAGIC_END:
                c = self._sock.recv(1)
                if c == '':
                    break
                s += c

            msg = s[:-1].decode('UTF-8')
            if self.verbose:
                print("RECV: {}".format(str(msg)))
            if msg == '{"status": "ok"}':
                continue
            yield json_decode(msg)

    def receive(self):
        for notification in self.lazy_receiver():
            if 'subscription' in notification:
                service = notification['subscription']['service']
                try:
                    index = next(i for i in range(len(self.subscriptions))
                                 if self.subscriptions[i]['public'] == service['public'])
                    del self.subscriptions[index]
                except StopIteration:
                    self.subscriptions.append(service)
            yield notification

    def query_api(self, controller, method, data=None):
        url = "https://{}/{}".format(self.server, controller)
        response = requests.request(method, url, data=data).json()
        if 'error' in response:
            raise PushjetApiException(response['error'])
        return response

    def get_subscriptions(self):
        if not self.subscriptions:
            if self.verbose:
                print("Fetching subscriptions for {}".format(self.uuid))
            resp = self.query_api('subscription', 'GET', {'uuid': self.uuid})
            self.subscriptions = [s['service'] for s in resp['subscriptions']]

        if self.verbose:
            for srv in self.subscriptions:
                print("{}: {}".format(srv['public'], srv['name']))

        return self.subscriptions

    def build_icon_cache(self):
        if not path.exists(self._CACHE_DIR):
            makedirs(self._CACHE_DIR)

        for service in self.get_subscriptions():
            icon_path = self.get_icon_path(service)
            if path.exists(icon_path):
                continue

            icon_url = service['icon'] or 'http://i.imgur.com/zQYX7F5.png'
            if self.verbose:
                print("Getting image {}({}) => {}".format(service['public'], icon_url, icon_path))
            data = requests.get(icon_url).content
            with open(icon_path, 'wb') as f:
                f.write(data)

    def get_icon_path(self, service):
        return path.join(self._CACHE_DIR, service['public'] + '.png')

if __name__ == '__main__':
    pushjet = LazyPushjetConnector(uuid=argv[1] if len(argv) > 1 else None, verbose=True)
    pushjet.build_icon_cache()
    pushjet.query_api('message', 'DELETE', data={'uuid': pushjet.uuid})

    notify2.init("Pushjet")
    pushjet.connect()
    print("Listening...")
    for notification in pushjet.receive():
        if 'subscription' in notification:
            pushjet.build_icon_cache()
        else:
            message = notification['message']
            title = message['title'] or message['service']['name']

            uri = 'file://{}'.format(pushjet.get_icon_path(message['service']))
            notif = notify2.Notification(title, message['message'], uri)
            print("{}: {}".format(title, message['message']))
            notif.show()
