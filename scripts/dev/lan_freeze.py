"""Operator-owned LAN generator freeze; client-only deployments remain available."""

import argparse
import json
import shlex

from facade_publish import remote

MARKER = "/home/ubuntu/turku3d-drafts/LAN_FREEZE.json"
DEFAULT_CUTOFF = "2026-10-30T04:00:00+02:00"


def guard_command(candidate=None):
    return "python3 -c " + shlex.quote(f"""
import datetime,json,pathlib
p=pathlib.Path({MARKER!r})
if p.exists():
 r=json.loads(p.read_text())
 if datetime.datetime.now(datetime.timezone.utc) >= datetime.datetime.fromisoformat(r['freeze_at']):
  candidate={candidate!r}
  if candidate and r.get('source'):
   def contents(root):
    root=pathlib.Path(root)
    result={{}}
    for name in ('pipeline','textures','game/public/basis'):
     directory=root/name
     if not directory.is_dir():raise SystemExit('Missing frozen source directory: '+name)
     for f in directory.rglob('*'):
      if f.is_file() and '__pycache__' not in f.parts and f.suffix!='.pyc':
       result[str(f.relative_to(root))]=f.read_bytes()
    return result
   if contents(candidate)==contents(r['source']):
    print('Frozen generator sources unchanged; serving-only deployment allowed')
    raise SystemExit(0)
  raise SystemExit('LAN generator freeze active since '+r['freeze_at']+'; publish client-only Pages fixes. Coordinate a forward generator exception with orchestrator.')
""")


def arm(cutoff):
    from datetime import datetime

    at = datetime.fromisoformat(cutoff)
    if at.tzinfo is None:
        raise ValueError("Freeze cutoff must include its UTC offset")
    record = dict(freeze_at=at.isoformat(), owner="lan-party-readiness")
    return remote(
        "python3 -c "
        + shlex.quote(f"""
import datetime,json,pathlib
p=pathlib.Path({MARKER!r})
if p.exists():
 old=json.loads(p.read_text())
 if old.get('commit') or datetime.datetime.now(datetime.timezone.utc)>=datetime.datetime.fromisoformat(old['freeze_at']):
  raise SystemExit('Existing freeze is active; coordinate an explicit forward exception with orchestrator')
r={record!r}
t=p.with_suffix('.tmp');t.write_text(json.dumps(r)+'\\n');t.replace(p)
print(json.dumps(r))
""")
    )


def capture():
    return json.loads(
        remote(
            "python3 -c "
            + shlex.quote(f"""
import datetime,json,pathlib
p=pathlib.Path({MARKER!r});r=json.loads(p.read_text())
now=datetime.datetime.now(datetime.timezone.utc)
if now<datetime.datetime.fromisoformat(r['freeze_at']):raise SystemExit('Freeze cutoff has not arrived')
home=p.parent
commit=(home/'SOURCE_COMMIT').read_text().strip()
if r.get('commit') and r['commit']!=commit:raise SystemExit('FROZEN GENERATOR CHANGED: '+r['commit']+' -> '+commit)
if not r.get('commit'):
 r.update(commit=commit,captured_at=now.isoformat(),source=str((home/'repo').resolve()),
          active_unit=(home/'ACTIVE_UNIT').read_text().strip(),
          code=json.loads(pathlib.Path('/srv/turku3d/work/coarse/.scheduler.json').read_text())['code'])
 t=p.with_suffix('.tmp');t.write_text(json.dumps(r,indent=2)+'\\n');t.replace(p)
print(json.dumps(r))
""")
        )
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["arm", "capture", "status"])
    parser.add_argument("--freeze-at", default=DEFAULT_CUTOFF)
    args = parser.parse_args()
    if args.action == "arm":
        print(arm(args.freeze_at))
    elif args.action == "capture":
        print(json.dumps(capture()))
    else:
        print(remote("cat " + MARKER))


if __name__ == "__main__":
    main()
