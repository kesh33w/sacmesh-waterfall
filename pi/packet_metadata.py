#!/usr/bin/env python3
"""Read-only radio metadata sanitizer. No network, private-channel keys, or DM text."""
import contextlib,functools,hashlib,json,os,sqlite3,struct,time
from pathlib import Path
from openhop_core.protocol import Identity,parse_advert_payload,decode_appdata
from openhop_core.node.handlers.group_text import GroupTextHandler
DB=os.environ.get('WATERFALL_OPENHOP_DB','/var/lib/openhop_repeater/production/repeater.db')
OUT=Path('/run/waterfall-metadata/events.json')
RADIOS={'host-rf':909875000,'bay':910525000}
TYPES={0:'REQ',1:'RESPONSE',2:'TXT_MSG',3:'ACK',4:'ADVERT',5:'GRP_TXT',6:'GRP_DATA',7:'ANON_REQ',8:'PATH',9:'TRACE',10:'MULTIPART',11:'CONTROL'}
# Only public hashtag channels previously confirmed to use their standard derived key.
CHANNELS=[]  # Public-channel allowlist awaiting explicit verification.
KEYS=[(n,hashlib.sha256(n.encode()).digest()[:16].hex()) for n in CHANNELS]
handler=GroupTextHandler(None,None,lambda *a:None,None)
def clean(value,limit):
    return ''.join(c for c in str(value) if c.isprintable())[:limit]
@functools.lru_cache(maxsize=1024)
def decode(kind,payload):
    p=bytes.fromhex(payload)
    if len(p)>512:return {}
    if kind==4:
        a=parse_advert_payload(p);pub=bytes.fromhex(a['pubkey']);sig=bytes.fromhex(a['signature']);app=a['appdata']
        if not Identity(pub).verify(pub+struct.pack('<I',a['timestamp'])+app,sig):return {}
        info=decode_appdata(app);name=clean(info.get('node_name') or info.get('name') or '',64)
        return {'sender':name,'identity':'signed advert origin','origin_key':pub.hex()} if name else {}
    if kind==5 and len(p)>=19:
        for name,key in KEYS:
            if handler._derive_channel_hash(key)!=p[0]:continue
            plain=handler._decrypt_channel_message(key,p[1:3],p[3:])
            data=handler._parse_plaintext_message(plain) if plain else None
            if not data:continue
            content=data['content'];sender,text=handler._extract_sender_from_message(content)
            return {'sender':clean(sender,64) if sender!='Unknown' else '', 'message':clean(text,200),'channel':name,'identity':'claimed group sender; not authenticated'}
    return {}
def collect():
    now=time.time()
    with contextlib.closing(sqlite3.connect('file:'+DB+'?mode=ro',uri=True,timeout=.1)) as db:
        db.execute('PRAGMA query_only=ON');db.row_factory=sqlite3.Row
        rows=db.execute('SELECT id,timestamp,type,length,rx_radio_id,payload,packet_hash FROM packets INDEXED BY idx_packets_timestamp WHERE timestamp>=? AND transmitted=0 ORDER BY timestamp DESC LIMIT 512',(now-60,)).fetchall()
    events=[]
    for r in reversed(rows):
        radio=r['rx_radio_id']
        if radio not in RADIOS:continue
        e={'time':r['timestamp'],'frequency_hz':RADIOS[radio],'radio':radio,'type':TYPES.get(r['type'],'OTHER'),'length':r['length'],'timing':'radio record time; approximate RF timing'}
        try:e.update(decode(r['type'],r['payload'] or ''))
        except Exception:pass
        events.append(e)
    return {'generated_at':now,'events':events,'feed_connected':True,'note':'Original source where known, not necessarily the last RF transmitter. Group names are claimed. Radio reports, not SDR decodes.'}
def main():
    while True:
        try:
            data=collect();tmp=OUT.with_suffix('.tmp');tmp.write_text(json.dumps(data,separators=(',',':')));os.chmod(tmp,0o644);os.replace(tmp,OUT)
        except Exception as exc:print('Metadata unavailable: '+type(exc).__name__,flush=True)
        time.sleep(2)
if __name__=='__main__':main()
