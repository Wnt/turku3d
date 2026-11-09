"""Low-cost terrain beyond the city survey, from attributed public elevation/land cover."""
from functools import lru_cache
from pathlib import Path
import numpy as np
from scipy.ndimage import map_coordinates
from turku3d import config as C
from .far_ground import survey_colours

@lru_cache(maxsize=2)
def grid(path):
    with np.load(path) as data:
        return {key: data[key] for key in data.files}

def covers(tile, path):
    data = grid(str(path)); step = float(data['step'])
    x,z = float(data['xmin']),float(data['zmin'])
    rows,cols = data['height'].shape
    return (tile.bbox.xmin >= x and tile.bbox.zmin >= z
            and tile.bbox.xmax <= x+(cols-1)*step and tile.bbox.zmax <= z+(rows-1)*step)

def regional_palette():
    """Linear RGB of the regional land-cover classes (ESA WorldCover codes): the regional terrain's vertex colours."""
    return {10:(.12,.19,.08),20:(.20,.25,.12),30:(.24,.29,.16),40:(.32,.34,.16),
            50:survey_colours()['built'],60:(.38,.35,.28),70:(.75,.78,.80),80:(.10,.19,.24),90:(.18,.26,.16),95:(.12,.19,.08),100:(.24,.29,.16)}

def mesh(tile, path):
    from .horizon import layer_mesh
    data = grid(str(path))
    if not covers(tile,path): raise ValueError(f'{tile.id}: outside regional source grid')
    step = 62.5 if tile.level >= 4 else 31.25 if tile.level >= 2 else 15.625
    count = int(round(tile.size/step))
    xs=np.linspace(tile.bbox.xmin,tile.bbox.xmax,count+1)
    zs=np.linspace(tile.bbox.zmin,tile.bbox.zmax,count+1)
    gx,gz=np.meshgrid(xs,zs)
    sample=np.array([(gz.ravel()-data['zmin'])/data['step'],(gx.ravel()-data['xmin'])/data['step']])
    h=map_coordinates(data['height'],sample,order=1,mode='nearest')
    cover=map_coordinates(data['cover'],sample,order=0,mode='nearest')
    h[cover==80]=0
    p=np.c_[gx.ravel(),h,gz.ravel()]
    palette=regional_palette()
    if not np.isin(cover,list(palette)).all(): raise ValueError('Unknown regional land-cover category')
    colours=np.array([palette[int(kind)] for kind in cover],dtype=np.float32)
    a=(np.arange(count)[:,None]*(count+1)+np.arange(count)[None,:]).ravel()
    ix=np.concatenate([np.c_[a,a+count+1,a+1],np.c_[a+1,a+count+1,a+count+2]])
    return layer_mesh('regional' if tile.level >= 4 else 'ground',p,ix,colours,np.asarray(tile.origin))

# Far canopy: WorldCover tree cover (10) and shrubland (20) drawn as crown mounds. The regional height is
# Copernicus GLO-30, a surface model: its forest already stands near the canopy top, so the shell drapes it with crown
# relief (m above the surface, between these bounds by a smooth field) rather than adding whole trees on top.
CANOPY = {10: (4.0, 9.0), 20: (1.5, 3.0)}
# Linear canopy albedo seen from afar, conifer and broadleaf ends of a smooth stand mix: about the leaves' own albedo
# (the impostor atlas: green 0.06, B/G 0.2), as the client also takes most of the sky's light away (FAR_CANOPY_AMBIENT).
# Calibrated against the harbour panorama's forest shores (natureref evidence/far-canopy.md).
CANOPY_COLOURS = ((0.021, 0.035, 0.008), (0.039, 0.059, 0.013))
# Cell size (m) of the 1 km survey nodes (drawn from ~0.6 km) and the 4 km regional tiles (from ~4.5 km): ~20 px cells
# where each starts. Finer cells (15.625, 62.5) added 0.65-0.98 M triangles to a far view (natureref perf s10-win).
CANOPY_STEP = {2: 31.25, 4: 125.0}


def _lattice(i, j, salt):
    """Deterministic [0, 1) per integer lattice point."""
    h = (np.asarray(i, np.int64) * 374761393 + np.asarray(j, np.int64) * 668265263 + salt * 2147483647) & 0xFFFFFFFF
    h = ((h ^ (h >> 13)) * 1274126177) & 0xFFFFFFFF
    return ((h ^ (h >> 16)) & 0xFFFFFF) / float(1 << 24)


