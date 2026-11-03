"""Loopback-only Elasticsearch lexical pilot protocol; no client dependency."""

import base64
import csv
import json
import math
import os
from pathlib import Path
import re
import subprocess
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

from download_esci import ROOT
from run_bm25 import JAR, encode

LOCALES = ('es', 'jp', 'us')
ANALYZERS = {loc: f'csl_{loc}' for loc in LOCALES}
EDGES = [('us', 'stop', 'the and of'), ('us', 'literal', 'red + shoes (nike):'),
         ('us', 'width', 'Ｓｈｏｅｓ'), ('us', 'repeat', 'shoes shoes'),
         ('es', 'stop', 'el y la'), ('es', 'accent', 'camión'),
         ('jp', 'compound', '関西国際空港'), ('jp', 'width', 'ＡＢＣ　シューズ'), ('jp', 'empty', '')]


def normalized(text):
    return unicodedata.normalize('NFKC', text)


def owned_index(name):
    if not re.fullmatch(r'csl-(?:pilot|fixture)-[a-z0-9-]{1,80}', name):
        raise ValueError('Use a task-owned, concrete csl-pilot-/csl-fixture- index name')
    return name


class LocalElasticsearch:
    def __init__(self, endpoint='http://127.0.0.1:19200'):
        value = urllib.parse.urlsplit(endpoint)
        if (value.scheme != 'http' or value.hostname not in ('127.0.0.1', 'localhost') or value.port != 19200
                or value.username or value.password or value.path not in ('', '/') or value.query or value.fragment):
            raise ValueError('Pilot endpoint must be unauthenticated loopback HTTP on port 19200')
        self.endpoint = endpoint.rstrip('/')

    def request(self, method, path, body=None, ndjson=False, missing=False):
        if not path.startswith('/') or path.startswith('//'):
            raise ValueError('Expected local relative API path')
        payload = body.encode() if ndjson else json.dumps(body, ensure_ascii=False).encode() if body is not None else None
        request = urllib.request.Request(self.endpoint + path, data=payload, method=method,
                                         headers={'Content-Type': 'application/x-ndjson' if ndjson else 'application/json'})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                data = response.read()
        except urllib.error.HTTPError as error:
            if missing and error.code == 404:
                return None
            raise RuntimeError(f'Elasticsearch {method} {path} returned HTTP {error.code}: {error.read(3000).decode(errors="replace")}') from None
        return json.loads(data) if data else {}

    def ready(self, seconds=120):
        started = time.perf_counter()
        while True:
            try:
                return self.request('GET', '/')
            except (OSError, RuntimeError):
                if time.perf_counter() - started >= seconds:
                    raise TimeoutError('Elasticsearch readiness deadline exceeded') from None
                time.sleep(1)


def index_definition(policy):
    analyzers = {'csl_us': {'type': 'english'}, 'csl_es': {'type': 'spanish'},
                 'csl_jp': {'type': 'custom', 'char_filter': [], 'tokenizer': 'csl_jp_tokenizer',
                            'filter': ['kuromoji_baseform', 'kuromoji_part_of_speech',
                                       'ja_stop', 'kuromoji_stemmer', 'lowercase']}}
    return {'settings': {'number_of_shards': 1, 'number_of_replicas': 0, 'refresh_interval': '-1',
                          'similarity': {'csl_bm25': {'type': 'BM25', 'k1': policy['k1'], 'b': policy['b'],
                                                      'discount_overlaps': True}},
                          'analysis': {'analyzer': analyzers,
                                       'tokenizer': {'csl_jp_tokenizer': {'type': 'kuromoji_tokenizer', 'mode': 'search',
                                                                          'discard_punctuation': True, 'discard_compound_token': True}}}},
            'mappings': {'dynamic': 'strict', 'properties': {
                'locale': {'type': 'keyword'}, 'product_id': {'type': 'keyword'},
                **{f'title_{loc}': {'type': 'text', 'analyzer': ANALYZERS[loc], 'search_analyzer': ANALYZERS[loc],
                                    'similarity': 'csl_bm25', 'norms': True, 'index_options': 'positions'} for loc in LOCALES}}}}


def search_body(locale, text, cutoff=100):
    if locale not in LOCALES or cutoff < 1:
        raise ValueError('Invalid retrieval scope')
    return {'size': cutoff, 'track_total_hits': True, 'track_scores': True, '_source': False,
            'fields': ['locale', 'product_id'], 'timeout': '10s',
            'sort': [{'_score': 'desc'}, {'product_id': 'asc'}],
            'query': {'bool': {'filter': [{'term': {'locale': locale}}],
                               'must': [{'match': {f'title_{locale}': {'query': normalized(text), 'operator': 'or',
                                           'auto_generate_synonyms_phrase_query': False, 'zero_terms_query': 'none',
                                           'fuzziness': 0, 'lenient': False}}}]}}}


def bulk_payload(products):
    keys, lines = set(), []
    for row in products:
        locale, product = row['locale'], row['product_id']
        if locale not in LOCALES or not isinstance(product, str) or not product or (locale, product) in keys:
            raise ValueError('Invalid/duplicate bulk identity')
        keys.add((locale, product))
        lines.append(json.dumps({'create': {'_id': f'{locale}:{product}'}}, separators=(',', ':')))
        lines.append(json.dumps({'locale': locale, 'product_id': product, f'title_{locale}': normalized(row['title'])}, ensure_ascii=False))
    return '\n'.join(lines) + '\n'


