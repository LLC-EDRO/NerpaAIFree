import test from 'node:test';import assert from 'node:assert/strict';import {copyOutputSchema} from '../src/copy-output-schema.js';
test('schema constrains addresses without hard character caps that break natural language',()=>{
 const layout:any={slots:[{key:'body',maxChars:200,textFit:{targetChars:100}},{key:'metric',maxChars:2,textFit:{targetChars:1}}],charts:[]};
 const initial=copyOutputSchema(layout,[]);
 assert.equal(initial.properties.fields.properties.body.$ref,'#/$defs/field');assert.equal(initial.properties.fields.properties.metric.$ref,'#/$defs/field');
 assert.ok(!JSON.stringify(initial).includes('maxLength'));
 const repair=copyOutputSchema(layout,[{key:'metric',reason:'overflow'}]);
 assert.deepEqual(repair.properties.fields.required,['metric']);assert.equal(repair.properties.fields.properties.metric.$ref,'#/$defs/field');assert.ok(repair.properties.repairAnalysis);
});
