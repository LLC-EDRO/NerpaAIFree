import test from 'node:test';
import assert from 'node:assert/strict';
import { validateSemantics, validateCopy, type Layout } from '../src/domain.js';
import { isCurrentTemplateContract, sourceRecoveryVersion } from '../src/template-version.js';

const layout: Layout = {id:'arbitrary', index:0, name:'Footer', usable:true, warnings:[], charts:[],
  slots:[{key:'caption', shapeId:7, role:'footer', text:'ПОЛЯРНЫЕ ЭКСПЕДИЦИИ · ОБЗОР',
    maxChars:80, size:14, x:40, y:450, w:500, h:40}]};
const semantics = (role: string) => validateSemantics({composition:'Подпись внизу', charts:[],
  fields:[{key:'caption', role, action:'preserve', required:true, group:'footer', intent:'Нижняя подпись'}]}, layout);

test('ordinary repeated footer wording is replaced even if AI calls it decoration', () => {
  for (const role of ['footer','body','decoration']) {
    const spec = semantics(role);
    assert.equal(spec.fields[0].action, 'replace');
    const copy = validateCopy({fields:{caption:{text:'СОВРЕМЕННАЯ АРХИТЕКТУРА', evidence:[]}},charts:{}},layout,spec,'');
    assert.equal(copy.data.fields.caption.text,'СОВРЕМЕННАЯ АРХИТЕКТУРА');
    assert.deepEqual(copy.issues,[]);
  }
});

test('a real brand identified by AI in a footer retains the original wording', () => {
  const spec = semantics('brand');
  assert.equal(spec.fields[0].action,'preserve');
  const copy = validateCopy({fields:{caption:{text:layout.slots[0].text,evidence:[]}},charts:{}},layout,spec,'');
  assert.deepEqual(copy.issues,[]);
});

test('old contracts refresh even if the user has not changed the approved revision', () => {
  const contract = {version:'nerpa-native-template/1',revision:4};
  assert.equal(isCurrentTemplateContract(undefined,4),false);
  assert.equal(isCurrentTemplateContract(contract,4),false);
  assert.equal(isCurrentTemplateContract({...contract,recoveryVersion:sourceRecoveryVersion-1},4),false);
  assert.equal(isCurrentTemplateContract({...contract,recoveryVersion:sourceRecoveryVersion},4),true);
  assert.equal(isCurrentTemplateContract({...contract,recoveryVersion:sourceRecoveryVersion},5),false);
});
