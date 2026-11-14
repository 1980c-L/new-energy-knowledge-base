"""Energy knowledge CLI. All writes confined to isolated bundle/output directories."""
import argparse
import json
from pathlib import Path
import sys

from bundle176 import Bundles, DEFAULT_STORE, WIKI, ONTOLOGY, read, write
from bridge176 import Bridge, render


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--store', type=Path, default=DEFAULT_STORE)
    sub = p.add_subparsers(dest='command', required=True)
    b = sub.add_parser('build')
    b.add_argument('--wiki', type=Path, default=WIKI)
    b.add_argument('--ontology', type=Path, default=ONTOLOGY)
    b.add_argument('--wiki-commit', required=True)
    b.add_argument('--ontology-commit', required=True)
    b.add_argument('--parent')
    b.add_argument('--reviewed', action='store_true', help='Explicit acknowledgement that changed source/derived data was reviewed')
    a = sub.add_parser('activate')
    a.add_argument('candidate')
    i = sub.add_parser('inspect')
    i.add_argument('--candidate')
    q = sub.add_parser('query')
    q.add_argument('--question', required=True)
    q.add_argument('--mode', choices=['raw', 'knowledge', 'assess', 'impact'], default='raw')
    q.add_argument('--policy-as-of')
    q.add_argument('--event-as-of')
    q.add_argument('--project', type=Path)
    q.add_argument('--node')
    q.add_argument('--part', action='append')
    q.add_argument('--candidate')
    q.add_argument('--out', type=Path)
    q.add_argument('--json', action='store_true')
    batch = sub.add_parser('batch')
    batch.add_argument('--input', type=Path, required=True)
    batch.add_argument('--out', type=Path, required=True)
    batch.add_argument('--candidate')
    lookup = sub.add_parser('lookup')
    lookup.add_argument('--candidate', required=True)
    lookup.add_argument('--ref', required=True)
    args = p.parse_args(argv)
    try:
        store = Bundles(args.store)
        if args.command == 'build':
            print(json.dumps(dict(candidate_id=store.build(args.wiki, args.ontology,
                             wiki_commit=args.wiki_commit, ontology_commit=args.ontology_commit,
                             parent=args.parent, reviewed=args.reviewed))))
            return 0
        if args.command == 'activate':
            store.activate(args.candidate)
            print(json.dumps(dict(active=store.active())))
            return 0
        if args.command == 'inspect':
            print(json.dumps(store.load(args.candidate or store.active()), ensure_ascii=False, indent=2))
            return 0
        if getattr(args, 'out', None):
            args.out.mkdir(parents=True, exist_ok=False)
        bridge = Bridge(args.store, args.candidate)
        try:
            if args.command == 'lookup':
                print(json.dumps(bridge.lookup(args.ref), ensure_ascii=False, indent=2))
                return 0
            if args.command == 'query':
                spec = dict(question=args.question, mode=args.mode)
                for key in ('policy_as_of', 'event_as_of', 'node'):
                    if getattr(args, key):
                        spec[key] = getattr(args, key)
                if args.project:
                    spec['project'] = read(args.project)
                if args.part:
                    spec['parts'] = args.part
                result = bridge.query(spec)
                if args.out:
                    write(args.out / 'result.json', result)
                    (args.out / 'result.md').write_text(render(result), encoding='utf-8')
                print(json.dumps(result, ensure_ascii=False, indent=2) if args.json else render(result))
                return 2 if result['state'] in {'input_error', 'source_error'} else 0
            errors, total = 0, 0
            with (args.out / 'results.jsonl').open('x', encoding='utf-8') as out:
                for line in args.input.read_text(encoding='utf-8-sig').splitlines():
                    if not line.strip():
                        continue
                    total += 1
                    try:
                        result = bridge.query(json.loads(line))
                    except json.JSONDecodeError:
                        result = dict(state='input_error', error=dict(code='invalid_json'))
                    errors += result['state'] in {'input_error', 'source_error'}
                    out.write(json.dumps(result, ensure_ascii=False) + '\n')
                    out.flush()
            summary = dict(total=total, errors=errors, candidate_id=bridge.candidate, paid_calls=0)
            write(args.out / 'summary.json', summary)
            print(json.dumps(summary))
            return 2 if errors else 0
        finally:
            bridge.close()
    except (ValueError, OSError, KeyError) as e:
        print(json.dumps(dict(state='source_error', error=str(e)), ensure_ascii=False))
        return 2


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    raise SystemExit(main())
