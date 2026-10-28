"""Freeze current OCI bytes while holding one nonurgent scorer demand lease."""
import hashlib
import os
from pathlib import Path
import threading
import time
import uuid
from urllib.parse import urljoin

import requests


def freeze_current(tile, destination, timeout=600):
    from storage import atomic_json
    import round2_oci as oci

    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    oci.DEST = destination
    folder = destination / tile
    folder.mkdir(exist_ok=True)
    client = f'score-{os.environ["TURKU3D_SANDBOX"]}-{os.getpid()}-{uuid.uuid4().hex[:8]}'
    params = dict(client=client, tiles=tile, urgent='', visible=tile)
    stopped = threading.Event()
    errors = []

    def heartbeat():
        while not stopped.wait(2):
            try:
                lease = requests.get(urljoin(oci.BASE, 'demand'), params=params, timeout=5)
                lease.raise_for_status()
            except requests.RequestException as error:
                errors.append(str(error))

    initial = requests.get(urljoin(oci.BASE, 'demand'), params=params, timeout=5)
    initial.raise_for_status()
    thread = threading.Thread(target=heartbeat, daemon=True)
    thread.start()
    started = time.monotonic()
    try:
        while time.monotonic() - started < timeout:
            response = requests.get(f'{oci.BASE}{tile}/manifest.json',
                params=dict(client=client, d=500), timeout=20)
            manifest = response.json()
            if response.status_code == 200 and not manifest.get('stale') and manifest.get('pass_kind') == 'full':
                visual = manifest['visual'][tile]
                payload = (folder / visual['url']).resolve()
                if not payload.is_relative_to(folder.resolve()):
                    raise ValueError('OCI visual escapes frozen tile')
                if not payload.exists() or hashlib.sha256(payload.read_bytes()).hexdigest() != visual['sha256']:
                    glb = requests.get(urljoin(f'{oci.BASE}{tile}/', visual['url']), timeout=90)
                    glb.raise_for_status()
                    if hashlib.sha256(glb.content).hexdigest() != visual['sha256']:
                        raise ValueError('OCI visual hash mismatch')
                    temporary = payload.with_suffix(f'.{os.getpid()}.tmp')
                    temporary.write_bytes(glb.content)
                    temporary.replace(payload)
                oci.textures(tile, folder, payload)
                atomic_json(folder / 'manifest.json', manifest)
                return dict(tile=tile, status='frozen', pass_kind='full', sha256=visual['sha256'],
                    path=str(payload), manifest_url=response.url, frozen_at=time.time(),
                    demand_client=client, demand_errors=errors, wait_seconds=time.monotonic()-started,
                    manifest_headers=dict(response.headers))
            if manifest.get('state') == 'failed':
                raise ValueError(f'OCI draft failed: {tile}')
            time.sleep(2)
        raise TimeoutError(f'Current full OCI draft not ready within {timeout}s: {tile}')
    finally:
        stopped.set()
        thread.join(timeout=6)
        try:
            requests.get(urljoin(oci.BASE, 'demand'),
                params=dict(client=client, tiles='', urgent='', visible=''), timeout=5)
        except requests.RequestException:
            pass
