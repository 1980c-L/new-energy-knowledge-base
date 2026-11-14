"""33-request proposal executor: disabled without a NEW bound user authorization; no retries."""
from decimal import Decimal
from datetime import datetime,timezone
import json
import os
import sys
import time
from routes165 import HERE,ROOT,EVIDENCE,read,save,sha
from answer165 import validate_answer

PROPOSAL=EVIDENCE/'generation-proposal'


def validate_authorization(card,manifest_sha,binding_sha):
    expected=dict(status='approved',maximum_calls=33,budget_ceiling_usd=.3,retries=0,max_output_tokens=1000,
                  manifest_sha256=manifest_sha,executor_binding_sha256=binding_sha)
    if any(card.get(k)!=v for k,v in expected.items()) or not card.get('authorization_evidence'):
        raise ValueError('new_bound_user_authorization_required')


def validate_output(raw,request):
    if request['contract']=='parts':return validate_answer(raw,request['parts'],request['evidence_packet'])
    try:
        obj=json.loads(raw)
        if set(obj)!={'claims','missing'}:raise ValueError('invalid_legacy_schema')
        converted=dict(parts=[dict(part_id='P1',status='insufficient' if obj['missing'] else 'answered',
                                   claims=obj['claims'],missing=obj['missing'])])
        return validate_answer(converted,[dict(part_id='P1',question='original question')],request['evidence_packet'])
    except (ValueError,KeyError,TypeError):return dict(state='invalid_output',semantic_review_required=True)


def parse_response(data,bound):
    if data.get('model') not in ['deepseek-flash','DeepSeek-V4.1-Flash-1017','deepseek-v4.1-flash-1017']:
        raise ValueError('unexpected_provider_model')
    if len(data.get('choices',[]))!=1 or data['choices'][0].get('finish_reason')!='stop':raise ValueError('incomplete_generation')
    message=data['choices'][0]['message'];usage=data['usage']
    if message.get('reasoning_content') or message.get('tool_calls') or not isinstance(message.get('content'),str):raise ValueError('unexpected_response_mode')
    pin,pout=usage['prompt_tokens'],usage['completion_tokens']
    if type(pin)!=int or type(pout)!=int or not 0<pin<=bound or not 0<pout<=1000 or usage['total_tokens']!=pin+pout:raise ValueError('unexpected_usage')
    return dict(raw_answer=message['content'],input_tokens=pin,output_tokens=pout,usage=usage,
                conservative_usd=float((Decimal(pin)*Decimal('.3')+Decimal(pout)*Decimal('1.2'))/1000000),
                provider_model=data['model'],system_fingerprint=data.get('system_fingerprint'),finish_reason='stop')


