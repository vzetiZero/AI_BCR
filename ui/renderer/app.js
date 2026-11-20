const api = window.aibcr;
const $ = (selector) => document.querySelector(selector);
const profileSelect = $('#site-select');
const profileCount = $('#profile-count');
const profileList = $('#profile-list');
const runButton = $('#run-profiles');
const notice = $('#notice');
const statusByIndex = new Map();

window.addEventListener('error', (event) => showNotice(String(event.message || event.error), true));
window.addEventListener('unhandledrejection', (event) => showNotice(String(event.reason), true));

function showNotice(message, isError = false) {
  notice.textContent = message;
  notice.dataset.state = isError ? 'error' : 'ready';
}

function profileIndex(game) {
  if (game === 'baccarat') return 1;
  const match = /^baccarat-profile-(\d+)$/.exec(game || '');
  return match ? Number(match[1]) : 0;
}

function updateProfile(index, state, detail = '') {
  const row = statusByIndex.get(index);
  if (!row) return;
  row.className = `profile-row ${state}`;
  row.querySelector('.profile-state').textContent = state === 'running' ? 'Đang chạy'
    : state === 'error' ? 'Có lỗi' : 'Đang mở';
  row.querySelector('.profile-detail').textContent = detail;
}

function renderProfiles(count, siteLabel, replies = []) {
  profileList.replaceChildren();
  statusByIndex.clear();
  $('#profile-total').textContent = `${count} profile · ${siteLabel}`;
  for (let index = 1; index <= count; index += 1) {
    const reply = replies[index - 1] || {};
    const row = document.createElement('article');
    row.className = `profile-row ${reply.ok ? 'opening' : 'error'}`;
    row.innerHTML = `<div class="profile-number">${String(index).padStart(2, '0')}</div>
      <div class="profile-info"><strong>Profile ${index}</strong><span class="profile-detail"></span></div>
      <span class="profile-state"></span>`;
    row.querySelector('.profile-detail').textContent = reply.note || reply.error || '';
    row.querySelector('.profile-state').textContent = reply.ok ? 'Đang mở' : 'Có lỗi';
    profileList.append(row);
    statusByIndex.set(index, row);
  }
}

function updateSitePreview() {
  const site = profileSelect.selectedOptions[0];
  $('#site-url').textContent = site ? site.dataset.url || '' : '';
  runButton.disabled = !site || !site.value;
}

profileSelect.addEventListener('change', updateSitePreview);
$('#profile-count').addEventListener('change', () => {
  const value = Math.max(1, Math.min(8, Number(profileCount.value) || 1));
  profileCount.value = String(value);
});

runButton.addEventListener('click', async () => {
  const count = Math.max(1, Math.min(8, Math.trunc(Number(profileCount.value) || 1)));
  profileCount.value = String(count);
  runButton.disabled = true;
  runButton.classList.add('busy');
  showNotice('Đang khởi chạy các profile…');
  try {
    const result = await api.openProfiles(count, profileSelect.value);
    if (!result || !result.ok) {
      showNotice((result && result.error) || 'Không mở được các profile.', true);
      return;
    }
    renderProfiles(result.count, result.site, result.replies);
    $('#close-profiles').disabled = false;
    const failed = result.replies.filter((reply) => !reply.ok).length;
    showNotice(failed
      ? `Đã gửi lệnh mở ${result.count} profile; ${failed} profile báo lỗi.`
      : `Đã gửi lệnh mở đồng thời ${result.count} profile tại ${result.site}.`);
  } catch (error) {
    showNotice(String(error.message || error), true);
  } finally {
    runButton.classList.remove('busy');
    runButton.disabled = !profileSelect.value;
  }
});

$('#close-profiles').addEventListener('click', async () => {
  const button = $('#close-profiles');
  button.disabled = true;
  try {
    const result = await api.closeProfiles();
    if (!result || !result.ok) {
      showNotice((result && result.error) || 'Không đóng được các profile.', true);
      return;
    }
    for (const index of statusByIndex.keys()) updateProfile(index, 'closed', 'Đã gửi lệnh đóng cửa sổ.');
    showNotice(`Đã gửi lệnh đóng ${result.count} cửa sổ Chrome.`);
  } catch (error) {
    showNotice(String(error.message || error), true);
  }
});

document.querySelectorAll('[data-win]').forEach((button) => {
  button.addEventListener('click', () => api.windowControl(button.dataset.win));
});

api.onEngineEvent(({ game, event, data }) => {
  const index = profileIndex(game);
  if (!index || !statusByIndex.has(index)) return;
  if (event === 'opened') updateProfile(index, 'running', String(data || 'Chrome đã mở.'));
  else if (event === 'open_error') updateProfile(index, 'error', String(data || 'Không mở được Chrome.'));
  else if (event === 'stopped') updateProfile(index, 'closed', String(data || 'Đã đóng profile.'));
  else if (event === 'state' && data && data.chrome_connected) {
    updateProfile(index, 'running', data.connection_text || 'Chrome đã kết nối.');
  }
});

(async function boot() {
  try {
    const info = await api.info();
    profileSelect.replaceChildren();
    for (const site of info.profileSites || []) {
      const option = document.createElement('option');
      option.value = site.id;
      option.textContent = site.label;
      option.dataset.url = site.url;
      profileSelect.append(option);
    }
    profileSelect.disabled = false;
    updateSitePreview();
    showNotice('Chọn số lượng profile và website, sau đó bấm Chạy đồng thời.');
  } catch (error) {
    showNotice(`Không tải được danh sách website: ${error.message || error}`, true);
  }
})();
