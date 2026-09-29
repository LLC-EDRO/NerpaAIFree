import test from 'node:test';
import assert from 'node:assert/strict';
import {layoutConflictCandidates,earlyLayoutRepairIssues,requiresLayoutRepair} from '../src/layout-conflicts.js';
const field=(key:string,x:number,shapeId:number)=>({key,x,y:10,w:100,h:40,shapeId,text:'Content'});
const layout={slots:[field('a',10,1),field('b',50,2),field('c',220,3)],charts:[]};
test('Intersecting frames require AI confirmation before repair',()=>{
 assert.equal(layoutConflictCandidates(layout).length,1);
 assert.deepEqual(earlyLayoutRepairIssues(layout,{}),[]);
 const issues=earlyLayoutRepairIssues(layout,{layoutIssues:[{keys:['a','b'],instruction:'Separate columns'}]});
 assert.deepEqual(issues.map((i:any)=>i.key),['a','b']);
 assert.deepEqual(earlyLayoutRepairIssues(layout,{layoutIssues:[{keys:['a','unknown'],instruction:'Move'}]}),[]);
 assert.deepEqual(earlyLayoutRepairIssues(layout,{layoutIssues:[{keys:['a','c'],instruction:'Move'}]}),[]);
});
test('Cells from the same native table do not force reconstruction',()=>{
 assert.deepEqual(layoutConflictCandidates({slots:[field('a',0,1),field('b',0,1)]}),[]);
});
test('Visible collisions route to geometry repair immediately, ordinary overflow does not',()=>{
 assert.equal(requiresLayoutRepair([{details:['rendered_text_overlap']}]),true);
 assert.equal(requiresLayoutRepair([{details:['source_text_frame_overflow']}]),false);
});
test('Native table overflow and intersecting replaceable captions reach AI before rendering',()=>{
 const grid={slots:[{...field('caption',20,1)},{...field('cell',20,2),cell:[0,0]}],charts:[],contentFitIssues:[{key:'cell',reason:'overflow'}]};
 const issues=earlyLayoutRepairIssues(grid,{fields:[{key:'caption',action:'replace'},{key:'cell',action:'replace'}]});
 assert.deepEqual(new Set(issues.map((i:any)=>i.key)),new Set(['caption','cell']));
 assert.deepEqual(earlyLayoutRepairIssues({...grid,contentFitIssues:[]},{fields:[{key:'caption',action:'preserve'}]}),[]);
});
test('Chart geometry makes AI-confirmed text/chart conflicts actionable',()=>{
 const slide={slots:[field('caption',20,1)],charts:[{...field('chart',30,2),text:undefined}]};
 assert.equal(earlyLayoutRepairIssues(slide,{layoutIssues:[{keys:['caption','chart'],instruction:'Move caption below chart'}]}).length,1);
});
