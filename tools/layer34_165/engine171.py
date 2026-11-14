"""Local-only engineering interface. Immutable candidates, explicitly pinned sessions.

No network/provider imports, credentials or execution of prepared messages.
Single writer per store; never edit published candidate directories.
"""
from copy import deepcopy
import hashlib
import importlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time
import uuid

from routes165 import ROOT, PLAN, OLD, SNAPSHOT, SNAPSHOT_SHA256, MODEL_REVISION, read, save
from context165 import build_packet, _validate
from answer165 import make_parts, build_messages, validate_answer
from structure168 import strict_json

DEFAULT_STORE = PLAN / '171-证据-第六层本地工程/candidates'
MODEL = Path('E:/hf-cache/models/embeddinggemma-2')
FORMAT = 'rag-local-candidate/v1'
ENCODING = dict(revision=MODEL_REVISION, dimensions=768,
                pooling='masked_mean_include_prompt_then_L2',
                query_prefix='task: search result | query: ',
                document_format='title: {source_title} | text: {text}')


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for data in iter(lambda: f.read(1024 * 1024), b''):
            h.update(data)
    return h.hexdigest()


def dependencies():
    paths = ['rag_core.py', 'tools/layer34_165/context165.py',
             'tools/layer34_165/answer165.py', 'tools/layer34_165/structure168.py',
             'tools/embeddinggemma_162/encode.py']
    return {p: digest(ROOT / p) for p in paths}


def validate_snapshot(value):
    _validate(value)
    for c in value['chunks']:
        if not isinstance(c.get('search_text'), str) or not c['search_text'].strip():
            raise ValueError('missing_search_text')
    return sorted(value['chunks'], key=lambda c: c['chunk_id'])


def document_text(c):
    return f"title: {c['source_title'] or 'none'} | text: {c['text']}"


def matrix_check(matrix, count):
    import numpy as np
    if (matrix.shape != (count, 768) or matrix.dtype != np.float32 or
            not np.isfinite(matrix).all() or
            (count and not np.allclose(np.linalg.norm(matrix, axis=1), 1, atol=1e-5))):
        raise ValueError('invalid_document_vectors')


class LocalEmbedding:
    """Reuse the verified 162 CPU encoder; lazy load once, no query cache."""
    def __init__(self):
        self.loaded = False
        self.loads = 0
        self.encodes = 0
        self.load_seconds = 0
        self.model_files = {}

    def _load(self):
        if self.loaded:
            return
        started = time.perf_counter()
        manifest = read(PLAN / '161-证据-EmbeddingGemma2恢复/download-manifest.json')
        if manifest['revision'] != MODEL_REVISION:
            raise ValueError('model_revision_mismatch')
        files = {}
        for row in manifest['files']:
            name = row['rfilename']
            p = MODEL / name
            if p.stat().st_size != row['size']:
                raise ValueError('model_size_mismatch:' + name)
            if 'lfs' in row:
                h = digest(p)
                if h != row['lfs']['sha256']:
                    raise ValueError('model_hash_mismatch:' + name)
            else:
                data = p.read_bytes()
                h = hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()
                if h != row['blobId']:
                    raise ValueError('model_blob_mismatch:' + name)
            files[name] = digest(p) if 'lfs' not in row else h
        # Keep environment local before importing any HF dependency.
        os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
                          HF_HUB_DISABLE_TELEMETRY='1', TOKENIZERS_PARALLELISM='false')
        module_dir = ROOT / 'tools/embeddinggemma_162'
        sys.path.insert(0, str(module_dir))
        try:
            enc = importlib.import_module('encode')
            if Path(enc.__file__).resolve() != (module_dir / 'encode.py').resolve():
                raise ValueError('encoder_import_conflict')
            tok, model = enc.load()
        finally:
            sys.path.remove(str(module_dir))
        self.encoder, self.tok, self.model = enc, tok, model
        self.model_files = files
        self.loads += 1
        self.load_seconds = time.perf_counter() - started
        self.loaded = True

    def encode(self, texts):
        self._load()
        # Encoder rejects overlong input rather than truncating it.
        vectors, lengths = self.encoder.encode(self.tok, self.model, texts)
        self.encodes += len(texts)
        return vectors, lengths


