#!/usr/bin/env python3
"""Delete the bulk run's UpCloud resources. NEVER run without Jonni's explicit OK.

  teardown.py results  --jonni-ok   detach and delete turku3d-bulk-results (100 GB) only
  teardown.py server   --jonni-ok   stop and delete turku3d-bulk with its 300 GB disk (later)

Each step names exactly the resources it deletes, acts only on turku3d-bulk's uuid and the
results storage's title, never on vm-control, and prints the inventory afterwards.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import upcloud  # noqa: E402

BULK_DISK = '01da8ead-8474-4745-9dfa-9442722ff869'  # turku3d-bulk-gen2-disk, 300 GB


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('what', choices=('results', 'server'))
    p.add_argument('--jonni-ok', action='store_true', help='required: Jonni explicitly approved this deletion')
    p.add_argument('--credential', type=Path, default=Path('~/.config/up_token'))
    a = p.parse_args()
    if not a.jonni_ok:
        raise SystemExit('refused: deleting UpCloud resources needs Jonni\'s explicit OK (--jonni-ok)')
    client = upcloud.Client(a.credential, log=Path.home() / 'turku3d/loops/perfgen/teardown-api.jsonl')
    storages = client.call('GET', '/storage/private')['storages']['storage']
    if a.what == 'results':
        found = [s for s in storages if s.get('title') == upcloud.RESULTS_TITLE]
        if len(found) != 1:
            raise SystemExit(f'expected exactly one {upcloud.RESULTS_TITLE}, found {len(found)}')
        uuid = found[0]['uuid']
        server = client.server()
        devices = {d['storage']: d['address'] for d in server['storage_devices']['storage_device']}
        if uuid in devices:
            client.stop()
            client.call('POST', f'/server/{upcloud.SERVER}/storage/detach', dict(storage_device=dict(address=devices[uuid])))
            client.start()
        client.call('DELETE', f'/storage/{uuid}', allow=True)
    else:
        client.stop()
        client.call('DELETE', f'/server/{upcloud.SERVER}?storages=1&backups=delete')
        if any(s['uuid'] == BULK_DISK for s in client.call('GET', '/storage/private')['storages']['storage']):
            client.call('DELETE', f'/storage/{BULK_DISK}', allow=True)
    print('\n'.join(client.inventory()))


if __name__ == '__main__':
    main()
