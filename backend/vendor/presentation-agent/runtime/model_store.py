"""Serialize large parser models without duplicating the entire object graph."""
import json
import gzip
import base64
import io
import tempfile
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from pydantic import BaseModel


def model_value(value):
    if isinstance(value, BaseModel):
        return dict(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (set, frozenset, tuple)):
        return list(value)
    raise TypeError(type(value).__name__)


def write_model(path, model):
    # DrawingML stores the same XML style fragments thousands of times in
    # icon-heavy decks. Intern those strings losslessly instead of copying
    # hundreds of MB into each parser snapshot and every subsequent load.
    counts={}
    def visit(value):
        if isinstance(value,BaseModel):
            for _,child in value:visit(child)
        elif isinstance(value,dict):
            for child in value.values():visit(child)
        elif isinstance(value,(list,tuple)):
            for child in value:visit(child)
        elif isinstance(value,str) and len(value)>120 and value.lstrip().startswith('<'):
            counts[value]=counts.get(value,0)+1
    visit(model)
    shared=[s for s,count in counts.items() if count>1]
    indexes={s:i for i,s in enumerate(shared)}
    def encode(value):
        if isinstance(value,str):return {'$nerpaXml':indexes[value]} if value in indexes else value
        if isinstance(value,BaseModel):return {k:encode(v) for k,v in value}
        if isinstance(value,dict):return {k:encode(v) for k,v in value.items()}
        if isinstance(value,(list,tuple)):return [encode(v) for v in value]
        return value
    encoded=encode(model) if shared else model
    if shared:encoded={**encoded,'_storageVersion':1,'_sharedXml':shared}
    if not shared:
        with path.open('w', encoding='utf-8') as stream:
            json.dump(encoded, stream, default=model_value, ensure_ascii=False, separators=(',', ':'))
        return
    # Keep a valid JSON envelope and stream the compressed payload; neither a
    # full serialized string nor a second uncompressed file is held in memory.
    with tempfile.TemporaryFile() as compressed:
        with gzip.GzipFile(fileobj=compressed, mode='wb', compresslevel=1, mtime=0) as gz:
            with io.TextIOWrapper(gz, encoding='utf-8') as stream:
                json.dump(encoded, stream, default=model_value, ensure_ascii=False, separators=(',', ':'))
        compressed.seek(0)
        with path.open('w', encoding='utf-8') as stream:
            stream.write('{"_storageVersion":2,"codec":"gzip+base64","data":"')
            while chunk := compressed.read(48*1024):
                stream.write(base64.b64encode(chunk).decode('ascii'))
            stream.write('"}')


def read_model(path):
    with path.open(encoding='utf-8') as stream:value=json.load(stream)
    if value.get('_storageVersion')==2:
        if value.get('codec')!='gzip+base64':raise ValueError('pptx_invalid_model_codec')
        with gzip.GzipFile(fileobj=io.BytesIO(base64.b64decode(value['data'],validate=True))) as stream:
            value=json.load(stream)
    if value.get('_storageVersion')!=1:return value
    shared=value.pop('_sharedXml');value.pop('_storageVersion')
    def decode(obj):
        if isinstance(obj,dict):
            if set(obj)=={'$nerpaXml'}:
                index=obj['$nerpaXml']
                if type(index)!=int or not 0<=index<len(shared):raise ValueError('pptx_invalid_model_reference')
                return shared[index]
            for k,v in obj.items():obj[k]=decode(v)
        elif isinstance(obj,list):
            for i,v in enumerate(obj):obj[i]=decode(v)
        return obj
    return decode(value)
