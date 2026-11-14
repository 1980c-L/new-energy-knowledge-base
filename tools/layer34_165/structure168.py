"""168 strict wire JSON and JSON-mode candidate; frozen 165 sources stay unchanged."""
import copy
import json

from execute_generation165 import validate_output as validate165
from execute_generation165 import parse_response as parse165


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('duplicate_json_key')
            result[key] = value
        return result

    def constant(value):
        raise ValueError('non_json_constant')

    if not isinstance(raw, str) or not raw.strip():
        raise ValueError('blank_content')
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)


def json_body(body):
    result = copy.deepcopy(body)
    result['response_format'] = dict(type='json_object')
    return result


def validate_output(raw, request):
    try:
        value = strict_json(raw)
        # Re-serialization only follows successful strict parsing; no field repair.
        return validate165(json.dumps(value, ensure_ascii=False), request)
    except (ValueError, TypeError, KeyError, AttributeError) as error:
        return dict(state='invalid_output', errors=[str(error)], semantic_review_required=True)


def parse_response(data, bound):
    result = parse165(data, bound)
    if not result['raw_answer'].strip():
        raise ValueError('blank_content')
    return result
