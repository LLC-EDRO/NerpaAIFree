import unittest
from runtime.security import xml, NS
from runtime.tables import resize_table_rows, expanded_table_layout
from copy import deepcopy


class NativeTableRows(unittest.TestCase):
    def table(self):
        return xml(f'''<p:sld xmlns:p="{NS['p']}" xmlns:a="{NS['a']}"><p:cSld><p:spTree><p:graphicFrame>
          <p:nvGraphicFramePr><p:cNvPr id="47" name="Table"/></p:nvGraphicFramePr><a:graphic><a:graphicData><a:tbl>
          <a:tblPr firstRow="1"/><a:tblGrid><a:gridCol w="2000000"/><a:gridCol w="3000000"/></a:tblGrid>
          <a:tr h="500000"><a:tc/><a:tc/></a:tr>
          <a:tr h="3000000"><a:tc><a:txBody><a:p><a:r><a:rPr sz="1800"/><a:t>Sample</a:t></a:r></a:p></a:txBody><a:tcPr marL="91440"/></a:tc><a:tc/></a:tr>
          </a:tbl></a:graphicData></a:graphic></p:graphicFrame></p:spTree></p:cSld></p:sld>'''.encode())

    def test_real_editable_rows_preserve_header_grid_and_total_height(self):
        root = self.table()
        resize_table_rows(root, {'47': 4})
        table = root.find('.//a:tbl', NS)
        rows = table.findall('a:tr', NS)
        self.assertEqual(len(rows), 5)
        self.assertEqual(rows[0].get('h'), '500000')
        self.assertEqual(sum(int(r.get('h')) for r in rows[1:]), 3000000)
        self.assertEqual([c.get('w') for c in table.findall('a:tblGrid/a:gridCol', NS)], ['2000000', '3000000'])
        for row in rows[1:]:
            self.assertEqual(len(row.findall('a:tc', NS)), 2)
            self.assertEqual(row.find('.//a:rPr', NS).get('sz'), '1800')
            self.assertEqual(row.find('.//a:tcPr', NS).get('marL'), '91440')

    def test_merged_cells_are_not_silently_split(self):
        root = self.table()
        root.find('.//a:tbl/a:tr/a:tc', NS).set('gridSpan','2')
        with self.assertRaisesRegex(ValueError, 'grid_not_regular'):
            resize_table_rows(root, {'47': 3})

    def test_fitter_gets_each_row_at_its_actual_height(self):
        slots = [dict(key=f's47_r{r}_c{c}', shapeId=47, cell=[r,c], x=c*200, y=40 if r else 0,
                      w=200, h=300 if r else 40) for r in range(2) for c in range(2)]
        frames = {s['key']:dict(height_emu=round(s['h']*12700)) for s in slots}
        layout, fitted = expanded_table_layout(dict(slots=slots),frames,{'47':3})
        self.assertEqual(len(layout['slots']),8)
        for slot in layout['slots']:
            if slot['cell'][0]:
                self.assertEqual(slot['h'],100)
                self.assertEqual(fitted[slot['key']]['height_emu'],1270000)

    def test_row_limit_is_ten_including_header_in_measurement_and_output(self):
        root=self.table()
        resize_table_rows(root,{'47':9})
        self.assertEqual(len(root.findall('.//a:tbl/a:tr',NS)),10)
        with self.assertRaisesRegex(ValueError,'invalid_table_rows'):
            resize_table_rows(self.table(),{'47':10})
        with self.assertRaisesRegex(ValueError,'invalid_table_rows'):
            expanded_table_layout({'slots':[]},{},{'47':10})

    def test_reduced_columns_have_identical_geometry_in_fitter_and_native_xml(self):
        root=self.table();table=root.find('.//a:tbl',NS)
        grid=table.find('a:tblGrid',NS);grid.append(deepcopy(grid[-1]))
        for row in table.findall('a:tr',NS):row.append(deepcopy(row[-1]))
        widths=[2000000/12700,3000000/12700,3000000/12700]
        slots=[dict(key=f's47_r{r}_c{c}',shapeId=47,cell=[r,c],x=sum(widths[:c]),y=40*r,w=widths[c],h=40 if not r else 300) for r in range(2) for c in range(3)]
        frames={s['key']:dict(width_emu=round(s['w']*12700),height_emu=round(s['h']*12700)) for s in slots}
        layout,fitted=expanded_table_layout(dict(slots=slots),frames,{'47':2},columns={'47':2})
        resize_table_rows(root,{'47':2},columns={'47':2})
        actual=[int(c.get('w')) for c in grid]
        self.assertEqual(actual,[3200000,4800000])
        self.assertEqual(len(table.findall('a:tr',NS)),3)
        for s in layout['slots']:
            self.assertEqual(round(s['w']*12700),actual[s['cell'][1]])
            self.assertEqual(fitted[s['key']]['width_emu'],actual[s['cell'][1]])
        self.assertTrue(all(len(r.findall('a:tc',NS))==2 for r in table.findall('a:tr',NS)))


if __name__ == '__main__': unittest.main()
