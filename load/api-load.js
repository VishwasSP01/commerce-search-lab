import http from 'k6/http';
import exec from 'k6/execution';
import { SharedArray } from 'k6/data';
import { Counter, Trend } from 'k6/metrics';

const workload = new SharedArray('queries', () => JSON.parse(open(__ENV.WORKLOAD)));
const base = __ENV.BASE_URL || 'http://127.0.0.1:18080';
if (!/^http:\/\/127\.0\.0\.1:[0-9]+$/.test(base)) throw new Error('Load target must be explicit loopback HTTP');
const rate = Number(__ENV.RATE), duration = Number(__ENV.DURATION), vus = Number(__ENV.VUS || 64);
if (!Number.isInteger(rate) || rate < 1 || rate > 1000 || !Number.isInteger(duration) || duration < 1 || duration > 60 || vus < 1 || vus > 64) throw new Error('Invalid bounded load parameters');
const started = new Counter('api_started');
const completed = new Counter('api_completed');
const wrong = new Counter('api_wrong_results');
const elapsed = new Trend('api_attempt_ms', true);

export const options = {
  scenarios: { search: { executor: 'constant-arrival-rate', rate, timeUnit: '1s', duration: `${duration}s`, preAllocatedVUs: vus, maxVUs: vus, gracefulStop: '5s' } },
  thresholds: { api_wrong_results: ['count==0'] },
  summaryTrendStats: ['avg', 'min', 'med', 'p(95)', 'p(99)', 'max'],
};

export default function () {
  const item = workload[exec.scenario.iterationInTest % workload.length];
  const tags = { locale: item.locale, query_id: String(item.query_id) };
  started.add(1, tags);
  const begin = Date.now();
  const response = http.post(`${base}/v1/search`, JSON.stringify({ query: item.query, locale: item.locale, limit: 10 }), {
    headers: { 'Content-Type': 'application/json' }, timeout: '3s', redirects: 0, tags: { name: 'product_search', locale: item.locale },
  });
  const durationMs = Date.now() - begin;
  let body = null, valid = false, code = '';
  try { body = response.json(); } catch (_) { /* Classify malformed responses below. */ }
  if (response.status === 200) {
    valid = body !== null && body.locale === item.locale && body.limit === 10 && body.retrieval === 'bm25'
      && Array.isArray(body.hits) && body.returned === item.expected.length && body.hits.length === item.expected.length;
    if (valid) {
      for (let i = 0; i < item.expected.length; i++) {
        if (body.hits[i].productId !== item.expected[i][0] || body.hits[i].score !== item.expected[i][1]) { valid = false; break; }
      }
    }
  } else if (response.status === 0) code = `transport_${response.error_code}`;
  else code = body && /^[A-Z0-9_]{1,64}$/.test(body.code || '') ? body.code : 'INVALID_ERROR_BODY';
  elapsed.add(durationMs, { ...tags, status: String(response.status), ok: String(valid), code });
  wrong.add(response.status === 200 && !valid ? 1 : 0);
  completed.add(1);
}

export function handleSummary(data) { return { [__ENV.SUMMARY]: JSON.stringify(data) }; }