def _noise(x, z, scale, salt):
    """Smooth value noise in [0, 1] over world metres: the same everywhere, so tiles and levels agree."""
    u, v = np.asarray(x) / scale, np.asarray(z) / scale
    i, j = np.floor(u).astype(np.int64), np.floor(v).astype(np.int64)
    fu, fv = u - i, v - j
    fu, fv = fu * fu * (3 - 2 * fu), fv * fv * (3 - 2 * fv)
    a, b = _lattice(i, j, salt), _lattice(i + 1, j, salt)
    c, d = _lattice(i, j + 1, salt), _lattice(i + 1, j + 1, salt)
    return (a + (b - a) * fu) + ((c + (d - c) * fu) - (a + (b - a) * fu)) * fv


def _sample(data, x, z, order):
    s = np.array([(np.ravel(z) - data['zmin']) / data['step'], (np.ravel(x) - data['xmin']) / data['step']])
    return map_coordinates(data['height' if order else 'cover'], s, order=order, mode='nearest').reshape(np.shape(x))


def _canopy_cells(data, x, z, step):
    """Crown relief (m above the surface, 0 = open) and stand mix of cells centred at x, z: the share of WorldCover
    canopy samples (15.625 m) inside the cell decides it; the relief follows the class and a smooth field."""
    n = max(1, int(round(step / float(data['step']))))
    offsets = (np.arange(n) + .5) / n - .5
    height = np.zeros(np.shape(x))
    share = np.zeros(np.shape(x))
    for dx in offsets:
        for dz in offsets:
            kind = _sample(data, x + dx * step, z + dz * step, 0)
            for klass, (lo, hi) in CANOPY.items():
                hit = kind == klass
                share += hit
                height += hit * (lo + (hi - lo) * _noise(x, z, 140.0, 11))
    share /= n * n
    height = np.where(share >= .5, height / np.maximum(share * n * n, 1) * (.6 + .4 * share), 0.0)
    return height, _noise(x, z, 260.0, 23)


# Lidar canopy (coarse/chm.py): where the city's 4 m canopy height model covers a block, the shell stands on the lidar
# ground at the measured canopy height instead of draping the surface model by land cover. It also finds the canopy
# the land cover misses (parks, yards, street trees) and the real clearings.
CHM_CANOPY = 2.0     # m: a lidar cell taller than this is canopy
CHM_STAND = 0.4      # canopy share that makes a shell cell part of a stand
CHM_LIFT = (.85, .7)  # corner height over the cells' mean canopy height: inside a stand, on its edge


@lru_cache(maxsize=1)
def chm_grid(path):
    with np.load(path) as data:
        return {key: data[key] for key in data.files}


