import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from copy import deepcopy
import pymupdf
from runtime.editor_patch import editor_patch,locate_target,write_package,body_value,select_pages
from runtime.test_lab_manual_text import ManualTextTest
from runtime.security import NS,xml,read_package,slide_order
from runtime.fill import serialize


class EditorPatchTests(unittest.TestCase):
    def source(self):
        slides={f'ppt/slides/s{i}.xml':serialize(ManualTextTest().source()) for i in range(3)}
        pres=f'<p:presentation xmlns:p="{NS["p"]}" xmlns:r="{NS["r"]}"><p:sldIdLst>'+''.join(f'<p:sldId id="{256+i}" r:id="r{i}"/>' for i in range(3))+'</p:sldIdLst></p:presentation>'
        rels=f'<Relationships xmlns="{NS["pr"]}">'+''.join(f'<Relationship Id="r{i}" Type="{NS["r"]}/slide" Target="slides/s{i}.xml"/>' for i in range(3))+'</Relationships>'
        return {**slides,'ppt/presentation.xml':pres.encode(),'ppt/_rels/presentation.xml.rels':rels.encode(),
                '_rels/.rels':f'<Relationships xmlns="{NS["pr"]}"><Relationship Id="presentation" Type="{NS["r"]}/officeDocument" Target="ppt/presentation.xml"/></Relationships>'.encode(),
                '[Content_Types].xml':f'<Types xmlns="{NS["ct"]}"><Default Extension="xml" ContentType="application/xml"/></Types>'.encode()}

    def test_duplicate_labels_resolve_by_binding_or_geometry_never_first(self):
        root=ManualTextTest().source();first=root.find('.//p:sp',NS);second=deepcopy(first)
        second.find('p:nvSpPr/p:cNvPr',NS).set('id','24');second.find('p:spPr/a:xfrm/a:off',NS).set('x',str(300*12700));first.getparent().append(second)
        field=dict(before='Заголовок',frame=dict(x=300,y=20,w=200,h=50))
        self.assertIs(locate_target(root,field)[0],second)
        field['binding']=dict(shapeId=23)
        self.assertIs(locate_target(root,field)[0],first)
        field.pop('binding');second.find('p:spPr/a:xfrm/a:off',NS).set('x',str(10*12700))
        with self.assertRaisesRegex(ValueError,'ambiguous'):locate_target(root,field)

    def test_render_only_changed_slide_preserve_package_pdf_pages_and_links(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp);parts=self.source();write_package(parts,folder/'source.pptx');(folder/'previews').mkdir()
            with pymupdf.open() as doc:
                for i in range(3):doc.new_page(width=720,height=405).insert_text((20,30),'Original '+str(i))
                doc[0].insert_link({'kind':pymupdf.LINK_GOTO,'from':pymupdf.Rect(10,10,60,40),'page':2,'to':pymupdf.Point(0,0)})
                doc[2].insert_link({'kind':pymupdf.LINK_URI,'from':pymupdf.Rect(10,10,60,40),'uri':'https://example.com/'})
                doc.set_toc([[1,'Third slide',3]]);doc.save(folder/'previews/presentation.pdf')
            def render(source,destination,binary):
                subset=read_package(source.read_bytes(),generated=True)
                self.assertEqual(len(slide_order(subset)),1)
                self.assertEqual(body_value(xml(subset[slide_order(subset)[0]]).find('.//p:sp',NS)),'Changed')
                with pymupdf.open() as doc:
                    page=doc.new_page(width=720,height=405);page.insert_text((20,30),'Changed')
                    doc.save(destination/'presentation.pdf');page.get_pixmap().save(destination/'slide-0.png')
                return 1
            request=dict(output=str(folder/'out'),soffice='unused',patches=[dict(index=1,fields=[dict(before='Заголовок',value='Changed',binding=dict(shapeId=23),frame=dict(x=10,y=20,w=200,h=50),style=dict(size=26,bold=False))])])
            with patch('runtime.editor_patch.render',render):result=editor_patch(folder,request)
            self.assertEqual(result['renderedSlides'],1)
            actual=read_package((folder/'out/presentation.pptx').read_bytes(),generated=True)
            self.assertEqual([name for name in parts if parts[name]!=actual[name]],['ppt/slides/s1.xml'])
            with pymupdf.open(folder/'out/presentation.pdf') as doc:
                self.assertEqual(len(doc),3);self.assertIn('Original 0',doc[0].get_text());self.assertIn('Changed',doc[1].get_text());self.assertIn('Original 2',doc[2].get_text())
                self.assertEqual(doc[0].get_links()[0]['page'],2);self.assertEqual(doc[2].get_links()[0]['uri'],'https://example.com/')
                self.assertEqual(doc.get_toc(),[[1,'Third slide',3]])
            self.assertFalse((folder/'out/slide-0.png').exists());self.assertTrue((folder/'out/slide-1.png').exists())

    def test_subset_keeps_requested_order(self):
        self.assertEqual(slide_order(select_pages(self.source(),[2,0])),['ppt/slides/s2.xml','ppt/slides/s0.xml'])


if __name__=='__main__':unittest.main()
