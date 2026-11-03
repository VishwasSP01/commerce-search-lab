"""Tiny synthetic real-backend parity fixture; no ESCI or pretrained model inputs."""

import argparse
import json
from pathlib import Path
import tempfile

from download_esci import ROOT
from run_elasticsearch_pilot import backend, measure
from es_pilot import LocalElasticsearch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--index', default='csl-fixture-parity-v1')
    args = parser.parse_args()
    policy = json.loads((ROOT / 'config/elasticsearch-pilot-v1.json').read_text())
    client = LocalElasticsearch()
    backend(client, policy)
    products = [{'locale': locale, 'product_id': product, 'title': title} for locale, product, title in [
        ('us', '001', 'Red running shoes'), ('us', '002', 'Blue shoes'), ('us', '003', 'the and of'),
        ('us', '004', 'ＳＨＯＥＳ'), ('es', '001', 'Zapatos rojos para correr'), ('es', '002', 'Camión azul'),
        ('es', '003', 'el y la'), ('jp', '001', '関西国際空港 シューズ'), ('jp', '002', '東京 空港'),
        ('jp', '003', 'カタカナ ＡＢＣ')]]
    products.sort(key=lambda p: (p['locale'], p['product_id']))
    queries = [{'locale': loc, 'query_id': query, 'query': text} for loc, query, text in [
        ('us', 1, 'shoes'), ('us', 2, 'the and of'), ('us', 3, 'red + shoes (nike):'), ('us', 4, 'shoes shoes'),
        ('es', 1, 'zapato'), ('es', 2, 'el y la'), ('es', 3, 'camión'),
        ('jp', 1, '関西国際空港'), ('jp', 2, 'ＡＢＣ'), ('jp', 3, '')]]
    queries.sort(key=lambda q: (q['locale'], q['query_id']))
    with tempfile.TemporaryDirectory(prefix='es-fixture-') as tmp:
        result = measure(client, args.index, products, queries, Path(tmp), policy)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if not result['summary']['passed_bounded_parity']:
            raise SystemExit('Native Elasticsearch fixture parity failed')


if __name__ == '__main__':
    main()
