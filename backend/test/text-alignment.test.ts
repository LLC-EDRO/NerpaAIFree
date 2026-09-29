import test from 'node:test';
import assert from 'node:assert/strict';
import { selectedAlignments, applicableAlignment } from '../src/text-alignment.js';
import { nativeSlide } from '../src/domain.js';
const choice={horizontal:'preserve',vertical:'center',reason:'Standalone label in a tall card'};
const slot={key:'label',shapeId:1,role:'title',x:10,y:10,w:200,h:180,size:30,maxChars:100,text:'Title'};
test('vertical alignment is independent of horizontal alignment and survives native request',()=>{
 const a=selectedAlignments([{key:'label',group:'solo',action:'replace',role:'title',alignment:choice}],[slot]);
 const slide=nativeSlide({fields:{label:{text:'Heading',evidence:[]}},charts:{}},{id:'test',index:0,name:'test',usable:true,warnings:[],slots:[slot],charts:[],textAlignment:a},'Topic',1);
 assert.deepEqual(slide.native.textAlignment.label,choice);
});
test('linked metric groups, tables, protected fields and ordinary copy stay unchanged',()=>{
 const fields=[{key:'label',group:'pair',action:'replace',role:'metric',alignment:choice},{key:'unit',group:'pair',action:'replace',role:'unit',alignment:choice}];
 assert.deepEqual(selectedAlignments(fields,[slot,{...slot,key:'unit'}]),{});
 assert.equal(applicableAlignment(choice,{...slot,cell:[0,0]},'Heading'),undefined);
 assert.equal(applicableAlignment(choice,slot,'A'.repeat(161)),undefined);
 assert.equal(applicableAlignment(choice,slot,'• First\n• Second'),undefined);
 assert.equal(applicableAlignment(choice,{...slot,h:30},'Heading'),undefined);
 assert.deepEqual(selectedAlignments([{key:'label',group:'solo',role:'brand',action:'preserve',alignment:choice}],[slot]),{});
});
test('horizontal center can be chosen without moving text vertically; invalid suggestion is ignored',()=>{
 assert.deepEqual(applicableAlignment({...choice,horizontal:'center',vertical:'preserve'},slot,'Label'),{...choice,horizontal:'center',vertical:'preserve'});
 assert.equal(applicableAlignment({horizontal:'justify'},slot,'Label'),undefined);
});
test('AI centering intent survives a short frame for native container resolution; lists still opt out',()=>{
 const layout:any={id:'small',slots:[{...slot,h:15}],charts:[],textAlignment:{label:choice}};
 const a=nativeSlide({fields:{label:{text:'Motor',evidence:[]}},charts:{}},layout,'Topic',1);
 assert.equal(a.native.alignmentIntent.label.vertical,'center');
 assert.equal(a.native.textAlignment.label,undefined);
 const b=nativeSlide({fields:{label:{text:'• One\n• Two',evidence:[]}},charts:{}},layout,'Topic',1);
 assert.deepEqual(b.native.alignmentIntent,{});
});
test('a semantic process chain does not suppress AI centering in separate cards',()=>{
 const fields=['a','b'].map(key=>({key,group:'same_process',role:'body',action:'replace',alignment:choice}));
 assert.equal(Object.keys(selectedAlignments(fields,[{...slot,key:'a'},{...slot,key:'b',x:400}])).length,2);
});
