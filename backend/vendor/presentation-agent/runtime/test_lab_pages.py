import unittest
from runtime.security import NS,xml
from runtime.page_numbers import renumber_pages

class PageNumbers(unittest.TestCase):
    def test_render_expected_page_uses_source_cohort_and_new_deck_order(self):
        from lxml import etree
        from runtime.page_numbers import pagination_padding,expected_page_fields
        roots=[self.shape(str(n),514,10) for n in (43,46,49)]
        for root in roots:
            root.find('.//a:off',NS).set('x',str(900*12700))
            shape=root.find('p:sp',NS);nv=etree.SubElement(shape,'{%s}nvSpPr'%NS['p']);etree.SubElement(nv,'{%s}cNvPr'%NS['p'],id='77')
        padding=pagination_padding(roots,960,540)
        native=dict(preserveTemplate=True,ordinal=2,sourceOrdinal=3,deckSlideCount=3)
        self.assertEqual(expected_page_fields(roots[2],960,540,native,2,3,padding),{77:'2'})
        self.assertEqual(roots[2].find('.//a:t',NS).text,'49')
    def test_nonconsecutive_plain_right_footer_sequence_is_renumbered(self):
        from runtime.page_numbers import pagination_padding
        for values in [(43,46,49),(91,24,17)]:
            roots=[self.shape(f'{n:03}',514,10) for n in values]
            for root in roots:root.find('.//a:off',NS).set('x',str(900*12700))
            padding=pagination_padding(roots,960,540)
            for i,root in enumerate(roots):
                renumber_pages(root,960,540,i+1,i+1,3,3,padding)
                self.assertEqual(root.find('.//a:t',NS).text,f'{i+1:03}')
        for values,x in [((43,46,49),100),((42,42,42),900)]:
            roots=[self.shape(str(n),514,10) for n in values]
            for root in roots:root.find('.//a:off',NS).set('x',str(x*12700))
            padding=pagination_padding(roots,960,540)
            renumber_pages(roots[0],960,540,1,1,3,3,padding)
            self.assertEqual(roots[0].find('.//a:t',NS).text,str(values[0]))

    def test_large_canvas_noncontiguous_excerpt_fraction_is_pagination(self):
        from runtime.page_numbers import pagination_padding
        roots=[self.shape(f'{n:02} / 36',775,24) for n in (3,9,22)]
        padding=pagination_padding(roots,1440,810)
        renumber_pages(roots[-1],1440,810,3,3,3,3,padding)
        self.assertEqual(roots[-1].find('.//a:t',NS).text,'03 / 03')
        repeated=[self.shape('24 / 36',775,24) for _ in range(3)]
        padding=pagination_padding(repeated,1440,810)
        renumber_pages(repeated[-1],1440,810,3,3,3,3,padding)
        self.assertEqual(repeated[-1].find('.//a:t',NS).text,'24 / 36')
    def test_reordered_right_footer_cohort_keeps_new_order(self):
        from runtime.page_numbers import pagination_padding
        roots=[self.shape(f'{n:02} / 36',775,24) for n in (22,3,9)]
        for root in roots:root.find('.//a:off',NS).set('x',str(1330*12700))
        padding=pagination_padding(roots,1440,810)
        renumber_pages(roots[0],1440,810,1,1,3,3,padding)
        self.assertEqual(roots[0].find('.//a:t',NS).text,'01 / 03')
    def shape(self,text,y,size):
        return xml(f'<p:sld xmlns:p="{NS["p"]}" xmlns:a="{NS["a"]}"><p:sp><p:spPr><a:xfrm><a:off x="0" y="{int(y*12700)}"/></a:xfrm></p:spPr><p:txBody><a:p><a:r><a:rPr sz="{size*100}"/><a:t>{text}</a:t></a:r></a:p></p:txBody></p:sp></p:sld>'.encode())
    def test_large_upper_ordinal_cohort_and_false_positives(self):
        from runtime.page_numbers import pagination_padding,upper_page_marker
        roots=[self.shape(f'{n:02}',35,60) for n in range(5,10)]
        padding=pagination_padding(roots,960,540)
        self.assertTrue(upper_page_marker(roots[-1].find('.//p:sp',NS),960,540,padding))
        renumber_pages(roots[-1],960,540,5,2,5,5,padding)
        self.assertEqual(roots[-1].find('.//a:t',NS).text,'02')
        for values,y in [([8,8,8,8],35),([5,6,7,8],220),([5],35)]:
            roots=[self.shape(str(n),y,60) for n in values]
            padding=pagination_padding(roots,960,540)
            self.assertFalse(upper_page_marker(roots[0].find('.//p:sp',NS),960,540,padding))

    def test_footer_renumbered(self):
        root=self.shape('10',514,10);renumber_pages(root,960,540,10,2);self.assertEqual(root.find('.//a:t',NS).text,'2')
    def test_statistic_untouched(self):
        for text,y,size,source in [('10',200,72,10),('82',514,12,10),('10',514,40,10)]:
            root=self.shape(text,y,size);renumber_pages(root,960,540,source,2);self.assertEqual(root.find('.//a:t',NS).text,text)

    def test_fraction_total_and_leading_zero_are_updated(self):
        root=self.shape('07 / 16',514,10)
        root.find('.//a:off',NS).set('x',str(850*12700))
        renumber_pages(root,960,540,7,3,9,16)
        self.assertEqual(root.find('.//a:t',NS).text,'03 / 09')
    def test_labels_separators_and_non_padded_numbers(self):
        for old,new in [('Слайд 7 из 16','Слайд 3 из 9'),('Page 7 of 16','Page 3 of 9'),('— 7 —','— 3 —'),('7/16','3/9')]:
            root=self.shape(old,514,10);renumber_pages(root,960,540,7,3,9,16)
            self.assertEqual(root.find('.//a:t',NS).text,new)
    def test_separate_runs_keep_colors_and_separator(self):
        from lxml import etree
        root=self.shape('07',514,10);paragraph=root.find('.//a:p',NS)
        for value,color in [(' / ','AAAAAA'),('16','FFFFFF')]:
            run=etree.SubElement(paragraph,'{%s}r'%NS['a']);props=etree.SubElement(run,'{%s}rPr'%NS['a'],sz='1000')
            etree.SubElement(etree.SubElement(props,'{%s}solidFill'%NS['a']),'{%s}srgbClr'%NS['a'],val=color)
            etree.SubElement(run,'{%s}t'%NS['a']).text=value
        before=[etree.tostring(r) for r in root.findall('.//a:rPr',NS)]
        renumber_pages(root,960,540,7,3,9,16)
        self.assertEqual([t.text for t in root.findall('.//a:t',NS)],['03',' / ','09'])
        self.assertEqual(before,[etree.tostring(r) for r in root.findall('.//a:rPr',NS)])
    def test_unrelated_fraction_and_statistic_not_changed(self):
        for text,y,size in [('10/20',200,14),('82%',514,10),('2026',514,10)]:
            root=self.shape(text,y,size);renumber_pages(root,960,540,7,3,9,16)
            self.assertEqual(root.find('.//a:t',NS).text,text)
    def test_explicit_field_works_without_geometry(self):
        root=self.shape('07',200,24)
        run=root.find('.//a:r',NS);run.tag='{%s}fld'%NS['a'];run.set('type','slidenum')
        renumber_pages(root,960,540,2,4,9)
        self.assertEqual(root.find('.//a:t',NS).text,'04')

    def test_double_digit_source_keeps_deck_leading_zero_style(self):
        from runtime.page_numbers import pagination_padding
        first=self.shape('02 / 16',514,10);later=self.shape('12 / 16',514,10)
        padding=pagination_padding([first,later],960,540)
        renumber_pages(later,960,540,12,6,9,16,padding)
        self.assertEqual(later.find('.//a:t',NS).text,'06 / 09')
    def test_group_coordinates_resolve_to_footer_position(self):
        from lxml import etree
        root=self.shape('07',14,10);shape=root.find('p:sp',NS);root.remove(shape)
        group=etree.SubElement(root,'{%s}grpSp'%NS['p'])
        props=etree.SubElement(group,'{%s}grpSpPr'%NS['p']);x=etree.SubElement(props,'{%s}xfrm'%NS['a'])
        for name,attributes in [('off',dict(x='0',y=str(500*12700))),('ext',dict(cx='1270000',cy='1270000')),('chOff',dict(x='0',y='0')),('chExt',dict(cx='1270000',cy='1270000'))]:
            etree.SubElement(x,'{%s}%s'%(NS['a'],name),**attributes)
        group.append(shape)
        renumber_pages(root,960,540,7,4,9)
        self.assertEqual(root.find('.//a:t',NS).text,'04')

    def test_excerpt_sequence_is_renumbered_but_repeated_footer_statistic_is_not(self):
        from runtime.page_numbers import pagination_padding
        roots=[self.shape(f'{i:02} / 30',514,10) for i in (6,7,8)]
        padding=pagination_padding(roots,960,540)
        renumber_pages(roots[0],960,540,1,1,3,3,padding)
        self.assertEqual(roots[0].find('.//a:t',NS).text,'01 / 03')
        roots=[self.shape('42',514,10) for _ in range(3)]
        padding=pagination_padding(roots,960,540)
        renumber_pages(roots[0],960,540,1,1,3,3,padding)
        self.assertEqual(roots[0].find('.//a:t',NS).text,'42')

if __name__=='__main__':unittest.main()
