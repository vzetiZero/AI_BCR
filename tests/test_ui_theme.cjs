// Node test: giao diện AI_BCR — theme sáng/tối và panel trái đóng/mở.
//
// Luật người dùng yêu cầu:
//  - Có giao diện sáng và tối, chuyển được và nhớ lựa chọn.
//  - Panel bên trái chứa menu đóng/mở được (không làm mất chức năng chọn trò chơi).
//  - Bản sáng phải thật sự đổi nền/chữ, không chỉ đổi tên thuộc tính.

const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

const ROOT = path.join(__dirname, '..');
const html = fs.readFileSync(path.join(ROOT, 'ui', 'renderer', 'index.html'), 'utf8');
const app = fs.readFileSync(path.join(ROOT, 'ui', 'renderer', 'app.js'), 'utf8');
const css = fs.readFileSync(path.join(ROOT, 'ui', 'renderer', 'styles.css'), 'utf8');

test('the titlebar exposes a theme toggle and a sidebar toggle', () => {
  assert.match(html, /id="theme-toggle"/);
  assert.match(html, /theme-icon-dark/);
  assert.match(html, /theme-icon-light/);
  assert.match(html, /id="sidebar-toggle"/);
  assert.match(html, /aria-controls="rail"/);
  assert.match(html, /<aside class="rail" id="rail">/);
});

test('the light theme redefines the real colour tokens', () => {
  const light = css.slice(css.indexOf(':root[data-theme="light"]'));
  const block = light.slice(0, light.indexOf('}'));
  for (const token of ['--bg:', '--text:', '--panel:', '--card-bg:', '--log-bg:']) {
    assert.ok(block.includes(token), `light theme missing ${token}`);
  }
  assert.match(block, /color-scheme: light/);
});

test('both themes declare a colour-scheme so native controls follow', () => {
  assert.match(css, /color-scheme: dark/);
  assert.match(css, /color-scheme: light/);
});

test('theme choice is applied on :root and remembered', () => {
  assert.match(app, /const THEME_KEY = 'aibcr\.theme'/);
  assert.match(app, /document\.documentElement\.dataset\.theme = next/);
  assert.match(app, /localStorage\.setItem\(THEME_KEY, next\)/);
  assert.match(app, /function initTheme/);
  assert.match(app, /function applyTheme/);
});

test('the sidebar collapses to an icon rail and remembers it', () => {
  assert.match(app, /const RAIL_KEY = 'aibcr\.railCollapsed'/);
  assert.match(app, /classList\.toggle\('rail-collapsed'/);
  assert.match(app, /function initRail/);
  assert.match(css, /\.shell\.rail-collapsed \.rail/);
});

test('a collapsed rail still shows the game icon and hides only the text', () => {
  assert.match(css, /\.shell\.rail-collapsed \.g-txt[\s\S]{0,40}display: none/);
  assert.doesNotMatch(css, /\.shell\.rail-collapsed \.g-ico[^{]*\{[^}]*display: none/);
});

test('the hamburger glyph is drawn with CSS, not an image or emoji', () => {
  assert.match(css, /\.tb-burger/);
  const burger = css.slice(css.indexOf('.tb-burger'));
  assert.match(burger.slice(0, 400), /linear-gradient\(currentColor/);
  assert.match(html, /class="tb-burger"/);
});

test('initTheme and initRail run at boot', () => {
  const boot = app.slice(app.indexOf('async function boot'));
  assert.match(boot, /initTheme\(\)/);
  assert.match(boot, /initRail\(\)/);
});

test('the renderer no longer hardcodes dark-only page background', () => {
  assert.doesNotMatch(css, /background: radial-gradient\(1200px 600px at 15% -10%, #17253f/);
  assert.match(css, /--app-glow/);
});
