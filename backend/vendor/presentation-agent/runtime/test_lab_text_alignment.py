import unittest
from types import SimpleNamespace as O
from runtime.security import NS,xml
from runtime.typography import inherited_vertical_alignment
from runtime.text_alignment import apply_alignment
from runtime import test_lab_rebuild as fixtures
from runtime.rebuild import apply,validate

class AlignmentTest(unittest.TestCase):
    def test_inherited_center_and_explicit_override(self):
        self.assertEqual(inherited_vertical_alignment({'text_frame':{'style_layers':[{'body_attributes':{'anchor':'ctr'}},{'body_attributes':{}}]}}),'center')
        self.assertEqual(inherited_vertical_alignment(O(text_frame=O(style_layers=[O(body_attributes={'anchor':'ctr'}),O(body_attributes={'anchor':'b'})]))),'bottom')
    def test_rebuild_keeps_center_and_bottom_in_large_source_frame(self):
        for align,anchor in [('center','ctr'),('bottom','b')]:
            root,p,n,_,_=fixtures.RebuildTest().source_scene()
            p['textAnchors'][0]['verticalAlign']=align
            self.assertEqual(validate(p,n,0),[])
            apply(root,p,n)
            self.assertEqual(root.find('.//p:txBody/a:bodyPr',NS).get('anchor'),anchor)
            self.assertEqual(root.find('.//a:pPr',NS).get('algn'),'l')
    def test_source_policy_centers_vertically_without_horizontal_or_geometry_change(self):
        root=xml(f'<p:sld xmlns:p="{NS["p"]}" xmlns:a="{NS["a"]}"><p:sp><p:nvSpPr><p:cNvPr id="1"/></p:nvSpPr><p:txBody><a:bodyPr anchor="t"/><a:p><a:pPr algn="l"/><a:r><a:t>Label</a:t></a:r></a:p></p:txBody></p:sp></p:sld>'.encode())
        slot=dict(key='label',shapeId=1,role='title',h=200,size=30)
        n=dict(fields={'label':'Label'},textAlignment={'label':dict(vertical='center',horizontal='preserve',reason='Tall isolated label')})
        apply_alignment(root,dict(slots=[slot]),n)
        self.assertEqual(root.find('.//a:bodyPr',NS).get('anchor'),'ctr')
        self.assertEqual(root.find('.//a:pPr',NS).get('algn'),'l')
        n['textAlignment']['label'].update(horizontal='center')
        apply_alignment(root,dict(slots=[slot]),n)
        self.assertEqual(root.find('.//a:pPr',NS).get('algn'),'ctr')
        self.assertEqual(root.find('.//a:t',NS).text,'Label')
    def test_rebuilt_horizontal_change_requires_explicit_ai_decision(self):
        root,p,n,_,_=fixtures.RebuildTest().source_scene();n['rebuild']['texts'][0]['align']='center'
        with self.assertRaises(ValueError):validate(p,n,0)
        n['rebuild']['texts'][0]['alignment']=dict(horizontal='center',vertical='preserve',reason='Standalone card label')
        self.assertEqual(validate(p,n,0),[])
