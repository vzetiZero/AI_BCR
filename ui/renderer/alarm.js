// Âm báo của AI_BCR: còi sự cố + chuông chúc mừng khi đủ KPI.
//
// Còi hú và chuông đều được TỔNG HỢP bằng Web Audio API (oscillator) nên không cần
// tệp âm thanh nào — bundle Electron không phải kèm file media.
//
// Hai âm thanh khác nhau để nghe là biết ngay chuyện gì:
//   - Sự cố (lỗi đặt chip, lệch tiền, mất phiên, hết vốn…) → còi hú 850↔1300Hz.
//   - Đủ KPI (chốt lời / đạt mục tiêu) → chuông đi lên C-E-G-C, nghe tích cực.

const ALARM_STORAGE_KEY = 'aibcr.alarm.enabled';
const ALARM_VOLUME_KEY = 'aibcr.alarm.volume';
const ALARM_THROTTLE_MS = 2500;
// Sự cố KHÁC nội dung thì chỉ cần cách nhau ngần này giây là kêu tiếp (không nuốt mất
// sự cố thứ hai chỉ vì sự cố thứ nhất vừa hú). Cùng nội dung thì chờ đủ
// ALARM_THROTTLE_MS mới kêu lại.
const ALARM_DISTINCT_GAP_MS = 800;
const SUCCESS_THROTTLE_MS = 4000;

// Chỉ những sự cố tài chính/an toàn mới hú còi. Thông báo tiến trình thường
// (đang chờ ván, đã vào bàn, bỏ qua tay) không được kích hoạt.
//
// Danh sách này KHÔNG phải danh sách trắng bắt buộc: backend đã phân loại mức log
// (`level === "error"`), nên một thông điệp lỗi mới thêm vẫn hú còi dù chưa có mẫu ở
// đây. Các mẫu dưới chỉ dùng để nhận diện thông điệp mức `info`/`warn` có tính sự cố.
const ALARM_PATTERNS = [
  'lệch tiền',
  'chênh lệch',
  'cược sai số tiền',
  'lỗi đặt chip',
  'lỗi đặt cược',
  'chưa đặt được chip',
  'chưa đặt được cược',
  'từ chối nhận chip',
  'web từ chối lệnh cược',
  'chưa bắt được websocket',
  'chip đã treo',
  'vượt quá giới hạn',
  'khác bàn',
  'lệch bàn',
  'không tìm thấy bàn',
  'không thấy ô cược',
  'không mở được bàn',
  'chưa nhận bàn',
  'không đồng bộ',
  'dừng cược',
  'chặn cược',
  'chặn đứng cược lệch',
  'hủy sạch chip',
  'hủy lệnh an toàn',
  'fail-safe rollback',
  'bảo toàn vốn',
  'tự động ngắt tk',
  'không xếp đủ chip',
  'không đặt được chip',
  'dừng hẳn',
  'mất phiên',
  'hết vốn',
  'không còn gì để đánh',
  'không kết nối',
  'không còn đọc được bàn',
  'mất kết nối',
  'đọc cầu lỗi',
  'chưa cập nhật',
  'nhiều cầu khác nhau',
  'bàn chưa mở cược',
  'tự phục hồi chưa xong',
  'chrome đã đóng',
  'không lưu được',
  'không ghi được',
];

// Dừng phiên do người dùng hoặc do luồng bình thường: KHÔNG hú còi. Danh sách này phải
// HẸP và cụ thể — mẫu quá rộng (vd. "quá", "tạm dừng") sẽ nuốt mất sự cố thật như
// "Dữ liệu quá 30 giây chưa cập nhật" hay "Chrome đã đóng · phiên tạm dừng".
const ALARM_SUPPRESS = [
  'đã dừng tool',
  'đang rời bàn',
  'đã huỷ tìm bàn hợp lệ',
  'đã chốt lời',
  'đã reset phiên',
  'đã tạo phiên mới',
];


let alarmContext = null;
let alarmEnabled = true;
let alarmVolume = 0.8;
let lastAlarmAt = 0;
let lastAlarmText = '';
let lastSuccessAt = 0;

function normalizeForAlarm(text) {
  return String(text || '')
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .replace(/[đĐ]/g, 'd')
    .toLowerCase();
}

const ALARM_PATTERNS_NORMALIZED = ALARM_PATTERNS.map(normalizeForAlarm);
const ALARM_SUPPRESS_NORMALIZED = ALARM_SUPPRESS.map(normalizeForAlarm);

