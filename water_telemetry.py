"""Private bounded telemetry for HTTP polling clients, not WebSocket connections."""
import collections, datetime, ipaddress, json, os, socketserver, threading, time, uuid
from pathlib import Path
from http.server import BaseHTTPRequestHandler

def stamp(t):
    return datetime.datetime.fromtimestamp(t, datetime.timezone.utc).isoformat().replace('+00:00','Z')

class Telemetry:
    def __init__(self):
        self.lock=threading.Lock(); self.boot=uuid.uuid4().hex; self.seq=0
        self.active={}; self.closed=collections.deque(maxlen=1000); self.requests=0; self.bytes=0; self.dropped=0
    def expire(self, now):
        for key,r in list(self.active.items()):
            if now-r['_last']>15:
                self.seq+=1; r.update(closed_at=stamp(r['_last']+15),end_sequence=self.seq,close_reason='read_timeout')
                self.closed.append(r); del self.active[key]
    def record(self, peer, headers, path, size, now=None):
        now=time.time() if now is None else now
        client=peer; forwarded=False
        # Only operator-configured forwarding peers are trusted.
        if peer in {p.strip() for p in os.environ.get('WATERFALL_TRUSTED_PROXIES', '').split(',') if p.strip()}:
            try:
                client=str(ipaddress.ip_address(headers.get('CF-Connecting-IP',''))); forwarded=True
            except ValueError: pass
        ua=headers.get('User-Agent','')[:1024]; key=(client,ua)
        with self.lock:
            self.expire(now); self.requests+=1; self.bytes+=size
            if key not in self.active:
                if len(self.active)>=500: self.dropped+=1; return
                self.active[key]=dict(connection_id=uuid.uuid4().hex,connected_at=stamp(now),closed_at='',end_sequence=0,domain=os.environ.get('WATERFALL_DOMAIN', 'waterfall.sacmesh.org'),peer_ip=peer,client_ip=client,address_source='trusted_forwarded' if forwarded else 'direct',user_agent=ua,origin=headers.get('Origin',''),messages_sent=0,messages_received=0,payload_bytes_sent=0,payload_bytes_received=0,queue_drops=0,activity_kind='http_polling',request_path=path[:4096],referrer=headers.get('Referer','')[:4096],_audio=0)
            r=self.active[key]; r.update(_last=now,last_read_at=stamp(now),last_write_at=stamp(now),request_path=path[:4096]); r['messages_sent']+=1; r['messages_received']+=1; r['payload_bytes_sent']+=size
            if path.startswith('/api/audio'): r['_audio']=now
    def snapshot(self, now=None):
        now=time.time() if now is None else now
        with self.lock:
            self.expire(now)
            def clean(r):
                return {**{k:v for k,v in r.items() if not k.startswith('_')},'audio_recent':now-r.get('_audio',0)<3}
            return dict(schema_version=1,source_id='waterfall',instance_id=self.boot,captured_at=stamp(now),sequence=self.seq,earliest_sequence=self.closed[0]['end_sequence'] if self.closed else self.seq+1,dropped_events=self.dropped,active=[clean(r) for r in self.active.values()],closed=[clean(r) for r in self.closed],http=[dict(requests=self.requests,response_bytes=self.bytes,duration_ms_total=0)],limits=dict(max_http_series=1,active_omitted=self.dropped))

def start(telemetry, path):
    path=Path(path); path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    if path.exists(): path.unlink()
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path!='/v1/snapshot': self.send_error(404); return
            body=json.dumps(telemetry.snapshot(),separators=(',',':')).encode(); self.send_response(200); self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body)
        def log_message(self,*args): pass
    server=socketserver.UnixStreamServer(str(path),Handler); os.chmod(path,0o600)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    return server
