const { test } = require('node:test');
const assert = require('node:assert/strict');
const ui = require('../../apituner/web/ui-state.js');

test('reachability does not hide missing remote pairing', () => {
  assert.deepEqual(ui.tunerIssues({ enabled: 'true', health: 'online', pair: 'unpaired' }), ['Remote not paired']);
});
test('disabled devices do not create setup alerts', () => {
  assert.deepEqual(ui.tunerIssues({ enabled: 'false', health: 'offline', needsKeys: 'true', error: 'Old failure' }), []);
});
test('independent issues survive together and historical errors stay labeled', () => {
  assert.deepEqual(ui.tunerIssues({ health: 'offline', needsKeys: 'true', pair: 'unknown', update: 'true', error: 'Encoder timeout' }),
    ['Device unreachable', 'D-pad setup needed', 'Pairing status unknown', 'Agent update available', 'Last error: Encoder timeout']);
});
test('healthy version and unknown initial state are not failures', () => {
  assert.deepEqual(ui.tunerIssues({}), []);
  assert.deepEqual(ui.tunerIssues({ health: 'online', pair: 'paired', update: 'false' }), []);
});
test('unknown package coverage never claims the app is available', () => {
  const channel = { package_name: 'tv.example' };
  for (const coverage of [null, {status:'unknown'}, {}]) {
    assert.deepEqual(ui.channelConfiguration(channel, coverage), { label: 'Not checked', warning: false });
  }
  assert.equal(ui.channelConfiguration(channel, {status:'ok'}).label, 'App available');
});
test('missing and partial packages remain actionable; alternate packages count', () => {
  assert.deepEqual(ui.channelConfiguration({}, null), {label:'Package not set', warning:true});
  assert.equal(ui.channelConfiguration({alternate_package_name:'tv.alternate'}, {status:'ok'}).warning, false);
  assert.deepEqual(ui.channelConfiguration({package_name:'tv.example'}, {status:'missing'}), {label:'App not installed', warning:true});
  assert.deepEqual(ui.channelConfiguration({package_name:'tv.example'}, {status:'partial'}), {label:'App on some tuners', warning:true});
});
