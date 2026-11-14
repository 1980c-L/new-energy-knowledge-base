"""Existing HF environment only: offline text encoding, index build and query timing."""
import os
os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1',
                  TRANSFORMERS_VERBOSITY='error', TOKENIZERS_PARALLELISM='false')
import argparse
import ctypes
from ctypes import wintypes
import json
from pathlib import Path
import platform
import time
import numpy as np
import torch
from transformers import AutoConfig, AutoModel, AutoTokenizer
from prepare import OUT, SNAP, MODEL, REV, read, save, sha


def resources():
    class Counters(ctypes.Structure):
        _fields_ = [('cb',wintypes.DWORD),('PageFaultCount',wintypes.DWORD)] + [(n,ctypes.c_size_t) for n in
                    ['PeakWorkingSetSize','WorkingSetSize','QuotaPeakPagedPoolUsage','QuotaPagedPoolUsage',
                     'QuotaPeakNonPagedPoolUsage','QuotaNonPagedPoolUsage','PagefileUsage','PeakPagefileUsage']]
    x = Counters()
    x.cb = ctypes.sizeof(x)
    ctypes.windll.psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
    ctypes.windll.kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    assert ctypes.windll.psapi.GetProcessMemoryInfo(ctypes.windll.kernel32.GetCurrentProcess(), ctypes.byref(x), x.cb)
    return dict(peak_working_set_bytes=x.PeakWorkingSetSize, working_set_bytes=x.WorkingSetSize,
                cpu_seconds=time.process_time(), resource_scope='this_process_windows_working_set_not_system_total')


def load():
    torch.set_num_threads(2)
    torch.manual_seed(162)
    cfg = AutoConfig.from_pretrained(MODEL, local_files_only=True, trust_remote_code=False)
    cfg.vision_config = cfg.audio_config = None
    tok = AutoTokenizer.from_pretrained(MODEL, local_files_only=True, trust_remote_code=False)
    model = AutoModel.from_pretrained(MODEL, config=cfg, local_files_only=True, trust_remote_code=False,
                                      dtype=torch.float32, low_cpu_mem_usage=True).eval()
    assert model.vision_tower is None and model.audio_tower is None
    return tok, model


