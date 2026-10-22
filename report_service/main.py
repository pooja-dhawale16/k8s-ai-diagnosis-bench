from pathlib import Path
from typing import Any

import yaml
from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

app = FastAPI(title='K8s AI Diagnosis Report Service')
TRUTH = Path(__file__).resolve().parent.parent / 'faults' / 'ground_truth.yaml'
STATE: dict = {}
LABEL = {
    'correct': '✅ correct',
    'partial': '⚠️ partial',
    'missed': '❌ missed',
    'no_explanation': '❔ no explanation',
}


class RawResults(BaseModel):
    backend: str
    model: str = 'unknown'
    explain: bool = True
    cases: list[dict[str, Any]]


def load_truth():
    items = yaml.safe_load(TRUTH.read_text())
    return {item['id']: item for item in items}


def details_text(case):
    parts = [r.get('details') or '' for r in (case.get('results') or [])]
    return ' '.join(parts).lower()


def verdict_for(case, info):
    if not case.get('results'):
        return 'missed'
    text = details_text(case)
    if not text.strip():
        return 'no_explanation'
    if any(k in text for k in info['root_cause_keywords']):
        return 'correct'
    return 'partial'


def build_report(raw):
    truth = load_truth()
    rows = []
    baseline = None
    for case in raw.cases:
        if case['id'] == 'baseline':
            baseline = case
            continue
        info = truth.get(case['id'])
        if info is None:
            continue
        verdict = verdict_for(case, info)
        first = ''
        for r in case.get('results') or []:
            if r.get('details'):
                first = r['details']
                break
        rows.append({
            'id': case['id'],
            'description': info['description'],
            'detected': verdict != 'missed',
            'verdict': verdict,
            'problems': len(case.get('results') or []),
            'ai_explanation': first,
            'expected_fix': info['expected_fix'],
            'seconds': case.get('seconds'),
        })
    summary = {
        'total': len(rows),
        'detected': sum(1 for r in rows if r['detected']),
        'correct': sum(1 for r in rows if r['verdict'] == 'correct'),
        'false_positives': len(baseline.get('results') or []) if baseline else 0,
    }
    return {'backend': raw.backend, 'model': raw.model, 'summary': summary, 'rows': rows}


def clean(text, limit=220):
    text = ' '.join((text or '').split()).replace('|', '/')
    return text[:limit] + ('...' if len(text) > limit else '')


def to_markdown(rep):
    s = rep['summary']
    lines = [
        '## 🤖 K8s AI Diagnosis Benchmark',
        '',
        'Backend: ' + rep['backend'] + ' / model: ' + rep['model'],
        '',
        '- Faults detected: **' + str(s['detected']) + '/' + str(s['total']) + '**',
        '- AI root cause correct (keyword-scored): **' + str(s['correct']) + '/' + str(s['total']) + '**',
        '- False positives on healthy baseline: **' + str(s['false_positives']) + '**',
        '',
        '| Fault | Detected | AI verdict | AI explanation (shortened) |',
        '|---|---|---|---|',
    ]
    for r in rep['rows']:
        detected = 'yes' if r['detected'] else 'no'
        lines.append('| ' + r['id'] + ' | ' + detected + ' | ' + LABEL[r['verdict']] + ' | ' + clean(r['ai_explanation']) + ' |')
    lines += ['', '_Scoring is automatic and keyword-based. See the README for limitations._']
    return '\n'.join(lines)


@app.get('/health')
def health():
    return {'status': 'ok'}


@app.post('/report')
def create_report(raw: RawResults):
    STATE['report'] = build_report(raw)
    return STATE['report']


def latest():
    if 'report' not in STATE:
        raise HTTPException(status_code=404, detail='no report yet')
    return STATE['report']


@app.get('/report')
def get_report():
    return latest()


@app.get('/report/markdown', response_class=PlainTextResponse)
def get_markdown():
    return to_markdown(latest())


@app.get('/metrics', response_class=PlainTextResponse)
def metrics():
    s = latest()['summary']
    out = [
        '# TYPE k8s_ai_faults_total gauge',
        'k8s_ai_faults_total ' + str(s['total']),
        '# TYPE k8s_ai_faults_detected gauge',
        'k8s_ai_faults_detected ' + str(s['detected']),
        '# TYPE k8s_ai_diagnosis_correct gauge',
        'k8s_ai_diagnosis_correct ' + str(s['correct']),
        '# TYPE k8s_ai_false_positives gauge',
        'k8s_ai_false_positives ' + str(s['false_positives']),
    ]
    return '\n'.join(out) + '\n'