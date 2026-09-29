import json,tempfile,unittest
from pathlib import Path
from runtime.assembly_cache import select_cached_analysis
from runtime.model_store import read_model

class SelectedCacheTest(unittest.TestCase):
    def test_reorder_duplicate_and_table_bindings_without_reparse(self):
        with tempfile.TemporaryDirectory() as tmp:
            source=Path(tmp)/'source';out=Path(tmp)/'selected';source.mkdir();out.mkdir()
            (out/'presentation.pptx').write_bytes(b'already assembled by native code')
            layouts=[dict(id=f'slide_{i}',index=i-1,part=f'ppt/slides/slide{i}.xml',slots=[dict(key='s4',text='original')]) for i in (1,2,3)]
            slides=[dict(slide_id=l['id'],source_part=l['part'],slide_index=l['index'],objects=[dict(source_object_id=l['id']+'_obj_4',source_part=l['part'],shape_id=4)]) for l in layouts]
            model=dict(slides=slides,masters=[],layouts=[],tables=[dict(source_part=layouts[1]['part'],table_id='slide_2_table_1',linked_object_id='slide_2_obj_4')])
            values={'analysis':dict(layouts=layouts,preparedSha256='old'),'frames':{l['id']:{'s4':{'width_emu':123}} for l in layouts},'effective-parser':model,'analyzer':{'slide_assignments':[dict(slide_id=l['id'],role_assignments=[]) for l in layouts]}}
            for key,value in values.items():(source/(key+'.json')).write_text(json.dumps(value))
            result=select_cached_analysis(source,['slide_2','slide_1','slide_2'],out)
            parsed=read_model(out/'effective-parser.json')
            self.assertNotIn('preparedSha256',result)
            self.assertEqual([l['id'] for l in result['layouts']],['selected_1','selected_2','selected_3'])
            self.assertEqual([s['source_part'] for s in parsed['slides']],[f'ppt/slides/labSlide{i}.xml' for i in (1,2,3)])
            self.assertEqual([t['linked_object_id'] for t in parsed['tables']],['selected_1_obj_4','selected_3_obj_4'])
            self.assertEqual(len(parsed['slides']),3)
            self.assertEqual(json.loads((out/'frames.json').read_text())['selected_3']['s4']['width_emu'],123)
            self.assertEqual(slides[1]['slide_id'],'slide_2')
