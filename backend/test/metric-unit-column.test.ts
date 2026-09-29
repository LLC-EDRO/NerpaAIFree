import test from 'node:test';
import assert from 'node:assert/strict';
import {validateCopy,validateSemantics} from '../src/domain.js';
const fixture=()=>{
  const slots=[['unit',200,70,'100'],['heading',200,20,'Текст'],['value',30,70,'100']].map(([key,x,y,text],i)=>({key:String(key),x:Number(x),y:Number(y),w:120,h:20,text:String(text),size:12,shapeId:i+1,role:'body' as const,maxChars:40}));
  const layout={id:'any',index:0,name:'Any',usable:true,warnings:[],charts:[],slots};
  const semantics=validateSemantics({composition:'Rows',charts:[],fields:slots.map(s=>({key:s.key,role:s.key==='heading'?'body':'metric',group:s.key==='heading'?'header':'row',intent:s.key,action:'replace',required:true}))},layout);
  const data={fields:{unit:{text:'тыс. руб./мес.',evidence:['120 тыс. руб./мес.']},value:{text:'120',evidence:['120 тыс. руб./мес.']},heading:{text:'Единица',evidence:[]}},charts:{}};
  return {layout,semantics,data};
};
test('separate value and unit in a graphic table do not demand two numbers',()=>{
 const {layout,semantics,data}=fixture();
 assert.deepEqual(validateCopy(data,layout,semantics,'120 тыс. руб./мес.').issues,[]);
 data.fields.value.text='999';
 assert.ok(validateCopy(data,layout,semantics,'120 тыс. руб./мес.').issues.some(i=>i.key==='value'&&i.reason==='unsupported_number'));
});
test('unit exception requires the actual column heading and a numeric peer in the same row',()=>{
 for(const mode of ['header','row','number','group']){
  const {layout,semantics,data}=fixture();
  if(mode==='header')data.fields.heading.text='Выручка';
  if(mode==='row')layout.slots.find(s=>s.key==='value')!.y+=35;
  if(mode==='number')data.fields.value.text='—';
  if(mode==='group')semantics.fields.find(s=>s.key==='value')!.group='unrelated';
  assert.ok(validateCopy(data,layout,semantics,'120 тыс. руб./мес.').issues.some(i=>i.key==='unit'&&i.reason==='metric_fact_required'),mode);
 }
});
test('native table addresses keep unit columns separate between tables and rows',()=>{
 const {layout,semantics,data}=fixture();
 for(const s of layout.slots){Object.assign(s,{shapeId:90,cell:s.key==='heading'?[0,1]:s.key==='unit'?[1,1]:[1,0]});}
 assert.deepEqual(validateCopy(data,layout,semantics,'120 тыс. руб./мес.').issues,[]);
 layout.slots.find(s=>s.key==='heading')!.shapeId=91;
 assert.ok(validateCopy(data,layout,semantics,'120 тыс. руб./мес.').issues.some(i=>i.key==='unit'&&i.reason==='metric_fact_required'));
});