def _chm_window(tile, data):
    """Grid rows and columns of ``tile`` and the ring of shell cells around it (two 1 km node cells), or None when the
    grid does not reach that far."""
    step, margin = float(data['step']), 2 * CANOPY_STEP[2]
    rows, cols = data['chm'].shape
    r0, r1 = int((tile.bbox.zmin - margin - float(data['zmin'])) // step), int((tile.bbox.zmax + margin - float(data['zmin'])) // step)
    c0, c1 = int((tile.bbox.xmin - margin - float(data['xmin'])) // step), int((tile.bbox.xmax + margin - float(data['xmin'])) // step)
    if r0 < 0 or c0 < 0 or r1 >= rows or c1 >= cols:
        return None
    return slice(r0, r1 + 1), slice(c0, c1 + 1)


def chm_covers(tile, path):
    """Every model cell of ``tile`` and of the ring of shell cells around it was scanned. The model grows block by block
    as its batch runs; a tile switches to it once its whole window is in."""
    data = chm_grid(str(path))
    window = _chm_window(tile, data)
    return window is not None and bool(data['seen'][window].all())


def chm_digest(tile, path):
    """Identity of the model inside ``tile``'s window: unchanged when the model grows elsewhere, so only the tiles it
    newly covers rebuild."""
    import hashlib
    data = chm_grid(str(path))
    window = _chm_window(tile, data)
    h = hashlib.sha256(f"{tile.id}:{float(data['step'])}".encode())
    for key in ('chm', 'dtm'):
        h.update(np.ascontiguousarray(data[key][window]).tobytes())
    return h.hexdigest()


def _chm_index(data, x, z):
    step = float(data['step']); rows, cols = data['chm'].shape
    r = np.clip(((np.asarray(z) - float(data['zmin'])) // step).astype(np.int64), 0, rows - 1)
    c = np.clip(((np.asarray(x) - float(data['xmin'])) // step).astype(np.int64), 0, cols - 1)
    return r, c


def _chm_ground(data, x, z):
    """Bilinear lidar ground (m) between the 4 m cell centres."""
    step = float(data['step']); rows, cols = data['dtm'].shape
    fr = (np.asarray(z, float) - float(data['zmin'])) / step - .5
    fc = (np.asarray(x, float) - float(data['xmin'])) / step - .5
    r0 = np.clip(np.floor(fr).astype(np.int64), 0, rows - 2); c0 = np.clip(np.floor(fc).astype(np.int64), 0, cols - 2)
    tr, tc = np.clip(fr - r0, 0, 1), np.clip(fc - c0, 0, 1)
    d = data['dtm']
    top = d[r0, c0] * (1 - tc) + d[r0, c0 + 1] * tc
    bottom = d[r0 + 1, c0] * (1 - tc) + d[r0 + 1, c0 + 1] * tc
    return (top * (1 - tr) + bottom * tr) / 10.0


def _chm_cells(data, x, z, step):
    """Mean and peak canopy height (m above the ground, 0 = open) and canopy share of shell cells centred at x, z,
    from the 4 m model cells inside each."""
    n = max(1, int(round(step / float(data['step']))))
    offsets = (np.arange(n) + .5) / n - .5
    total, count, peak = np.zeros(np.shape(x)), np.zeros(np.shape(x)), np.zeros(np.shape(x))
    for dx in offsets:
        for dz in offsets:
            h = data['chm'][_chm_index(data, x + dx * step, z + dz * step)] / 10.0
            hit = h > CHM_CANOPY
            count += hit; total += h * hit; peak = np.maximum(peak, h)
    share = count / (n * n)
    stand = share >= CHM_STAND
    return np.where(stand, total / np.maximum(count, 1), 0.0), np.where(stand, peak, 0.0), share


def _orient(points, faces, towards):
    """Flip the faces whose normal points away from ``towards`` (one direction per face)."""
    tri = points[faces]
    normal = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    flip = (normal * towards).sum(1) < 0
    faces[flip] = faces[flip][:, ::-1]
    return faces


def canopy(tile, path, step, layer='canopy', chm=None, ground_colour=None):
    """One mound per canopy cell of ``tile`` over the regional surface: an apex (jittered) at the cell's crown relief and
    four corners sunk between crowns or kept high on the stand's edge, with skirts from the edge corners to 2 m below
    the open neighbour's surface. A skirt runs from the crowns' edge colour at the top to the open ground's colour at
    its foot: ``ground_colour`` (linear RGB) where the caller draws its own ground, else the regional land cover's
    colour at the open neighbour (the regional terrain's). Every height and colour is a function of world position, so
    neighbouring tiles meet; None without canopy. With ``chm`` (coarse/chm.py) covering the tile, the cells, heights
    and ground come from the lidar canopy height model instead."""
    from .horizon import layer_mesh
    data = grid(str(path))
    if not covers(tile, path):
        raise ValueError(f'{tile.id}: outside regional source grid')
    n = int(round(tile.size / step))
    # Cells -1..n: the ring outside the tile decides the shared corners and the skirts on the tile edge.
    centres = (np.arange(-1, n + 1) + .5) * step
    gx, gz = np.meshgrid(tile.bbox.xmin + centres, tile.bbox.zmin + centres)
    if chm is not None and chm_covers(tile, chm):
        lidar = chm_grid(str(chm))
        h, peak, share = _chm_cells(lidar, gx, gz, step)
        mix = .5 * np.clip(share, 0, 1) + .5 * _noise(gx, gz, 260.0, 23)
        crest = h + .5 * (peak - h)
        surface = lambda x, z: _chm_ground(lidar, x, z)
        inside, edge = CHM_LIFT
    else:
        h, mix = _canopy_cells(data, gx, gz, step)
        crest = h
        surface = lambda x, z: _sample(data, x, z, 1)
        # Between crowns inside a stand the corners sink to a third of the relief; the stand's edge keeps most of
        # it, so its skirt is the forest wall over the open ground beside it.
        inside, edge = .35, .8
    if ground_colour is None:
        lookup = np.zeros((256, 3))
        for kind, rgb in regional_palette().items():
            lookup[kind] = rgb
        open_colour = lambda x, z: lookup[_sample(data, x, z, 0).astype(np.int64)]  # noqa: E731
    else:
        open_colour = lambda x, z: np.broadcast_to(np.asarray(ground_colour, float), (np.size(x), 3))  # noqa: E731
    on = h > 0
    rows, cols = np.nonzero(on[1:-1, 1:-1])
    if not len(rows):
        return None
    low, high = np.asarray(CANOPY_COLOURS[0]), np.asarray(CANOPY_COLOURS[1])
    colour = (low + (high - low) * mix[..., None]) * (.85 + .3 * _noise(gx, gz, 45.0, 37))[..., None]
    # Corner (a, b) at (xmin + b step, zmin + a step) joins padded cells (a..a+1, b..b+1).
    meet = [(slice(None, -1), slice(None, -1)), (slice(None, -1), slice(1, None)),
            (slice(1, None), slice(None, -1)), (slice(1, None), slice(1, None))]
    count = sum(on[m] for m in meet)
    mean_h = sum(h[m] for m in meet) / np.maximum(count, 1)
    mean_c = sum(colour[m] * on[m][..., None] for m in meet) / np.maximum(count, 1)[..., None]
    lift = mean_h * np.where(count == 4, inside, edge)
    corner_colour = mean_c * np.where(count == 4, .7, .55)[..., None]
    kx, kz = np.meshgrid(tile.bbox.xmin + np.arange(n + 1) * step, tile.bbox.zmin + np.arange(n + 1) * step)
    ground = surface(kx, kz)
    top = np.stack([kx, ground + lift, kz], -1)
    cell_x = np.floor(gx[1:-1, 1:-1][rows, cols] / step).astype(np.int64)
    cell_z = np.floor(gz[1:-1, 1:-1][rows, cols] / step).astype(np.int64)
    ax = gx[1:-1, 1:-1][rows, cols] + (_lattice(cell_x, cell_z, 41) - .5) * .4 * step
    az = gz[1:-1, 1:-1][rows, cols] + (_lattice(cell_x, cell_z, 43) - .5) * .4 * step
    apex = np.c_[ax, surface(ax, az) + crest[1:-1, 1:-1][rows, cols], az]
    corners = [(rows, cols), (rows, cols + 1), (rows + 1, cols + 1), (rows + 1, cols)]  # NW, NE, SE, SW
    m = len(rows)
    points = [apex] + [top[r, c] for r, c in corners]
    colours = [colour[1:-1, 1:-1][rows, cols]] + [corner_colour[r, c] for r, c in corners]
    cell = np.arange(m)
    faces = [np.c_[cell, (k + 1) * m + cell, (k + 1) % 4 * m + m + cell] for k in range(4)]
    towards = [np.tile([0.0, 1.0, 0.0], (4 * m, 1))]
    offset = 5 * m
    # North, east, south, west edges: (row step, column step, first corner, second corner).
    for dr, dc, a, b in ((-1, 0, 0, 1), (0, 1, 1, 2), (1, 0, 2, 3), (0, -1, 3, 0)):
        k = np.nonzero(~on[1 + rows + dr, 1 + cols + dc])[0]
        if not len(k):
            continue
        ra, ca = corners[a][0][k], corners[a][1][k]
        rb, cb = corners[b][0][k], corners[b][1][k]
        # The surface model ramps up into a stand: the wall reaches down to the open neighbour's surface.
        ox, oz = gx[1 + rows[k] + dr, 1 + cols[k] + dc], gz[1 + rows[k] + dr, 1 + cols[k] + dc]
        outside = surface(ox, oz)
        down_a, down_b = top[ra, ca].copy(), top[rb, cb].copy()
        down_a[:, 1] = np.minimum(ground[ra, ca], outside) - 2
        down_b[:, 1] = np.minimum(ground[rb, cb], outside) - 2
        # A forest edge seen from afar is crowns down to the ground, not a shadow: the skirt keeps the crowns' edge
        # colour at the top and fades into the open ground at its foot. Its foot was a quarter of the crown colour,
        # which stood as a near-black band under every stand edge at 1-3 km (natureref evidence/far-walls.md).
        foot = open_colour(ox, oz)
        q = len(k)
        points += [top[ra, ca], top[rb, cb], down_b, down_a]
        colours += [corner_colour[ra, ca], corner_colour[rb, cb], foot, foot]
        index = offset + np.arange(q)
        faces += [np.c_[index, index + q, index + 2 * q], np.c_[index, index + 2 * q, index + 3 * q]]
        towards.append(np.tile([float(dc), 0.0, float(dr)], (2 * q, 1)))
        offset += 4 * q
    points = np.concatenate(points)
    faces = _orient(points, np.concatenate(faces), np.concatenate(towards))
    return layer_mesh(layer, points, faces, np.concatenate(colours), np.asarray(tile.origin))


def build(tile, path):
    terrain=mesh(tile,path)
    trees=canopy(tile,path,CANOPY_STEP[4],'canopy-regional')
    meshes=[terrain]+([trees] if trees is not None else [])
    return meshes,dict(triangles=sum(len(m.indices) for m in meshes),draws=len(meshes),regional=True)
