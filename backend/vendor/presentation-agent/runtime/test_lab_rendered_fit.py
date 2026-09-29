import unittest,tempfile,zipfile
from pathlib import Path
from unittest.mock import patch
from runtime.rendered_fit import fitted_size, font_repair_group, repair_rendered_fit
from runtime.security import NS,xml

class RenderedFitTest(unittest.TestCase):
    def test_group_xml_update_is_atomic_and_only_runs_once_per_row(self):
        for unsupported in (False,True):
            texts=[dict(key='left',size=16),dict(key='right',size=16)]
            profile=dict(textAnchors=[dict(key=t['key'],shapeId=i+10,minSize=12) for i,t in enumerate(texts)],metricAlignmentGroups=[dict(keys=['left','right'])])
            native=dict(sourceSlideId='any',rebuild=dict(texts=texts),fields=dict(left='42',right='57'))
            shapes=[]
            for i,text in enumerate(['42','57']):
                field='<a:fld/>' if unsupported and i==1 else ''
                shapes.append(f'<p:sp><p:nvSpPr><p:cNvPr id="{i+10}"/></p:nvSpPr><p:txBody><a:bodyPr/><a:p><a:r><a:rPr sz="1600"/><a:t>{text}</a:t></a:r>{field}</a:p></p:txBody></p:sp>')
            source=f'<p:sld xmlns:p="{NS["p"]}" xmlns:a="{NS["a"]}"><p:cSld><p:spTree>{"".join(shapes)}</p:spTree></p:cSld></p:sld>'
            issues=[dict(self.issue(),slide=0,key=key) for key in ['left','right']]
            for issue in issues:issue['overflowPt']['bottom']=2.2
            with tempfile.TemporaryDirectory() as tmp:
                path=Path(tmp)/'deck.pptx'
                with zipfile.ZipFile(path,'w') as z:
                    z.writestr('ppt/slides/nerpaSlide1.xml',source)
                    z.writestr('ppt/presentation.xml',f'<p:presentation xmlns:p="{NS["p"]}"/>')
                    z.writestr('[Content_Types].xml','<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
                    z.writestr('ppt/_rels/presentation.xml.rels','<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>')
                with patch('runtime.rendered_fit.profiles',return_value={'any':profile}):
                    output,changes=repair_rendered_fit(Path(tmp),[dict(native=native)],path,dict(issues=issues))
                with zipfile.ZipFile(path) as z:root=xml(z.read('ppt/slides/nerpaSlide1.xml'))
                sizes=root.xpath('.//a:rPr/@sz',namespaces=NS)
                self.assertEqual(sizes[0],sizes[1])
                self.assertEqual(len(changes),0 if unsupported else 2)
                expected=16 if unsupported else fitted_size(texts[0],profile['textAnchors'][0],issues[0])
                self.assertEqual([t['size'] for t in output[0]['native']['rebuild']['texts']],[expected,expected])
                self.assertEqual(texts[0]['size'],16)

    def test_final_render_correction_keeps_metric_group_equal_or_changes_neither(self):
        texts=[dict(key='left',size=70),dict(key='right',size=70)]
        profile=dict(textAnchors=[dict(key=t['key'],minSize=12) for t in texts],metricAlignmentGroups=[dict(keys=['left','right'])])
        group=font_repair_group(dict(texts=texts),profile,texts[0],67)
        self.assertEqual([(s['key'],size) for s,_,size in group],[('left',67),('right',67)])
        self.assertEqual([t['size'] for t in texts],[70,70])
        profile['textAnchors'][1]['minSize']=69
        self.assertEqual(font_repair_group(dict(texts=texts),profile,texts[0],67),[])

    def issue(self,**overflow):
        return dict(details=['rendered_text_outside_frame'],overflowPt=dict(left=0,top=0,right=0,bottom=0,**overflow),frameBox=dict(x=0,y=0,w=140,h=65))
    def test_small_measured_overhang_can_fit_without_rewriting_copy(self):
        issue=self.issue();issue['overflowPt']['bottom']=2.2
        size=fitted_size(dict(size=16),dict(minSize=12),issue)
        self.assertIsNotNone(size);self.assertGreaterEqual(size,14.4);self.assertLess(size,16)
    def test_large_overflow_unknown_measurement_and_readability_floor_still_go_to_ai(self):
        issue=self.issue();issue['overflowPt']['bottom']=14
        self.assertIsNone(fitted_size(dict(size=16),dict(minSize=12),issue))
        issue['overflowPt']['bottom']=2.2
        self.assertIsNone(fitted_size(dict(size=16),dict(minSize=16),issue))
        issue['details']=['rendered_text_overlap']
        self.assertIsNone(fitted_size(dict(size=16),dict(minSize=12),issue))
