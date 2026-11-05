#!/bin/bash
# Stage a bulk host for a city-wide migration window, from the Mac. No persistent UpCloud storage between runs:
# each run creates the host, stages it from osgallery-dev, and deletes it WITH its disk afterwards.
#   stage.sh all --train ALIAS [--train ALIAS ...] --smoke-tile TILE   create, bootstrap, upload, verify, smoke
#   stage.sh create [--disk GB]   CLOUDNATIVE-1xCPU-4GB + GB (default 260) Standard disk in fi-hel1; writes server.json
#   stage.sh bootstrap            user wnt, swap, rsync, the gen2 TTL kit, host scripts, a $TIP checkout; tools and
#                                 server.json to osgallery-dev
#   stage.sh upload --train ...   upload.sh on osgallery-dev (parallel rsync, nice 19/ionice idle); prints the rate
#   stage.sh verify               catalog + sha256 check on the host (verify_stage.py); writes manifest.json
#   stage.sh smoke --train A --smoke-tile T   A's whole chain on T from the base, compared with osgallery-dev's T
#   stage.sh grow GB              stop, resize the disk to GB, start, grow the filesystem (before a window)
#   stage.sh delete               stop and DELETE the host with its storage (asks for "yes"; only server.json's uuid)
# State (server.json, manifest, verify output) lives in $BULK_STATE (default ~/turku3d/bulk-gen3). The UpCloud token
# is ~/.config/up_token and is never printed. This never changes the plan: the 64-core window is window.py up on
# osgallery-dev, only with Jonni's OK.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
G=${BULK_STATE:-$HOME/turku3d/bulk-gen3}
TITLE=${BULK_TITLE:-turku3d-bulk3}
OSG=osgallery-dev
OD=turku3d-local/loops/bulkgen3                  # on osgallery-dev, relative to its home
F=/home/wnt/turku3d-local/loops/tiles/farm
API=https://api.upcloud.com/1.3
TIP=${TIP:-local/loop-integration}
CMD=${1:-}; shift || true
TRAINS=(); TILES=(); DISK=260
while [ $# -gt 0 ]; do
  case "$1" in
    --train) TRAINS+=("$2"); shift 2 ;;
    --smoke-tile) TILES+=("$2"); shift 2 ;;
    --disk) DISK=$2; shift 2 ;;
    *) [ "$CMD" = grow ] && [ -z "${GROW:-}" ] && { GROW=$1; shift; continue; }; echo "unknown argument $1"; exit 2 ;;
  esac
