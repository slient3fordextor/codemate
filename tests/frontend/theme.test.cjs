const { test } = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const source = fs.readFileSync('app/static/theme.js', 'utf8');
function boot(saved, native, blocked = false) {
  let ready, stored, posted;
  const buttons = ['princess', 'starry'].map(theme => ({
    dataset: { themeChoice: theme },
    setAttribute(name, value) { this[name] = value; },
    addEventListener(name, fn) { this[name] = fn; },
  }));
  const document = {
    documentElement: { dataset: {} },
    querySelectorAll: () => buttons,
    addEventListener: (_, fn) => { ready = fn; },
  };
  vm.runInNewContext(source, {
    document,
    localStorage: {
      getItem() { if (blocked) throw Error('blocked'); return saved; },
      setItem(_, value) { if (blocked) throw Error('blocked'); stored = value; },
    },
    window: { codemateTheme: native, webkit: { messageHandlers: {
      codemateTheme: { postMessage(value) { posted = value; } },
    } } },
  });
  ready();
  return { document, buttons, saved: () => stored, posted: () => posted };
}
test('restores saved theme and toggles both themes without reload', () => {
  const ui = boot('starry');
  assert.equal(ui.document.documentElement.dataset.theme, 'starry');
  for (const button of ui.buttons) {
    button.click();
    assert.equal(ui.document.documentElement.dataset.theme, button.dataset.themeChoice);
    assert.equal(button['aria-pressed'], 'true');
    assert.equal(ui.saved(), button.dataset.themeChoice);
    assert.equal(ui.posted(), button.dataset.themeChoice);
  }
});
test('native saved theme takes precedence over per-port browser storage', () => {
  assert.equal(boot('princess', 'starry').document.documentElement.dataset.theme, 'starry');
});
test('invalid or unavailable storage falls back and does not block switching', () => {
  assert.equal(boot('invalid').document.documentElement.dataset.theme, 'princess');
  const ui = boot(null, undefined, true);
  ui.buttons[1].click();
  assert.equal(ui.document.documentElement.dataset.theme, 'starry');
  assert.equal(ui.posted(), 'starry');
});
