import unittest
from runtime.rendered_frame_overlap import editing_frame,verified_pair
from runtime.security import NS,xml

class FrameOverlapTest(unittest.TestCase):
    def test_only_explicit_unpainted_frames_qualify(self):
        root=xml(f'<p:sp xmlns:p="{NS["p"]}" xmlns:a="{NS["a"]}"><p:spPr><a:noFill/><a:ln><a:noFill/></a:ln></p:spPr></p:sp>'.encode())
        self.assertTrue(editing_frame(root))
        root.find('p:spPr/a:ln/a:noFill',NS).tag='{%s}solidFill'%NS['a']
        self.assertFalse(editing_frame(root))
    def test_real_glyph_page_font_and_object_collisions_remain_errors(self):
        issue=dict(key='a',details=['rebuild_overlap'],blocker=dict(key='b'))
        self.assertTrue(verified_pair(issue,{'a','b'},[]))
        self.assertFalse(verified_pair(issue,{'a'},[]))
        for detail in ('rendered_text_overlap','rendered_text_missing','rendered_missing_glyph','rendered_text_outside_page','rendered_text_overlaps_reserved_object'):
            self.assertFalse(verified_pair(issue,{'a','b'},[dict(key='b',details=[detail])]))
        issue['details']=['rebuild_outside_page'];self.assertFalse(verified_pair(issue,{'a','b'},[]))
