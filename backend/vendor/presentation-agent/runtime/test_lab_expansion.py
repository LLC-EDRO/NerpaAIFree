import unittest
from types import SimpleNamespace
from copy import deepcopy
from runtime.security import xml,NS
from runtime.safe_text_expansion import expand_fields,apply_expansion,expanded_layout,expansion_report
from runtime.text_frames import TemplateTextFrame,TemplateParagraph,TemplateTextFitter
from runtime.geometry import intersection,inside
from runtime.photo_safety import replacement_photo_issues

class ExpansionTest(unittest.TestCase):
    def scene(self):
        root=xml(f'''<p:sld xmlns:p="{NS['p']}" xmlns:a="{NS['a']}"><p:cSld><p:spTree><p:sp><p:nvSpPr><p:cNvPr id="91"/></p:nvSpPr><p:spPr><a:xfrm><a:off x="254000" y="254000"/><a:ext cx="762000" cy="508000"/></a:xfrm><a:prstGeom prst="rect"/><a:noFill/><a:ln><a:noFill/></a:ln></p:spPr><p:txBody><a:bodyPr anchor="t"/></p:txBody></p:sp></p:spTree></p:cSld></p:sld>'''.encode())
        slot=dict(key='heading',shapeId=91,x=20,y=20,w=60,h=40,size=22,align='left',text='Test')
        frame=TemplateTextFrame(source_element_id='fixture',source_fingerprint='v1',width_emu=60*12700,height_emu=40*12700,top_emu=0,bottom_emu=0,left_emu=0,right_emu=0,wrap=True,paragraphs=[TemplateParagraph(has_text=True,font_family='DejaVu Sans',font_size_pt=22)])
        item=self.item(91,20,20,60,40)
        context=SimpleNamespace(roots={'layout':root},slides={'layout':[item]},page=dict(x=0,y=0,w=720,h=405))
        return dict(id='layout',slots=[slot]),{'heading':frame.model_dump()},context
    def item(self,id,x,y,w,h,kind='shape'):
        return dict(shape_id=id,object_kind=kind,geometry=dict(x_emu=x*12700,y_emu=y*12700,width_emu=w*12700,height_emu=h*12700))
    def grow(self,layout,frames,context,text='Useful longer heading'):
        return expand_fields(layout,frames,{'heading':text},context,TemplateTextFitter())
    def test_small_sample_grows_minimally_right_and_fits_same_text(self):
        layout,frames,context=self.scene();before=deepcopy(frames)
        fitted,changes=self.grow(layout,frames,context)
        c=changes['heading'];self.assertGreater(c['w'],60);self.assertLess(c['w'],400);self.assertEqual(c['h'],40)
        self.assertEqual((c['x'],c['y']),(20,20));self.assertEqual(frames,before)
        self.assertEqual(TemplateTextFitter().fit(TemplateTextFrame.model_validate(fitted['heading']),['Useful longer heading']).status,'fits')
        apply_expansion(context.roots['layout'],changes)
        transform=context.roots['layout'].find('.//a:xfrm',NS)
        self.assertEqual(transform.find('a:off',NS).get('x'),'254000')
        self.assertEqual(int(transform.find('a:ext',NS).get('cx')),round(c['w']*12700))
        report=expansion_report({0:changes})[0];self.assertEqual(report['before']['w'],60);self.assertEqual(report['after']['w'],c['w'])
    def test_fitting_copy_is_unchanged(self):
        layout,frames,context=self.scene();result,changes=self.grow(layout,frames,context,'Test')
        self.assertEqual(changes,{});self.assertEqual(result,frames)
    def test_picture_and_page_bound_expansion(self):
        layout,frames,context=self.scene();context.slides['layout'].append(self.item(300,200,0,520,405,'picture'))
        result,changes=self.grow(layout,frames,context)
        c=changes['heading'];self.assertLessEqual(c['x']+c['w'],200);self.assertGreater(c['h'],40)
        self.assertTrue(inside(c,context.page))
    def test_no_space_or_existing_overlay_keeps_original_for_ai_repair(self):
        for obstacle in [self.item(5,0,0,720,405,'picture'),self.item(6,0,60,720,345)]:
            layout,frames,context=self.scene();context.page['w']=80;context.slides['layout'].append(obstacle)
            result,changes=self.grow(layout,frames,context)
            self.assertEqual(changes,{})
    def test_ineligible_frames_are_not_changed(self):
        for variant in ['center','cell','fill','anchor','group','rotated','mixed_alignment']:
            layout,frames,context=self.scene();root=context.roots['layout'];node=root.find('.//p:sp',NS)
            if variant=='center':layout['slots'][0]['align']='center'
            elif variant=='cell':layout['slots'][0]['cell']={}
            elif variant=='fill':node.find('p:spPr/a:noFill',NS).tag='{'+NS['a']+'}solidFill'
            elif variant=='anchor':node.find('p:txBody/a:bodyPr',NS).set('anchor','ctr')
            elif variant=='rotated':node.find('p:spPr/a:xfrm',NS).set('rot','60000')
            elif variant=='mixed_alignment':
                from lxml import etree
                paragraph=etree.SubElement(node.find('p:txBody',NS),'{'+NS['a']+'}p')
                etree.SubElement(paragraph,'{'+NS['a']+'}pPr',algn='ctr')
            elif variant=='group':
                from lxml import etree
                parent=node.getparent();parent.remove(node);group=etree.SubElement(parent,'{'+NS['p']+'}grpSp');group.append(node)
            self.assertEqual(self.grow(layout,frames,context)[1],{},variant)
    def test_replacement_image_checks_use_grown_text_frame(self):
        layout,frames,context=self.scene();_,changes=self.grow(layout,frames,context)
        grown=expanded_layout(layout,changes)
        metadata=dict(visualSlots=[dict(shapeId=7,box=dict(x=100,y=20,w=100,h=100))])
        slide=dict(visualSlotsVersion=1,native=dict(mode='source',fields={'heading':'Useful longer heading'}),images=[dict(shapeId=7,image='asset',background='opaque')])
        self.assertEqual(replacement_photo_issues(layout,metadata,slide),[])
        self.assertTrue(replacement_photo_issues(grown,metadata,slide))
    def test_background_container_bounds_growth(self):
        layout,frames,context=self.scene();root=context.roots['layout'];tree=root.find('p:cSld/p:spTree',NS)
        node=xml(f'<p:sp xmlns:p="{NS["p"]}" xmlns:a="{NS["a"]}"><p:nvSpPr><p:cNvPr id="7"/></p:nvSpPr><p:spPr><a:prstGeom prst="rect"/></p:spPr></p:sp>'.encode());tree.insert(0,node)
        context.slides['layout'].insert(0,self.item(7,10,10,180,220))
        _,changes=self.grow(layout,frames,context);self.assertTrue(inside(changes['heading'],dict(x=10,y=10,w=180,h=220)))

    def test_two_expanding_fields_cannot_claim_the_same_space(self):
        layout,frames,context=self.scene();root=context.roots['layout'];node=deepcopy(root.find('.//p:sp',NS))
        node.find('p:nvSpPr/p:cNvPr',NS).set('id','92');node.find('p:spPr/a:xfrm/a:off',NS).set('x',str(120*12700))
        root.find('p:cSld/p:spTree',NS).append(node)
        layout['slots'].append(dict(layout['slots'][0],key='second',shapeId=92,x=120))
        frames['second']=deepcopy(frames['heading']);context.slides['layout'].append(self.item(92,120,20,60,40))
        fields={'heading':'Useful longer heading','second':'Second longer heading'}
        _,changes=expand_fields(layout,frames,fields,context,TemplateTextFitter())
        self.assertEqual(set(changes),set(fields));self.assertEqual(intersection(changes['heading'],changes['second']),0)
