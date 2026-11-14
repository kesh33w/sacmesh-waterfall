import os
import unittest
from unittest.mock import patch
from water_telemetry import Telemetry

class TelemetryTests(unittest.TestCase):
    def test_forwarded_header_requires_configured_peer(self):
        with patch.dict(os.environ, {'WATERFALL_TRUSTED_PROXIES': ''}):
            t=Telemetry(); t.record('127.0.0.1', {'CF-Connecting-IP':'192.0.2.7'}, '/', 3, now=100)
            self.assertEqual(t.snapshot(now=100)['active'][0]['client_ip'],'127.0.0.1')
        with patch.dict(os.environ, {'WATERFALL_TRUSTED_PROXIES': '127.0.0.1'}):
            t=Telemetry(); t.record('127.0.0.1', {'CF-Connecting-IP':'192.0.2.7'}, '/', 3, now=100)
            self.assertEqual(t.snapshot(now=100)['active'][0]['client_ip'],'192.0.2.7')
    def test_idle_viewers_expire(self):
        t=Telemetry(); t.record('192.0.2.7', {}, '/', 3, now=100)
        s=t.snapshot(now=116)
        self.assertEqual(len(s['active']),0); self.assertEqual(len(s['closed']),1)
    def test_invalid_forwarded_ip_uses_peer(self):
        with patch.dict(os.environ, {'WATERFALL_TRUSTED_PROXIES':'127.0.0.1'}):
            t=Telemetry(); t.record('127.0.0.1', {'CF-Connecting-IP':'not-an-ip'}, '/', 3, now=100)
            self.assertEqual(t.snapshot(now=100)['active'][0]['client_ip'],'127.0.0.1')

if __name__=='__main__': unittest.main()