done
mkdir -p "$G"
api() { curl -sf -H "Authorization: Bearer $(cat ~/.config/up_token)" -H 'Content-Type: application/json' "$@"; }
conf() { python3 -c "import json; print(json.load(open('$G/server.json'))['$1'])"; }
IP() { conf ip; }
UUID() { conf uuid; }
state() { api "$API/server/$(UUID)" | python3 -c 'import json,sys; print(json.load(sys.stdin)["server"]["state"])'; }
until_state() { until [ "$(state)" = "$1" ]; do sleep 5; done; }
need_trains() { [ ${#TRAINS[@]} -ge 1 ] || { echo "--train ALIAS required"; exit 2; }; }
resolved() { ssh $OSG "python3 $OD/bin/resolve.py ${TRAINS[*]}"; }

create() {
  if [ -f "$G/server.json" ] && api "$API/server/$(UUID)" > /dev/null 2>&1; then echo "exists: $(UUID) $(IP)"; return; fi
  osg=$(ssh $OSG 'test -f ~/.ssh/bulk_gen3 || ssh-keygen -q -t ed25519 -N "" -C osgallery-dev-bulk -f ~/.ssh/bulk_gen3
                  cat ~/.ssh/bulk_gen3.pub')
  body=$(TITLE=$TITLE python3 - "$osg" "$DISK" <<'PY'
import json, os, sys
keys = [open(os.path.expanduser('~/.ssh/id_rsa.pub')).read().strip(), sys.argv[1].strip()]
print(json.dumps({"server": {"zone": "fi-hel1", "title": os.environ["TITLE"], "hostname": os.environ["TITLE"],
  "plan": "CLOUDNATIVE-1xCPU-4GB", "metadata": "yes", "firewall": "off",
  "storage_devices": {"storage_device": [{"action": "clone", "storage": "01000000-0000-4000-8000-000030240200",
    "title": os.environ["TITLE"] + "-disk", "size": int(sys.argv[2]), "tier": "standard"}]},
  "networking": {"interfaces": {"interface": [{"ip_addresses": {"ip_address": [{"family": "IPv4"}]}, "type": "public"}]}},
  "login_user": {"username": "root", "create_password": "no", "ssh_keys": {"ssh_key": keys}}}}))
PY
)
  api -X POST --data "$body" "$API/server" | python3 -c "
import json, sys, time
s = json.load(sys.stdin)['server']
ip = [i['address'] for i in s['ip_addresses']['ip_address'] if i['access'] == 'public'][0]
json.dump(dict(uuid=s['uuid'], title=s['title'], plan=s['plan'], ip=ip, zone='fi-hel1',
               storage=[d['storage'] for d in s['storage_devices']['storage_device']], disk_gb=$DISK, tier='standard',
               created=time.strftime('%F %T UTC', time.gmtime())), open('$G/server.json', 'w'), indent=1)
print('created', s['uuid'], ip)"
  until_state started
  ssh-keygen -R "$(IP)" > /dev/null 2>&1 || true
  ssh $OSG "ssh-keygen -R $(IP) > /dev/null 2>&1 || true"
  until ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=5 "root@$(IP)" true 2>/dev/null; do sleep 5; done
}

bootstrap() {
  H=root@$(IP)
  ssh "$H" 'set -e; export DEBIAN_FRONTEND=noninteractive
    id wnt >/dev/null 2>&1 || useradd -m -u 1000 -s /bin/bash wnt
    apt-get -qq update >/dev/null; apt-get -qq install -y rsync fio time >/dev/null
    [ -f /swapfile ] || { fallocate -l 8G /swapfile; chmod 600 /swapfile; mkswap -q /swapfile; echo "/swapfile none swap sw 0 0" >> /etc/fstab; }
    swapon /swapfile 2>/dev/null || true
    mkdir -p /home/wnt/turku3d-local/loops/tiles/farm /bulk/turku3d/lidarpc/raw/pistepilvi /home/wnt/turku3d-bulk-gen3
    git config --global --add safe.directory "*"'
  rsync -a "$HERE/../gen2/ttl/" "$H:/root/ttl/" && ssh "$H" 'chmod +x /root/ttl/*.sh && /root/ttl/install.sh'
  rsync -a "$HERE/window-pass.sh" "$HERE/trim-decoded.py" "$HERE/verify_stage.py" "$H:/home/wnt/turku3d-bulk-gen3/"
  tmp=$(mktemp -d)
  git clone -q --depth 1 --branch "$TIP" "file://$(git -C "$HERE" rev-parse --path-format=absolute --git-common-dir)" "$tmp/repo"
  rsync -a --delete "$tmp/repo/" "$H:/home/wnt/turku3d-bulk-gen3/repo/"; rm -rf "$tmp"
  ssh "$H" 'chown -R 1000:1000 /home/wnt /bulk; git -C /home/wnt/turku3d-bulk-gen3/repo log -1 --format="checkout %H %s"'
  ssh $OSG "mkdir -p $OD/bin $OD/window"
  rsync -a "$HERE/upload.sh" "$HERE/sync-train.sh" "$HERE/resolve.py" "$OSG:$OD/bin/"
  rsync -a "$HERE/window.py" "$HERE/upcloud.py" "$OSG:$OD/window/"
  rsync -a "$G/server.json" "$OSG:$OD/server.json"
}

used() { ssh "root@$(IP)" "df -B1 --output=used / | tail -1"; }
upload() {
  need_trains; a=$(used); t0=$(date +%s)
  ssh $OSG "cd $OD && mv -f upload.log upload.log.prev 2>/dev/null; setsid -f nohup bin/upload.sh $(IP) ${TRAINS[*]} \
            > upload.out 2>&1 < /dev/null"
  until ssh $OSG "grep -q 'all done' $OD/upload.log 2>/dev/null"; do sleep 60; done
  b=$(used); t=$(( $(date +%s) - t0 ))
  ssh $OSG "tail -2 $OD/upload.log"
  python3 -c "print(f'staged {($b-$a)/1e9:.1f} GB in {$t/60:.1f} min: {($b-$a)/$t/1e6:.1f} MB/s')"
}

verify() {
  H=root@$(IP); D=/home/wnt/turku3d-bulk-gen3/verify
  V=$(resolved | python3 -c "import json,sys; print(next(iter(json.load(sys.stdin)['source_versions'].values())))")
  ssh $OSG "python3 -c \"
import json, os
V = '$V'
f = json.load(open(V + '/source-version.json'))['files']
print('\n'.join(p for p in f if os.path.isfile(os.path.join(V, p))))\"" > "$G/presence.txt"
  # The host lists what it received outside the catalog trees; osgallery-dev hashes those same paths.
  ssh "$H" "find $F/products $F/revisions /home/wnt/turku3d-bulk-warmup/envs /home/wnt/turku3d-bulk-warmup/tools -type f \
            ! -path '*/.rsync-partial/*' ! -path '*/__pycache__/*' ! -path '*/.stage/*'" > "$G/files-host.txt"
  ssh $OSG "cd / && nice -n19 ionice -c3 xargs -d '\n' -a /dev/stdin -P4 -n200 sha256sum 2>/dev/null" \
    < "$G/files-host.txt" > "$G/sums-osg.txt" || true
  # A path the farm changed after the copy (a live spot rebuild, a base tile being built) cannot be compared.
  comm -23 <(sort "$G/files-host.txt") <(sed 's/^[0-9a-f]*  //' "$G/sums-osg.txt" | sort) > "$G/gone-on-osg.txt"
  echo "not comparable (gone on osgallery-dev since the copy): $(wc -l < "$G/gone-on-osg.txt")"
  ssh "$H" "mkdir -p $D"; rsync -a "$G/presence.txt" "$G/sums-osg.txt" "$H:$D/"
  ssh "$H" "python3 /home/wnt/turku3d-bulk-gen3/verify_stage.py $D $V" || true
  rsync -a "$H:$D/verify.json" "$G/verify.json"
  python3 - "$G" <<'PY'
import json, sys, time
g = sys.argv[1]; v = json.load(open(f'{g}/verify.json'))
json.dump(dict(made=time.strftime('%FT%TZ', time.gmtime()), verify=v,
               note='source version + survey blocks against source-version.json (size+sha256); products, revisions '
                    'and env against sha256 of the same paths on osgallery-dev (sums-osg.txt). Live farm '
                    'changes after the copy show as sums failures; sync-train.sh mirrors them before a window.'),
          open(f'{g}/manifest.json', 'w'), indent=1)
print('manifest ok' if v['ok'] else 'manifest NOT ok: read verify.json', f'{g}/manifest.json')
PY
}

smoke() {
  need_trains; [ ${#TILES[@]} -ge 1 ] || { echo "--smoke-tile TILE required (repeatable)"; exit 2; }
  H=root@$(IP); A=${TRAINS[0]}
  read -r REV CODE GEN STEPS < <(resolved | python3 -c "
import json, sys; a = json.load(sys.stdin)['aliases']['$A']
print(a['revision'], a['code'], a['generation'], ','.join(a['steps']))")
  # The whole chain from the base: remove the host's view of the alias's outputs for the tiles first (on the cached
  # mount that is a whiteout in the local overlay; osgallery-dev keeps its copy as the reference).
  # The pass also skips tiles its journal lists as finished: drop their rows from the host's copy of the journal.
  ssh "$H" "for t in ${TILES[*]}; do for g in ${STEPS//,/ }; do rm -rf $F/products/\$g/\$t; done; done
            J=$F/migrations/$GEN/pass.jsonl
            [ ! -f \$J ] || su wnt -c \"python3 -c 'import json,os,sys; p=sys.argv[1]; k=set(sys.argv[2:]); \
r=[l for l in open(p) if json.loads(l).get(\\\"tile\\\") not in k]; open(p+\\\".tmp\\\",\\\"w\\\").writelines(r); \
os.replace(p+\\\".tmp\\\",p)' \$J ${TILES[*]}\""
  rx() { ssh "$H" "cat /sys/class/net/wg0/statistics/rx_bytes 2>/dev/null || echo 0"; }
  b0=$(rx); t0=$(date +%s)
  ssh "$H" "cd $F/revisions/$CODE/repo && /usr/bin/time -v su wnt -c \
    'REPO=$F/revisions/$CODE/repo TURKU3D_WORK=$F/work/migrate TURKU3D_THREADS_PER_WORKER=1 \
     /home/wnt/turku3d-local/loops/tiles/shared-tiles-run.sh python scripts/dev/tile_city_pass.py --farm-root $F \
     --revision $REV --max-jobs ${SMOKE_LANES:-1} --blocks ${SMOKE_BLOCKS:-1} --high 99 --low 98 --interval 5 \
     --survey-interval 30 --min-free-mem-gb 0.5 --checkpoint wires@2 --supply /bulk/turku3d/lidarpc/raw/pistepilvi \
     --farm-url http://127.0.0.1:9 ${TILES[*]}' 2>&1 | grep -E 'Elapsed|Maximum resident|Traceback|Error' ;
     tail -${#TILES[@]} $F/migrations/$GEN/pass.jsonl | cut -c1-300"
  t=$(( $(date +%s) - t0 )); b=$(( $(rx) - b0 ))
  python3 -c "print(f'smoke: {len(\"${TILES[*]}\".split())} tiles in {$t} s; tunnel received {$b/1e9:.2f} GB ({$b/max($t,1)/1e6:.1f} MB/s)')"
  for TILE in "${TILES[@]}"; do
    sums() { ssh "$1" "cd $F/products/$GEN/$TILE 2>/dev/null && find . -type f ! -name ready.json ! -name stage-times.json \
                       -print0 | sort -z | xargs -0 sha256sum"; }
    sums "$H" > "$G/smoke-host-$TILE.txt"; sums $OSG > "$G/smoke-osg-$TILE.txt" || true
    if [ ! -s "$G/smoke-host-$TILE.txt" ]; then echo "smoke: $TILE has no $A product on the host"
    elif [ ! -s "$G/smoke-osg-$TILE.txt" ]; then echo "smoke: $TILE migrated; osgallery-dev has no $A product of it to compare"
    elif diff -q "$G/smoke-osg-$TILE.txt" "$G/smoke-host-$TILE.txt" > /dev/null; then
      echo "smoke: $TILE byte-identical to osgallery-dev ($(wc -l < "$G/smoke-host-$TILE.txt") files)"
    else echo "smoke: $TILE differs:"; diff "$G/smoke-osg-$TILE.txt" "$G/smoke-host-$TILE.txt" | head -20; fi
  done
}

grow() {
  [ -n "${GROW:-}" ] || { echo "grow GB"; exit 2; }
  api -X POST --data '{"stop_server":{"stop_type":"soft","timeout":"60"}}' "$API/server/$(UUID)/stop" > /dev/null
  until_state stopped
  api -X PUT --data "{\"storage\":{\"size\":$GROW}}" "$API/storage/$(python3 -c "import json; print(json.load(open('$G/server.json'))['storage'][0])")" > /dev/null
  api -X POST "$API/server/$(UUID)/start" > /dev/null; until_state started
  until ssh -o ConnectTimeout=5 "root@$(IP)" true 2>/dev/null; do sleep 5; done
  ssh "root@$(IP)" 'growpart /dev/vda 2; resize2fs /dev/vda2 > /dev/null; df -h / | tail -1'
}

delete() {
  read -r -p "Delete $(UUID) ($(IP)) WITH its storage? Type yes: " ok; [ "$ok" = yes ] || exit 1
  api -X POST --data '{"stop_server":{"stop_type":"soft","timeout":"60"}}' "$API/server/$(UUID)/stop" > /dev/null || true
  until_state stopped
  api -X DELETE "$API/server/$(UUID)?storages=1&backups=delete" && echo deleted &&
    mv "$G/server.json" "$G/server.deleted-$(date +%s).json"
}

case "$CMD" in
  create|bootstrap|upload|verify|smoke|grow|delete) "$CMD" ;;
  all) create; bootstrap; upload; verify; smoke ;;
  *) sed -n 2,16p "$0"; exit 2 ;;
esac
