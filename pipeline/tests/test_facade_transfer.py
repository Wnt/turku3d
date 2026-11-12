import unittest
import numpy as np
import shapely
from turku3d.coarse.inputs import Building
from turku3d.scene.facade_transfer import qualified_buildings


class FacadeTransferTest(unittest.TestCase):
    def test_missing_cues_transfer_without_changing_source_shell_or_known_fields(self):
        b=Building('source-part',shapely.box(0,0,12,8),3.,10.,'building')
        b.facade_key='stable';b.attributes={'levels':2,'levels_source':'register','material_class':'unknown','roof_material':'copper'}
        b.roof_parts=[(b.footprint,np.array([0.,0.,1.,10.]))]
        row={'buildingId':'stable','year':1887,'levels':'2','registerEvidence':{'material_class':'wood'},
             'grammar':{'match':{'accepted':True,'id':'way/1','tags':{'roof:material':'metal','building:colour':'linen'}}}}
        result,receipts=qualified_buildings([b],{'buildings':[row]});a=result[0]
        self.assertEqual(a.attributes['material_class'],'wood');self.assertEqual(a.attributes['roof_material'],'copper')
        self.assertEqual(b.attributes['material_class'],'unknown');self.assertNotIn('construction_year',b.attributes)
        self.assertEqual(a.footprint.wkb,b.footprint.wkb);self.assertEqual((a.base,a.top),(b.base,b.top))
        self.assertTrue(np.array_equal(a.roof_parts[0][1],b.roof_parts[0][1]))
        self.assertFalse(receipts[0]['unknownMaterial']);self.assertNotIn('wall_colour',a.attributes)
        self.assertEqual(receipts[0]['archetypeStatus'],'existing architectural prior, not an observed facade')

    def test_unknown_and_known_conflicts_remain_explicit(self):
        b=Building('stable',shapely.box(0,0,12,8),0,6,'building');b.attributes={'levels':1,'material_class':'brick'}
        row={'buildingId':'stable','year':1900,'levels':2,'registerEvidence':{'material_class':'wood'},'grammar':{'match':None}}
        result,receipts=qualified_buildings([b],{'buildings':[row]})
        self.assertEqual(result[0].attributes['material_class'],'brick');self.assertEqual(result[0].attributes['levels'],1)
        self.assertEqual(set(receipts[0]['knownAttributeConflicts']),{'material_class','levels'})
        b.attributes={};row['registerEvidence']={}
        result,receipts=qualified_buildings([b],{'buildings':[row]})
        self.assertTrue(receipts[0]['unknownMaterial']);self.assertNotIn('material_class',result[0].attributes)
        with self.assertRaises(ValueError):qualified_buildings([b],{'buildings':[]})
