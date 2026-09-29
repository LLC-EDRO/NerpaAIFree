import test from 'node:test';
import assert from 'node:assert/strict';
import {layoutEditability,preferEditableLayouts} from '../src/layout-selection.js';
import type {Layout} from '../src/domain.js';

const plain:Layout={id:'text',index:0,name:'Editable text',usable:true,warnings:[],charts:[],slots:[{key:'s9',shapeId:9,role:'body',text:'42%',maxChars:50,size:20,x:10,y:10,w:80,h:30}],visuals:{unlabelledShapes:12}};
const table:Layout={...plain,id:'native-grid',slots:[0,1].flatMap(r=>[0,1].map(c=>({...plain.slots[0],key:`s99_r${r}_c${c}`,shapeId:99,cell:[r,c],x:c*80,y:r*30})))};
test('parsed native objects outrank drawn statistics; no slide IDs or titles imply editability',()=>{
 const image:Layout={...plain,id:'flat-picture',name:'Editable table',slots:[]};
 const recovered={...table,id:'recovered',drawnTableShapeIds:[99]};
 const chart={...plain,id:'chart',charts:[{key:'chart1'}]};
 assert.equal(layoutEditability(plain).graphicDataEditable,false);
 assert.equal(layoutEditability(image).nativeTables,0);
 assert.deepEqual(preferEditableLayouts([image,plain,recovered,chart,table]).map(l=>l.id),['chart','native-grid','recovered','text','flat-picture']);
 assert.equal(layoutEditability(table).tables[0].maxTotalRows,10);
 assert.equal(layoutEditability(recovered).recoveredEditableTables,1);
 assert.equal(layoutEditability(recovered).nativeTables,0);
});