class Store:
    def __init__(self, root=DEFAULT_STORE):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        marker = self.root / 'store.json'
        if not marker.exists():
            if any(self.root.iterdir()):
                raise ValueError('refuse_unowned_nonempty_store')
            save(marker, dict(format=FORMAT, purpose='isolated_research_only'))
        if read(marker) != dict(format=FORMAT, purpose='isolated_research_only'):
            raise ValueError('store_identity_mismatch')

    def path(self, candidate):
        if not isinstance(candidate, str) or not re.fullmatch(r'c-[0-9a-f]{32}', candidate):
            raise ValueError('invalid_candidate_id')
        path = self.root / candidate
        if path.is_symlink() or path.resolve().parent != self.root:
            raise ValueError('candidate_path_escape')
        return path

    def active(self):
        pointer = read(self.root / 'active.json')
        candidate = pointer['candidate_id']
        self.load(candidate)
        if digest(self.path(candidate) / 'manifest.json') != pointer['manifest_sha256']:
            raise ValueError('active_identity_mismatch')
        return candidate

    def load(self, candidate):
        p = self.path(candidate)
        m = read(p / 'manifest.json')
        if (m.get('format') != FORMAT or m.get('candidate_id') != candidate or
                m.get('dependencies') != dependencies()):
            raise ValueError('candidate_implementation_mismatch')
        if digest(p / 'snapshot.json') != m['snapshot_sha256']:
            raise ValueError('snapshot_identity_mismatch')
        snap = read(p / 'snapshot.json')
        chunks = validate_snapshot(snap)
        if m['chunk_ids'] != [c['chunk_id'] for c in chunks]:
            raise ValueError('candidate_chunk_mapping_mismatch')
        if m['has_vectors']:
            if m['encoding'] != ENCODING or digest(p / 'documents.npy') != m['vector_sha256']:
                raise ValueError('vector_identity_mismatch')
        return m, snap, chunks

    def activate(self, candidate):
        self.load(candidate)  # Complete validation before replacing our pointer.
        pointer = dict(candidate_id=candidate, manifest_sha256=digest(self.path(candidate) / 'manifest.json'))
        temporary = self.root / ('pointer-' + uuid.uuid4().hex + '.tmp')
        save(temporary, pointer)
        os.replace(temporary, self.root / 'active.json')
        return pointer

    def build(self, snapshot_path, *, existing_index=None, parent=None, embedding=None, vectors=False):
        """Create, validate, publish an inactive candidate. Errors retain stage for diagnosis.

        existing_index adopts 162 without encoding; parent enables unchanged-text
        vector reuse. No activation occurs here; an interrupted stage is harmless.
        """
        started = time.perf_counter()
        raw = Path(snapshot_path).read_bytes()
        snap = json.loads(raw)
        chunks = validate_snapshot(snap)
        parent_data = self.load(parent) if parent else None
        if existing_index is not None and parent is not None:
            raise ValueError('adopt_and_parent_mutually_exclusive')
        candidate = 'c-' + uuid.uuid4().hex
        stage = self.root / ('.stage-' + candidate)
        stage.mkdir()
        try:
            (stage / 'snapshot.json').write_bytes(raw)
            encoded, reused = 0, 0
            has_vectors = bool(existing_index or vectors or (parent_data and parent_data[0]['has_vectors']))
            origin = None
            if has_vectors:
                import numpy as np
                if existing_index is not None:
                    p = Path(existing_index)
                    old = read(p / 'manifest.json')
                    if (any(old.get(k) != v for k, v in ENCODING.items()) or
                            old['snapshot_sha256'] != digest(stage / 'snapshot.json') or
                            digest(p / 'chunks.json') != old['chunks_sha256'] or
                            read(p / 'chunks.json') != chunks or
                            digest(p / 'documents.npy') != old['vector_sha256']):
                        raise ValueError('adopted_index_identity_mismatch')
                    matrix = np.load(p / 'documents.npy', allow_pickle=False)
                    matrix_check(matrix, len(chunks))
                    reused = len(chunks)
                    origin = dict(path=str(p.resolve()), manifest_sha256=digest(p / 'manifest.json'))
                else:
                    reusable = {}
                    if parent_data and parent_data[0]['has_vectors']:
                        prev = np.load(self.path(parent) / 'documents.npy', allow_pickle=False)
                        matrix_check(prev, len(parent_data[2]))
                        reusable = {document_text(c): prev[i] for i, c in enumerate(parent_data[2])}
                    missing = list(dict.fromkeys(document_text(c) for c in chunks if document_text(c) not in reusable))
                    if missing:
                        if embedding is None:
                            embedding = LocalEmbedding()
                        values, _ = embedding.encode(missing)
                        matrix_check(values, len(missing))
                        reusable.update(zip(missing, values))
                    matrix = np.stack([reusable[document_text(c)] for c in chunks]) if chunks else np.empty((0,768), dtype=np.float32)
                    encoded = len(missing)
                    reused = sum(document_text(c) not in set(missing) for c in chunks)
                    matrix_check(matrix, len(chunks))
                np.save(stage / 'documents.npy', matrix, allow_pickle=False)
            m = dict(format=FORMAT, candidate_id=candidate, parent=parent,
                     snapshot_sha256=digest(stage / 'snapshot.json'),
                     library_revision=snap['preview_revision'], chunk_ids=[c['chunk_id'] for c in chunks],
                     has_vectors=has_vectors, encoding=ENCODING if has_vectors else None,
                     vector_sha256=digest(stage / 'documents.npy') if has_vectors else None,
                     dependencies=dependencies(), origin_index=origin,
                     build=dict(encoded_texts=encoded, reused_rows=reused, total_rows=len(chunks),
                                seconds=time.perf_counter()-started),
                     paid_api_calls=0, downloads=0, installs=0)
            save(stage / 'manifest.json', m)
            # Final name appears only after every artifact is fully written.
            stage.rename(self.path(candidate))
            self.load(candidate)
            return candidate
        except Exception as error:
            if stage.exists():
                save(stage / 'failure.json', dict(state='build_failed', error_type=type(error).__name__,
                                                detail=str(error), active_changed=False))
            raise


