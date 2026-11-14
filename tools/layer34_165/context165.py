"""Pure, deterministic evidence assembly over the fixed public snapshot.

No retrieval, question IDs, scoring rules, file writes or model calls. Record
IDs are packet-local E1... IDs, never original chunk IDs. Character budgets
include explicit separators in record content, but exclude context metadata.
"""
from collections import defaultdict
from copy import deepcopy
import json

BLOCK_SEPARATOR = '\n\n[block boundary]\n\n'
GAP_SEPARATOR = '\n\n[non-contiguous original pieces]\n\n'
TABLE_SEPARATOR = '\n\n[table piece boundary; original header retained]\n\n'


class _SourceError(ValueError):
    pass


def _identity(c):
    return c['source_id'], c['document_version'], c['block_id']


def _integer(value):
    return type(value) is int and value >= 0


def _validate(snapshot):
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get('chunks'), list):
        raise _SourceError('snapshot.chunks must be a list')
    if (not isinstance(snapshot.get('urls'), dict) or
            not isinstance(snapshot.get('preview_revision'), str) or not snapshot['preview_revision']):
        raise _SourceError('snapshot requires urls and preview_revision')
    by_id, blocks, first_position = {}, defaultdict(list), {}
    for index, c in enumerate(snapshot['chunks']):
        if not isinstance(c, dict):
            raise _SourceError('chunk must be an object')
        for key in ('chunk_id', 'block_id', 'source_id', 'document_version',
                    'source_title', 'source_role'):
            if not isinstance(c.get(key), str) or not c[key]:
                raise _SourceError('missing chunk identity: ' + key)
        if c['chunk_id'] in by_id:
            raise _SourceError('duplicate chunk_id: ' + c['chunk_id'])
        url = snapshot['urls'].get(c['source_id'])
        if not isinstance(url, str) or not url:
            raise _SourceError('missing source URL: ' + c['source_id'])
        if not isinstance(c.get('text'), str) or not c['text']:
            raise _SourceError('empty or invalid text: ' + c['chunk_id'])
        if not isinstance(c.get('heading_path'), list) or not all(
                isinstance(x, str) for x in c['heading_path']):
            raise _SourceError('invalid heading_path: ' + c['chunk_id'])
        loc, span = c.get('locator'), c.get('span')
        if (not isinstance(loc, dict) or not loc.get('kind') or
                not _integer(loc.get('start')) or not _integer(loc.get('end')) or
                loc['end'] < loc['start']):
            raise _SourceError('invalid source locator: ' + c['chunk_id'])
        if not isinstance(span, dict):
            raise _SourceError('missing span: ' + c['chunk_id'])
        if 'char_start' in span or 'char_end' in span:
            if (not _integer(span.get('char_start')) or not _integer(span.get('char_end'))
                    or span['char_end'] - span['char_start'] != len(c['text'])):
                raise _SourceError('invalid character span: ' + c['chunk_id'])
        elif not (isinstance(span.get('data_row_indexes'), list) and
                  span['data_row_indexes'] and all(_integer(i) for i in span['data_row_indexes'])):
            raise _SourceError('span requires character offsets or table row indexes')
        by_id[c['chunk_id']] = c
        key = _identity(c)
        first_position.setdefault(key, index)
        blocks[key].append(c)
    documents = defaultdict(list)
    for key, pieces in blocks.items():
        first = pieces[0]
        for c in pieces:
            if any(c[k] != first[k] for k in ('heading_path', 'source_title', 'source_role')):
                raise _SourceError('inconsistent parent block metadata: ' + key[2])
        pieces.sort(key=lambda c: (c['span'].get('char_start',
                                  min(c['span'].get('data_row_indexes', [0]))), c['chunk_id']))
        documents[key[:2]].append(key)
    for keys in documents.values():
        document_pieces = [c for key in keys for c in blocks[key]]
        first = document_pieces[0]
        if any(c['source_title'] != first['source_title'] or
               c['source_role'] != first['source_role'] or
               c['locator']['kind'] != first['locator']['kind'] for c in document_pieces):
            raise _SourceError('conflicting document identity or source locator units')
        keys.sort(key=lambda key: (min(c['locator']['start'] for c in blocks[key]),
                                   min(c['locator']['end'] for c in blocks[key]),
                                   first_position[key]))
    return by_id, blocks, documents


