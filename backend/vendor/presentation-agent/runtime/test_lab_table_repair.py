import unittest
from types import SimpleNamespace as Obj
from unittest.mock import patch
from runtime.table_repair import recover_table_rows
from runtime.text_frames import TemplateTextFrame, TemplateParagraph
from runtime.security import NS,xml

class TableRowRecoveryTest(unittest.TestCase):
    def fixture(self, heights, merged=False):
        part='ppt/slides/slide8.xml'
        rows=[Obj(height_emu=h*12700,cells=[Obj(h_merge=False,v_merge=merged,col_span=1,row_span=1,row=r,col=0)]) for r,h in enumerate(heights)]
        table=Obj(source_level='slide',linked_object_id='table',rows=rows,grid_column_widths_emu=[300*12700])
        shape=Obj(source_object_id='table',shape_id=700,source_part=part,hidden=False,children=[])
        model=Obj(tables=[table],slides=[Obj(objects=[shape])])
        content=f'<p:sld xmlns:p="{NS["p"]}" xmlns:a="{NS["a"]}"><p:cSld><p:spTree><p:graphicFrame><p:nvGraphicFramePr><p:cNvPr id="700"/></p:nvGraphicFramePr><a:graphic><a:graphicData><a:tbl><a:tblGrid><a:gridCol w="3810000"/></a:tblGrid>'+''.join(f'<a:tr h="{h*12700}"><a:tc><a:txBody><a:p><a:r><a:t>Text</a:t></a:r></a:p></a:txBody></a:tc></a:tr>' for h in heights)+'</a:tbl></a:graphicData></a:graphic></p:graphicFrame></p:spTree></p:cSld></p:sld>'
        def frame(t,c,s):
            return TemplateTextFrame(source_element_id='x',source_fingerprint='x',width_emu=300*12700,height_emu=heights[c.row]*12700,
                top_emu=4*12700,bottom_emu=4*12700,paragraphs=[TemplateParagraph(has_text=True,font_family='DejaVu Sans',font_size_pt=18)])
        return {part:content.encode()},model,frame

    def test_short_rows_recover_without_changing_total_height_grid_or_content(self):
        parts,model,frame=self.fixture([5,70])
        with patch('runtime.table_repair.cell_frame',side_effect=frame):
            result,changes=recover_table_rows(parts,model)
            again,second=recover_table_rows(result,model)
        self.assertEqual(len(changes),1)
        c=changes[0]
        self.assertEqual(sum(c['beforeHeightsEmu']),sum(c['afterHeightsEmu']))
        self.assertGreater(c['afterHeightsEmu'][0],c['beforeHeightsEmu'][0])
        a,b=xml(next(iter(parts.values()))),xml(next(iter(result.values())))
        self.assertEqual([n.text for n in a.findall('.//a:t',NS)],[n.text for n in b.findall('.//a:t',NS)])
        self.assertEqual(a.find('.//a:gridCol',NS).attrib,b.find('.//a:gridCol',NS).attrib)
        self.assertEqual(result,again)

    def test_merged_and_insufficient_total_space_are_unchanged(self):
        for heights,merged in [([5,70],True),([5,5],False)]:
            parts,model,frame=self.fixture(heights,merged)
            with patch('runtime.table_repair.cell_frame',side_effect=frame):
                result,changes=recover_table_rows(parts,model)
            self.assertEqual(result,parts)
            self.assertEqual(changes,[])

    def test_horizontal_merge_coverage_is_preserved(self):
        parts,model,frame=self.fixture([5,70])
        table=model.tables[0]
        table.grid_column_widths_emu=[150*12700,150*12700]
        for row in table.rows: row.cells[0].col_span=2
        with patch('runtime.table_repair.cell_frame',side_effect=frame):
            result,changes=recover_table_rows(parts,model)
        self.assertEqual(len(changes),1)
        self.assertTrue(all(row.cells[0].col_span==2 for row in table.rows))
