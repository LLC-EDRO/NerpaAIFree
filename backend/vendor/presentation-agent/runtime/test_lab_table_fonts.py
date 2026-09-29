import unittest
from types import SimpleNamespace as Obj
from unittest.mock import patch
from runtime.fonts import materialize_table_fonts
from runtime.security import xml, NS

class InheritedTableFonts(unittest.TestCase):
    def test_missing_implicit_face_is_materialized_without_changing_content(self):
        part='ppt/slides/slide1.xml'
        source=f'''<p:sld xmlns:p="{NS['p']}" xmlns:a="{NS['a']}"><p:cSld><p:spTree><p:graphicFrame><p:nvGraphicFramePr><p:cNvPr id="7"/></p:nvGraphicFramePr><a:graphic><a:graphicData><a:tbl><a:tr><a:tc><a:txBody><a:p><a:pPr><a:extLst/></a:pPr><a:r><a:rPr b="1"/><a:t>3600</a:t></a:r></a:p></a:txBody></a:tc></a:tr></a:tbl></a:graphicData></a:graphic></p:graphicFrame></p:spTree></p:cSld></p:sld>'''.encode()
        shape=Obj(source_object_id='table-7',source_part=part,shape_id=7,children=[])
        table=Obj(linked_object_id='table-7',source_level='slide',rows=[Obj(cells=[Obj()])])
        model=Obj(slides=[Obj(objects=[shape])],tables=[table])
        with patch('runtime.tables.cell_frame',return_value=Obj(paragraphs=[Obj(font_family='Missing Sans')])):
            result=materialize_table_fonts({part:source},model,[dict(original='Missing Sans',replacement='Arial')])
        root=xml(result[part])
        self.assertEqual(root.xpath('.//a:t/text()',namespaces=NS),['3600'])
        self.assertEqual(root.xpath('.//a:rPr/@b',namespaces=NS),['1'])
        self.assertEqual(root.xpath('.//a:latin/@typeface',namespaces=NS),['Arial','Arial'])
        self.assertEqual([n.tag.rsplit('}',1)[-1] for n in root.find('.//a:pPr',NS)],['defRPr','extLst'])
