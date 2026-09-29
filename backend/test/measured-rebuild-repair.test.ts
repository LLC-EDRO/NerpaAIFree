import test from 'node:test';
import assert from 'node:assert/strict';
import {separateRebuiltFrames,replaceUnsupportedGlyphs} from '../src/measured-rebuild-repair.js';
const fixture=()=>{
 const texts=[{key:'heading',x:50,y:30,w:100,h:24,size:14},{key:'caption',x:50,y:50,w:100,h:20,size:10}];
 const page={x:0,y:0,w:400,h:300};
 return {scene:{texts,tables:[],charts:[],pictures:[]},profile:{page,textAnchors:texts.map(t=>({...t,allowFrameOverflow:true,repairRegion:page})),protectedBoxes:[],decorations:[]},issues:[{key:'caption',details:['rebuild_overlap'],blocker:texts[0]}]};
};
test('measured frame separation preserves words, source order, width and fonts',()=>{
 const {scene,profile,issues}=fixture(),before=structuredClone(scene);
 const next=separateRebuiltFrames(scene,issues,profile);
 assert.equal(next.texts[1].y,56);assert.deepEqual(next.texts[0],scene.texts[0]);
 assert.deepEqual({...next.texts[1],y:50},scene.texts[1]);assert.deepEqual(scene,before);
});
test('painted shapes and dense/protected neighbouring content do not get moved blindly',()=>{
 const {scene,profile,issues}=fixture();profile.textAnchors.forEach(a=>a.allowFrameOverflow=false);
 assert.equal(separateRebuiltFrames(scene,issues,profile),undefined);
 profile.textAnchors.forEach(a=>{a.allowFrameOverflow=true;a.repairRegion={x:a.x,y:a.y,w:a.w,h:a.h};});
 assert.equal(separateRebuiltFrames(scene,issues,profile),undefined);
});
test('only native-proven equivalent glyphs are replaced, with evidence unchanged',()=>{
 const data={fields:{x:{text:'40 → 12 минут',evidence:['f1']},logo:{text:'A → B',evidence:[]}},charts:{}};
 const issue={key:'x',details:['native_source_font_missing_glyph'],characterReplacements:{'→':'->'}};
 const result=replaceUnsupportedGlyphs(data,[issue,{...issue,key:'logo'}],new Set(['logo']));
 assert.equal(result.fields.x.text,'40 -> 12 минут');assert.deepEqual(result.fields.x.evidence,['f1']);
 assert.equal(result.fields.logo.text,data.fields.logo.text);assert.equal(data.fields.x.text,'40 → 12 минут');
 assert.equal(replaceUnsupportedGlyphs(data,[{...issue,characterReplacements:{'→':'100'}}]),undefined);
 assert.equal(replaceUnsupportedGlyphs(data,[{...issue,details:['source_text_frame_overflow']}]),undefined);
});
test('a transparent caption can clear a fixed picture edge without moving the picture',()=>{
 const page={x:0,y:0,w:600,h:400};
 const label={key:'caption',x:200,y:150,w:120,h:20,size:12};
 const picture={shapeId:42,x:315,y:140,w:180,h:35};
 const scene={texts:[label],pictures:[picture],charts:[],tables:[]};
 const profile={page,textAnchors:[{...label,allowFrameOverflow:true,repairRegion:page}],protectedBoxes:[],decorations:[]};
 const issues=[{key:'caption',details:['rebuild_overlap'],blocker:picture}];
 const next=separateRebuiltFrames(scene,issues,profile);
 assert.equal(next.texts[0].x,193);assert.equal(next.texts[0].y,150);
 assert.deepEqual(next.pictures,scene.pictures);assert.equal(scene.texts[0].x,200);
 profile.protectedBoxes=[{x:185,y:150,w:15,h:20}] as any;
 const blocked=separateRebuiltFrames(scene,issues,profile);
 assert.ok(!blocked||blocked.texts[0].x!==193);
});
