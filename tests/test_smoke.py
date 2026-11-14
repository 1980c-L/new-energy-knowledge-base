"""Portable exported runtime checks. No Dify, model API, GPU or network required."""
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools/integration176'))
from bridge176 import Bridge


class SmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bridge = Bridge(ROOT / 'data/bundles')

    @classmethod
    def tearDownClass(cls):
        cls.bridge.close()

    def test_raw_keeps_safety_exception(self):
        result = self.bridge.query({'question': '第二十四条 电网企业 安全必要技术要求', 'mode': 'raw'})
        self.assertTrue(any('保证电网和设备安全运行的必要技术要求' in row['content']
                            for row in result['original']['packet']['records']))
        self.assertEqual(result['generation']['paid_calls'], 0)

    def test_wiki_claim_retains_exception(self):
        result = self.bridge.query({'question': '并网可以附加保证电网安全必要技术要求吗',
            'mode': 'knowledge', 'policy_as_of': '2025-12-31'})
        self.assertTrue(any(row['claim_id'] == 'W-GRID-08' and '必要技术要求除外' in row['claim_text']
                            for row in result['wiki']['claims']))

    def test_unclear_is_distinct_from_no_match(self):
        result = self.bridge.query({'question': '50千伏接入方案答复时限是多少',
            'mode': 'knowledge', 'policy_as_of': '2025-12-31'})
        self.assertEqual(result['state'], 'official_unclear')
        missing = self.bridge.query({'question': 'ZXQVV998877XYZ', 'mode': 'raw'})
        self.assertEqual(missing['state'], 'no_match')

    def test_assessment_requires_project_fields(self):
        result = self.bridge.query({'question': '这个项目如何分类', 'mode': 'assess'})
        self.assertEqual(result['state'], 'input_error')

    def test_relations_reach_mode_rule(self):
        result = self.bridge.query({'question': '国家第五条变化影响哪些规则', 'mode': 'impact',
            'node': 'ontology:PV-NAT-2025-007-ATTACH#clause=第五条'})
        self.assertIn('O-R-002', result['impact']['direct_rules'])


if __name__ == '__main__':
    unittest.main()
