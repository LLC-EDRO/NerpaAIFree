import unittest
import tempfile
import json
import io
from pathlib import Path
from unittest.mock import patch
from runtime.analysis import validate_fields
import bridge

class WarningExportTest(unittest.TestCase):
    def test_original_slide_preservation_cannot_smuggle_edits(self):
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d)
            (folder/'analysis.json').write_text(json.dumps(dict(layouts=[dict(id='a',slots=[dict(key='s1',text='Source')])])) )
            native=dict(sourceSlideId='a',mode='source',preserveSource=True,fields={'s1':'Source'},charts={})
            with patch('runtime.analysis.read_frames',return_value={}):
                self.assertEqual(validate_fields(folder,[dict(native=native)]),[])
                native['fields']['s1']='Unvalidated edit'
                with self.assertRaisesRegex(ValueError,'pptx_invalid_source_preservation'):
                    validate_fields(folder,[dict(native=native)])
    def run_export(self, preview_failure=False, check_failure=False):
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d);output=folder/'output'
            def fill(*args):
                output.mkdir(exist_ok=True);(output/'presentation.pptx').write_bytes(b'valid-file-placeholder')
                return dict(issues=[dict(slide=0,reason='overflow')],pptxWritten=True,packageCleanup={},fieldChanges=[])
            request=dict(action='export',folder=d,output=str(output),slides=[{}],images={},soffice='unused',allowQualityWarnings=True)
            with patch('bridge.sys.stdin',io.StringIO(json.dumps(request))),patch('bridge.fill',side_effect=fill),patch('bridge.render',side_effect=RuntimeError('renderer failed') if preview_failure else None),patch('bridge.inspect_render',side_effect=RuntimeError('checker failed') if check_failure else None,return_value=dict(version=1,pages=[],issues=[])):
                result=bridge.main()
            self.assertTrue(result['pptxWritten'])
            self.assertTrue((output/'render-quality.json').exists())
            return result
    def test_renderer_failure_keeps_pptx_with_explicit_warning(self):
        result=self.run_export(preview_failure=True)
        self.assertFalse(result['pdfAvailable'])
        self.assertEqual(result['warnings'][0]['reason'],'preview_unavailable')
    def test_inspector_failure_keeps_pdf_and_pptx_with_explicit_warning(self):
        result=self.run_export(check_failure=True)
        self.assertTrue(result['pdfAvailable'])
        self.assertEqual(result['warnings'][0]['reason'],'quality_check_unavailable')
