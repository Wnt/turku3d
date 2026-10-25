"""One worker budget for sandbox tools."""

import os


def jobs(env=None, cores=None):
    env = os.environ if env is None else env
    cores = (os.cpu_count() or 1) if cores is None else cores
    value = env.get("TURKU3D_JOBS", str(max(2, cores // 3)))
    if not value.isdecimal() or int(value) < 1:
        raise ValueError("TURKU3D_JOBS must be a positive integer")
    return int(value)


if __name__ == "__main__":
    print(jobs())