function loadAlarmSettings() {
  try {
    const savedEnabled = localStorage.getItem(ALARM_STORAGE_KEY);
    if (savedEnabled !== null) alarmEnabled = savedEnabled === 'true';
    const rawVolume = localStorage.getItem(ALARM_VOLUME_KEY);
    // Number(null) === 0 từng làm còi im lặng tuyệt đối; chỉ đọc khi thật sự có giá trị.
    if (rawVolume !== null && rawVolume.trim() !== '') {
      const savedVolume = Number(rawVolume);
      if (Number.isFinite(savedVolume) && savedVolume >= 0 && savedVolume <= 1) {
        alarmVolume = savedVolume;
      }
    }
  } catch (error) {
    // localStorage có thể bị chặn; giữ mặc định.
  }
}

function ensureAlarmContext() {
  if (!alarmContext) {
    const Ctor = window.AudioContext || window.webkitAudioContext;
    if (Ctor) alarmContext = new Ctor();
  }
  if (alarmContext && alarmContext.state === 'suspended') {
    alarmContext.resume().catch(() => {});
  }
  return alarmContext;
}

function alarmVolumePercent() {
  return Math.round(alarmVolume * 100);
}

function syncAlarmUI() {
  const toggle = document.getElementById('alarm-toggle');
  const icon = document.getElementById('alarm-sound-icon');
  const stateText = document.getElementById('alarm-state-text');
  const volume = document.getElementById('alarm-volume');
  const volumeText = document.getElementById('alarm-volume-text');
  const box = document.getElementById('alarm-sound-box');

  if (toggle) toggle.setAttribute('aria-pressed', String(alarmEnabled));
  if (icon) {
    icon.textContent = alarmEnabled
      ? (alarmVolume > 0 ? '\uD83D\uDD0A' : '\uD83D\uDD08')
      : '\uD83D\uDD07';
  }
  if (stateText) stateText.textContent = alarmEnabled ? 'BẬT' : 'TẮT';
  if (volume) volume.value = String(alarmVolumePercent());
  if (volumeText) volumeText.textContent = `${alarmVolumePercent()}%`;
  if (box) box.classList.toggle('off', !alarmEnabled);
  if (toggle) toggle.title = alarmEnabled ? 'Tắt âm báo' : 'Bật âm báo';
}

function setAlarmEnabled(enabled) {
  alarmEnabled = Boolean(enabled);
  try { localStorage.setItem(ALARM_STORAGE_KEY, String(alarmEnabled)); } catch (error) {}
  syncAlarmUI();
}

function setAlarmVolume(percent) {
  const value = Math.max(0, Math.min(100, Math.trunc(Number(percent) || 0)));
  alarmVolume = value / 100;
  try { localStorage.setItem(ALARM_VOLUME_KEY, String(alarmVolume)); } catch (error) {}
  syncAlarmUI();
}

function flashAlarmBox(className = 'flashing', ticks = 8) {
  const box = document.getElementById('alarm-sound-box');
  if (!box) return;
  let count = 0;
  const timer = setInterval(() => {
    count += 1;
    box.classList.toggle(className, count % 2 === 1);
    if (count >= ticks) {
      clearInterval(timer);
      box.classList.remove(className);
    }
  }, 250);
}

function masterGain(context) {
  const master = context.createGain();
  master.gain.setValueAtTime(alarmVolume, context.currentTime);
  master.connect(context.destination);
  return master;
}

// -------- còi sự cố: 3 xung ngắn 850→1250Hz rồi 1 xung dài 1300→850Hz.
function playAlarmSiren() {
  if (!alarmEnabled || alarmVolume <= 0) return false;
  const context = ensureAlarmContext();
  if (!context) return false;
  try {
    const now = context.currentTime;
    const master = masterGain(context);
    const pulses = [
      { start: 0, duration: 0.30, from: 850, to: 1250 },
      { start: 0.40, duration: 0.30, from: 850, to: 1250 },
      { start: 0.80, duration: 0.30, from: 850, to: 1250 },
      { start: 1.20, duration: 0.80, from: 1300, to: 850 },
    ];
    for (const pulse of pulses) {
      const oscillator = context.createOscillator();
      const gain = context.createGain();
      oscillator.type = 'sawtooth';
      const startAt = now + pulse.start;
      const endAt = startAt + pulse.duration;
      oscillator.frequency.setValueAtTime(pulse.from, startAt);
      oscillator.frequency.exponentialRampToValueAtTime(pulse.to, endAt);
      gain.gain.setValueAtTime(0, startAt);
      gain.gain.linearRampToValueAtTime(0.85, startAt + 0.04);
      gain.gain.setValueAtTime(0.85, endAt - 0.04);
      gain.gain.linearRampToValueAtTime(0, endAt);
      oscillator.connect(gain);
      gain.connect(master);
      oscillator.start(startAt);
      oscillator.stop(endAt);
    }
    flashAlarmBox('flashing');
    return true;
  } catch (error) {
    return false;
  }
}

