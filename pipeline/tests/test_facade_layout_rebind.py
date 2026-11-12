import unittest
from copy import deepcopy
from turku3d.scene.facade_layout_rebind import rebind_instructions


class RebindTests(unittest.TestCase):
    def setUp(self):
        self.old=dict(start=4,count=20,module=22,bay=3.,size=[19.,.1],origin=[2.,8.5],storey=.1,ground=.1,upper=.1,wall='#919b99',opening=[.55,.5,.2,.1])
        self.new={**deepcopy(self.old),'start':28,'count':36,'size':[19.,5.96],'storey':4.85,'ground':4.85,'upper':4.85}

    def test_rebind_keeps_old_ranges_and_all_style_values(self):
        original=deepcopy(self.old);rows,count=rebind_instructions([self.old],[self.new]);row=rows[0]
        self.assertEqual(count,1);self.assertEqual(row['start'],4);self.assertEqual(row['count'],20)
        for key in ['module','bay','origin','wall','opening']:self.assertEqual(row[key],original[key])
        self.assertEqual(row['storey'],4.85);self.assertEqual(row['size'],[19.,5.96]);self.assertEqual(self.old,original)

    def test_ambiguous_style_or_valid_original_never_silently_changes(self):
        conflict={**self.new,'storey':4.1}
        with self.assertRaisesRegex(ValueError,'ambiguous'):rebind_instructions([self.old],[self.new,conflict])
        with self.assertRaisesRegex(ValueError,'noncollapsed'):rebind_instructions([{**self.old,'storey':3.}],[self.new])
        with self.assertRaisesRegex(ValueError,'ambiguous'):rebind_instructions([self.old],[{**self.new,'wall':'#ffffff'}])

if __name__=='__main__':unittest.main()
