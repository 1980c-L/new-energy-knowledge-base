"""One local entry for original text, Wiki materials and executable policy rules."""
from copy import deepcopy
from datetime import date
import json
import math
from pathlib import Path

from bundle176 import Bundles, Store, call, read
from engine171 import Session

FIELDS = {'question', 'mode', 'project', 'policy_as_of', 'event_as_of', 'top_k',
          'parts', 'node', 'max_chars', 'request_id'}
MODES = {'raw', 'knowledge', 'assess', 'impact'}
PROVINCES = {'浙江': 'zhejiang', '广东': 'guangdong', '江苏': 'jiangsu'}


def validate_project(project, schema):
    if set(project) - set(schema):
        raise ValueError('unknown_project_fields:' + ','.join(sorted(set(project) - set(schema))))
    for key, value in project.items():
        if value is None:
            continue  # Unknown remains unknown, never convert it to false.
        field = schema[key]
        kind = field['type']
        valid = False
        if kind == 'enum':
            valid = isinstance(value, str) and value in field['values']
        elif kind == 'boolean':
            valid = type(value) is bool
        elif kind == 'number':
            valid = type(value) in (float, int) and math.isfinite(value) and value >= 0
        elif kind == 'date':
            try:
                valid = isinstance(value, str) and date.fromisoformat(value).isoformat() == value
            except ValueError:
                valid = False
        elif kind in {'string', 'text'}:
            valid = isinstance(value, str) and bool(value.strip())
        if not valid:
            raise ValueError('invalid_project_value:' + key)


def validate(spec):
    if not isinstance(spec, dict) or set(spec) - FIELDS:
        raise ValueError('unknown_request_fields')
    s = deepcopy(spec)
    if not isinstance(s.get('question'), str) or not s['question'].strip() or len(s['question']) > 4000:
        raise ValueError('question_required_or_too_long')
    s['question'] = s['question'].strip()
    s.setdefault('mode', 'raw')
    if not isinstance(s['mode'], str) or s['mode'] not in MODES:
        raise ValueError('invalid_mode')
    for field in ('policy_as_of', 'event_as_of'):
        if s.get(field) is not None:
            v = s[field]
            if not isinstance(v, str) or date.fromisoformat(v).isoformat() != v:
                raise ValueError('invalid_date:' + field)
    for field, default, lo, hi in [('top_k', 5, 1, 20), ('max_chars', 8000, 0, 8000)]:
        s.setdefault(field, default)
        if type(s[field]) is not int or not lo <= s[field] <= hi:
            raise ValueError('invalid_' + field)
    if 'parts' in s and (not isinstance(s['parts'], list) or not 1 <= len(s['parts']) <= 8 or
                         any(not isinstance(p, str) or not p.strip() or len(p) > 1000 for p in s['parts'])):
        raise ValueError('invalid_parts')
    if 'request_id' in s and (not isinstance(s['request_id'], str) or len(s['request_id']) > 200):
        raise ValueError('invalid_request_id')
    if 'node' in s and (not isinstance(s['node'], str) or not s['node'].strip()):
        raise ValueError('invalid_node')
    if s['mode'] == 'impact' and not s.get('node'):
        raise ValueError('impact_node_required')
    if 'project' in s and not isinstance(s['project'], dict):
        raise ValueError('invalid_project')
    if s['mode'] == 'assess' and not s.get('project'):
        raise ValueError('structured_project_required')
    if s.get('project') and s['mode'] != 'assess':
        raise ValueError('project_requires_assess_mode')
    p = s.get('project', {})
    if p.get('policy_as_of') is not None and s.get('policy_as_of') is not None and p['policy_as_of'] != s['policy_as_of']:
        raise ValueError('project_request_date_conflict')
    for name, value in PROVINCES.items():
        if name in s['question'] and p.get('province') not in (None, value):
            raise ValueError('project_question_province_conflict')
    return s


