#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import socket
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.connect(('127.0.0.1', 9999))
s.send(sys.argv[1].encode('utf-8'))
s.close()
