import assert from 'node:assert/strict';
import {entryScore, rankEntries} from '../src/dic/web/ranking.mjs';
const state={scope:'Latin', evidence:'all', ancestors:false, metric:'words', sort:'desc',query:'',scoredOnly:false};
const origin=(group,ancestor,distance,extra={})=>({group,ancestor,word_distance:distance,language:group==='Latin'?'la':'tr',direct:true,relation:'prose',sense:1,...extra});
const word=(word,origins,forms=[])=>({word,origins,forms});
const a=word('a',[origin('Latin','a',0.1),origin('Turkish','a',0.99)]);
const b=word('b',[origin('Latin','b',0.5)]);
const missing=word('missing',[origin('Latin','missing',null)]);
assert.deepEqual(rankEntries([a,missing,b],state).map(row=>row.entry.word),['b','a','missing'],'Latin rankings must ignore Turkish distances');
assert.deepEqual(rankEntries([b,missing,a],{...state,sort:'asc'}).map(row=>row.entry.word),['a','b','missing'],'missing is not a zero distance');
assert.equal(entryScore(a,{...state,scope:'Turkic'}).distance,0.99,'Turkic scope includes Turkish origins');
const multi=word('multi',[origin('Latin','one',0.1),origin('Latin','two',0.9)]);
assert.equal(entryScore(multi,state).distance,0.9);
assert.equal(entryScore(multi,{...state,sort:'asc'}).distance,0.1);
const remote=word('pământ',[origin('Latin','paumentum',0.3),origin('Latin','pavimentum',0.8,{direct:false})],['pământuri']);
assert.equal(entryScore(remote,state).distance,0.3,'remote ancestors are opt-in');
assert.equal(entryScore(remote,{...state,ancestors:true}).distance,0.8);
assert.equal(rankEntries([remote],{...state,query:'pavimentum'}).length,0,'search must respect ancestor scope');
assert.equal(rankEntries([remote],{...state,query:'pavimentum',ancestors:true}).length,1);
assert.equal(rankEntries([remote],{...state,query:'pamanturi'}).length,1,'grouped form search ignores accents');
assert.equal(rankEntries([a,b],{...state,evidence:'strict'}).length,0,'prose is not inherited');
assert.equal(rankEntries([a,b,missing],{...state,scoredOnly:true}).length,2);
assert.equal(entryScore(a,{...state,metric:'definitions'}).distance,null,'definition coverage must not silently fall back to word distance');
const definition=word('definition',[origin('Latin','meaning',0.9,{definition_distance:0.2})]);
assert.equal(entryScore(definition,{...state,metric:'definitions'}).distance,0.2);
console.log('Ranking regressions passed: scope, direction, multiple origins, null scores, ancestors, forms, evidence, definition coverage.');

const {readFileSync} = await import('node:fs');
const requestedCases = JSON.parse(readFileSync(new URL('./fixtures/latin_regressions.json', import.meta.url), 'utf8'));
const requestedEntries = requestedCases.map(test => word(test.word, test.origins.map(o => origin('Latin',o.ancestor,null,{direct:o.direct}))));
for (const test of requestedCases) {
  for (const query of test.queries) {
    assert.deepEqual(rankEntries(requestedEntries,{...state,query}).map(row=>row.entry.word),[test.word], `${query} must identify ${test.word}`);
    const decomposed = query.normalize('NFD');
    assert.deepEqual(rankEntries(requestedEntries,{...state,query:decomposed}).map(row=>row.entry.word),[test.word], 'Decomposed Unicode must work too');
  }
}
