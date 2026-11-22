// Node test: âm báo của AI_BCR (còi sự cố + chuông đủ KPI).
//
// Luật người dùng yêu cầu:
//  - Có âm báo THÀNH CÔNG khi đủ KPI (chốt lời).
//  - Có âm báo LỖI để biết đang có sự cố.
//  - Bật/tắt và âm lượng được nhớ giữa các lần mở app.
//  - Không kèm tệp âm thanh: mọi thứ tổng hợp bằng Web Audio.

const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

const ROOT = path.join(__dirname, '..');
const source = fs.readFileSync(path.join(ROOT, 'ui', 'renderer', 'alarm.js'), 'utf8');
const html = fs.readFileSync(path.join(ROOT, 'ui', 'renderer', 'index.html'), 'utf8');
const app = fs.readFileSync(path.join(ROOT, 'ui', 'renderer', 'app.js'), 'utf8');
const css = fs.readFileSync(path.join(ROOT, 'ui', 'renderer', 'styles.css'), 'utf8');

test('alarm.js exposes a global API for the plain-script renderer', () => {
  assert.match(source, /window\.aibcrAlarm = \{/);
  assert.match(source, /init: initAlarm/);
  assert.match(source, /notify: alarmForMessage/);
  assert.match(source, /success: successForMessage/);
});

test('both sounds are synthesised with Web Audio, no audio files', () => {
  assert.match(source, /createOscillator/);
  assert.match(source, /createGain/);
  assert.match(source, /sawtooth/);
  assert.match(source, /playSuccessChime/);
  assert.doesNotMatch(source, /\.mp3|\.wav|\.ogg|new Audio\(/);
});

test('the error siren sweeps like the reference pulses', () => {
  assert.match(source, /850/);
  assert.match(source, /1250/);
  assert.match(source, /1300/);
  assert.match(source, /exponentialRampToValueAtTime/);
});

test('the success chime is an ascending major arpeggio (C-E-G-C)', () => {
  for (const freq of ['523.25', '659.25', '783.99', '1046.50']) {
    assert.ok(source.includes(freq), `missing chime note: ${freq}`);
  }
  // Đi lên: cao độ sau lớn hơn cao độ trước.
  const notes = [...source.matchAll(/freq: ([0-9.]+), start:/g)].map((m) => Number(m[1]));
  assert.equal(notes.length, 4);
  for (let i = 1; i < notes.length; i += 1) assert.ok(notes[i] > notes[i - 1]);
});

test('the success chime uses sine, not the harsh siren waveform', () => {
  const chime = source.slice(source.indexOf('function playSuccessChime'));
  const body = chime.slice(0, chime.indexOf('function testAlarmSound'));
  assert.match(body, /'sine'/);
  assert.doesNotMatch(body, /sawtooth/);
});

test('critical incidents are recognised without diacritics', () => {
  assert.match(source, /normalize\('NFD'\)/);
  assert.match(source, /ALARM_PATTERNS_NORMALIZED/);
  for (const phrase of ['lệch tiền', 'lệch bàn', 'không xếp đủ chip', 'dừng cược',
    'từ chối nhận chip', 'vượt quá giới hạn', 'không tìm thấy bàn', 'hết vốn']) {
    assert.ok(source.includes(phrase), `missing pattern: ${phrase}`);
  }
});

test('ordinary info messages stay silent', () => {
  assert.match(source, /level \|\| ''\)\.toLowerCase\(\) !== 'error'/);
});

test('repeat sirens are throttled', () => {
  assert.match(source, /ALARM_THROTTLE_MS = 2500/);
  assert.match(source, /now - lastAlarmAt < ALARM_THROTTLE_MS/);
  assert.match(source, /SUCCESS_THROTTLE_MS/);
});

test('enable state and volume persist in localStorage', () => {
  assert.match(source, /aibcr\.alarm\.enabled/);
  assert.match(source, /aibcr\.alarm\.volume/);
  assert.match(source, /localStorage\.setItem\(ALARM_STORAGE_KEY/);
  assert.match(source, /localStorage\.setItem\(ALARM_VOLUME_KEY/);
});

test('a missing saved volume does not mute the alarm', () => {
  // Number(null) === 0 từng làm câm toàn bộ âm báo.
  assert.match(source, /rawVolume !== null/);
  assert.match(source, /alarmVolume = 0\.8/);
});

test('the UI provides toggle, volume, and both test buttons', () => {
  assert.match(html, /id="alarm-sound-box"/);
  assert.match(html, /id="alarm-toggle"/);
  assert.match(html, /id="alarm-volume"/);
  assert.match(html, /id="alarm-volume-text"/);
  assert.match(html, /id="alarm-test"/);
  assert.match(html, /id="alarm-test-success"/);
  assert.match(html, /id="alarm-state-text"/);
});

test('alarm.js loads before app.js', () => {
  const alarmAt = html.indexOf('src="alarm.js"');
  const appAt = html.indexOf('src="app.js"');
  assert.ok(alarmAt >= 0 && appAt > alarmAt, 'alarm.js must load before app.js');
});

test('the renderer rings the siren for errors and the chime for KPI', () => {
  assert.match(app, /window\.aibcrAlarm\?\.init\?\.\(\)/);
  assert.match(app, /window\.aibcrAlarm\?\.notify\?\./);
  assert.match(app, /window\.aibcrAlarm\?\.success\?\./);
  assert.match(app, /function syncKpiAudio/);
  assert.match(app, /if \(level === 'error'\) window\.aibcrAlarm\?\.notify\?\./);
});

test('the KPI sound only fires when the state changes, not on every render', () => {
  const fn = app.slice(app.indexOf('function syncKpiAudio'));
  const body = fn.slice(0, fn.indexOf('let lastNoteErrorAt'));
  assert.match(body, /state\.kpi\.signature === signature/);
  assert.match(body, /previous === null/);
});

test('the alarm box flashes red on errors and green on KPI success', () => {
  assert.match(css, /\.alarm-box\.flashing/);
  assert.match(css, /\.alarm-box\.success/);
  assert.match(css, /\.alarm-box\.off/);
  assert.ok(source.includes("flashAlarmBox('flashing')"));
  assert.ok(source.includes("flashAlarmBox('success'"));
});
