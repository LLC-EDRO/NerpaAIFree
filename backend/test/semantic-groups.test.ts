import test from 'node:test';
import assert from 'node:assert/strict';
import {applySemanticGroups,needsGroupRepair,groundSemanticsInCards} from '../src/semantic-groups.js';
import {metricRepairIssues,holisticRepairIssues} from '../src/repair.js';
const semantics={fields:[
 {key:'value',role:'metric',group:'number',action:'replace'},
 {key:'caption',role:'metric_label',group:'caption',action:'replace'},
 {key:'brand',role:'brand',group:'brand',action:'preserve'},
]};
test('native card boundaries correct cross-card semantic pairs without template IDs or changing text roles',()=>{
 const source={fields:['titleA','captionB','titleB','captionA'].map((key,i)=>({key,role:i%2?'body':'title',action:'replace',group:i<2?'wrongA':'wrongB'}))};
 const anchors=['titleA','captionA','titleB','captionB'].map((sourceKey,i)=>({sourceKey,container:{shapeId:i<2?785:412,w:300,h:130}}));
 const fixed=groundSemanticsInCards(source,{page:{w:1000,h:600},textAnchors:anchors});
 assert.equal(fixed.fields.find(f=>f.key==='titleA').group,fixed.fields.find(f=>f.key==='captionA').group);
 assert.notEqual(fixed.fields[0].group,fixed.fields[1].group);
 assert.equal(source.fields[0].group,'wrongA');
 assert.deepEqual(fixed.fields.map(({group,...rest})=>rest),source.fields.map(({group,...rest})=>rest));
});
test('full-page panels and protected fields are not regrouped as cards',()=>{
 const anchors=semantics.fields.map(f=>({sourceKey:f.key,container:{shapeId:456,w:1000,h:600}}));
 assert.deepEqual(groundSemanticsInCards(semantics,{page:{w:1000,h:600},textAnchors:anchors}),semantics);
 const compact=anchors.map(a=>({...a,container:{...a.container,w:150,h:100}}));
 assert.deepEqual(groundSemanticsInCards(semantics,{page:{w:1000,h:600},textAnchors:compact}).fields[2],semantics.fields[2]);
});
test('AI grouping unlocks the associated caption for unit relocation without changing the source contract',()=>{
 assert.equal(needsGroupRepair(semantics.fields),true);
 const fixed=applySemanticGroups({groups:[['value','caption']]},semantics);
 assert.equal(needsGroupRepair(fixed.fields),false);
 assert.equal(semantics.fields[0].group,'number');
 assert.deepEqual(fixed.fields[2],semantics.fields[2]);
 assert.deepEqual(metricRepairIssues([{key:'value',reason:'overflow'}],fixed.fields).map(i=>i.key),['value','caption']);
});
test('group suggestions cannot omit fields, duplicate keys or unlock branding',()=>{
 for(const groups of [[['value']],[['value','caption','brand']],[['value','value']],[['value','unknown']]])
  assert.throws(()=>applySemanticGroups({groups},semantics));
});
test('repeated substantive conflicts unlock the slide for AI but preserve branding and do not escalate citation-only mistakes',()=>{
 const issues=[{key:'value',reason:'overflow'}];
 assert.equal(holisticRepairIssues(issues,semantics.fields,issues).length,1);
 assert.deepEqual(holisticRepairIssues(issues,semantics.fields,[...issues,...issues,...issues]).map(i=>i.key),['value','caption']);
 assert.equal(holisticRepairIssues([{key:'value',reason:'evidence_invalid'}],semantics.fields,[...issues,...issues,...issues]).length,1);
});
