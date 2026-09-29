import unittest
from types import SimpleNamespace
from runtime.security import xml,NS
from app.presentation.parser.agent import PresentationParser
from app.presentation.models import TextFrameProperties

class EmptyArtworkTest(unittest.TestCase):
    def test_empty_vector_art_does_not_duplicate_master_text_styles(self):
        root=xml(f'<p:sldMaster xmlns:p="{NS["p"]}" xmlns:a="{NS["a"]}"><p:txStyles><p:otherStyle><a:lvl1pPr><a:defRPr sz="2400"/></a:lvl1pPr></p:otherStyle><p:titleStyle><a:lvl1pPr/></p:titleStyle></p:txStyles></p:sldMaster>'.encode())
        art=SimpleNamespace(text_frame=TextFrameProperties(),has_text_content=False,placeholder_type=None,type='shape')
        text=SimpleNamespace(text_frame=TextFrameProperties(),has_text_content=True,placeholder_type=None,type='shape')
        placeholder=SimpleNamespace(text_frame=TextFrameProperties(),has_text_content=False,placeholder_type='title',type='shape')
        PresentationParser._apply_master_paragraph_styles([art,text,placeholder],root,'master')
        self.assertEqual(art.text_frame.style_layers,[])
        self.assertEqual(len(text.text_frame.style_layers),1)
        self.assertEqual(len(placeholder.text_frame.style_layers),1)
