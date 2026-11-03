import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from es_pilot import (LocalElasticsearch, bulk_payload, canonical_tokens, compare_rankings, index_definition,
                      java_tokens, owned_index, parse_search, search_body, validate_bulk)
from run_elasticsearch_pilot import sample_queries
from run_bm25 import JAR

POLICY = {'k1': 1.2, 'b': .75, 'raw_score_factor': 2.2, 'score_absolute_tolerance': 1e-6,
          'score_relative_tolerance': 1e-5, 'sample_seed': 'fixture', 'queries_per_locale': 1}


class ElasticsearchProtocolTests(unittest.TestCase):
    def test_endpoint_and_index_scope_reject_remote_credentials_and_wildcards(self):
        LocalElasticsearch()
        for endpoint in ['http://example.com:19200', 'http://user:pass@localhost:19200',
                         'https://localhost:19200', 'http://localhost:9200', 'http://localhost:19200/path']:
            with self.assertRaises(ValueError):
                LocalElasticsearch(endpoint)
        self.assertEqual(owned_index('csl-pilot-a'), 'csl-pilot-a')
        for name in ['products', '*', 'csl-pilot-*', 'csl-pilot-a/_all', 'csl-pilot-']:
            with self.assertRaises(ValueError):
                owned_index(name)

    def test_explicit_analysis_similarity_and_literal_query_contract(self):
        definition = index_definition(POLICY)
        self.assertEqual(definition['settings']['number_of_shards'], 1)
        self.assertEqual(definition['settings']['similarity']['csl_bm25']['k1'], 1.2)
        self.assertEqual(definition['settings']['analysis']['analyzer']['csl_jp']['char_filter'], [])
        body = search_body('us', 'Ｓｈｏｅｓ + (red):')
        match = body['query']['bool']['must'][0]['match']['title_us']
        self.assertEqual(match['query'], 'Shoes + (red):')
        self.assertFalse(match['auto_generate_synonyms_phrase_query'])
        self.assertEqual(body['query']['bool']['filter'], [{'term': {'locale': 'us'}}])

    def test_bulk_preserves_string_locale_ids_and_rejects_partial_failures(self):
        rows = [{'locale': loc, 'product_id': '001', 'title': 'Ｓｈｏｅｓ'} for loc in ('es', 'jp')]
        lines = [json.loads(line) for line in bulk_payload(rows).splitlines()]
        self.assertEqual(lines[0]['create']['_id'], 'es:001')
        self.assertEqual(lines[2]['create']['_id'], 'jp:001')
        validate_bulk({'errors': False, 'items': [{'create': {'status': 201}}]}, 1)
        for response in [{'errors': True, 'items': [{'create': {'status': 409}}]},
                         {'errors': False, 'items': []}, {'errors': False, 'items': [{'create': {'status': 400}}]}]:
            with self.assertRaises(ValueError):
                validate_bulk(response, 1)
        with self.assertRaises(ValueError):
            bulk_payload(rows + rows[:1])

    def test_search_response_rejects_timeout_wrong_locale_duplicates_and_bad_order(self):
        hit = {'_id': 'es:001', '_score': 2.2, 'fields': {'locale': ['es'], 'product_id': ['001']}}
        response = {'timed_out': False, '_shards': {'failed': 0, 'successful': 1, 'total': 1}, 'hits': {'hits': [hit]}}
        self.assertEqual(parse_search(response, 'es', {('es', '001')}), [('001', 2.2)])
        bad = copy.deepcopy(response);bad['timed_out'] = True
        with self.assertRaises(ValueError):parse_search(bad, 'es', {('es', '001')})
        bad = copy.deepcopy(response);bad['hits']['hits'].append(hit)
        with self.assertRaises(ValueError):parse_search(bad, 'es', {('es', '001')})
        with self.assertRaises(ValueError):parse_search(response, 'jp', {('jp', '001')})

    def test_normalization_is_declared_not_fitted_and_mismatches_remain_visible(self):
        reference = {('es', 1): [('001', 1.)], ('jp', 1): []}
        actual = {('es', 1): [('001', 2.2)], ('jp', 1): []}
        rows = compare_rankings(reference, actual, POLICY)
        self.assertTrue(all(r['ordered_ids_equal'] and not r['normalized_score_mismatches'] for r in rows))
        rows = compare_rankings(reference, actual | {('es', 1): [('001', 3.)]}, POLICY)
        self.assertEqual(len(rows[0]['normalized_score_mismatches']), 1)
        rows = compare_rankings(reference, {('es', 1): [('002', 2.2)], ('jp', 1): []}, POLICY)
        self.assertEqual(rows[0]['missing_ids'], ['001'])
        with self.assertRaises(ValueError):compare_rankings(reference, {}, POLICY)

    def test_query_sampling_is_reproducible_and_label_independent(self):
        groups = {(loc, q): {'query': f'q{q}', 'labels': {'x': 'E'}} for loc in ('es', 'jp', 'us') for q in range(4)}
        a = sample_queries(groups, POLICY)
        groups = {k: dict(v, labels={'y': 'I'}) for k, v in reversed(list(groups.items()))}
        self.assertEqual(a, sample_queries(groups, POLICY))
        self.assertEqual(len(a), 3)

    def test_analyzer_attribute_contract_preserves_graph_positions_and_empty_streams(self):
        self.assertEqual(canonical_tokens({'tokens': []}), [])
        row = canonical_tokens({'tokens': [{'token': 'a', 'position': 0, 'positionLength': 2, 'start_offset': 0, 'end_offset': 2}]})[0]
        self.assertEqual(row['position_length'], 2)
        with self.assertRaises(ValueError):canonical_tokens({'tokens': [{'token': 'a', 'position': -1, 'start_offset': 0, 'end_offset': 1}]})


class AnalyzerBridgeTests(unittest.TestCase):
    @unittest.skipUnless(JAR.exists() or os.environ.get('REQUIRE_BM25_JAR') == '1', 'Build packaged Java artifact first')
    def test_packaged_analyzer_graph_attributes_unicode_and_empty_probes(self):
        self.assertTrue(JAR.exists(), 'Required packaged Java artifact is missing')
        with tempfile.TemporaryDirectory() as tmp:
            probes = [{'locale': 'us', 'id': 'empty', 'text': 'the and of'},
                      {'locale': 'us', 'id': 'width', 'text': 'Ｓｈｏｅｓ'},
                      {'locale': 'jp', 'id': 'graph', 'text': '関西国際空港'}]
            rows = java_tokens(probes, Path(tmp))
        self.assertEqual(rows['empty'], [])
        self.assertEqual(rows['width'][0]['term'], 'shoe')
        self.assertEqual([t['term'] for t in rows['graph']], ['関西', '国際', '空港'])
        self.assertEqual([t['position'] for t in rows['graph']], [0, 1, 2])
        self.assertTrue(all(t['position_length'] == 1 for t in rows['graph']))


if __name__ == '__main__':
    unittest.main()
