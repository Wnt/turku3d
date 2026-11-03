"""Upload an immutable ``git archive`` snapshot of a commit to the farm (never git push)."""
import json
import os
import shlex
import subprocess
import tempfile


def upload(revision, metadata, *, host, root):
    """Install ROOT/revisions/REVISION atomically; an existing snapshot is kept as is."""
    target = root + '/revisions/' + revision
    q = shlex.quote
    if subprocess.run(['ssh', host, 'test -f ' + q(target + '/revision.json')]).returncode == 0:
        return False
    temp = target + '.upload-' + str(os.getpid())
    subprocess.run(['ssh', host, 'mkdir -p ' + q(temp + '/repo')], check=True)
    archive = subprocess.Popen(['git', 'archive', revision], stdout=subprocess.PIPE)
    unpack = subprocess.run(['ssh', host, 'tar -x -C ' + q(temp + '/repo')], stdin=archive.stdout)
    archive.stdout.close()
    if archive.wait() or unpack.returncode:
        raise RuntimeError('snapshot upload failed')
    with tempfile.NamedTemporaryFile('w') as file:
        json.dump(metadata, file)
        file.flush()
        subprocess.run(['scp', '-q', file.name, host + ':' + temp + '/revision.json'], check=True)
    subprocess.run(['ssh', host,
                    'mv -T ' + q(temp) + ' ' + q(target) + ' 2>/dev/null || { test -f ' +
                    q(target + '/revision.json') + ' && rm -rf ' + q(temp) + '; }'], check=True)
    return True
