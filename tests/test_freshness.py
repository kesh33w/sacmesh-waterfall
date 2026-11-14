"""Test freshness without starting the server or requiring SDR dependencies."""
import ast
from pathlib import Path
import time
import unittest
source=ast.parse((Path(__file__).resolve().parents[1]/'server.py').read_text())
function=next(n for n in source.body if isinstance(n,ast.FunctionDef) and n.name=='metadata_is_fresh')
namespace={'time':time}
exec(compile(ast.Module(body=[function],type_ignores=[]),'<freshness>','exec'),namespace)
fresh=namespace['metadata_is_fresh']
class FreshnessTests(unittest.TestCase):
    def test_boundaries(self):
        cases=[(100,True),(102,True),(102.001,False),(90,False),(90.001,True),(float('nan'),False),(True,False),('100',False)]
        for stamp,expected in cases:
            with self.subTest(stamp=stamp):self.assertEqual(fresh(stamp,now=100),expected)
