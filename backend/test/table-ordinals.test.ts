import test from 'node:test';
import assert from 'node:assert/strict';
import { validateCopy,validateSemantics,type Layout } from '../src/domain.js';
const layout:Layout={id:'arbitrary',index:0,name:'Table',usable:true,warnings:[],charts:[],slots:[
 {key:'header',text:'№',cell:[0,0]}, {key:'row',text:'',cell:[1,0]}, {key:'stat',text:'',cell:[1,1]},
].map(s=>({...s,shapeId:913,role:'table_cell',maxChars:30,size:18,x:0,y:0,w:100,h:30}))};
const semantics=validateSemantics({composition:'Table',fields:layout.slots.map(s=>({key:s.key,role:'body',group:s.key,intent:'Text',action:'replace',required:true})),charts:[]},layout);
function check(header:string,ordinal:string,metric:string){return validateCopy({fields:{header:{text:header,evidence:[]},row:{text:ordinal,evidence:[]},stat:{text:metric,evidence:[]}},charts:{}},layout,semantics,'').issues;}
test('decorative Unicode ordinals normalize only inside the confirmed numbering column',()=>{
 for(const text of ['¹','①','１']) {
  const result=validateCopy({fields:{header:{text:'№',evidence:[]},row:{text,evidence:[]},stat:{text:'Example',evidence:[]}},charts:{}},layout,semantics,'');
  assert.equal(result.data.fields.row.text,'1');
 }
 const result=validateCopy({fields:{header:{text:'Год',evidence:[]},row:{text:'¹',evidence:[]},stat:{text:'Example',evidence:[]}},charts:{}},layout,semantics,'');
 assert.equal(result.data.fields.row.text,'¹');
});
test('native row ordinal needs no factual citation but adjacent metrics do',()=>{
 assert.deepEqual(check('№','1','Тезис'),[]);
 assert.ok(check('№','1','85%').some(i=>i.key==='stat'&&i.reason==='unsupported_number'));
});
test('changed column meaning or arbitrary values cannot bypass factual checks',()=>{
 assert.ok(check('Год','1','Тезис').some(i=>i.key==='row'&&i.reason==='unsupported_number'));
 assert.ok(check('№','42','Тезис').some(i=>i.key==='row'&&i.reason==='unsupported_number'));
});
