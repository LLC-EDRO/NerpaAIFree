import test from 'node:test';
import assert from 'node:assert/strict';
import {copySchema,validateCopy,type Layout} from '../src/domain.js';
test('empty chart arrays normalize without hiding missing native charts',()=>{
 assert.deepEqual(copySchema.parse({fields:{},charts:[]}).charts,{});
 const layout:Layout={id:'any',index:0,name:'Chart',usable:true,warnings:[],slots:[],charts:[{key:'actual-chart'}]};
 assert.throws(()=>validateCopy({fields:{},charts:[]},layout,{composition:'chart',fields:[],charts:[]},''),/charts/);
});
test('nonempty chart arrays are not silently discarded',()=>{
 assert.throws(()=>copySchema.parse({fields:{},charts:[{title:'Data'}]}));
});