// -------- chuông thành công: hợp âm đi lên C5-E5-G5-C6, ngân dài, nghe "thắng".
// Dùng sóng sine + harmonic nhẹ để không chói như còi sự cố.
function playSuccessChime() {
  if (!alarmEnabled || alarmVolume <= 0) return false;
  const context = ensureAlarmContext();
  if (!context) return false;
  try {
    const now = context.currentTime;
    const master = masterGain(context);
    // Mỗi nốt: cao độ, lúc bắt đầu, độ dài. Nốt càng về sau ngân càng lâu.
    const notes = [
      { freq: 523.25, start: 0.00, duration: 0.34 },
      { freq: 659.25, start: 0.16, duration: 0.38 },
      { freq: 783.99, start: 0.32, duration: 0.44 },
      { freq: 1046.50, start: 0.48, duration: 0.95 },
    ];
    for (const note of notes) {
      for (const [type, level] of [['sine', 0.9], ['triangle', 0.22]]) {
        const oscillator = context.createOscillator();
        const gain = context.createGain();
        oscillator.type = type;
        const startAt = now + note.start;
        const endAt = startAt + note.duration;
        oscillator.frequency.setValueAtTime(note.freq, startAt);
        gain.gain.setValueAtTime(0, startAt);
        gain.gain.linearRampToValueAtTime(level, startAt + 0.03);
        gain.gain.exponentialRampToValueAtTime(0.001, endAt);
        oscillator.connect(gain);
        gain.connect(master);
        oscillator.start(startAt);
        oscillator.stop(endAt);
      }
    }
    flashAlarmBox('success', 10);
    return true;
  } catch (error) {
    return false;
  }
}

function testAlarmSound() {
  ensureAlarmContext();
  playAlarmSiren();
}

function testSuccessSound() {
  ensureAlarmContext();
  playSuccessChime();
}

// Hú còi nếu thông điệp là sự cố; chống hú lặp trong 2.5 giây.
//
// Backend đã gắn mức cho từng log. `level === "error"` là tín hiệu CHÍNH: mọi thông
// điệp lỗi mới thêm đều hú còi mà không phải sửa danh sách mẫu. Danh sách mẫu chỉ mở
// rộng cho thông điệp `warn`/`info` mang tính sự cố (ví dụ "Chip đã treo trên bàn").
// Danh sách chặn loại các lần dừng hợp lệ (dừng tool, rời bàn, chốt lời) để không hú
// oan mỗi lần người dùng bấm Dừng.
function alarmForMessage(message, { force = false, level = '' } = {}) {
  const text = normalizeForAlarm(message);
  const suppressed = ALARM_SUPPRESS_NORMALIZED.some((pattern) => text.includes(pattern));
  if (!force && suppressed) return false;
  if (!force) {
    const isError = String(level || '').toLowerCase() === 'error';
    const matchesPattern = ALARM_PATTERNS_NORMALIZED.some((pattern) => text.includes(pattern));
    if (!isError && !matchesPattern) return false;
  }
  const now = Date.now();
  const sameAsLast = text === lastAlarmText;
  const gap = sameAsLast ? ALARM_THROTTLE_MS : ALARM_DISTINCT_GAP_MS;
  if (now - lastAlarmAt < gap) return false;
  lastAlarmAt = now;
  lastAlarmText = text;
  return playAlarmSiren();
}

// Chuông chúc mừng khi đạt KPI. Không phụ thuộc mẫu lỗi; chỉ chống lặp.
function successForMessage(message, { force = false } = {}) {
  const now = Date.now();
  if (!force && now - lastSuccessAt < SUCCESS_THROTTLE_MS) return false;
  lastSuccessAt = now;
  return playSuccessChime();
}

function initAlarm() {
  loadAlarmSettings();
  syncAlarmUI();

  const toggle = document.getElementById('alarm-toggle');
  if (toggle) {
    toggle.addEventListener('click', () => {
      setAlarmEnabled(!alarmEnabled);
      if (alarmEnabled) testAlarmSound();
    });
  }
  const volume = document.getElementById('alarm-volume');
  if (volume) volume.addEventListener('input', (event) => setAlarmVolume(event.target.value));
  const test = document.getElementById('alarm-test');
  if (test) test.addEventListener('click', () => testAlarmSound());
  const testSuccess = document.getElementById('alarm-test-success');
  if (testSuccess) testSuccess.addEventListener('click', () => testSuccessSound());

  // AudioContext cần một thao tác người dùng trước khi phát được.
  document.addEventListener('click', () => ensureAlarmContext(), { passive: true });
  document.addEventListener('keydown', () => ensureAlarmContext(), { passive: true });
}

window.aibcrAlarm = {
  init: initAlarm,
  notify: alarmForMessage,
  success: successForMessage,
  test: testAlarmSound,
  testSuccess: testSuccessSound,
  setEnabled: setAlarmEnabled,
  setVolume: setAlarmVolume,
  isEnabled: () => alarmEnabled,
  volumePercent: alarmVolumePercent,
  patterns: ALARM_PATTERNS,
};
