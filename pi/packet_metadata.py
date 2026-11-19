#!/usr/bin/env python3
"""Read-only radio metadata sanitizer. No network, private-channel keys, or DM text."""
import contextlib,functools,hashlib,json,os,sqlite3,struct,time
from pathlib import Path
from openhop_core.protocol import Identity,parse_advert_payload,decode_appdata
from openhop_core.node.handlers.group_text import GroupTextHandler
DB=os.environ.get('WATERFALL_OPENHOP_DB','/var/lib/openhop_repeater/production/repeater.db')
OUT=Path('/run/waterfall-metadata/events.json')
# Every radio id the repeater can record, current and historical. A TX on an id missing here is
# silently skipped, so add new radios here when they join the repeater's config.
RADIOS={'host-rf':909875000,'bay':910525000,'rak909':909875000,'rak910':910525000,
        # Former ids, kept so older records still place correctly.
        'rak910-shadow':910525000,'v3-909-shadow':909875000,'v3-910-shadow':910525000}
TYPES={0:'REQ',1:'RESPONSE',2:'TXT_MSG',3:'ACK',4:'ADVERT',5:'GRP_TXT',6:'GRP_DATA',7:'ANON_REQ',8:'PATH',9:'TRACE',10:'MULTIPART',11:'CONTROL'}
# Only public hashtag channels previously confirmed to use their standard derived key.
# Public Bay Area directory and operator-approved Sacramento hashtag channels.
CHANNELS=['#909public', '#909test', '#910public', '#910test', '#95033', '#95033earthquake', '#95033power', '#95033pulsepoint', '#95033weather', '#alameda', '#alert', '#bookclub', '#bot', '#cert', '#earthquake', '#eastbay', '#event', '#fire', '#healthcheck', '#marin', '#mcmonday', '#meshcore-rs', '#novato', '#power', '#public', '#queer', '#sacmesh', '#sacramento', '#sanleandro', '#sf', '#sfsacbridge', '#slo', '#sonoma', '#sonoma-county', '#south-bay-peninsula', '#test', '#thegoodplace', '#wardriving', '#weather', '#yo']
KEYS=[(n,hashlib.sha256(n.encode()).digest()[:16].hex()) for n in CHANNELS]
# Firmware-default Public key, also bundled by CoreScope upstream. Not a private credential.
KEYS.append(('Public','8b3387e9c5cdea6ac9e5edbaa115cd72'))
handler=GroupTextHandler(None,None,lambda *a:None,None)
known_nodes={}
known_nodes_at=0
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
        matches=[]
        for name,key in KEYS:
            if handler._derive_channel_hash(key)!=p[0]:continue
            plain=handler._decrypt_channel_message(key,p[1:3],p[3:])
            data=handler._parse_plaintext_message(plain) if plain else None
            if not data:continue
            matches.append(name)
        if len(matches)==1:return {'channel':matches[0]}  # Never export chat bodies or claimed chat names.
    return {}
def last_hop(value,size,nodes):
    if not value or size not in (1,2,3):return {}
    h=str(value).lower()
    if len(h)!=size*2 or any(c not in '0123456789abcdef' for c in h):return {}
    matches=[name for key,name in nodes.items() if key.startswith(h)]
    result={'last_hop_hash':h.upper(),'last_hop_candidates':len(matches),
            'last_hop_evidence':'final flood-path hash; not authenticated RF transmitter identity'}
    if len(matches)==1 and matches[0]:result['last_hop_name']=clean(matches[0],64)
    return result

def read_records(db, since):
    """Physical successful egresses replace logical TX rows; retain legacy TX only
    for older databases without the egress table. Failed attempts are never transmit markers.
    Query egress time independently: a queued packet can be older than the window.
    """
    has_egress = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='packet_egress'").fetchone()
    tx_filter = ' AND NOT p.transmitted' if has_egress else ''
    rows = [dict(r) for r in db.execute(
        'SELECT p.*, NULL AS egress_id FROM packets p INDEXED BY idx_packets_timestamp '
        'WHERE p.timestamp>=?'+tx_filter+' '
        'ORDER BY p.timestamp DESC LIMIT 512', (since,))]
    if has_egress:
        for r in db.execute(
            'SELECT p.*, e.id AS egress_id, e.timestamp AS egress_time, e.radio_id AS egress_radio '
            'FROM packet_egress e INDEXED BY idx_packet_egress_time_radio '
            'JOIN packets p ON p.id=e.packet_id WHERE e.timestamp>=? AND e.success=1 '
            'ORDER BY e.timestamp DESC LIMIT 512', (since,)):
            row = dict(r)
            row.update(timestamp=row['egress_time'], tx_radio_id=row['egress_radio'], transmitted=1)
            rows.append(row)
    return sorted(rows, key=lambda r: (r['timestamp'], r['id'], r['egress_id'] or 0))[-512:]

def collect():
    global known_nodes,known_nodes_at
    now=time.time()
    with contextlib.closing(sqlite3.connect('file:'+DB+'?mode=ro',uri=True,timeout=.1)) as db:
        db.execute('PRAGMA query_only=ON');db.row_factory=sqlite3.Row
        if now-known_nodes_at>60:
            known_nodes={}
            for n in db.execute('SELECT pubkey,node_name FROM adverts ORDER BY last_seen DESC LIMIT 10000'):
                known_nodes.setdefault(str(n['pubkey']).lower(),n['node_name'] or '')
            known_nodes_at=now
        rows=read_records(db, now-60)
    try:
        transmitter=json.loads(Path('/opt/waterfall-metadata/transmitter.json').read_text())
        transmitter_name=clean(transmitter.get('name',''),64)
    except Exception:transmitter_name=''
    events=[]
    for r in rows:
        is_tx=bool(r['transmitted'])
        radio=r['tx_radio_id'] if is_tx else r['rx_radio_id']
        if radio not in RADIOS:continue
        if not is_tx and radio not in ('host-rf','bay'):continue
        e={'time':r['timestamp'],'frequency_hz':RADIOS[radio],'radio':radio,'direction':'tx' if is_tx else 'rx','type':TYPES.get(r['type'],'OTHER'),'length':r['length'],'timing':'radio record time; approximate RF timing'}
        e['event_id']=('egress:'+str(r['egress_id'])) if r['egress_id'] is not None else 'packet:'+str(r['id'])
        if is_tx and r['egress_id'] is not None:e['timing']='physical radio TX completion; approximate RF end time'
        if is_tx and transmitter_name:e['transmitter_name']=transmitter_name
        if not is_tx:e.update(last_hop(r['upstream_hash'],r['upstream_hash_size'],known_nodes))
        try:e.update(decode(r['type'],r['payload'] or ''))
        except Exception:pass
        events.append(e)
    return {'generated_at':now,'events':events,'feed_connected':True,'note':'Last flood-path hop where available. Names with ? are unique known-prefix candidates, not authenticated transmitters. Radio reports, not SDR decodes.'}
def main():
    while True:
        try:
            data=collect();tmp=OUT.with_suffix('.tmp');tmp.write_text(json.dumps(data,separators=(',',':')));os.chmod(tmp,0o644);os.replace(tmp,OUT)
        except Exception as exc:print('Metadata unavailable: '+type(exc).__name__,flush=True)
        time.sleep(2)
if __name__=='__main__':main()
