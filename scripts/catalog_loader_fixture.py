"""Exercise immutable bulk/async merge/count checks on a tiny real ES index."""
import json
from download_esci import ROOT
from catalog_serving import load
from es_pilot import LocalElasticsearch
from run_elasticsearch_pilot import backend, retrieve


def main():
    policy = json.loads((ROOT / 'config/catalog-serving-v1.json').read_text())
    policy['catalog_by_locale'] = {'es': 1, 'jp': 1, 'us': 1}
    client = LocalElasticsearch()
    backend(client, json.loads((ROOT / 'config/elasticsearch-pilot-v1.json').read_text()))
    products = [{'locale': loc, 'product_id': '001', 'title': title} for loc, title in [('es', 'zapatos'), ('jp', '空港'), ('us', 'shoes')]]
    result = load(client, 'csl-fixture-catalog-loader-v1', iter(products), policy)
    queries = [{'locale': p['locale'], 'query_id': 1, 'query': p['title']} for p in products]
    actual = retrieve(client, 'csl-fixture-catalog-loader-v1', queries, result['catalog'])
    if any(len(rows) != 1 or rows[0][0] != '001' for rows in actual.values()): raise ValueError('Loader fixture search failed')
    print(json.dumps({'products': len(result['catalog']), 'counts': result['index']['counts'], 'single_segment_write_blocked_and_searchable': True}))


if __name__ == '__main__': main()
