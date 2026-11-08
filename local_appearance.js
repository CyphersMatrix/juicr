/* Appearance is browser-local and independent of tests, model settings and credentials. */
(function (root) {
  'use strict';
  const themes = ['default', 'lemonade', 'espresso', 'cyberpunk'];
  const modes = ['novice', 'learned', 'expert'];
  const storageKey = 'juicr.appearance.v1';
  function normalize(value) {
    value = value && typeof value === 'object' ? value : {};
    return {theme: themes.includes(value.theme) ? value.theme : 'default',
            mode: modes.includes(value.mode) ? value.mode : 'novice'};
  }
  function create(storage, onChange) {
    let value;
    try { value = normalize(JSON.parse(storage.getItem(storageKey))); }
    catch (_) { value = normalize(null); }
    function update(patch) {
      value = normalize({...value, ...patch});
      let remembered = true;
      try { storage.setItem(storageKey, JSON.stringify(value)); }
      catch (_) { remembered = false; }
      onChange({...value});
      return { ...value, remembered };
    }
    onChange({...value});
    return {get: () => ({...value}), update};
  }
  if (typeof module !== 'undefined' && module.exports) module.exports = {normalize, create};
  if (!root.document) return;
  // Reading localStorage can itself be denied by the browser.
  let storage;
  try { storage = root.localStorage; }
  catch (_) { storage = {getItem() {return null;}, setItem() {throw Error('Storage unavailable');}}; }
  const preferences = create(storage, value => {
    root.document.documentElement.dataset.theme = value.theme;
    root.document.documentElement.dataset.mode = value.mode;
    root.document.dispatchEvent(new CustomEvent('juicr-appearance', {detail: value}));
  });
  function refresh() {
    const mode = preferences.get().mode;
    root.document.querySelectorAll('[data-guidance-long]').forEach(node => {
      node.textContent = mode === 'novice' ? node.dataset.guidanceLong : node.dataset.guidanceShort;
    });
  }
  root.JUICRAppearance = {...preferences, refresh};
  root.document.addEventListener('DOMContentLoaded', () => {
    const theme = root.document.getElementById('theme-choice');
    const mode = root.document.getElementById('mode-choice');
    const status = root.document.getElementById('appearance-status');
    function sync() {
      const current = preferences.get();
      theme.value = current.theme; mode.value = current.mode; refresh();
    }
    function change(patch) {
      const result = preferences.update(patch); sync();
      status.textContent = result.remembered ? 'Appearance saved in this browser.' :
        'Appearance changed for this page. Browser storage is unavailable.';
      status.classList.toggle('sr-only', result.remembered);
      status.classList.toggle('hint', !result.remembered);
    }
    theme.addEventListener('change', () => change({theme: theme.value}));
    mode.addEventListener('change', () => change({mode: mode.value}));
    sync();
  });
})(typeof window === 'undefined' ? {} : window);
