"""Test query independently of optional radio crypto dependencies."""
import ast
import sqlite3
import unittest
from pathlib import Path
source=ast.parse((Path(__file__).parents[1]/'pi/packet_metadata.py').read_text())
ns={}
exec(compile(ast.Module(body=[n for n in source.body if isinstance(n,ast.FunctionDef) and n.name=='read_records'],type_ignores=[]),'metadata','exec'),ns)
read_records=ns['read_records']
class EgressTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:');self.db.row_factory=sqlite3.Row
        self.db.executescript('CREATE TABLE packets(id INTEGER PRIMARY KEY,timestamp REAL,transmitted INTEGER,tx_radio_id TEXT); CREATE INDEX idx_packets_timestamp ON packets(timestamp);')
    def egress(self):
        self.db.executescript('CREATE TABLE packet_egress(id INTEGER PRIMARY KEY,packet_id INTEGER,timestamp REAL,radio_id TEXT,success INTEGER); CREATE INDEX idx_packet_egress_time_radio ON packet_egress(timestamp,radio_id,success);')
    def test_legacy(self):
        self.db.execute("INSERT INTO packets VALUES(1,101,1,'bay')")
        self.assertEqual(len(read_records(self.db,100)),1)
    def test_two_legs_old_logical_and_failed(self):
        self.egress();self.db.execute("INSERT INTO packets VALUES(1,1,1,'bay')")
        self.db.executemany('INSERT INTO packet_egress VALUES(?,?,?,?,?)',[(1,1,101,'bay',1),(2,1,102,'rak910-shadow',1),(3,1,103,'bay',0)])
        r=read_records(self.db,100)
        self.assertEqual([(x['timestamp'],x['tx_radio_id']) for x in r],[(101,'bay'),(102,'rak910-shadow')])
    def test_no_logical_duplicate_or_failed_fallback(self):
        self.egress();self.db.execute("INSERT INTO packets VALUES(1,101,1,'bay')")
        self.db.execute("INSERT INTO packet_egress VALUES(1,1,102,'bay',0)")
        self.assertEqual(read_records(self.db,100),[])
    def test_rx_preserved(self):
        self.egress();self.db.execute('INSERT INTO packets VALUES(1,101,0,NULL)')
        self.assertEqual(read_records(self.db,100)[0]['transmitted'],0)
    def test_every_live_radio_has_a_frequency(self):
        # A TX on an unknown id is dropped from the waterfall, so the live ids must all be listed.
        radios=next(ast.literal_eval(n.value) for n in source.body if isinstance(n,ast.Assign) and getattr(n.targets[0],'id','')=='RADIOS')
        for rid,hz in {'g3909':909875000,'g3910':910525000,'rak909':909875000,'rak910':910525000,'host-rf':909875000,'bay':910525000,'rak910-shadow':910525000}.items():
            self.assertEqual(radios.get(rid),hz,rid)
    def test_receptions_show_from_the_main_g3_radios(self):
        rx=next(ast.literal_eval(n.value) for n in source.body if isinstance(n,ast.Assign) and getattr(n.targets[0],'id','')=='RX_RADIOS')
        self.assertEqual(rx,{'g3909','g3910','host-rf','bay'})
if __name__=='__main__':unittest.main()
