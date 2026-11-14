"""Isolated subprocess host for each frozen provider's own implementation."""
import json
import os
from pathlib import Path
import subprocess
import sys


def call(root, provider, operation, request=None):
    env = dict(os.environ, PYTHONIOENCODING='utf-8', PYTHONDONTWRITEBYTECODE='1')
    result = subprocess.run([sys.executable, '-B', str(Path(__file__).resolve()),
                             str(root), provider, operation],
                            input=json.dumps(request or {}, ensure_ascii=False),
                            text=True, encoding='utf-8', capture_output=True, env=env, timeout=90)
    if result.returncode:
        raise ValueError('provider_error:' + provider + ':' + result.stderr[-1200:])
    return json.loads(result.stdout)


def host(root, provider, operation, request):
    sys.path.insert(0, str(root / 'tools'))
    sys.path.insert(0, str(root / 'src'))
    if provider == 'wiki':
        import wiki_tool as wt
        if operation == 'query':
            import wiki_query
            return wiki_query.build(request['question'], request.get('policy_as_of'),
                                    request.get('event_as_of'), None,
                                    request.get('top_k', 5), 8000)
        p = wt.Project(str(root))
        p.load_sources()
        p.load_claims()
        docs = wt.build_wiki_docs(p, 'raw')
        for d in docs:
            d['locator'] = d['id'].split('#', 1)[1]
        # Virtual structure refs remain resolvable, but are not retrieval documents.
        for c in p.claims:
            for ref in c.get('source_refs', []):
                if ref['locator'] == '全文结构' and not any(
                        d['id'] == ref['source_id'] + '#全文结构' for d in docs):
                    docs.append(dict(id=ref['source_id'] + '#全文结构', source_id=ref['source_id'],
                                     locator='全文结构', text=p.structure_summary(ref['source_id']),
                                     section='structure', kind='structure'))
        return dict(sources=p.sources, units=docs, claims=p.claims)
    if operation == 'query':
        import ask
        problems = ask.validate_requests([request])
        if problems:
            raise ValueError('invalid_ask_request:' + json.dumps(problems, ensure_ascii=False))
        return ask.ask(**request)
    if operation == 'impact':
        result = subprocess.run([sys.executable, '-B', str(root / 'tools/ontology_tool.py'),
                                 'relations', 'impact', request['node'], '--json'],
                                text=True, encoding='utf-8', capture_output=True, timeout=30)
        if result.returncode:
            raise ValueError('unknown_relation_node')
        return json.loads(result.stdout)
    sources, units = [], []
    for file in ('sources.jsonl', 'sources_ext.jsonl'):
        if (root / file).exists():
            sources += [json.loads(s) for s in (root / file).read_text(encoding='utf-8').splitlines() if s.strip()]
    for s in sources:
        file = root / s['text_path'].replace('\\', '/')
        for line in file.read_text(encoding='utf-8').splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get('locator_id') and row.get('text'):
                units.append(dict(id=row['locator_id'], source_id=row['source_id'],
                                  locator=row.get('value') or row['locator_id'].split('#', 1)[1],
                                  text=row['text'], section=row.get('section') or row.get('chapter') or '',
                                  kind=row.get('kind'), original=row))
    rules = json.loads((root / 'knowledge/rules.json').read_text(encoding='utf-8'))['rules']
    schema = json.loads((root / 'model/schema.json').read_text(encoding='utf-8'))
    return dict(sources=sources, units=units, rules=rules, project_fields=schema['fields'])


if __name__ == '__main__':
    print(json.dumps(host(Path(sys.argv[1]), sys.argv[2], sys.argv[3], json.load(sys.stdin)),
                     ensure_ascii=False))
