import tempfile
import unittest
from pathlib import Path
import pymupdf
from runtime.editor_background import editor_background


class EditorBackgroundTests(unittest.TestCase):
    def test_glyph_removal_preserves_photo_vector_and_neighbour(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'previews').mkdir()
            doc=pymupdf.open();p=doc.new_page(width=720,height=405)
            p.draw_rect(p.rect,color=None,fill=(0,.2,.5))
            p.draw_circle((60,240),30,fill=(1,.5,0))
            p.insert_text((30,65),'Editable heading',fontsize=24,color=(1,1,1))
            p.insert_text((430,65),'Protected logo',fontsize=20,color=(1,1,1))
            original=p.get_pixmap(alpha=False)
            doc.save(root/'previews/presentation.pdf');doc.close()
            request=dict(output=str(root/'output'),page=0,width=720,height=405,fields=[dict(path='heading',text='Editable heading',x=20,y=30,w=280,h=70)])
            result=editor_background(root,request)
            self.assertEqual(result['paths'],['heading'])
            self.assertAlmostEqual(result['appearances']['heading']['size'],24,places=2)
            self.assertEqual(result['appearances']['heading']['color'],'#FFFFFF')
            self.assertAlmostEqual(result['appearances']['heading']['baselineTop'],35,places=2)
            clean=pymupdf.Pixmap(root/'output/slide-0.png')
            # Transparent redaction preserves the original coloured background.
            self.assertEqual(clean.pixel(100,100),(0,51,127))
            self.assertEqual(original.pixel(60,240),clean.pixel(round(60*1600/720),round(240*1600/720)))
            request['fields'][0]['text']='No matching text'
            self.assertEqual(editor_background(root,request)['paths'],[])

    def test_empty_field_and_invalid_page(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'previews').mkdir()
            doc=pymupdf.open();doc.new_page();doc.save(root/'previews/presentation.pdf');doc.close()
            request=dict(output=str(root/'out'),page=0,width=595,height=842,fields=[dict(path='empty',text='',x=0,y=0,w=100,h=100)])
            result=editor_background(root,request)
            self.assertEqual(result['paths'],['empty'])
            self.assertEqual(result['appearances']['empty']['size'],20)
            request['page']=2
            with self.assertRaisesRegex(ValueError,'page_invalid'):editor_background(root,request)
