import tempfile,unittest
from pathlib import Path
import pymupdf
from PIL import Image
from runtime.cached_blank import cached_blank_page

class CachedBlankTest(unittest.TestCase):
    def test_native_list_markers_do_not_force_a_second_conversion(self):
        with tempfile.TemporaryDirectory() as tmp,pymupdf.open() as doc:
            page=doc.new_page(width=400,height=200)
            page.insert_text((20,50),'* First\n* Second',fontsize=18)
            layout=dict(slots=[dict(key='body',shapeId=2,text='First\nSecond',x=10,y=20,w=300,h=100)])
            profile=dict(textAnchors=[dict(shapeId=2)],tables=[])
            self.assertTrue(cached_blank_page(page,layout,profile,400,200,Path(tmp)/'blank.png',{'body':{'paragraphs':[{'bullet_text':'*'},{'bullet_text':'*'}]}}))
            self.assertIn('First',page.get_text())

    def test_removes_editable_text_and_keeps_artwork_and_brand(self):
        with tempfile.TemporaryDirectory() as tmp,pymupdf.open() as doc:
            page=doc.new_page(width=400,height=200)
            page.draw_rect(pymupdf.Rect(0,0,400,200),fill=(0,0,.6),color=None)
            page.insert_text((20,50),'BRAND',fontsize=20,color=(1,1,1))
            page.insert_text((20,120),'EDIT THIS',fontsize=20,color=(1,1,1))
            original=page.get_text()
            layout=dict(slots=[dict(key='copy',shapeId=2,text='EDIT THIS',x=15,y=85,w=250,h=50)])
            output=Path(tmp)/'blank.png'
            self.assertTrue(cached_blank_page(page,layout,dict(textAnchors=[dict(shapeId=2)],tables=[]),400,200,output))
            self.assertEqual(page.get_text(),original)
            image=Image.open(output);self.assertEqual(image.size,(1280,640))
            # The removed text reveals the exact blue artwork, without a box.
            region=image.crop((48,272,848,432));self.assertEqual(len(region.getcolors(1000000)),1)
            self.assertGreater(len(image.crop((48,64,448,180)).getcolors(1000000)),1)

    def test_unmatched_copy_requires_native_fallback_and_writes_no_image(self):
        with tempfile.TemporaryDirectory() as tmp,pymupdf.open() as doc:
            page=doc.new_page(width=400,height=200);page.insert_text((20,50),'ACTUAL')
            layout=dict(slots=[dict(shapeId=2,text='MISSING',x=0,y=0,w=400,h=200)])
            output=Path(tmp)/'blank.png'
            self.assertFalse(cached_blank_page(page,layout,dict(textAnchors=[dict(shapeId=2)],tables=[]),400,200,output))
            self.assertFalse(output.exists())
