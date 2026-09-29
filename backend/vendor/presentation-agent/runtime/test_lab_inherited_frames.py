import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch
from runtime.safe_text_expansion import ExpansionContext
from runtime.frame_appearance import transparent_inheritance
from runtime.security import NS,xml
class InheritedFrameTest(unittest.TestCase):
    def test_context_resolves_master_and_layout_placeholder_collections(self):
        def part(ident,style):
            return (f'<p:sld xmlns:p="{NS["p"]}" xmlns:a="{NS["a"]}"><p:cSld><p:spTree><p:sp><p:nvSpPr><p:cNvPr id="{ident}"/></p:nvSpPr><p:spPr>{style}</p:spPr></p:sp></p:spTree></p:cSld></p:sld>').encode()
        parts={'master':part(4,'<a:noFill/><a:ln><a:noFill/></a:ln>'),'layout':part(11,''),'slide':part(77,'')}
        master=dict(source_object_id='M',shape_id=4,source_part='master')
        layout=dict(source_object_id='L',shape_id=11,source_part='layout')
        slide=dict(source_object_id='S',shape_id=77,source_part='slide',source_level='slide',placeholder_type='subTitle',text_frame={'style_layers':[{'source_object_id':s} for s in ('M','L','S')]})
        model=dict(masters=[{'placeholders':[master]}],layouts=[{'placeholders':[layout]}],slides=[{'slide_id':'page','objects':[slide]}])
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d);(folder/'source.pptx').write_bytes(b'fixture')
            with patch('runtime.model_store.read_model',return_value=model),patch('runtime.safe_text_expansion.effective_source',return_value=folder/'source.pptx'),patch('runtime.safe_text_expansion.read_package',return_value=parts):
                context=ExpansionContext(folder,dict(width=720,height=405,layouts=[{'id':'page','part':'slide'}]))
                self.assertTrue(context.transparent_placeholder('page',77))
    def shape(self,style):
        return xml((f'<p:sp xmlns:p="{NS["p"]}" xmlns:a="{NS["a"]}"><p:spPr>{style}</p:spPr></p:sp>').encode())
    def test_explicit_transparency_survives_empty_placeholder_layers(self):
        chain=[self.shape('<a:noFill/><a:ln><a:noFill/></a:ln>'),self.shape('<a:prstGeom prst="rect"/>'),self.shape('')]
        self.assertTrue(transparent_inheritance(chain))
    def test_unknown_visible_theme_and_effects_are_not_guessed_transparent(self):
        self.assertFalse(transparent_inheritance([self.shape('')]))
        for style in ('<a:solidFill/>','<a:effectLst><a:outerShdw/></a:effectLst>','<a:ln><a:solidFill/></a:ln>'):
            self.assertFalse(transparent_inheritance([self.shape('<a:noFill/><a:ln><a:noFill/></a:ln>'),self.shape(style)]))
