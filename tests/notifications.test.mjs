import test from 'node:test';
import assert from 'node:assert/strict';
import {NoticeStore} from '../web/notifications.js';

function storage() {
  const values = new Map();
  return {getItem: key => values.get(key), setItem: (key, value) => values.set(key, value)};
}

test('dismissed notices stay inactive during polling and after reload', () => {
  const saved = storage(), store = new NoticeStore(saved);
  const notice = {message: 'Feature failed', identity: 'version-1:failed'};
  store.put('feedback', notice);
  store.dismiss('feedback');
  store.put('feedback', notice);
  assert.equal(store.items.size, 0);
  const reloaded = new NoticeStore(saved);
  reloaded.put('feedback', notice);
  assert.equal(reloaded.items.size, 0);
});

test('a new outcome for the same source can notify again', () => {
  const store = new NoticeStore(storage());
  store.put('feedback', {message: 'Failed', identity: 'v1:failed'});
  store.dismiss('feedback');
  store.put('feedback', {message: 'Ready for review', identity: 'v2:ready'});
  assert.equal(store.items.size, 1);
});

test('sources remain independent and resolving one keeps the other', () => {
  const store = new NoticeStore(storage());
  store.put('update', {message: 'Restart'});
  store.put('error', {message: 'Retry'});
  store.clear('update');
  assert.equal(store.items.get('error').message, 'Retry');
});

test('unchanged polling does not rebuild the notification UI', () => {
  const store = new NoticeStore(storage());
  assert.equal(store.put('update', {message: 'Restart'}), true);
  assert.equal(store.put('update', {message: 'Restart'}), false);
});

test('storage failures do not break dismissal in the current window', () => {
  const store = new NoticeStore({getItem() {throw Error('blocked');}, setItem() {throw Error('full');}});
  store.put('error', {message: 'Retry'}); store.dismiss('error');
  store.put('error', {message: 'Retry'});
  assert.equal(store.items.size, 0);
});
