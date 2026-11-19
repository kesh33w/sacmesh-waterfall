#!/usr/bin/env python3
"""Receive-only SacMesh waterfall. Bounded RAM, no IQ or spectrum disk recording.
DSP settings/window adapted from WATER smf_utility_bot/sdr_assist.py.
"""
import base64
import collections
import ipaddress
import json
import os
from pathlib import Path
import signal
import subprocess
import threading
import time
from urllib.parse import urlparse, parse_qs
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import numpy as np
from water_telemetry import Telemetry, start as start_telemetry
telemetry = Telemetry()

RATE, CENTER, LOW, HIGH = 2400000, 910200000, 909500000, 910900000
FFT = 4096
FREQ = CENTER + np.fft.fftshift(np.fft.fftfreq(FFT, 1 / RATE))
MASK = (FREQ >= LOW) & (FREQ < HIGH)
# Max pool FFT bins into WATER's 5 kHz display bins.
GROUP = ((FREQ[MASK] - LOW) // 5000).astype(int)
WINDOW = np.blackman(FFT)
rows = collections.deque(maxlen=600)
lock = threading.Lock()
sequence = 0
child = None
stop = threading.Event()
audio_rows = collections.deque(maxlen=40)
audio_requested = 0.0
audio_sequence = 0
AUDIO_RATE = 15000
CHANNELS = [909875000, 910525000]
# 6 kHz audio low-pass before decimating the 150 kHz AM envelope to 15 kHz.
taps = np.sinc(2 * 6000 / 150000 * (np.arange(63) - 31)) * np.hamming(63)
taps /= taps.sum()
audio_tail = [np.zeros(62), np.zeros(62)]
audio_phase = 0
audio_level = [0.01, 0.01]


def demodulate(payload):
    global audio_phase
    raw = np.frombuffer(payload, dtype=np.uint8).astype(np.float32)
    iq = ((raw[::2] - 127.5) + 1j * (raw[1::2] - 127.5)) / 128
    transformed = np.fft.fftshift(np.fft.fft(iq.reshape(-1, FFT), axis=1), axes=1)
    result = []
    for channel, frequency in enumerate(CHANNELS):
        center_bin = int(round((frequency - CENTER) / RATE * FFT)) + FFT // 2
        band = transformed[:, center_bin - 128:center_bin + 128].copy()
        band[:, np.abs(np.arange(-128, 128) * RATE / FFT) > 31250] = 0
        envelope = np.abs(np.fft.ifft(np.fft.ifftshift(band, axes=1), axis=1)).ravel() / 16
        # Envelope detection is AM monitoring, not LoRa decoding.
        filtered = np.convolve(np.concatenate([audio_tail[channel], envelope]), taps, 'valid')
        audio_tail[channel] = envelope[-62:]
        mono = filtered[audio_phase::10]
        mono -= mono.mean()
        audio_level[channel] = max(.001, .9 * audio_level[channel] + .1 * float(np.sqrt(np.mean(mono ** 2))))
        pcm = (np.tanh(mono / (audio_level[channel] * 4)) * 24000).astype('<i2')
        result.append(base64.b64encode(pcm.tobytes()).decode())
    audio_phase = (audio_phase - len(envelope)) % 10
    return result


# Passive host journal metadata only. No payloads, identities, or radio commands.
packet_events = collections.deque(maxlen=512)
packet_lock = threading.Lock()
packet_feed_ok = False


# IQ may come from a separate receiver host (for example an SDR on another machine at the
# same site); packet metadata always comes from WATERFALL_SDR_HOST, where the bridge runs.
GAIN_DB = float(os.environ.get('WATERFALL_GAIN_DB', '9.9'))


def ssh_command(command, host=None):
    return ['/usr/bin/ssh', '-T', '-i', '/run/ssh/id_ed25519',
            '-o', 'UserKnownHostsFile=/run/ssh/known_hosts', '-o', 'StrictHostKeyChecking=yes',
            '-o', 'BatchMode=yes', '-o', 'ControlMaster=auto', '-o', 'ControlPath=/tmp/ssh-%C',
            '-o', 'ControlPersist=30', '-o', 'ConnectTimeout=5', '-o', 'ServerAliveInterval=10',
            '-o', 'ServerAliveCountMax=2', host or os.environ['WATERFALL_SDR_HOST'], command]


def metadata_is_fresh(generated_at, now=None):
    # NTP-synchronized Pi and NAS may differ slightly. Never accept arbitrary future data.
    if not isinstance(generated_at, (int, float)) or isinstance(generated_at, bool):
        return False
    age = (time.time() if now is None else now) - generated_at
    return -2.0 <= age < 10.0


def packet_feed():
    # Receives only pre-sanitized observations, never the bridge database.
    global packet_feed_ok
    while not stop.is_set():
        try:
            p = subprocess.run(ssh_command('metadata'), capture_output=True, timeout=5)
            if p.returncode or len(p.stdout) > 512000:
                raise ValueError('Metadata unavailable')
            value = json.loads(p.stdout)
            if not metadata_is_fresh(value['generated_at']):
                raise ValueError('Stale metadata')
            temporary = Path('/run/waterfall-metadata/events.tmp')
            temporary.write_bytes(p.stdout)
            temporary.replace('/run/waterfall-metadata/events.json')
            packet_feed_ok = True
        except (OSError, ValueError, KeyError, subprocess.SubprocessError):
            packet_feed_ok = False
        stop.wait(1)


system_lock = threading.Lock()
system_cache = {}
system_updated = 0.0
system_cpu_previous = None


def system_stats():
    global system_cache, system_updated, system_cpu_previous
    with system_lock:
        now = time.monotonic()
        if system_cache and now - system_updated < 5:
            return system_cache
        ticks = list(map(int, Path('/proc/stat').read_text().splitlines()[0].split()[1:9]))
        total, idle = sum(ticks), ticks[3] + ticks[4]
        usage = None
        if system_cpu_previous and total > system_cpu_previous[0]:
            usage = round(100 * (1 - (idle-system_cpu_previous[1])/(total-system_cpu_previous[0])), 1)
        system_cpu_previous = (total, idle)
        mem = {line.split(':')[0]: int(line.split()[1]) for line in Path('/proc/meminfo').read_text().splitlines()}
        result = {'host': 'NAS', 'cpu_percent': usage,
                  'memory_used_mib': round((mem['MemTotal']-mem['MemAvailable'])/1024),
                  'memory_total_mib': round(mem['MemTotal']/1024), 'sample_time': time.time()}
        try:
            result['temperature_c'] = round(int(Path('/sys/class/thermal/thermal_zone0/temp').read_text())/1000,1)
        except (OSError, ValueError):
            pass
        try:
            flags = int(subprocess.check_output(['/usr/bin/vcgencmd','get_throttled'],timeout=1,text=True).strip().split('=')[1],16)
            result['throttled_now'] = bool(flags & 4)
            result['undervoltage_now'] = bool(flags & 1)
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
        system_cache, system_updated = result, now
        return result


def spectrum(payload):
    values = np.frombuffer(payload, dtype=np.uint8).astype(np.float32)
    samples = ((values[::2] - 127.5) + 1j * (values[1::2] - 127.5)) / 128
    frames = samples.reshape(-1, FFT)
    frames -= frames.mean(axis=1, keepdims=True)
    power = 20 * np.log10(np.maximum(np.abs(np.fft.fftshift(
        np.fft.fft(frames * WINDOW, axis=1), axes=1)) / FFT, 1e-9))
    bins = np.full(280, -180.)
    np.maximum.at(bins, GROUP, power[:, MASK].max(axis=0))
    return bins


def capture():
    global child, sequence, audio_sequence
    child = subprocess.Popen(ssh_command('iq', os.environ.get('WATERFALL_IQ_HOST') or None), stdout=subprocess.PIPE)
    peak = np.full(280, -180.)
    count = 0
    clipped = 0
    last = time.monotonic()
    try:
        while not stop.is_set():
            payload = child.stdout.read(FFT * 2 * 32)
            if len(payload) != FFT * 2 * 32:
                raise RuntimeError('SDR stream ended')
            peak = np.maximum(peak, spectrum(payload))
            if time.monotonic() - audio_requested < 5:
                channels = demodulate(payload)
                with lock:
                    audio_sequence += 1
                    audio_rows.append({'seq': audio_sequence, 'time': time.time(), 'pcm': channels})
            raw = np.frombuffer(payload, dtype=np.uint8)
            clipped += int(np.count_nonzero((raw <= 1) | (raw >= 254)))
            count += len(payload) // 2
            now = time.monotonic()
            if now - last >= .20:
                with lock:
                    sequence += 1
                    rows.append({'seq': sequence, 'time': time.time(),
                                 'db': np.round(peak, 1).tolist(), 'samples': count, 'clipped_percent': round(100 * clipped / (2 * count), 4)})
                    while rows and rows[-1]['time'] - rows[0]['time'] > 60:
                        rows.popleft()
                peak.fill(-180)
                count = 0
                clipped = 0
                last = now
    except Exception as exc:
        print(str(exc), flush=True)
        if not stop.is_set():
            os.kill(os.getpid(), signal.SIGTERM)


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(5)

    def do_GET(self):
        global audio_requested
        address = ipaddress.ip_address(self.client_address[0])
        if not (address.is_private or address in ipaddress.ip_network('100.64.0.0/10')):
            self.send_error(403)
            return
        parsed = urlparse(self.path)
        if parsed.path == '/':
            body = Path(__file__).with_name('index.html').read_bytes()
            mime = 'text/html; charset=utf-8'
        elif parsed.path == '/api/packets':
            try:
                metadata_path = Path('/run/waterfall-metadata/events.json')
                if metadata_path.stat().st_size > 512000:
                    raise ValueError('Oversize metadata')
                metadata = json.loads(metadata_path.read_text())
                if not metadata_is_fresh(metadata['generated_at']):
                    raise ValueError('Stale metadata')
                body = json.dumps(metadata, separators=(',', ':')).encode()
            except (OSError, ValueError, KeyError):
                with packet_lock:
                    events = [e for e in packet_events if -2.0 <= time.time()-e['time'] <= 60]
                body = json.dumps({'events': events, 'feed_connected': packet_feed_ok, 'note': 'Radio observations, not SDR packet decodes. Host receipt times are approximate.'}, separators=(',', ':')).encode()
            mime = 'application/json'
        elif parsed.path == '/api/system':
            body = json.dumps(system_stats(), separators=(',', ':')).encode()
            mime = 'application/json'
        elif parsed.path == '/api/audio':
            query = parse_qs(parsed.query)
            try:
                channel = int(query.get('channel', ['0'])[0])
                after = int(query.get('after', ['0'])[0])
                if channel not in (0, 1, 2) or after < 0:
                    raise ValueError()
            except ValueError:
                self.send_error(400)
                return
            audio_requested = time.monotonic()
            with lock:
                available = list(audio_rows)
            if available and after > available[-1]['seq']:
                after = 0  # A receiver restart resets the sequence.
            fresh = [r for r in available if r['seq'] > after and time.time() - r['time'] < 1]
            if after == 0:
                fresh = fresh[-3:]
            body = json.dumps({'sample_rate': AUDIO_RATE, 'demodulation': 'AM envelope',
                               'frequency_hz': CHANNELS if channel == 2 else [CHANNELS[channel]],
                               'chunks': [{'seq': r['seq'], 'time': r['time'], 'pcm': r['pcm'] if channel == 2 else [r['pcm'][channel]]} for r in fresh]},
                              separators=(',', ':')).encode()
            mime = 'application/json'
        elif parsed.path == '/api/spectrum':
            with lock:
                data = list(rows)
            age = time.time() - data[-1]['time'] if data else None
            try:
                after = max(0, int(parse_qs(parsed.query).get('after', ['0'])[0]))
            except ValueError:
                self.send_error(400)
                return
            reset = bool(data and after > data[-1]['seq'])
            if after and not reset:
                data = [row for row in data if row['seq'] > after]
            body = json.dumps({'center_hz': CENTER, 'sample_rate': RATE,
                               'low_hz': LOW, 'high_hz': HIGH, 'gain_db': GAIN_DB,
                               'markers_hz': [909875000, 910525000],
                               'age_seconds': age, 'live': age is not None and age < 2,
                               'reset': reset, 'rows': data}, separators=(',', ':')).encode()
            mime = 'application/json'
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; frame-ancestors https://sacmesh.org")
        self.end_headers()
        try:
            self.wfile.write(body)
            telemetry.record(self.client_address[0], self.headers, self.path, len(body))
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, *_):
        pass


class BoundedServer(ThreadingHTTPServer):
    # A slow public viewer cannot create unlimited worker threads.
    slots = threading.BoundedSemaphore(12)

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            request.close()
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()


def shutdown(*_):
    stop.set()
    if child and child.poll() is None:
        child.terminate()
        try:
            child.wait(timeout=3)
        except subprocess.TimeoutExpired:
            child.kill()
    raise SystemExit(0)


if __name__ == '__main__':
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    start_telemetry(telemetry, '/telemetry/telemetry.sock')
    server = BoundedServer((os.environ.get('WATERFALL_BIND', '127.0.0.1'), int(os.environ.get('WATERFALL_PORT', '8767'))), Handler)
    server.daemon_threads = True
    threading.Thread(target=capture, daemon=True).start()
    threading.Thread(target=packet_feed, daemon=True).start()
    print('SacMesh waterfall on NAS; receive-only SDR transport', flush=True)
    server.serve_forever()
