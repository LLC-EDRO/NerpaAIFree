import test from 'node:test';
import assert from 'node:assert/strict';
import {preserveMetricComposition,retainsSourceLineContact} from '../src/source-composition.js';
import {separateRebuiltFrames} from '../src/measured-rebuild-repair.js';
import {fittedRebuildSizes,rebuildLayout} from '../src/slide-rebuild.js';

function fixture(){
 const page={x:0,y:0,w:720,h:405};
 const pictures=[{shapeId:30,x:27,y:220,w:180,h:2},{shapeId:31,x:277,y:220,w:180,h:2}];
 const texts=pictures.map((p,i)=>({key:i?'r_value':'r_title',sourceKey:String(i),shapeId:50+i,x:20+i*250,y:90,w:180,h:150,size:100,minSize:12,font:'Arial',color:'#0077FF',bold:true,align:'left',verticalAlign:'bottom',allowFrameOverflow:true,repairRegion:page,sourceLineContacts:[{shapeId:p.shapeId,axis:'horizontal',box:p}]}));
 const profile={version:8,page,textAnchors:texts,metricAlignmentGroups:[{keys:texts.map(t=>t.key),axis:'bottom',sameSize:true}],pictures:pictures.map(p=>({shapeId:p.shapeId,box:p})),charts:[],tables:[],decorations:[],protectedBoxes:[],fonts:['Arial'],colors:['#0077FF'],fingerprint:'fixture'};
 const scene={texts:texts.map(t=>({...t,size:70})),pictures,charts:[],tables:[]};return {profile,scene};
}
test('intentional raster rail contact never lifts just one metric',()=>{
 const {profile,scene}=fixture();scene.texts[0].x+=7;
 assert.ok(retainsSourceLineContact(scene.texts[0],scene.pictures[0],profile));
 assert.equal(separateRebuiltFrames(scene,[{key:'r_title',details:['rebuild_overlap'],blocker:scene.pictures[0]}],profile),undefined);
 assert.equal(retainsSourceLineContact(scene.texts[0],{...scene.pictures[0],shapeId:99},profile),false);
});
test('AI height and font changes retain the source baseline and equal metric sizes',()=>{
 const {profile,scene}=fixture();scene.texts[0].h=120;scene.texts[1].size=60;
 const normalized=preserveMetricComposition(scene.texts,profile);
 assert.deepEqual(normalized.map(t=>t.y+t.h),[240,240]);assert.deepEqual(normalized.map(t=>t.size),[60,60]);
 assert.equal(scene.texts[0].h,120);assert.equal(scene.texts[0].y,90);
 assert.deepEqual(preserveMetricComposition(scene.texts,{...profile,metricAlignmentGroups:[]}),scene.texts);
});
test('a measured width correction uses a common size without another model request',()=>{
 const {profile,scene}=fixture();const next=fittedRebuildSizes(scene,[{key:'r_value',reason:'overflow',details:['source_text_frame_overflow'],measuredWidthPt:240,availableWidthPt:180}],profile);
 assert.equal(next.texts[0].size,next.texts[1].size);assert.ok(next.texts[0].size<70);
 assert.deepEqual(next.texts.map((t:any)=>t.y+t.h),[240,240]);
});
test('rebuild proposal normalization applies composition before creating native slots',()=>{
 const {profile,scene}=fixture();const texts=scene.texts.map(({key,x,y,w,h,size,font,color,bold,align})=>({key,x,y,w,h:h-20,size,font,color,bold,align}));
 const result=rebuildLayout({...scene,texts,reason:'Fit the values',data:{fields:{r_title:{text:'42',evidence:[]},r_value:{text:'57',evidence:[]}},charts:{}}},{id:'arbitrary',slots:[],charts:[]} as any,profile);
 assert.deepEqual(result.scene.texts.map(t=>t.y+t.h),[240,240]);
 assert.deepEqual(result.layout.slots.map(t=>t.y+t.h),[240,240]);
});
test('real caption collisions move the caption without detaching a metric from its rail',()=>{
 const {profile,scene}=fixture();const caption={key:'caption',x:20,y:235,w:190,h:30,size:14,allowFrameOverflow:true,repairRegion:profile.page};
 (profile.textAnchors as any[]).push(caption);(scene.texts as any[]).push(caption);
 const next=separateRebuiltFrames(scene,[{key:'r_title',details:['rebuild_overlap'],blocker:caption}],profile);
 assert.ok(next);assert.equal(next.texts[0].y,90);assert.ok(next.texts[2].y>=242);
});
