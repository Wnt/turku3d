"""Relocate retained inventory pointers without changing immutable score evidence."""

import json
from pathlib import Path


def relocate(value, root):
    """Translate old sandbox pointers only when the durable target actually exists."""
    if isinstance(value, dict):
        return {key: relocate(item, root) for key, item in value.items()}
    if isinstance(value, list):
        return [relocate(item, root) for item in value]
    if isinstance(value, str) and value.startswith("/") and "/gmaps-round3/" in value:
        suffix = value.split("/gmaps-round3/", 1)[1]
        if ".." in Path(suffix).parts:
            return value
        destination = Path(root) / suffix
        if destination.resolve().is_relative_to(Path(root).resolve()) and destination.exists():
            return str(destination)
    return value


def restore_pointers(root):
    """Keep reports/manifests byte-identical: their hashes are part of paired evidence."""
    from storage import atomic_json

    root = Path(root)
    paths = [*root.glob("synthetic/*coverage.json"), root / "synthetic/proof.json"]
    paths += list(root.glob("synthetic/captures/*/capture.json"))
    paths += list(root.glob("synthetic/captures/*/oci-snapshot.json"))
    changed = 0
    for path in paths:
        if not path.exists():
            continue
        original = json.loads(path.read_text())
        restored = relocate(original, root)
        if restored != original:
            atomic_json(path, restored)
            changed += 1
    return changed


def inventory(root):
    """Portable manifest of capture audits and referenced-body presence; never reuse old scores."""
    import hashlib
    import os
    from collections import Counter

    root = Path(root)
    plan = json.loads((root / 'synthetic/expanded-plan.json').read_text())
    quarters = {}
    for row in plan['quarters']:
        key = row['coverage_id']
        path = root / 'synthetic/captures' / key / 'capture.json'
        if not path.exists():
            quarters[key] = dict(status='missing', tile=row['tile'])
            continue
        payload = path.read_bytes()
        record = json.loads(payload)
        present = {entry.name for entry in os.scandir(path.parent / 'responses') if entry.is_file()}
        missing = [item['file'] for item in record['responses'] if Path(item['file']).name not in present]
        proof = record.get('detail_progress', {})
        complete = record.get('inventory_state') == 'eligible' and proof.get('complete') is True and not missing
        quarters[key] = dict(status='eligible' if complete else 'incomplete', tile=row['tile'],
                             capture=str(path.relative_to(root)), capture_sha256=hashlib.sha256(payload).hexdigest(),
                             responses=len(record['responses']), missing_response_files=len(missing),
                             metadata_complete=bool(proof.get('metadata_complete')),
                             terminal_nodes=proof.get('available', proof.get('terminal_data_nodes')),
                             loaded=proof.get('loaded', proof.get('terminal_loaded')))
    return dict(version=1, expected_scored_quarters=57, expected_calibration_abstentions=3,
                method='Retained independent completeness audits plus referenced-body presence; scores must be rerun',
                states=dict(Counter(row['status'] for row in quarters.values())), quarters=quarters)


def main():
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, type=Path)
    args = parser.parse_args()
    result = inventory(args.root)
    target = args.root / 'reference-inventory.json'
    temporary = target.with_suffix('.tmp')
    temporary.write_text(json.dumps(result, indent=2) + '\n')
    temporary.replace(target)
    print(json.dumps(dict(path=str(target), states=result['states'])))


if __name__ == '__main__':
    main()
