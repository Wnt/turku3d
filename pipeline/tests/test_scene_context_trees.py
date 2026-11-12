"""Context vegetation preserves source identity and world placement."""
import copy
import tempfile
import unittest
from pathlib import Path

import numpy as np
import shapely

from turku3d.package.gltf import read_glb
from turku3d.scene.context_trees import compile_context_trees
from turku3d.vegetation.visual import array


class ContextTreesTest(unittest.TestCase):
    def fixture(self):
        return dict(version=1, nodes=[dict(variant="tree_default_fall",
            matrix=[1,0,0,0,0,1,0,0,0,0,1,0,100,0,200,1],
            positions=[[1,3,1],[20,4,20],[30,5,30]], scales=[[2,9,2]]*3,
            rotations=[[0,0,0,1]]*3, ids=["inside","owned","context"], species=["Tilia"]*3)])

    def test_filters_in_world_coordinates_and_retains_exact_transforms(self):
        with tempfile.TemporaryDirectory() as directory:
            asset, identities=compile_context_trees([self.fixture(),self.fixture()],directory,
                occupied_ids=["owned"],bounds=[100,200,110,210],water=shapely.Point(130,230))
            self.assertEqual(identities,["context"])
            document,binary=read_glb((Path(directory)/asset["url"]).read_bytes())
            wrappers=[document["nodes"][i] for i in document["nodes"][0]["children"]]
            self.assertEqual(len(wrappers),1)
            self.assertEqual(wrappers[0]["matrix"],self.fixture()["nodes"][0]["matrix"])
            for child in wrappers[0]["children"]:
                node=document["nodes"][child]
                self.assertEqual(node["extras"]["instance_object_ids"],["context"])
                self.assertEqual(node["extras"]["instance_species"],["Tilia"])
                self.assertEqual(node["extras"]["instance_near_water"],[True])
                attr=node["extensions"]["EXT_mesh_gpu_instancing"]["attributes"]
                np.testing.assert_array_equal(array(document,binary,attr["TRANSLATION"]),[[30,5,30]])
                np.testing.assert_array_equal(array(document,binary,attr["SCALE"]),[[2,9,2]])

    def test_same_variant_in_distinct_cells_keeps_separate_placement_groups(self):
        first=self.fixture();second=copy.deepcopy(first)
        second['nodes'][0]['ids']=['other:'+key for key in second['nodes'][0]['ids']]
        second['nodes'][0]['matrix'][12]+=250
        with tempfile.TemporaryDirectory() as directory:
            asset,identities=compile_context_trees([first,second],directory,
                occupied_ids=[],bounds=[0,0,1,1],water=shapely.Point())
            self.assertEqual(len(identities),6)
            document,_=read_glb((Path(directory)/asset['url']).read_bytes())
            represented=[]
            for parent in document['nodes'][0]['children']:
                variants={}
                for index in document['nodes'][parent]['children']:
                    extras=document['nodes'][index]['extras']
                    variants.setdefault(extras['variant'],extras['instance_object_ids'])
                represented.extend(key for group in variants.values() for key in group)
            self.assertCountEqual(represented,identities)

    def test_rejects_incomplete_or_mismatched_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            bad=copy.deepcopy(self.fixture());bad["nodes"][0]["scales"].pop()
            for source in ({"state":"preparing"},bad):
                with self.assertRaises(ValueError):
                    compile_context_trees([source],directory,occupied_ids=[],bounds=[0,0,1,1],water=shapely.Point())


if __name__ == "__main__":
    unittest.main()
