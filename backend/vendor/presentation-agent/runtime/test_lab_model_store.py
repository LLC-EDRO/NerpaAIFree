import json,tempfile,unittest
from pathlib import Path
from pydantic import BaseModel
from runtime.model_store import write_model,read_model
class Entry(BaseModel):
    text:str
    nested:list['Entry']=[]
class ModelStoreTest(unittest.TestCase):
    def test_stream_has_same_json_contract_as_pydantic(self):
        shared=Entry(text='Привет')
        model=Entry(text='root',nested=[shared,shared])
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'parser.json';write_model(p,model)
            self.assertEqual(json.loads(p.read_text()),json.loads(model.model_dump_json()))
    def test_repeated_xml_is_lossless_and_shared_after_load(self):
        fragment='<a:p xmlns:a="test">'+'text'*100+'</a:p>'
        model=Entry(text='root',nested=[Entry(text=fragment) for _ in range(100)])
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'model.json';write_model(p,model);restored=read_model(p)
            self.assertEqual(restored,json.loads(model.model_dump_json()))
            self.assertLess(p.stat().st_size,len(model.model_dump_json())//3)
            self.assertIs(restored['nested'][0]['text'],restored['nested'][1]['text'])
