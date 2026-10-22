# Injects each fault with Helm, runs K8sGPT, saves raw results.
import json
import os
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHART = ROOT / 'chart' / 'demo-app'
FAULTS = ROOT / 'faults'
OUT = ROOT / 'results'
FILTERS = 'Pod,Deployment,Service,Ingress'
SETTLE = int(os.getenv('SETTLE_SECONDS') or '60')
BACKEND = os.getenv('K8SGPT_BACKEND') or 'google'
EXPLAIN = (os.getenv('EXPLAIN') or '1') == '1'


def sh(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


def parse_json(text):
    start = text.find('{')
    if start == -1:
        return None
    try:
        obj, _ = json.JSONDecoder().raw_decode(text[start:])
        return obj
    except json.JSONDecodeError:
        return None


def analyze(namespace):
    cmd = ['k8sgpt', 'analyze', '--namespace', namespace, '--filter', FILTERS, '--output', 'json']
    if EXPLAIN:
        cmd += ['--explain', '--backend', BACKEND]
    data, err = None, ''
    for attempt in range(3):
        p = sh(cmd)
        err = 'STDOUT: ' + p.stdout[-700:] + ' STDERR: ' + p.stderr[-700:]
        data = parse_json(p.stdout)
        if data is not None:
            results = data.get('results') or []
            missing = [r for r in results if EXPLAIN and not r.get('details')]
            if not missing:
                break
        time.sleep(20 * (attempt + 1))
    data = data or {'status': 'error'}
    data['results'] = data.get('results') or []
    return data, err


def run_case(case_id, values_file):
    namespace = 'bench-' + case_id
    started = time.time()
    cmd = ['helm', 'upgrade', '--install', 'demo', str(CHART), '--namespace', namespace, '--create-namespace']
    if values_file:
        cmd += ['-f', str(values_file)]
    else:
        cmd += ['--wait', '--timeout', '120s']
    helm = sh(cmd)
    time.sleep(SETTLE)
    data, err = analyze(namespace)
    sh(['kubectl', 'delete', 'namespace', namespace, '--wait=false'])
    return {
        'id': case_id,
        'helm_ok': helm.returncode == 0,
        'seconds': round(time.time() - started, 1),
        'status': data.get('status'),
        'results': data['results'],
        'stderr': err,
    }


def main():
    OUT.mkdir(exist_ok=True)
    cases = [('baseline', None)]
    for f in sorted(FAULTS.glob('*.yaml')):
        if f.stem != 'ground_truth':
            cases.append((f.stem, f))
            only = os.getenv('ONLY')
            if only:
                cases = [c for c in cases if c[0] == 'baseline' or c[0] in only.split(',')]
            results = []
    for case_id, values in cases:
        print('Running', case_id, flush=True)
        results.append(run_case(case_id, values))
        time.sleep(5)
    payload = {
        'backend': BACKEND,
        'model': os.getenv('LLM_MODEL') or 'unknown',
        'explain': EXPLAIN,
        'cases': results,
    }
    (OUT / 'raw_results.json').write_text(json.dumps(payload, indent=2))


if __name__ == '__main__':
    main()