class Bridge:
    def __init__(self, root=None, candidate=None):
        self.bundles = Bundles() if root is None else Bundles(root)
        self.candidate = candidate or self.bundles.active()
        self.manifest = self.bundles.load(self.candidate)
        self.path = self.bundles.path(self.candidate)
        self.mapping = read(self.path / 'mapping.json')
        self.catalogs = read(self.path / 'catalogs.json')
        self.raw = Session(Store(self.bundles.root / 'raw171'), self.manifest['raw_candidate'])

    def close(self):
        self.raw.close()

    def lookup(self, ref, *, _already_verified=False):
        if not _already_verified:
            self.bundles.load(self.candidate)
        if ref not in self.mapping['units']:
            raise ValueError('unknown_reference')
        u = deepcopy(self.mapping['units'][ref])
        source = self.mapping['sources'][u['provider'] + ':' + u['source_id']]
        root = self.path / u['provider']
        u.update(candidate_id=self.candidate, source=source,
                 local_original=str((root / source['raw_path'].replace('\\', '/')).resolve()),
                 local_normalized=str((root / source['text_path'].replace('\\', '/')).resolve()),
                 document_version=source['text_path_sha256'])
        return u

    def impact(self, node):
        refs = [node] if node in self.mapping['units'] else [
            l['ref'] for l in self.mapping['links'] if l['object_id'] == node]
        if not refs:
            # Delegate ontology entity/fact/rule IDs to the frozen relations/v1 interface.
            return dict(ontology=[call(self.path / 'ontology', 'ontology', 'impact', dict(node=node))],
                        claims=[], direct_rules=[], note='No mapped Wiki reference; no edge does not mean no impact.')
        groups = {self.mapping['units'][r]['source_group'] for r in refs}
        links = [l for l in self.mapping['links'] if l['source_group'] in groups]
        ontology_nodes = sorted({u['id'] for u in self.mapping['units'].values()
                                 if u['provider'] == 'ontology' and u['source_group'] in groups})
        relation = [call(self.path / 'ontology', 'ontology', 'impact', dict(node=n)) for n in ontology_nodes]
        return dict(ontology=relation, claims=sorted({l['object_id'] for l in links if l['kind'] == 'claim'}),
                    direct_rules=sorted({l['object_id'] for l in links if l['kind'] == 'rule'}),
                    refs=refs, note='Mapped direct citations; related_rules are candidates, not confirmed impact.')

    def query(self, spec):
        result = dict(schema_version='energy-local/v1', state='input_error',
                      identity=dict(candidate_id=self.candidate,
                                    provider_commits={k: v['commit'] for k, v in self.manifest['providers'].items()}),
                      generation=dict(state='not_executed', execution_allowed=False, paid_calls=0),
                      outbound=dict(allowed=False, reason='No new content or generation authorization'),
                      references=[], warnings=[])
        try:
            s = validate(spec)
            p = s.get('project', {})
            known = self.catalogs['ontology']['project_fields']
            validate_project(p, known)
            if p.get('policy_as_of') and not s.get('policy_as_of'):
                s['policy_as_of'] = p['policy_as_of']
            result['request'] = s
            self.bundles.load(self.candidate)  # Fail closed on snapshot drift, even after session creation.
            result['state'] = 'source_error'
            refs = set()
            if s['mode'] == 'impact':
                result['impact'] = self.impact(s['node'])
                result['state'] = 'impact_ready'
                result['answer'] = '变更影响为已建引用及关系的追溯结果；候选影响需复核，未建边不等于没有影响。'
                refs.update(result['impact'].get('refs', []))
            else:
                raw_spec = dict(question=s['question'], method='A', mode='window', max_chars=s['max_chars'],
                                max_seeds=s['top_k'], max_records=s['top_k'])
                if s.get('parts'):
                    raw_spec['parts'] = s['parts']
                original = self.raw.query(raw_spec)
                result['original'] = original
                if original['state'] not in {'success', 'no_match', 'budget_empty'}:
                    raise ValueError('original_retrieval_failed')
                for r in original['packet']['records']:
                    refs.update(p['locator']['ref'] for p in r['pieces'])
                result['state'] = original['state']
                result['answer'] = ('以下是冻结资料的原文候选，请按定位核对。' if refs else
                                    '本次检索未取得可展示原文；这不等于法规没有规定。')
                if s['mode'] in {'knowledge', 'assess'}:
                    w = call(self.path / 'wiki', 'wiki', 'query',
                             {k: s[k] for k in ('question', 'policy_as_of', 'event_as_of', 'top_k') if k in s})
                    result['wiki'] = w
                    result['warnings'] += w.get('warnings', [])
                    for c in w['claims']:
                        for endpoint in ('start_role', 'end_role'):
                            c['temporal_scope'][endpoint] = c['temporal_scope'].get(endpoint) or 'rule_validity'
                        refs.update('wiki:' + r['source_id'] + '#' + r['locator'] for r in c['evidence'])
                    for c in w['contexts']:
                        if 'wiki:' + c['id'] in self.mapping['units']:
                            refs.add('wiki:' + c['id'])
                    req = {k: s[k] for k in ('question', 'project', 'policy_as_of', 'top_k', 'request_id') if k in s}
                    ontology = call(self.path / 'ontology', 'ontology', 'query', req)
                    if ontology.get('schema_version') != 'ask/v1':
                        raise ValueError('ontology_contract_drift')
                    result['ontology'] = ontology
                    result['warnings'] += ontology.get('warnings', [])
                    rlist = ontology['answer'].get('source_refs', []) + (
                        ontology.get('assessment') or {}).get('source_refs', [])
                    refs.update('ontology:' + r['locator_id'] for r in rlist if isinstance(r, dict))
                    if s['mode'] == 'assess':
                        assessment = ontology.get('assessment')
                        result['state'] = assessment['result_status'] if assessment else ontology['answer']['verdict']
                        result['answer'] = ontology['answer']['summary']
                        result['missing_fields'] = (assessment or {}).get('missing_fields', [])
                        when = p.get('policy_as_of') or s.get('policy_as_of')
                        if (p.get('province') == 'guangdong' and p.get('rural_public_site') is True
                                and when and when > '2028-02-09'):
                            result['state'] = 'out_of_scope'
                            result['answer'] = '广东农村公共场所试行期满后的规则未覆盖，不能根据国家兜底分支确认分类。'
                            result['diagnostics'] = dict(unadopted_ontology=result.pop('ontology'),
                                                         reason='outside_supported_period')
                            result['warnings'].append('保留底层工具原始结果仅供诊断，首版不采纳该期满后的肯定分类。')
                    else:
                        result['state'] = 'official_unclear' if w.get('answer_kind') == 'unknown' else 'materials_ready'
                        if ontology['answer']['verdict'] in {'out_of_scope', 'province_pointer', 'abolished_document'}:
                            result['state'] = ontology['answer']['verdict']
                        result['answer'] = '以下为整理材料和候选原文；主张的条件、时点及unknown应一并阅读。'
                result['warnings'].append('仅按冻结来源回答，未联网核实当前政策效力；检索命中不等于问题已有完整答案。')
            missing = sorted(r for r in refs if r not in self.mapping['units'])
            if missing:
                raise ValueError('unresolved_output_references:' + ','.join(missing))
            result['references'] = [self.lookup(ref, _already_verified=True) for ref in sorted(refs)]
            result['independent_source_groups'] = len({x['document_group'] for x in result['references']})
            result['prepared'] = dict(execution_allowed=False, state='local_review_only',
                                      parts=s.get('parts', [s['question']]),
                                      requirements=['Preserve scope, conditions, endpoint roles and unknown.',
                                                    'Do not override insufficient/conflict or infer missing project fields.',
                                                    'Derived statements and original quotes are different representations.'])
        except (ValueError, TypeError, KeyError, FileNotFoundError) as e:
            result['error'] = dict(code=str(e).split(':', 1)[0], detail=str(e))
            result.pop('prepared', None)
            if result['state'] not in {'input_error', 'source_error'}:
                result['state'] = 'source_error'
            result['answer'] = '输入或来源校验未通过，不能采用本次中间结果。'
            partial = {k: result.pop(k) for k in ('ontology', 'wiki', 'impact', 'original') if k in result}
            if partial:
                result['diagnostics'] = dict(unadopted_partial_results=partial)
            result['references'] = []
        return result


