"""Read-only inventory of reviewed walls intersecting retained facade artwork."""
import json
import numpy as np
from turku3d import config as C
from turku3d.facade_assets import building, root
from turku3d.facade_programs import matching_wall, measured


def inventory():
    from turku3d.facade_assets import legacy_rows
    index_path = root() / "index.json"
    index = json.loads(index_path.read_text()) if index_path.is_file() else {"buildings": legacy_rows()}
    rows = []
    for key in sorted(index['buildings']):
        program = measured(key)
        replaced, structural = [], []
        for facade in building(key)['facades']:
            points = np.asarray(facade['coordinates'])[:, :2]
            a, b = points[0], points[-1]
            base = facade['base_height']
            height = facade['metadata']['height_m']
            vertices = np.array([[a[0]-C.ORIGIN_E,base,C.ORIGIN_N-a[1]],
                                 [b[0]-C.ORIGIN_E,base,C.ORIGIN_N-b[1]],
                                 [b[0]-C.ORIGIN_E,base+height,C.ORIGIN_N-b[1]],
                                 [a[0]-C.ORIGIN_E,base+height,C.ORIGIN_N-a[1]]])
            normal = facade.get("normal")
            if normal is not None and np.dot(np.array(normal)[[0,2]], (vertices[1]-vertices[0])[[2,0]] * [1,-1]) < 0:
                vertices = vertices[[1,0,3,2]]
            own = matching_wall(vertices, program)
            if own and ('render_sections' not in own or own['render_sections']):
                replaced.append(facade['metadata']['facade_index'])
                if own.get('structure'):
                    structural.append(facade['metadata']['facade_index'])
        if replaced:
            rows.append(dict(building=key, stored_maps_displaced=replaced,
                             structure_only_maps=structural))
    return dict(stored_buildings=len(index['buildings']), affected_buildings=len(rows),
                affected_maps=sum(len(r['stored_maps_displaced']) for r in rows),
                structure_only_buildings=sum(bool(r['structure_only_maps']) for r in rows),
                structure_only_maps=sum(len(r['structure_only_maps']) for r in rows),
                method='Historical per-wall eligibility on surveyed stored segments; not a count of already served tile pixels',
                buildings=rows)


if __name__ == '__main__':
    print(json.dumps(inventory(), indent=2))
