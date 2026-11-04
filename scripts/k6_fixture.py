"""Native k6 against controlled loopback HTTP: validate metrics and wrong results."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading

from api_load import parse_metrics
from download_esci import ROOT
from install_k6 import install


def main():
    wrong = False
    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'
        def do_POST(self):
            self.rfile.read(int(self.headers['Content-Length']))
            data = json.dumps({'locale': 'us', 'limit': 10, 'returned': 1, 'retrieval': 'bm25',
                               'hits': [{'productId': 'wrong' if wrong else '001', 'score': 2.2}]}).encode()
            self.send_response(200); self.send_header('Content-Type', 'application/json'); self.send_header('Content-Length', str(len(data))); self.end_headers(); self.wfile.write(data)
        def log_message(self, *_): pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        binary = install()
        with tempfile.TemporaryDirectory(prefix='k6-fixture-') as tmp:
            directory = Path(tmp)
            workload = directory / 'workload.json'; workload.write_text(json.dumps([{'locale': 'us', 'query_id': 1, 'query': 'shoes', 'expected': [['001', 2.2]]}]))
            for label in ('correct', 'incorrect'):
                wrong = label == 'incorrect'
                env = dict(os.environ, WORKLOAD=str(workload), BASE_URL=f'http://127.0.0.1:{server.server_port}', RATE='5', DURATION='2', VUS='2', SUMMARY=str(directory / f'{label}-summary.json'))
                raw = directory / f'{label}.ndjson'
                result = subprocess.run([str(binary), 'run', '--no-usage-report', '--out', f'json={raw}', str(ROOT / 'load/api-load.js')], env=env, capture_output=True, text=True, timeout=30)
                if result.returncode not in (0, 99): raise RuntimeError(result.stderr[-3000:])
                report = parse_metrics(raw, 5, 2)
                if (report['qualified'] == wrong or report['incorrect_http200'] != (report['started'] if wrong else 0)
                        or report['dropped'] != 0 or report['started'] < 9): raise ValueError('Controlled k6 result accounting failed')
                print(json.dumps({'case': label, 'started': report['started'], 'incorrect_http200': report['incorrect_http200'], 'qualified': report['qualified']}))
    finally:
        server.shutdown(); server.server_close(); thread.join()


if __name__ == '__main__': main()