def validate_bulk(response, expected):
    items = response.get('items', [])
    if response.get('errors') is not False or len(items) != expected:
        raise ValueError('Bulk item failure or response coverage mismatch; no blind retry')
    if any(set(item) != {'create'} or item['create'].get('status') != 201 or 'error' in item['create'] for item in items):
        raise ValueError('Unexpected bulk create result')


def parse_search(response, locale, catalog, cutoff=100):
    shards = response.get('_shards', {})
    if ('error' in response or response.get('timed_out') is not False or response.get('terminated_early', False)
            or shards.get('failed') != 0 or shards.get('successful') != 1 or shards.get('total') != 1):
        raise ValueError('Partial, timed-out or failed search')
    output, seen = [], set()
    for hit in response['hits']['hits']:
        fields, score = hit.get('fields', {}), hit.get('_score')
        product = fields.get('product_id', [])
        if (fields.get('locale') != [locale] or len(product) != 1 or not isinstance(product[0], str)
                or (locale, product[0]) not in catalog or hit.get('_id') != f'{locale}:{product[0]}'
                or type(score) not in (float, int) or not math.isfinite(score) or score < 0):
            raise ValueError('Invalid search product/locale/score')
        product = product[0]
        if product in seen or len(output) >= cutoff or output and (-score, product) < (-output[-1][1], output[-1][0]):
            raise ValueError('Invalid retrieval order/duplicates/cutoff')
        seen.add(product)
        output.append((product, score))
    return output


def canonical_tokens(response):
    result = []
    for token in response['tokens']:
        row = {'term': token['token'], 'position': token['position'], 'position_length': token.get('positionLength', 1),
               'start_offset': token['start_offset'], 'end_offset': token['end_offset']}
        if (not isinstance(row['term'], str) or any(type(row[k]) is not int for k in row if k != 'term')
                or row['position'] < 0 or row['position_length'] < 1 or not 0 <= row['start_offset'] <= row['end_offset']):
            raise ValueError('Malformed analyzer attributes')
        result.append(row)
    return result


def java_tokens(probes, directory):
    classes = directory / 'classes'
    classes.mkdir()
    source = ROOT / 'tools/java/lab/commerce/search/AnalyzerParity.java'
    subprocess.run(['javac', '--release', '21', '-cp', str(JAR), '-d', str(classes), str(source)], check=True)
    with (directory / 'probes.tsv').open('w', encoding='utf-8') as handle:
        for row in probes:
            handle.write(f"{row['locale']}\t{row['id']}\t{encode(normalized(row['text']))}\n")
    run = subprocess.run(['java', '-cp', os.pathsep.join((str(classes), str(JAR))), 'lab.commerce.search.AnalyzerParity',
                          str(directory / 'probes.tsv'), str(directory / 'reference-tokens.tsv')],
                         check=True, capture_output=True, text=True)
    metadata = dict(line.split('=', 1) for line in run.stdout.splitlines() if '=' in line)
    if metadata.get('lucene') != '10.6.0' or int(metadata['probes']) != len(probes):
        raise ValueError('Packaged analyzer version/probe coverage mismatch')
    output = {}
    for line in (directory / 'reference-tokens.tsv').read_text().splitlines():
        identity, packed = line.split('\t')
        if identity in output:
            raise ValueError('Duplicated analyzer probe')
        values = []
        for item in packed.split(',') if packed else []:
            term, position, length, start, end = item.split(':')
            values.append({'term': base64.b64decode(term, validate=True).decode(), 'position': int(position),
                           'position_length': int(length), 'start_offset': int(start), 'end_offset': int(end)})
        output[identity] = values
    if set(output) != {p['id'] for p in probes}:
        raise ValueError('Analyzer probe identity coverage mismatch')
    return output


def compare_rankings(reference, actual, policy):
    if set(reference) != set(actual):
        raise ValueError('Parity query coverage mismatch')
    rows = []
    for key in sorted(reference):
        expected, found = reference[key], actual[key]
        ordered = [p for p, _ in expected] == [p for p, _ in found]
        by_id = dict(found)
        score_errors = [{'product_id': p, 'reference': score, 'raw_es': by_id[p], 'normalized_es': by_id[p] / policy['raw_score_factor']}
                        for p, score in expected if p in by_id and not math.isclose(score, by_id[p] / policy['raw_score_factor'],
                            rel_tol=policy['score_relative_tolerance'], abs_tol=policy['score_absolute_tolerance'])]
        rows.append({'locale': key[0], 'query_id': key[1], 'reference_returned': len(expected), 'es_returned': len(found),
                     'ordered_ids_equal': ordered, 'normalized_score_mismatches': score_errors,
                     'max_normalized_absolute_score_error': max((abs(score - by_id[p] / policy['raw_score_factor']) for p, score in expected if p in by_id), default=0.),
                     'missing_ids': sorted(set(dict(expected)) - set(by_id)), 'added_ids': sorted(set(by_id) - set(dict(expected)))})
    return rows
