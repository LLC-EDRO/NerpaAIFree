import test from 'node:test';
import assert from 'node:assert/strict';
import { planContentNumbers } from '../src/domain.js';
test('known citation IDs are not numerical claims in a plan',()=>{
 assert.deepEqual(planContentNumbers('43% (evidence-12); 2024', ['evidence-12']),['43','2024']);
 assert.deepEqual(planContentNumbers('evidence-121 12 people', ['evidence-12']),['121','12']);
});
