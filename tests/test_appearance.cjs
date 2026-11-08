// Run with node tests/test_appearance.cjs; no dependencies, network or model calls.
const assert = require('node:assert/strict');
const {normalize, create} = require('../local_appearance.js');
assert.deepEqual(normalize(null), {theme:'default',mode:'novice'});
assert.deepEqual(normalize({theme:'unknown',mode:'unknown'}), {theme:'default',mode:'novice'});
assert.deepEqual(normalize({theme:'espresso',mode:'unknown'}), {theme:'espresso',mode:'novice'});
const memory = new Map();
const storage = {getItem:k=>memory.get(k)||null,setItem:(k,v)=>memory.set(k,v)};
const changes=[];
const preferences=create(storage,value=>changes.push(value));
for (const theme of ['default','lemonade','espresso','cyberpunk']) {
  preferences.update({theme});
  for (const mode of ['novice','learned','expert']) {
    preferences.update({mode});
    assert.deepEqual(preferences.get(), {theme,mode});
    assert.deepEqual(create(storage,()=>{}).get(), {theme,mode});
  }
}
preferences.update({theme:'espresso'});
assert.equal(preferences.get().mode,'expert');
preferences.update({mode:'learned'});
assert.equal(preferences.get().theme,'espresso');
const copy=preferences.get();copy.mode='novice';
assert.equal(preferences.get().mode,'learned');
const blocked=create({getItem(){throw Error('Denied');},setItem(){throw Error('Denied');}},()=>{});
assert.deepEqual(blocked.get(),{theme:'default',mode:'novice'});
assert.deepEqual(blocked.update({theme:'cyberpunk'}),{theme:'cyberpunk',mode:'novice',remembered:false});
const malformed=create({getItem:()=>'{broken',setItem(){}},()=>{});
assert.deepEqual(malformed.get(),{theme:'default',mode:'novice'});
assert.equal(memory.size,1); // Only appearance preferences are persisted.
assert.ok(changes.length>12);
process.stdout.write('Appearance checks passed: all 12 combinations, independence, persistence, invalid data and unavailable storage.\n');
