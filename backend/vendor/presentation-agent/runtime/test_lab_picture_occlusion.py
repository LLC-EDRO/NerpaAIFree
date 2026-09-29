import unittest,io
import pymupdf
from PIL import Image
from runtime.security import xml,NS
from runtime.layout_metadata import text_occlusions,picture_text_limits,opaque_picture
from runtime.render_quality import inspect_page
from runtime.text_regions import constrained_frame
from runtime.text_frames import TemplateTextFrame,TemplateParagraph,TemplateTextFitter

class PictureOcclusionTest(unittest.TestCase):
    def test_raster_alpha_is_not_treated_as_solid_foreground(self):
        part='ppt/slides/random.xml'
        parts={part:f'<p:sld xmlns:p="{NS["p"]}" xmlns:a="{NS["a"]}"><p:pic><p:nvPicPr><p:cNvPr id="55"/></p:nvPicPr></p:pic></p:sld>'.encode()}
        item=dict(shape_id=55,object_kind='picture',source_part=part,media_id='ppt/media/test.png')
        for alpha,expected in [(255,True),(0,False),(100,False)]:
            out=io.BytesIO();Image.new('RGBA',(20,20),(30,80,200,alpha)).save(out,format='PNG');parts[item['media_id']]=out.getvalue()
            self.assertEqual(opaque_picture(item,parts),expected)

    def test_pdf_checks_actual_glyphs_not_empty_frame_intersection(self):
        doc=pymupdf.open();page=doc.new_page(width=300,height=200)
        page.insert_text((10,40),'Short title',fontsize=18)
        page.draw_rect(pymupdf.Rect(150,0,300,200),color=None,fill=(0,0,1),overlay=True)
        contract=dict(key='title',value='Short title',rect=pymupdf.Rect(0,0,250,100))
        self.assertEqual(inspect_page(page,[contract])['issues'],[])
        hidden=doc.new_page(width=300,height=200)
        hidden.insert_text((150,40),'Hidden title',fontsize=18)
        hidden.draw_rect(pymupdf.Rect(150,0,300,200),color=None,fill=(0,0,1),overlay=True)
        issues=inspect_page(hidden,[dict(contract,value='Hidden title',rect=pymupdf.Rect(0,0,300,100))])['issues']
        self.assertTrue(any('rendered_text_occluded' in i['details'] for i in issues))
        doc.close()
    def scene(self, fill='<a:solidFill><a:srgbClr val="2255ff"/></a:solidFill>',x=150):
        part='ppt/slides/any.xml'
        root=xml(f'<p:sld xmlns:p="{NS["p"]}" xmlns:a="{NS["a"]}"><p:sp><p:nvSpPr><p:cNvPr id="71"/></p:nvSpPr><p:txBody><a:bodyPr/></p:txBody></p:sp><p:sp><p:nvSpPr><p:cNvPr id="83"/></p:nvSpPr><p:spPr><a:prstGeom prst="rect"/>{fill}</p:spPr></p:sp></p:sld>'.encode())
        from lxml import etree
        parts={part:etree.tostring(root)}
        text=dict(shape_id=71,object_kind='shape',z_index=15)
        picture=dict(shape_id=83,object_kind='shape',placeholder_type='pic',z_index=0,source_part=part,geometry=dict(x_emu=x*12700,y_emu=0,width_emu=300*12700,height_emu=200*12700))
        slot=dict(key='title',shapeId=71,x=10,y=10,w=200,h=80,size=20,align='left',text='')
        return [text,picture],[slot],parts,root

    def test_partial_frame_overlap_is_not_blanket_ban_and_uses_paint_order(self):
        items,slots,parts,root=self.scene()
        self.assertEqual(text_occlusions(items,slots,parts),[])
        limits=picture_text_limits(items,slots,parts,root,{})
        self.assertEqual(limits['title']['w'],138)
        self.assertTrue(limits['title']['explicitLines'])
        self.assertEqual(picture_text_limits(items[::-1],slots,parts,root,{}),{})

    def test_no_fill_and_translucent_placeholder_do_not_claim_opacity(self):
        for fill in ['<a:noFill/>','<a:solidFill><a:srgbClr val="ffffff"><a:alpha val="50000"/></a:srgbClr></a:solidFill>','']:
            items,slots,parts,root=self.scene(fill,x=0)
            self.assertEqual(text_occlusions(items,slots,parts),[])
            self.assertEqual(picture_text_limits(items,slots,parts,root,{}),{})

    def test_full_opaque_occlusion_is_still_rejected_with_object_identity(self):
        items,slots,parts,root=self.scene(x=0)
        issue=text_occlusions(items,slots,parts)[0]
        self.assertEqual(issue['imageShapeId'],83)
        self.assertEqual(issue['geometryVersion'],2)

    def test_visible_width_does_not_invent_wraps_or_mutate_native_frame(self):
        items,slots,parts,root=self.scene()
        limit=picture_text_limits(items,slots,parts,root,{})['title']
        frame=TemplateTextFrame(source_element_id='x',source_fingerprint='x',width_emu=200*12700,height_emu=80*12700,top_emu=0,bottom_emu=0,left_emu=0,right_emu=0,wrap=True,paragraphs=[TemplateParagraph(has_text=True,font_family='DejaVu Sans',font_size_pt=20)])
        fitter=TemplateTextFitter();restricted=constrained_frame(frame.model_dump(),slots[0],limit,fitter)
        self.assertEqual(frame.width_emu,restricted.width_emu)
        self.assertEqual(fitter.fit(restricted,['A short title']).status,'fits')
        self.assertEqual(fitter.fit(restricted,['A long title that would wrap into several lines']).status,'overflow')
        self.assertIsNone(frame.visible_width_emu)

    def test_resized_content_box_can_wrap_before_picture(self):
        _,slots,_,_=self.scene()
        slots[0]['w']=140
        limit=dict(x=10,y=10,w=138,h=80,explicitLines=True,blockerRole='foreground_picture')
        frame=TemplateTextFrame(source_element_id='x',source_fingerprint='x',width_emu=140*12700,height_emu=80*12700,top_emu=0,bottom_emu=0,left_emu=3*12700,right_emu=3*12700,wrap=True,paragraphs=[TemplateParagraph(has_text=True,font_family='DejaVu Sans',font_size_pt=12)])
        fitter=TemplateTextFitter();restricted=constrained_frame(frame.model_dump(),slots[0],limit,fitter)
        self.assertIsNone(restricted.visible_width_emu)
        self.assertEqual(fitter.fit(restricted,['This text wraps normally inside the repaired native frame']).status,'fits')
