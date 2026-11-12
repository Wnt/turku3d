"""Region links select one exact complete scene, without fallback."""
import copy
import unittest

from turku3d.scene.regions import SCHEMA, select_region, validate_catalog


class RegionTests(unittest.TestCase):
    def catalog(self):
        return dict(schema=SCHEMA, defaultRegion='port-arthur', regions=[dict(
            id='port-arthur', label='Port Arthur', url='/scenes/port-arthur.scene.json', release='a'*64,
            bounds=[-1000.,250.,-875.,375.], spawn=dict(position=[-950.,8.,300.], headingDeg=90.,
                vehicle=dict(position=[-950.,8.,302.],yaw=0.)))])

    def test_exact_scene_pin_and_unknown_region(self):
        catalog=self.catalog();row=catalog['regions'][0]
        scene={key:row[key] for key in ('id','release','bounds','spawn')}
        validate_catalog(catalog, {'port-arthur':scene})
        self.assertEqual(select_region(catalog),row)
        with self.assertRaisesRegex(ValueError,'Unknown'):select_region(catalog,'office')
        scene['release']='b'*64
        with self.assertRaisesRegex(ValueError,'differs'):validate_catalog(catalog,{'port-arthur':scene})

    def test_reject_unsafe_incomplete_or_duplicate_descriptors(self):
        for url in ('https://example.com/scenes/x.scene.json','//example.com/x','/scenes/../x.scene.json',
                    '/scenes/x.scene.json?version=2','/scenes/x.scene.json#fragment'):
            catalog=self.catalog();catalog['regions'][0]['url']=url
            with self.assertRaises(ValueError):validate_catalog(catalog)
        for mutate in (lambda c:c.update(defaultRegion='missing'),
                       lambda c:c['regions'].append(copy.deepcopy(c['regions'][0])),
                       lambda c:c['regions'][0]['spawn'].update(position=[0.,8.,300.])):
            catalog=self.catalog();mutate(catalog)
            with self.assertRaises(ValueError):validate_catalog(catalog)


if __name__=='__main__':unittest.main()
