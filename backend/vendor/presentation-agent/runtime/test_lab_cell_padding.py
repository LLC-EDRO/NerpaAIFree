import unittest
from copy import deepcopy
from types import SimpleNamespace
from runtime.cell_padding import expand_cell_padding,apply_cell_padding,EMU
import runtime.test_lab_expansion as expansion_fixture
import runtime.test_lab_table_rows as table_fixture
from runtime.tables import resize_table_rows
from runtime.security import NS
from runtime.safe_text_expansion import expanded_layout

class CellPaddingTest(unittest.TestCase):
    def scene(self):
        layout,frames,_=expansion_fixture.ExpansionTest().scene()
        layout['slots'][0].update(cell=[2,0],shapeId=47)
        frames['heading'].update(left_emu=20*EMU,right_emu=20*EMU)
        return layout,frames
    def test_padding_reclaims_minimum_space_without_grid_or_font_changes(self):
        layout,frames=self.scene();before=deepcopy(frames)
        class Fitter:
            def fit(self,frame,text):return SimpleNamespace(status='fits' if frame.width_emu-frame.left_emu-frame.right_emu>=30*EMU else 'overflow')
        result,changes=expand_cell_padding(layout,frames,{'heading':'2'},Fitter())
        self.assertEqual(frames,before)
        self.assertEqual(result['heading']['width_emu'],frames['heading']['width_emu'])
        self.assertEqual(result['heading']['paragraphs'],frames['heading']['paragraphs'])
        self.assertGreaterEqual(result['heading']['left_emu'],2*EMU)
        self.assertLess(result['heading']['left_emu'],frames['heading']['left_emu'])
        self.assertGreater(result['heading']['left_emu'],14*EMU)
        # Padding was measured on resized rows, but must not change the source
        # grid before fill.py performs that resize itself.
        source=deepcopy(layout);source['slots'][0]['h']=20
        self.assertEqual(expanded_layout(source,changes),source)
        root=table_fixture.NativeTableRows().table();resize_table_rows(root,{'47':3})
        grid=deepcopy(root.find('.//a:tblGrid',NS))
        apply_cell_padding(root,changes)
        rows=root.findall('.//a:tbl/a:tr',NS)
        self.assertEqual(len(rows),4)
        self.assertEqual(rows[2].find('a:tc/a:tcPr',NS).get('marL'),str(result['heading']['left_emu']))
        self.assertEqual([x.get('w') for x in root.findall('.//a:tblGrid/a:gridCol',NS)],[x.get('w') for x in grid])
        self.assertEqual(rows[2].find('.//a:rPr',NS).get('sz'),'1800')
    def test_unfit_or_unverifiable_text_does_not_change_margins(self):
        layout,frames=self.scene()
        for state in ['overflow','unverifiable']:
            fitter=SimpleNamespace(fit=lambda frame,text:SimpleNamespace(status=state))
            result,changes=expand_cell_padding(layout,frames,{'heading':'Long'},fitter)
            self.assertEqual(result,frames);self.assertFalse(changes)
    def test_regular_text_frame_is_not_treated_as_table_cell(self):
        layout,frames=self.scene();del layout['slots'][0]['cell']
        fitter=SimpleNamespace(fit=lambda *args:self.fail('Non-cell measured'))
        self.assertEqual(expand_cell_padding(layout,frames,{'heading':'2'},fitter),(frames,{}))