def execute():
    # Absence of this new approval refuses before reading credentials or creating execution.
    card=read(PROPOSAL/'approval.json')
    binding=read(PROPOSAL/'executor-binding.json')
    validate_authorization(card,sha(PROPOSAL/'manifest.json'),sha(PROPOSAL/'executor-binding.json'))
    if sha(__file__)!=binding['executor_sha256']:raise ValueError('executor_drift')
    manifest=read(PROPOSAL/'manifest.json')
    if manifest['maximum_calls']!=33 or manifest['budget_ceiling_usd']!=.3:raise ValueError('manifest_scope_drift')
    candidate=read(EVIDENCE/'candidate-freeze.json')
    for name,digest in candidate['code_sha256'].items():
        if sha(HERE/name)!=digest:raise ValueError('candidate_drift')
    items={}
    for name,digest in manifest['request_sha256'].items():
        if sha(PROPOSAL/name)!=digest:raise ValueError('frozen_request_drift')
        request=read(PROPOSAL/name);body=request['body']
        if set(body)!={'model','messages','temperature','thinking','max_tokens','stream'} or body['model']!='deepseek-flash' or body['max_tokens']!=1000 or body['temperature']!=.2 or body['thinking']!={'type':'disabled'} or body['stream'] is not False:
            raise ValueError('body_parameter_drift')
        if sum(len(m['content'].encode('utf-8')) for m in body['messages'])+1024!=request['input_byte_reservation']:
            raise ValueError('input_bound_drift')
        items[request['id']]=request
    if len(items)!=33 or set(items)!=set(manifest['request_order']):raise ValueError('request_set_drift')
    execution=EVIDENCE/'generation-execution';execution.mkdir(exist_ok=False)
    sys.path.insert(0,str(ROOT/'tools/answer_comparison_163'))
    from batch163 import read_key
    key=read_key()
    if any(key in json.dumps(r['body'],ensure_ascii=False) for r in items.values()):raise ValueError('credential_in_payload')
    import requests
    results=[];spent=Decimal(0)
    with (execution/'events.jsonl').open('x',encoding='utf-8') as events, requests.Session() as session:
        session.trust_env=False;session.mount('https://',requests.adapters.HTTPAdapter(max_retries=0))
        def event(value):
            value['at']=datetime.now(timezone.utc).isoformat()
            events.write(json.dumps(value,ensure_ascii=False)+'\n');events.flush();os.fsync(events.fileno())
        for index,key_id in enumerate(manifest['request_order'],1):
            request=items[key_id]
            if spent+Decimal(str(request['reserved_usd']))>Decimal('.30'):raise ValueError('next_reservation_exceeds_limit')
            body=json.dumps(request['body'],ensure_ascii=False,separators=(',',':')).encode('utf-8')
            payload=execution/f'{index:02d}-{key_id}-payload.json'
            with payload.open('xb') as f:f.write(body)
            event(dict(event='started',attempt=index,id=key_id,payload_sha256=sha(payload)))
            t=time.perf_counter();row=dict(id=key_id,case_id=request['case_id'],arm=request['arm'],contract=request['contract'],attempt=index,status='unknown')
            try:
                response=session.post('https://api.deepseek.com/chat/completions',headers={'Authorization':'Bearer '+key,'Content-Type':'application/json'},data=body,timeout=(10,90),allow_redirects=False)
                raw=response.content;echo=key.encode('utf-8') in raw;raw=raw.replace(key.encode('utf-8'),b'[REDACTED]')
                rawpath=execution/f'{index:02d}-{key_id}-response.json'
                with rawpath.open('xb') as f:f.write(raw)
                row.update(http_status=response.status_code,raw_response_sha256=sha(rawpath),secret_echo_redacted=echo,payload_sha256=sha(payload))
                if echo or response.status_code!=200:raise ValueError('http_or_secret_echo_error')
                data=parse_response(json.loads(raw),request['input_byte_reservation']);row.update(data)
                spent+=Decimal(str(data['conservative_usd']))
                checked=validate_output(data['raw_answer'],request);row['structure']=checked
                if checked['state']!='citation_structure_valid':raise ValueError('output_structure_invalid')
                if spent>Decimal('.30'):raise ValueError('actual_cost_limit')
                row['status']='completed'
            except Exception as error:
                row['status']='stopped';row['error_type']=type(error).__name__
                if isinstance(error,ValueError) and str(error) in ['http_or_secret_echo_error','unexpected_provider_model','incomplete_generation','unexpected_response_mode','unexpected_usage','output_structure_invalid','actual_cost_limit']:
                    row['error_code']=str(error)
            row['elapsed_seconds']=time.perf_counter()-t;results.append(row)
            save(execution/f'{index:02d}-{key_id}-result.json',row)
            event(dict(event='finished',attempt=index,id=key_id,status=row['status'],conservative_spent_usd=float(spent)))
            print(json.dumps({k:row.get(k) for k in ['attempt','id','status','input_tokens','output_tokens','elapsed_seconds']}),flush=True)
            if row['status']!='completed':break
    success=len(results)==33 and all(r['status']=='completed' for r in results)
    save(execution/'result.json',dict(state='completed' if success else 'stopped_no_retry',attempts=len(results),completed=sum(r['status']=='completed' for r in results),
        maximum_calls=33,budget_ceiling_usd=.3,retries=0,conservative_usd=float(spent),approval_sha256=sha(PROPOSAL/'approval.json'),
        executor_binding_sha256=sha(PROPOSAL/'executor-binding.json'),rows=results))
    if not success:raise SystemExit(2)


if __name__=='__main__':execute()
