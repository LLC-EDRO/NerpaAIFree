import test from 'node:test';
import assert from 'node:assert/strict';
import { numbers, evidenceNumbers } from '../src/domain.js';
test('calendar dates agree across dotted and Russian notation without decimal truncation',()=>{
 assert.deepEqual(numbers('Срок 1.10.2027'), numbers('Срок 1 октября 2027'));
 assert.notDeepEqual(numbers('1.10.2027'), numbers('1.1.2027'));
 assert.ok(!numbers('1.10.2027').includes('1.1'));
 assert.ok(evidenceNumbers('1 октября 2027').includes('2027'));
 assert.ok(!evidenceNumbers('2027').includes('date:2027-10-01'));
 assert.deepEqual(numbers('1.10%'),['1.1']);
});
test('native fact checks recognize typographical thousands without merging years or decimals',()=>{
 assert.deepEqual(numbers('12 841; 1\u00a0065,7; 25\u202f000; 2024 2025'),['12841','1065.7','25000','2024','2025']);
 assert.deepEqual(numbers('2–3 дня; 1.5%'),['2','3','1.5']);
});
test('spelled time ranges agree with numeric copy but unrelated/composite words are not numbers',()=>{
 const values=evidenceNumbers('двух-трёхдневный аудит, пятилетний план, четырёхчасовая проверка');
 for(const n of ['2','3','5','4'])assert.ok(values.includes(n));
 assert.ok(!evidenceNumbers('двадцать один человек').includes('1'));
 assert.deepEqual(evidenceNumbers('трибуна, двухсотлетний'),[]);
});
test('fractions preserve numerator/denominator as a single value',()=>{
 assert.deepEqual(numbers('≈1 / 3; 2/3; 2018'),['1/3','2/3','2018']);
 assert.ok(evidenceNumbers('около одной трети потока').includes('1/3'));
 assert.ok(evidenceNumbers('две трети').includes('2/3'));
 assert.ok(!evidenceNumbers('две трети').includes('1/3'));
 assert.ok(!evidenceNumbers('третьего этапа').includes('1/3'));
});
test('known internal citations move out of slide text; unknown markers remain detectable',async()=>{
 const {extractInlineEvidence}=await import('../src/domain.js');
 assert.deepEqual(extractInlineEvidence('42% [evidence-1]',new Map([['evidence-1','Confirmed 42%']])) ,{text:'42%',evidence:['Confirmed 42%']});
 assert.deepEqual(extractInlineEvidence('42% [evidence-999]',new Map()),{text:'42% [evidence-999]',evidence:[]});
});
test('table citation completion is limited to same row and same source identity',async()=>{
 const {validateCopy}=await import('../src/domain.js');
 const facts=[{id:'f1',text:'В том же аудите переработано 60%.',sourceIds:['report']},{id:'f2',text:'Аудит выполнен в 2018 году.',sourceIds:['report']}];
 const layout:any={slots:[{key:'year',shapeId:7,cell:[1,0],text:'Год'},{key:'context',shapeId:7,cell:[1,1],text:'Контекст'}],charts:[]};
 const semantics:any={fields:layout.slots.map((s:any)=>({key:s.key,action:'replace',role:'body',required:true})),charts:[]};
 const data={fields:{year:{text:'2018',evidence:['f1']},context:{text:'Аудит',evidence:['f2']}},charts:{}};
 const check=(l=layout,f=facts)=>validateCopy(data,l,semantics,f.map(x=>x.text).join('\n'),f);
 assert.deepEqual(check().issues,[]);
 assert.ok(check({...layout,slots:[layout.slots[0],{...layout.slots[1],cell:[2,1]}]}).issues.some((i:any)=>i.reason==='unsupported_number'));
 assert.ok(check(layout,[facts[0],{...facts[1],sourceIds:['different-report']}]).issues.some((i:any)=>i.reason==='unsupported_number'));
 assert.deepEqual(data.fields.year.evidence,['f1']);
});
test('invalid prose citations are removed, while ungrounded numeric claims remain blocked',async()=>{
 const {validateCopy}=await import('../src/domain.js');
 const layout:any={slots:[{key:'text',text:'',role:'body'}],charts:[]},semantics:any={fields:[{key:'text',role:'body',action:'replace',required:true}],charts:[]};
 const check=(text:string)=>validateCopy({fields:{text:{text,evidence:['Invented quote']}},charts:{}},layout,semantics,'');
 const prose=check('Рекомендуется провести аудит');assert.deepEqual(prose.issues,[]);assert.deepEqual(prose.data.fields.text.evidence,[]);assert.equal(prose.citationWarnings.length,1);
 assert.ok(check('99% организаций').issues.some(i=>i.reason==='unsupported_number'));
});
test('signed quantities, decimal equivalence and comma sequences retain their meanings',()=>{
 assert.deepEqual(numbers('−3,1; +2,20; -1.40; 24.0; -0,0'),['-3.1','2.2','-1.4','24','0']);
 assert.deepEqual(numbers('1,2,3,4: 20,24,28,23 Н; 14,5 мм'),['1','2','3','4','20','24','28','23','14.5']);
 assert.deepEqual(numbers('2–3; 2-3; evidence-121'),['2','3','2','3','121']);
});
test('negative chart facts accept only the correct sign',async()=>{
 const {validateCopy}=await import('../src/domain.js');
 const source='Отклонение А −3,1 мм; Б +2,2 мм.';
 const layout:any={slots:[],charts:[{key:'c',pointCount:2,seriesCount:1,type:'barChart'}]};
 const semantics:any={fields:[],charts:[{key:'c',intent:'Отклонения'}]};
 const check=(values:number[])=>validateCopy({fields:{},charts:{c:{title:'мм',categories:['А','Б'],series:[{name:'Отклонение',values}],evidence:[source]}}},layout,semantics,source);
 assert.deepEqual(check([-3.1,2.2]).issues,[]);
 assert.ok(check([3.1,2.2]).issues.some(i=>i.reason==='chart_evidence'));
});
