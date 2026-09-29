import unittest
import runtime.test_lab_expansion as fixtures
from runtime.geometry_repair import apply_repairs,repair_context
from runtime.safe_text_expansion import apply_expansion
from runtime.security import NS

class GeometryRepairTest(unittest.TestCase):
    def scene(self):return fixtures.ExpansionTest().scene()
    def test_centered_anchor_can_grow_without_changing_style(self):
        layout,frames,context=self.scene()
        body=context.roots['layout'].find('.//a:bodyPr',NS);body.set('anchor','ctr')
        proposal={'heading':dict(x=20,y=18,w=60,h=44)}
        fitted,changes,issues=apply_repairs(layout,frames,proposal,context)
        self.assertEqual(issues,[]);self.assertEqual(fitted['heading']['height_emu'],44*12700)
        apply_expansion(context.roots['layout'],changes)
        self.assertEqual(body.get('anchor'),'ctr')
        self.assertEqual(context.roots['layout'].find('.//a:off',NS).get('y'),str(18*12700))
        self.assertEqual(repair_context(layout,context,{'heading'})['fields'][0]['anchor'],'ctr')
    def test_rejects_collision_page_escape_and_nonfinite(self):
        for b in [dict(x=-5,y=20,w=100,h=45),dict(x=20,y=20,w=float('nan'),h=40),dict(x=20,y=20,w=120,h=50)]:
            layout,frames,context=self.scene();context.slides['layout'].append(fixtures.ExpansionTest().item(4,110,10,20,100,'picture'))
            fitted,changes,issues=apply_repairs(layout,frames,{'heading':b},context)
            self.assertTrue(issues);self.assertFalse(changes);self.assertEqual(fitted,frames)
    def test_local_move_and_shrink_can_remove_original_overlap(self):
        layout,frames,context=self.scene()
        context.slides['layout'].append(fixtures.ExpansionTest().item(5,70,10,100,60,'chart'))
        fitted,changes,issues=apply_repairs(layout,frames,{'heading':dict(x=20,y=75,w=60,h=45)},context)
        self.assertEqual(issues,[]);self.assertEqual(changes['heading']['y'],75)
        fitted,changes,issues=apply_repairs(layout,frames,{'heading':dict(x=20,y=20,w=48,h=40)},context)
        self.assertEqual(issues,[]);self.assertEqual(fitted['heading']['width_emu'],48*12700)
    def test_unknown_field_and_table_are_not_changed(self):
        layout,frames,context=self.scene()
        for key in ['absent','heading']:
            if key=='heading':layout['slots'][0]['cell']=[0,0]
            self.assertTrue(apply_repairs(layout,frames,{key:dict(x=20,y=20,w=80,h=40)},context)[2])
    def test_two_proposals_cannot_overlap_each_other(self):
        from copy import deepcopy
        layout,frames,context=self.scene();node=deepcopy(context.roots['layout'].find('.//p:sp',NS))
        node.find('p:nvSpPr/p:cNvPr',NS).set('id','92');context.roots['layout'].find('p:cSld/p:spTree',NS).append(node)
        layout['slots'].append(dict(layout['slots'][0],key='next',shapeId=92,x=100))
        frames['next']=deepcopy(frames['heading']);context.slides['layout'].append(fixtures.ExpansionTest().item(92,100,20,60,40))
        _,changes,issues=apply_repairs(layout,frames,{'heading':dict(x=20,y=20,w=75,h=40),'next':dict(x=90,y=20,w=70,h=40)},context)
        self.assertTrue(issues);self.assertFalse(changes)
