import copy
import unittest
from shapely.geometry import box
from turku3d.scene.grammar import footprint_match, qualify_facade


class GrammarTransferTest(unittest.TestCase):
    def test_unique_overlap_and_ambiguous_or_partial_abstention(self):
        target=box(0,0,10,10)
        source={'id':'way/1','geometry':box(.1,0,10.1,10),'tags':{'building:material':'wood'}}
        match=footprint_match(target,[source]);self.assertTrue(match['accepted']);self.assertGreater(match['metrics']['iou'],.97)
        self.assertTrue(footprint_match(target,[source,copy.deepcopy(source)])['accepted'])
        conflict={**source,'tags':{'building:material':'brick'}}
        self.assertFalse(footprint_match(target,[source,conflict])['accepted'])
        duplicate={**source,'id':'way/2'}
        self.assertFalse(footprint_match(target,[source,duplicate])['accepted'])
        self.assertFalse(footprint_match(target,[{**source,'geometry':box(0,0,25,10)}])['accepted'])

    def test_mapped_timber_transfers_family_without_copying_hero_details(self):
        match={'accepted':True,'id':'way/109814360','tags':{'building:material':'wood','building:levels':'1.5','building:colour':'linen','roof:shape':'hipped','roof:material':'metal'}}
        result=qualify_facade({'construction_year':1887,'levels':2,'levels_source':'register'},match)
        self.assertEqual(result['archetype'],'timber_traditional')
        self.assertEqual(result['attributes']['levels'],2)
        self.assertEqual(result['cues']['mapped_levels']['value'],1.5)
        self.assertNotIn('wall_colour',result['attributes'])
        self.assertFalse(result['constraints']['copyHeroOrnaments'])
        self.assertFalse(result['constraints']['inferExactOpenings'])
        self.assertEqual(result['cues']['material_class']['support'],.4)

    def test_approved_fields_register_and_unknown_material_survive(self):
        program={'walls':[{'windows':{'count':{'value':6,'support':.9}}}]};description={'material':'plaster','storeys':3};before=copy.deepcopy((program,description))
        result=qualify_facade({'material_class':'render','levels':3}, {'accepted':True,'id':'way/1','tags':{'building:material':'wood','building:levels':'1'}},approved_program=program,existing_description=description)
        self.assertEqual(result['attributes']['material_class'],'render');self.assertEqual(result['attributes']['levels'],3)
        self.assertEqual((program,description),before);self.assertEqual(len(result['retained']),2)
        unknown=qualify_facade({'construction_year':1886,'levels':1},{'accepted':False,'tags':{'building:material':'wood'}})
        self.assertTrue(unknown['constraints']['unknownMaterial']);self.assertNotIn('material_class',unknown['attributes'])
        fractional=qualify_facade({}, {'accepted':True,'id':'way/2','tags':{'building:levels':'1.5'}})
        self.assertNotIn('levels',fractional['attributes'])


if __name__=='__main__':unittest.main()