def _jieba():
    try:
        return importlib.import_module('jieba')
    except ModuleNotFoundError:
        # The HF venv shares this base interpreter; reuse the installed pure-Python
        # package without installing or adding the system site-packages globally.
        base = Path(sys.base_prefix) / 'Lib/site-packages'
        if not (base / 'jieba/__init__.py').is_file():
            raise ValueError('existing_jieba_missing')
        sys.path.insert(0, str(base))
        try:
            return importlib.import_module('jieba')
        finally:
            sys.path.remove(str(base))


class Session:
    def __init__(self, store, candidate=None, embedding=None):
        self.store = store
        self.embedding = embedding or LocalEmbedding()
        self._cache = tempfile.TemporaryDirectory(prefix='rag171-jieba-')
        self.bm25_builds = self.index_loads = self.query_count = self.matrix_loads = 0
        self.open(candidate or store.active())

    def close(self):
        self._cache.cleanup()

    def open(self, candidate):
        start = time.perf_counter()
        m, snap, chunks = self.store.load(candidate)
        # Assignment after validation: failed switch leaves the working session intact.
        self.candidate, self.manifest, self._snapshot, self._chunks = candidate, m, snap, chunks
        self._matrix = self._bm25 = self._docs = self._tokenizer = None
        self.matrix_load_seconds = 0
        self.index_load_seconds = time.perf_counter()-start
        self.index_loads += 1

    def _lexical(self, question):
        if not self._chunks:
            return []
        if self._bm25 is None:
            jieba = _jieba()
            if jieba.__version__ != '0.42.1':
                raise ValueError('bm25_tokenizer_drift')
            sys.path.insert(0, str(ROOT))
            try:
                from rag_core import BM25
                from v2.retrieve import CORE_SHA256
            finally:
                sys.path.remove(str(ROOT))
            if digest(ROOT / 'rag_core.py').upper() != CORE_SHA256:
                raise ValueError('bm25_core_drift')
            tokenizer = jieba.Tokenizer()
            tokenizer.tmp_dir = self._cache.name
            tokenizer.cache_file = 'jieba.cache'
            docs = [dict(c, tokens=[t for t in tokenizer.lcut(c['search_text']) if t.strip()]) for c in self._chunks]
            model = BM25(docs)
            self._bm25, self._docs, self._tokenizer = model, docs, tokenizer
            self.bm25_builds += 1
        tokens = [t for t in self._tokenizer.lcut(question) if t.strip()]
        pairs = [(self._bm25._score(tokens, c), c['chunk_id']) for c in self._docs]
        return [dict(chunk_id=cid, score=s) for s, cid in sorted(pairs, key=lambda x:(-x[0],x[1])) if s > 0]

    def _vector(self, question):
        self.last_encode_seconds = self.last_inference_seconds = self.last_query_tokens = None
        self.last_model_was_loaded = self.embedding.loaded
        if not self.manifest['has_vectors']:
            raise ValueError('candidate_has_no_vector_index')
        if not self._chunks:
            return []
        import numpy as np
        if self._matrix is None:
            load_start = time.perf_counter()
            matrix = np.load(self.store.path(self.candidate) / 'documents.npy', allow_pickle=False)
            # Check the bytes now too: BM25 sessions can defer matrix loading.
            if digest(self.store.path(self.candidate) / 'documents.npy') != self.manifest['vector_sha256']:
                raise ValueError('vector_identity_mismatch')
            matrix_check(matrix, len(self._chunks))
            self._matrix = matrix
            self.matrix_load_seconds = time.perf_counter()-load_start
            self.matrix_loads += 1
        start = time.perf_counter()
        was_loaded = self.embedding.loaded
        vector, lengths = self.embedding.encode([ENCODING['query_prefix'] + question])
        matrix_check(vector, 1)
        self.last_encode_seconds = time.perf_counter()-start
        self.last_inference_seconds = max(0, self.last_encode_seconds - (0 if was_loaded else self.embedding.load_seconds))
        self.last_model_was_loaded = was_loaded
        self.last_query_tokens = lengths[0]
        scores = self._matrix @ vector[0]
        order = sorted(range(len(self._chunks)), key=lambda i:(-float(scores[i]), self._chunks[i]['chunk_id']))
        return [dict(chunk_id=self._chunks[i]['chunk_id'], score=float(scores[i])) for i in order]

    def query(self, spec):
        start = time.perf_counter()
        out = dict(schema='rag-local-query/v1', state='input_error',
                   generation=dict(state='not_executed', paid_api_calls=0),
                   prepared=None, evidence_is_not_answerability_proof=True)
        try:
            if not isinstance(spec, dict):
                raise ValueError('request_must_be_object')
            allowed = {'question','parts','method','mode','max_chars','max_seeds','max_records'}
            if set(spec) - allowed:
                raise ValueError('unknown_request_fields')
            question = spec.get('question')
            parts = make_parts(question, spec.get('parts'))
            method, mode = spec.get('method','A'), spec.get('mode','window')
            if method not in ('A','V') or mode not in ('chunks','parent','window'):
                raise ValueError('invalid_method_or_mode')
            limits = {k:spec.get(k,default) for k,default in [('max_chars',8000),('max_seeds',20),('max_records',20)]}
            if any(type(v) is not int or not 0 <= v <= (8000 if k=='max_chars' else 20) for k,v in limits.items()):
                raise ValueError('invalid_budget')
        except (ValueError, TypeError, KeyError) as error:
            out['error'] = dict(code=str(error))
            return out
        try:
            ranks = self._lexical(question) if method=='A' else self._vector(question)
            packet = build_packet(self._snapshot, [r['chunk_id'] for r in ranks[:60]], mode=mode, **limits)
            out.update(state=packet['state'], packet=packet,
                       request=dict(question=question, parts=parts, method=method, mode=mode, **limits),
                       identity=dict(candidate_id=self.candidate, snapshot_sha256=self.manifest['snapshot_sha256'],
                                     manifest_sha256=digest(self.store.path(self.candidate)/'manifest.json'),
                                     library_revision=self.manifest['library_revision'],
                                     encoding=deepcopy(self.manifest['encoding']), dependencies=deepcopy(self.manifest['dependencies'])),
                       retrieval=dict(ranking=ranks[:60], query_encoding_cached=False,
                                      answerability_threshold_calibrated=False if method=='V' else None))
            if packet['state']=='success':
                out['prepared'] = dict(messages=build_messages(question,packet,parts),
                                       response_format=dict(type='json_object'), execution_allowed=False,
                                       semantic_review_required=True)
            self.query_count += 1
            out['runtime'] = dict(total_seconds=time.perf_counter()-start, index_load_seconds=self.index_load_seconds,
                                  index_loads=self.index_loads, bm25_builds=self.bm25_builds,
                                  matrix_loads=self.matrix_loads, matrix_load_seconds=self.matrix_load_seconds,
                                  query_count=self.query_count, model_loads=self.embedding.loads,
                                  model_load_seconds=self.embedding.load_seconds,
                                  query_encode_including_first_model_load_seconds=getattr(self,'last_encode_seconds',None) if method=='V' else None,
                                  query_encode_seconds=getattr(self,'last_inference_seconds',None) if method=='V' else None,
                                  model_previously_loaded=getattr(self,'last_model_was_loaded',False) if method=='V' else None,
                                  query_tokens=getattr(self,'last_query_tokens',None) if method=='V' else None)
        except (ValueError, OSError, KeyError, TypeError, ImportError, RuntimeError, AssertionError) as error:
            out.update(state='source_error', error=dict(code=str(error), error_type=type(error).__name__))
        return out

    def lookup(self, candidate, chunk_id):
        m, snap, chunks = self.store.load(candidate)
        for c in chunks:
            if c['chunk_id']==chunk_id:
                return dict(deepcopy(c), candidate_id=candidate, snapshot_sha256=m['snapshot_sha256'],
                            source_url=snap['urls'][c['source_id']])
        raise ValueError('chunk_not_in_candidate')

    def validate(self, raw_answer, result):
        try:
            obj = strict_json(raw_answer)
            return validate_answer(obj, result['request']['parts'], result['packet'])
        except (ValueError, KeyError, TypeError) as error:
            return dict(state='invalid_output', errors=[str(error)], semantic_review_required=True)
