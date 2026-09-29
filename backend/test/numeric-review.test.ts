import test from 'node:test';
import assert from 'node:assert/strict';
import {numericReviewIssues} from '../src/numeric-review.js';
import {parseContentReview} from '../src/content-review.js';

test('literal citation misses go to semantic review without suppressing physical or proven factual errors',()=>{
 const warning={key:'share',reason:'unsupported_number',text:'30%',unsupportedNumbers:['30']};
 const overflow={key:'share',reason:'overflow',details:['source_text_frame_overflow']};
 const contradiction={key:'label',reason:'content_mismatch',category:'factual_error'};
 assert.deepEqual(numericReviewIssues([warning,overflow,contradiction]),{issues:[overflow,contradiction],warnings:[warning]});
 const reviewed=parseContentReview({issues:[{key:'share',category:'factual_error',claim:'30%',sourceQuote:'Из 100 участников 20 ответили да.',message:'Доля равна 20%, а не 30%.'}]},
   {fields:{share:{text:'30%'}},charts:{}},'Из 100 участников 20 ответили да.');
 assert.equal(reviewed.issues[0].reason,'content_mismatch');
 assert.equal(numericReviewIssues(reviewed.issues).issues.length,1);
});

test('a grounded contradiction spanning metric and caption is actionable only within the same visual group',()=>{
 const source='Период пилота — 12 недель.';
 const data={fields:{n:{text:'3'},caption:{text:'Недель — длительность пилота'},other:{text:'Человека'}},charts:{}};
 const fields=[{key:'n',group:'duration'},{key:'caption',group:'duration'},{key:'other',group:'team'}];
 const raw={issues:[{key:'n',category:'factual_error',claim:'3 — Недель — длительность пилота',sourceQuote:source,message:'Заменить 3 на 12.'}]};
 assert.equal(parseContentReview(raw,data,source,fields).issues.length,1);
 raw.issues[0].claim='3 человека';
 assert.equal(parseContentReview(raw,data,source,fields).issues.length,0);
 raw.issues[0].claim='30 — Недель — длительность пилота';
 assert.equal(parseContentReview(raw,data,source,fields).issues.length,0);
 raw.issues[0].claim='3 — рабочих недели';
 assert.equal(parseContentReview(raw,data,source,fields).issues.length,0);
});
