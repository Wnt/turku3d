"""Shared client-cache routes; run this batch under host_slots.py browser admission."""
import os
import subprocess


def main():
    env = dict(os.environ)
    # Test the published worker. Preview injection can be superseded by the
    # browser's automatic worker update and is not deployment evidence.
    for name in ('ARRIVAL_WORKER', 'IMMUTABLE_WORKER'):
        env.pop(name, None)
    # The exact-byte restart/offline case already passed in the initial deployed
    # run. Keep this remaining selection serial under one explicit GPU reservation.
    commands = [('game/scripts/tile-arrival-profile.mjs', label, seconds, speed)
                 for label, seconds, speed in [('deployed-50', '120', '50'),
                                                ('deployed-200', '60', '200'),
                                                ('deployed-fast-fly', '30', '500')]]
    env['ARRIVAL_PROFILE'] = 'deployed-immutable'
    env['ARRIVAL_REQUIRE_CACHE'] = '1'
    for command in commands:
        result = subprocess.run(['node', *command], env=env)
        if result.returncode:
            return result.returncode
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