def render(result):
    lines = [result.get('answer', '查询失败'), '状态：' + result['state'],
             '资料版本：' + result['identity']['candidate_id']]
    if result.get('error'):
        lines.append('原因：' + result['error']['detail'])
    ontology = result.get('ontology', {}) if result['state'] not in {'out_of_scope', 'source_error', 'input_error'} else {}
    for point in ontology.get('answer', {}).get('points', []):
        lines.append(str(point))
    if result.get('missing_fields'):
        lines.append('还缺字段：' + ', '.join(result['missing_fields']))
    for c in result.get('wiki', {}).get('claims', []):
        lines += ['', '[' + c['claim_id'] + '] ' + c['claim_text'],
                  '条件：' + json.dumps(c['conditions'], ensure_ascii=False),
                  '时点：' + json.dumps(c['temporal_scope'], ensure_ascii=False)]
    if result.get('impact'):
        lines += ['', json.dumps(result['impact'], ensure_ascii=False, indent=2)]
    for r in result['references']:
        lines += ['', '[' + r['ref'] + ']', r['text'],
                  '本地原件：[' + r['source']['title'] + '](' + r['local_original'].replace('\\', '/') + ')',
                  '定位：' + r['locator'] + '；版本：' + r['document_version'][:16]]
    lines += [''] + ['注意：' + w for w in result['warnings']]
    lines.append('云生成未执行。')
    return '\n'.join(lines)