def _content(pieces):
    """Concatenate only verified adjacent character spans in the same block."""
    parts = []
    previous = None
    for piece in pieces:
        if previous is not None:
            a, b = previous['span'], piece['span']
            if _identity(previous) != _identity(piece):
                parts.append(BLOCK_SEPARATOR)
            elif ('char_end' in a and 'char_start' in b and
                  a['char_end'] == b['char_start']):
                pass
            elif 'data_row_indexes' in a and 'data_row_indexes' in b:
                parts.append(TABLE_SEPARATOR)
            else:
                parts.append(GAP_SEPARATOR)
        parts.append(piece['text'])
        previous = piece
    return ''.join(parts)


def _block_metadata(pieces):
    groups = {}
    for c in pieces:
        groups.setdefault(_identity(c), []).append(c)
    result = []
    for key, group in groups.items():
        loc = group[0]['locator']
        result.append(dict(block_id=key[2], chunk_ids=[c['chunk_id'] for c in group],
                           locator=dict(kind=loc['kind'],
                                        start=min(c['locator']['start'] for c in group),
                                        end=max(c['locator']['end'] for c in group)),
                           locator_semantics='bounding_range_not_continuity',
                           spans=[deepcopy(c['span']) for c in group]))
    return result


def build_packet(snapshot: dict, ranked_ids: list[str], *, mode='window',
                 max_seeds=20, max_records=20, max_chars=8000) -> dict:
    """Build chunks, full indexed parents, or parents plus immediate neighbours.

    The caller authenticates the fixed file hash/index revision before calling.
    This function validates internal source identity and preserves all source
    fields in independent ``pieces`` copies. ``selected_chunk_ids`` means all
    actually emitted original pieces; ``expanded_chunk_ids`` excludes seeds.
    Only ranked_ids[:max_seeds] can trigger a group. No later-rank backfill.
    """
    out = dict(state='input_error', mode=mode, records=[], excluded=[], errors=[],
               selected_chunk_ids=[], expanded_chunk_ids=[], seed_ids=[],
               body_chars=0, context='', groups=[], stats={},
               budget=dict(max_seeds=max_seeds, max_records=max_records, max_chars=max_chars))

    def finish(state):
        out['state'] = state
        pieces = [p for r in out['records'] for p in r['pieces']]
        selected = [p['chunk_id'] for p in pieces]
        seeds = set(out['seed_ids'])
        out['selected_chunk_ids'] = selected
        out['expanded_chunk_ids'] = [cid for cid in selected if cid not in seeds]
        out['body_chars'] = sum(len(r['content']) for r in out['records'])
        out['context'] = '\n\n'.join(
            '[' + r['id'] + '] ' + json.dumps(dict(title=r['title'], **r['metadata']),
                                             ensure_ascii=False, sort_keys=True) +
            '\n' + r['content'] for r in out['records'])
        source_chars = sum(len(p['text']) for p in pieces)
        seed_chars = sum(len(p['text']) for p in pieces if p['chunk_id'] in seeds)
        out['stats'] = dict(
            input_ranked_count=len(ranked_ids) if isinstance(ranked_ids, list) else 0,
            considered_seed_count=min(len(ranked_ids), max_seeds)
            if isinstance(ranked_ids, list) and _integer(max_seeds) else 0,
            seed_count=len(seeds), selected_seed_count=len(seeds.intersection(selected)),
            record_count=len(out['records']), raw_chunk_count=len(pieces),
            block_count=len({_identity(p) for p in pieces}),
            body_chars=out['body_chars'], source_text_chars=source_chars,
            separator_chars=out['body_chars'] - source_chars,
            selected_seed_source_chars=seed_chars,
            expanded_source_chars=source_chars - seed_chars,
            extra_context_chars=out['body_chars'] - seed_chars,
            context_chars=len(out['context']),
            metadata_and_context_overhead_chars=len(out['context']) - out['body_chars'])
        return out

    if (mode not in ('chunks', 'parent', 'window') or
            not all(_integer(x) for x in (max_seeds, max_records, max_chars)) or
            not isinstance(ranked_ids, list) or
            not all(isinstance(x, str) and x for x in ranked_ids)):
        out['errors'].append(dict(code='invalid_input', detail='invalid mode, budget or ranked IDs'))
        return finish('input_error')
    try:
        by_id, blocks, documents = _validate(snapshot)
    except _SourceError as exc:
        out['errors'].append(dict(code='invalid_source', detail=str(exc)))
        return finish('source_error')
    unknown = [cid for cid in ranked_ids if cid not in by_id]
    if unknown:
        out['errors'].append(dict(code='unknown_chunk_id', chunk_ids=list(dict.fromkeys(unknown))))
        return finish('input_error')
    if not ranked_ids:
        return finish('no_match')
    out['seed_ids'] = list(dict.fromkeys(ranked_ids[:max_seeds]))
    selected_ids, selected_records = set(), {}
    for cid in out['seed_ids']:
        seed = by_id[cid]
        key = _identity(seed)
        group_keys = [key]
        if mode == 'window':
            keys = documents[key[:2]]
            position = keys.index(key)
            group_keys = [keys[i] for i in range(max(0, position - 1), min(len(keys), position + 2))
                          if blocks[keys[i]][0]['heading_path'] == seed['heading_path']]
        full = [seed] if mode == 'chunks' else [c for k in group_keys for c in blocks[k]]
        window_ids = [c['chunk_id'] for c in full]
        new = [c for c in full if c['chunk_id'] not in selected_ids]
        reused_ids = list(dict.fromkeys(selected_records[c['chunk_id']]
                                       for c in full if c['chunk_id'] in selected_ids))
        content = _content(new)
        group = dict(seed_id=cid, seed_rank=ranked_ids.index(cid) + 1,
                     chunk_ids=window_ids, new_chunk_ids=[c['chunk_id'] for c in new],
                     block_ids=list(dict.fromkeys(c['block_id'] for c in full)),
                     full_body_chars=len(_content(full)), new_body_chars=len(content),
                     reused_record_ids=reused_ids)
        out['groups'].append(group)
        if not new:
            group['status'] = 'already_covered'
            continue
        reason = None
        if len(out['records']) >= max_records:
            reason = 'record_budget'
        elif out['body_chars'] + len(content) > max_chars:
            reason = 'body_char_budget'
        if reason:
            group['status'] = 'excluded'
            out['excluded'].append(dict(**deepcopy(group), reason=reason))
            continue
        record_id = 'E' + str(len(out['records']) + 1)
        block_info = _block_metadata(new)
        metadata = dict(record_type='original_chunk' if mode == 'chunks' else 'context_' + mode,
                        synthetic_record_id=True, source_id=seed['source_id'],
                        source_role=seed['source_role'], document_version=seed['document_version'],
                        source_url=snapshot['urls'][seed['source_id']],
                        library_revision=snapshot['preview_revision'],
                        heading_path=deepcopy(seed['heading_path']),
                        chunk_ids=[c['chunk_id'] for c in new],
                        block_ids=[b['block_id'] for b in block_info], blocks=block_info,
                        locator=dict(kind=seed['locator']['kind'],
                                     start=min(c['locator']['start'] for c in new),
                                     end=max(c['locator']['end'] for c in new)),
                        locator_semantics='bounding_range_not_continuity')
        provenance = dict(seed_ids=[i for i in out['seed_ids'] if i in window_ids],
                          trigger_seed_id=cid, seed_rank=group['seed_rank'],
                          window_chunk_ids=window_ids, reused_record_ids=reused_ids,
                          assembly_mode=mode)
        out['records'].append(dict(id=record_id, content=content, title=seed['source_title'],
                                   metadata=metadata, pieces=deepcopy(new), provenance=provenance))
        out['body_chars'] += len(content)
        for c in new:
            selected_ids.add(c['chunk_id'])
            selected_records[c['chunk_id']] = record_id
        group.update(status='selected', record_id=record_id)
    return finish('success' if out['records'] else 'budget_empty')
