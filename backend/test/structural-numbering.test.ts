import test from 'node:test';
import assert from 'node:assert/strict';
import {structuralPrefixes,structuralOrdinalMarkers} from '../src/structural-numbering.js';
import {validateCopy} from '../src/domain.js';

function validate(texts:string[],role='body',sourceTexts?:string[]) {
 const slots=texts.map((text,i)=>({key:`field${i}`,shapeId:i+1,text:sourceTexts?.[i] || 'Example',role:'body',size:14,maxChars:1000,x:i*200,y:50,w:190,h:100}));
 const layout:any={id:'arbitrary-source',slots,charts:[]};
 const semantics:any={composition:'Steps',fields:slots.map(s=>({key:s.key,role,intent:'Step heading',group:'steps',action:'replace',required:true})),charts:[]};
 return validateCopy({fields:Object.fromEntries(slots.map((s,i)=>[s.key,{text:texts[i],evidence:[]}])),charts:{}},layout,semantics,'');
}

test('zero-padded numbered headings are structure in normal source filling',()=>{
 const texts=['01 Откройте «Активация»','02 Выберите путь','03 Проверьте статус','04 Устраните сбой'];
 const result=validate(texts);
 assert.deepEqual(result.issues,[]);
 assert.deepEqual(Object.values(result.data.fields).map(v=>v.text),texts);
});
test('common ordinal formats work without template-specific shape IDs',()=>{
 for(const texts of [['1. Начните','2. Продолжите'],['1)Начните','2)Продолжите'],['01. Начните','02. Продолжите'],['Шаг 1: Начните','Шаг 2: Продолжите'],['Этап 1 — Начните','Этап 2 — Продолжите'],['1 — Начните','2 — Продолжите']]) assert.deepEqual(validate(texts).issues,[],texts.join(' / '));
 assert.deepEqual(validate(['1 Начните','2 Продолжите'],'step_number').issues,[]);
});
test('only ordinal prefix is exempt; factual quantities still require evidence',()=>{
 const issues=validate(['01 Наймите 20 сотрудников','02 Проверьте 30 заявок']).issues;
 assert.deepEqual(issues.map(i=>i.unsupportedNumbers),[['20'],['30']]);
 for (const texts of [['01 млн рублей','02 млн рублей'],['01 января','02 февраля'],['1 посетитель','2 посетителя'],['2024 Итоги','2025 План'],['01 Начните','03 Закончите'],['01 Начните','01 Продолжите']]) assert.ok(validate(texts).issues.length,texts.join(' / '));
 assert.ok(validate(['01 Прибыль','02 Выручка'],'metric').issues.length);
});
test('native cells never inherit heading ordinal exemptions',()=>{
 assert.equal(structuralPrefixes([{key:'a',text:'01 Начните',cell:[1,0]},{key:'b',text:'02 Продолжите',cell:[2,0]}]).size,0);
});

test('standalone source badges keep their field identity, punctuation and leading zeros',()=>{
 const result=validate(['03','01','02'],'label',['01.','02.','03.']);
 assert.deepEqual(Object.values(result.data.fields).map(v=>v.text),['01.','02.','03.']);
 assert.deepEqual(result.issues,[]);
 for(const texts of [['1','2'],['01','03'],['01','01']])
   assert.equal(structuralOrdinalMarkers(texts.map((text,i)=>({key:String(i),text}))).size,0);
 for(const extra of [{role:'metric'},{cell:[1,0]}])
   assert.equal(structuralOrdinalMarkers(['01.','02.'].map((text,i)=>({key:String(i),text,...extra}))).size,0);
});

test('source ordinals survive model shortening, glued source runs and continuation on another slide',()=>{
 const result=validate(['Начните','Продолжите'],'body',['01Организатор','02Структура']);
 assert.deepEqual(Object.values(result.data.fields).map(v=>v.text),['01 Начните','02 Продолжите']);
 assert.deepEqual(result.issues,[]);
 assert.deepEqual(validate(['03 Продолжите','04 Завершите']).issues,[]);
 assert.deepEqual(validate(['Откройте раздел','02 Продолжите','03 Завершите'],'step_number').issues,[]);
});