def encode(tok, model, texts, progress=False):
    arrays, lengths = [], []
    with torch.inference_mode():
        for start in range(0, len(texts), 4):
            batch = tok(texts[start:start+4], return_tensors='pt', padding=True, truncation=False)
            lens = batch['attention_mask'].sum(1).tolist()
            assert max(lens) <= 8192, 'No silent truncation allowed'
            lengths.extend(lens)
            hidden = model(**batch).last_hidden_state.float()
            mask = batch['attention_mask'].unsqueeze(-1).float()
            pooled = (hidden * mask).sum(1) / mask.sum(1).clamp(min=1)
            vec = torch.nn.functional.normalize(pooled, p=2, dim=-1)
            assert torch.isfinite(vec).all() and vec.shape[1] == 768
            arrays.append(vec.cpu().numpy())
            if progress and (start % 40 == 0 or start+4 >= len(texts)):
                print(json.dumps(dict(encoded=min(start+4,len(texts)), total=len(texts))), flush=True)
    result = np.concatenate(arrays)
    assert np.allclose(np.linalg.norm(result, axis=1), 1, atol=1e-5)
    return result, lengths


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['build','query'])
    parser.add_argument('--index', type=Path, default=OUT/'index')
    parser.add_argument('--out', type=Path, default=OUT/'vectors.json')
    args = parser.parse_args()
    start, cpu_start = time.perf_counter(), time.process_time()
    frozen = read(OUT/'freeze.json')
    assert sha(SNAP) == frozen['snapshot_sha256']
    for p,h in frozen['model_config_hashes'].items():
        assert sha(MODEL/p) == h
    tok, model = load()
    load_seconds = time.perf_counter()-start
    if args.mode == 'build':
        args.index.mkdir(exist_ok=False)
        chunks = sorted(read(SNAP)['chunks'], key=lambda c:c['chunk_id'])
        texts = [f"title: {c['source_title'] or 'none'} | text: {c['text']}" for c in chunks]
        enc_start = time.perf_counter()
        vec, lengths = encode(tok, model, texts, progress=True)
        encode_seconds = time.perf_counter()-enc_start
        np.save(args.index/'documents.npy', vec, allow_pickle=False)
        save(args.index/'chunks.json', chunks)
        save(args.index/'manifest.json', dict(model_repo='google/embeddinggemma-2', revision=REV, text_only=True,
             dtype='float32', dimensions=768, threads=2, batch_size=4, pooling='masked_mean_include_prompt_then_L2',
             query_prefix='task: search result | query: ', document_format='title: {source_title} | text: {text}',
             snapshot_sha256=sha(SNAP), queries_sha256=frozen['queries_sha256'], freeze_sha256=sha(OUT/'freeze.json'),
             code_sha256=sha(Path(__file__)), vector_sha256=sha(args.index/'documents.npy'),
             chunks_sha256=sha(args.index/'chunks.json'), token_lengths=lengths, max_tokens=max(lengths),
             shape=list(vec.shape), model_load_seconds=load_seconds, document_encode_seconds=encode_seconds,
             build_total_seconds=time.perf_counter()-start, build_cpu_seconds=time.process_time()-cpu_start,
             resources=resources(), versions=dict(torch=torch.__version__, transformers=__import__('transformers').__version__,
             numpy=np.__version__, python=platform.python_version()), downloads=0, installs=0, paid_api_calls=0))
        print(json.dumps(read(args.index/'manifest.json') | dict(token_lengths='omitted_in_stdout')), flush=True)
    else:
        assert not args.out.exists(), 'Refuse evidence overwrite'
        manifest = read(args.index/'manifest.json')
        assert manifest['snapshot_sha256'] == sha(SNAP) and manifest['revision'] == REV
        assert sha(args.index/'documents.npy') == manifest['vector_sha256']
        assert sha(args.index/'chunks.json') == manifest['chunks_sha256']
        istart = time.perf_counter()
        documents = np.load(args.index/'documents.npy', allow_pickle=False)
        chunks = read(args.index/'chunks.json')
        index_load_seconds = time.perf_counter()-istart
        queries = read(OUT/'queries.json')
        assert sha(OUT/'queries.json') == frozen['queries_sha256']
        rows = {}
        # Two warm rounds after first complete round; no query-vector cache.
        for repeat in range(3):
            for q in queries:
                t, cpu = time.perf_counter(), time.process_time()
                vector, lengths = encode(tok, model, ['task: search result | query: '+q['question']])
                encoding = time.perf_counter()-t
                st = time.perf_counter()
                scores = (documents @ vector[0]).astype(float)
                ranks = sorted(range(len(chunks)), key=lambda i:(-scores[i], chunks[i]['chunk_id']))
                rank_seconds = time.perf_counter()-st
                timing = dict(repeat=repeat, encode_seconds=encoding, search_seconds=rank_seconds,
                              total_seconds=time.perf_counter()-t, cpu_seconds=time.process_time()-cpu,
                              tokens=lengths[0], first_query_after_process_load=repeat==0 and q==queries[0])
                if repeat == 0:
                    rows[q['id']] = dict(ranking=[dict(chunk_id=chunks[i]['chunk_id'], score=float(scores[i])) for i in ranks], timings=[timing])
                else:
                    assert [x['chunk_id'] for x in rows[q['id']]['ranking']] == [chunks[i]['chunk_id'] for i in ranks]
                    rows[q['id']]['timings'].append(timing)
                print(json.dumps(dict(query=q['id'], repeat=repeat, seconds=timing['total_seconds'])), flush=True)
        save(args.out, dict(rows=rows, model_load_seconds=load_seconds, index_load_seconds=index_load_seconds,
             first_query_end_to_end_seconds=load_seconds+index_load_seconds+rows[queries[0]['id']]['timings'][0]['total_seconds'],
             process_total_seconds=time.perf_counter()-start, resources=resources(), code_sha256=sha(Path(__file__)),
             manifest_sha256=sha(args.index/'manifest.json'), query_cache=False, network=False, paid_api_calls=0))


if __name__ == '__main__':
    main()
