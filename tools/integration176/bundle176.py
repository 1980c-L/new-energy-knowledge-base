"""Local immutable provider bundles and explicit provenance links; no networking."""
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import uuid
import zipfile

from provider176 import call

HERE = Path(__file__).resolve().parent
RAG = HERE.parents[1]
sys.path.insert(0, str(RAG / 'tools/layer34_165'))
from engine171 import Store, digest

DEFAULT_STORE = RAG.parent / '项目资料/发展规划/176-证据-三项目第一版/bundles'
WIKI = Path('D:/codebuddy产出/项目/新能源知识Wiki')
ONTOLOGY = Path('E:/deepseek harness/项目/新能源Ontology')
FORMAT = 'energy-bundle/v1'


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def norm(text):
    # Only presentation differences; preserve actual words, digits and punctuation.
    text = re.sub(r'^第[一二三四五六七八九十百零〇0-9]+条\s*', '', text)
    return re.sub(r'\s+', '', text).replace('答：', '', 1)


def hash_text(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def archive(root, commit, dest):
    dirty = bool(subprocess.check_output(['git', '-C', str(root), 'status', '--porcelain']).strip())
    if dirty and commit == 'HEAD':
        raise ValueError('provider_worktree_dirty:' + str(root))
    sha = subprocess.check_output(['git', '-C', str(root), 'rev-parse', commit], text=True).strip()
    paths = ['tools', 'src', 'model', 'knowledge', 'interface', 'normalized', 'raw',
             'wiki', 'exports', 'claims.jsonl', 'sources.jsonl', 'sources_ext.jsonl']
    tracked = subprocess.check_output(['git', '-C', str(root), 'ls-tree', '--name-only', sha], text=True).splitlines()
    selected = [p for p in paths if p in tracked]
    blob = subprocess.check_output(['git', '-C', str(root), '-c', 'core.autocrlf=false',
                                    'archive', '--format=zip', sha, '--', *selected])
    dest.mkdir()
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        for name in z.namelist():
            if name.endswith('/'):
                continue
            target = dest / name
            if not target.resolve().is_relative_to(dest.resolve()):
                raise ValueError('archive_path_escape')
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(z.read(name))
    return dict(commit=sha, owner_root=str(root.resolve()), owner_worktree_dirty_at_capture=dirty)


def catalog(root, provider):
    value = call(root, provider, 'catalog')
    sources = {s['source_id']: s for s in value['sources']}
    for s in value['sources']:
        for field in ('text_path', 'raw_path'):
            p = root / s[field].replace('\\', '/')
            if not p.is_file():
                raise ValueError('source_file_missing:' + str(p))
            s[field + '_sha256'] = digest(p)
        if s.get('raw_sha256') and s['raw_sha256'] != s['raw_path_sha256']:
            raise ValueError('source_raw_identity_mismatch:' + s['source_id'])
    value['units'] = [u for u in value['units'] if sources[u['source_id']].get('role') == 'build']
    value['sources'] = [s for s in value['sources'] if s.get('role') == 'build']
    return value


def map_catalogs(wiki, ontology):
    """One canonical group for verified equivalent units; unresolved links stay explicit."""
    units, sources, links = {}, {}, []
    for provider, c in [('ontology', ontology), ('wiki', wiki)]:
        for s in c['sources']:
            sources[provider + ':' + s['source_id']] = s
        for u in c['units']:
            ref = provider + ':' + u['id']
            units[ref] = dict(u, provider=provider, ref=ref, text_sha256=hash_text(u['text']))
    by_source_text = {}
    for ref, u in units.items():
        s = sources[u['provider'] + ':' + u['source_id']]
        # canonical document identity is verified URL + number, never just a title/id.
        identity = (s.get('canonical_url'), s.get('document_number'))
        # Versions of one publisher document are not independent corroboration.
        # Their byte identities remain on each source record/reference separately.
        u['document_group'] = hash_text(json.dumps(identity, ensure_ascii=False))
        by_source_text.setdefault((identity, norm(u['text'])), []).append(ref)
    for key, refs in by_source_text.items():
        group = hash_text(json.dumps(key, ensure_ascii=False))
        for ref in refs:
            units[ref]['source_group'] = group
            units[ref]['equivalent_refs'] = [r for r in refs if r != ref]
    for c in wiki.get('claims', []):
        for r in c.get('source_refs', []):
            ref = 'wiki:' + r['source_id'] + '#' + r['locator']
            u = units.get(ref)
            matched = bool(u and norm(r['quote']) in norm(u['text']))
            links.append(dict(kind='claim', object_id=c['claim_id'], ref=ref, quote=r['quote'],
                              resolved=matched, source_group=u['source_group'] if u else None))
    for rule in ontology.get('rules', []):
        for r in rule.get('source_refs', []):
            locator = r if isinstance(r, str) else r.get('locator_id')
            ref = 'ontology:' + str(locator)
            u = units.get(ref)
            quote = r.get('quote') if isinstance(r, dict) else None
            matched = bool(u and (not quote or norm(quote) in norm(u['text'])))
            links.append(dict(kind='rule', object_id=rule['rule_id'], ref=ref, quote=quote,
                              resolved=matched, source_group=u['source_group'] if u else None))
    return dict(sources=sources, units=units, links=links,
                summary=dict(unit_count=len(units), link_count=len(links),
                             unresolved_links=[l for l in links if not l['resolved']],
                             equivalent_units=sum(bool(u['equivalent_refs']) for u in units.values())))


def raw_snapshot(mapping, revision):
    chunks, urls, seen = [], {}, set()
    for ref, u in mapping['units'].items():
        if u['kind'] not in {'clause', 'qa', 'section', 'item', 'raw'} or u['source_group'] in seen:
            continue
        seen.add(u['source_group'])
        source_key = u['provider'] + ':' + u['source_id']
        s = mapping['sources'][source_key]
        urls[source_key] = s['canonical_url']
        text = u['text']
        chunks.append(dict(chunk_id=hash_text(ref), block_id=hash_text(ref), source_id=source_key,
                           document_version=s['text_path_sha256'], source_title=s['title'], source_role='build',
                           text=text, search_text=u['locator'] + ' ' + text,
                           heading_path=[str(u['section']), u['locator']],
                           locator=dict(kind='normalized_unit', start=0, end=0, ref=ref),
                           span=dict(char_start=0, char_end=len(text))))
    return dict(preview_revision=revision, urls=urls, chunks=chunks)


class Bundles:
    """Single writer, immutable candidates; sessions pin IDs explicitly."""
    def __init__(self, root=DEFAULT_STORE):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        marker = self.root / 'bundle-store.json'
        if not marker.exists():
            if any(self.root.iterdir()):
                raise ValueError('refuse_unowned_store')
            write(marker, dict(format=FORMAT))
        if read(marker) != dict(format=FORMAT):
            raise ValueError('store_format_error')

    def path(self, candidate):
        if not isinstance(candidate, str) or not re.fullmatch(r'b-[a-f0-9]{32}', candidate):
            raise ValueError('invalid_bundle_id')
        p = self.root / candidate
        if p.is_symlink() or p.resolve().parent != self.root:
            raise ValueError('bundle_path_escape')
        return p

    def load(self, candidate):
        p = self.path(candidate)
        m = read(p / 'manifest.json')
        if m.get('format') != FORMAT or m.get('candidate_id') != candidate:
            raise ValueError('bundle_identity_error')
        actual = {f.relative_to(p).as_posix(): digest(f) for f in p.rglob('*')
                  if f.is_file() and f.name != 'manifest.json'}
        if actual != m['files']:
            raise ValueError('bundle_file_drift')
        Store(self.root / 'raw171').load(m['raw_candidate'])
        return m

    def active(self):
        pointer = read(self.root / 'active.json')
        self.load(pointer['candidate_id'])
        if digest(self.path(pointer['candidate_id']) / 'manifest.json') != pointer['manifest_sha256']:
            raise ValueError('active_manifest_drift')
        return pointer['candidate_id']

    def activate(self, candidate):
        m = self.load(candidate)
        if m.get('review_required'):
            raise ValueError('derived_review_required')
        temp = self.root / ('pointer-' + uuid.uuid4().hex + '.tmp')
        write(temp, dict(candidate_id=candidate,
                         manifest_sha256=digest(self.path(candidate) / 'manifest.json')))
        os.replace(temp, self.root / 'active.json')

    def build(self, wiki_root=WIKI, ontology_root=ONTOLOGY, *, wiki_commit='HEAD',
              ontology_commit='HEAD', parent=None, reviewed=False):
        candidate = 'b-' + uuid.uuid4().hex
        stage = self.root / ('.stage-' + candidate)
        stage.mkdir()
        try:
            identities = {p: archive(root, commit, stage / p) for p, root, commit in
                          [('wiki', Path(wiki_root), wiki_commit), ('ontology', Path(ontology_root), ontology_commit)]}
            wc, oc = catalog(stage / 'wiki', 'wiki'), catalog(stage / 'ontology', 'ontology')
            mapping = map_catalogs(wc, oc)
            if mapping['summary']['unresolved_links']:
                raise ValueError('unresolved_derived_links:' + json.dumps(mapping['summary']['unresolved_links'], ensure_ascii=False))
            write(stage / 'catalogs.json', dict(wiki=wc, ontology=oc))
            write(stage / 'mapping.json', mapping)
            write(stage / 'raw-snapshot.json', raw_snapshot(mapping, candidate))
            changes = self.diff(parent, mapping, wc, oc) if parent else dict(changed_refs=[], claims=[], rules=[])
            write(stage / 'changes.json', changes)
            raw = Store(self.root / 'raw171').build(stage / 'raw-snapshot.json')
            files = {f.relative_to(stage).as_posix(): digest(f) for f in stage.rglob('*') if f.is_file()}
            m = dict(format=FORMAT, candidate_id=candidate, parent=parent, providers=identities,
                     created_at=datetime.now(timezone.utc).isoformat(), raw_candidate=raw, files=files,
                     review_required=bool(parent and any(changes[k] for k in ('changed_refs', 'claims', 'rules')) and not reviewed),
                     review_acknowledged=reviewed, generation=dict(state='not_executed', execution_allowed=False),
                     mapping_summary=mapping['summary'])
            write(stage / 'manifest.json', m)
            stage.rename(self.path(candidate))
            self.load(candidate)
            return candidate
        except Exception as e:
            if stage.exists():
                write(stage / 'failure.json', dict(state='build_failed', detail=str(e), active_changed=False))
            raise

    def diff(self, parent, mapping, wc, oc):
        self.load(parent)
        old = read(self.path(parent) / 'mapping.json')
        previous = read(self.path(parent) / 'catalogs.json')
        changed = [ref for ref in sorted(set(old['units']) | set(mapping['units']))
                   if old['units'].get(ref) != mapping['units'].get(ref) or
                   old['sources'].get(ref.split(':', 1)[0] + ':' +
                                       (old['units'].get(ref) or mapping['units'][ref])['source_id']) !=
                   mapping['sources'].get(ref.split(':', 1)[0] + ':' +
                                           (mapping['units'].get(ref) or old['units'][ref])['source_id'])]
        affected = {l['object_id'] for l in old['links'] + mapping['links'] if l['ref'] in changed}
        for kind, key, rows in [('wiki', 'claims', wc['claims']), ('ontology', 'rules', oc['rules'])]:
            id_key = 'claim_id' if key == 'claims' else 'rule_id'
            before = {x[id_key]: x for x in previous[kind][key]}
            after = {x[id_key]: x for x in rows}
            affected.update(k for k in set(before) | set(after) if before.get(k) != after.get(k))
        return dict(changed_refs=changed, claims=sorted(x for x in affected if x.startswith('W-')),
                    rules=sorted(x for x in affected if x.startswith('O-R-')),
                    meaning='direct mapped dependencies; no edge does not prove no impact')
