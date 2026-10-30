"""Shared live generator signal for codex-task checks and the orchestrator sentinel."""

import json
import urllib.request

# Jonni's healthy thresholds for the textured (full) pass.
MIN_SUCCESS = .99
MAX_P50_SECONDS = 10

URL = 'https://turku.lab.madekivi.fi/coarse/health'


def alerts(health):
    signal = health.get('build_health')
    if signal is None:
        return ['generator build-health signal missing']
    reasons = []
    full = signal['passes']['full']
    if signal['players_online']:
        if full['success_rate'] is not None and full['success_rate'] < MIN_SUCCESS:
            reasons.append(f"full-pass success {full['success_rate']:.0%} < {MIN_SUCCESS:.0%}")
        if full['p50_seconds'] is not None and full['p50_seconds'] > MAX_P50_SECONDS:
            reasons.append(f"full-pass p50 {full['p50_seconds']:.1f}s > {MAX_P50_SECONDS}s")
    errors = {}
    for row in signal['passes'].values():
        for kind, count in row['error_kinds'].items():
            errors[kind] = errors.get(kind, 0) + count
    reasons.extend(f'repeated {kind}: {count}' for kind, count in sorted(errors.items()) if count > 1)
    return reasons


def smoke_alerts(health):
    reasons = alerts(health)
    signal = health.get('build_health')
    if signal is not None:
        full = signal['passes']['full']
        if full['failed']:
            reasons.append(f"recent full passes: {full['failed']} failed")
        if full['p50_seconds'] is not None and full['p50_seconds'] > MAX_P50_SECONDS:
            reason = f"full-pass p50 {full['p50_seconds']:.1f}s > {MAX_P50_SECONDS}s"
            if reason not in reasons:
                reasons.append(reason)
    return reasons


def fetch():
    with urllib.request.urlopen(URL, timeout=2) as response:
        return json.load(response)


def sentinel_events():
    try:
        return ['generator health: ' + reason for reason in alerts(fetch())]
    except (OSError, ValueError, KeyError) as error:
        return ['generator health unavailable: ' + type(error).__name__]


def print_live():
    try:
        health = fetch()
        print(json.dumps(dict(build_health=health.get('build_health'), alerts=alerts(health))))
    except (OSError, ValueError, KeyError) as error:
        print('generator health unavailable: ' + type(error).__name__)


if __name__ == '__main__':
    print_live()
