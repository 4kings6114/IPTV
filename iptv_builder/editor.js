// Entirely local: no requests, analytics, player, credentials or backend.
const editor = report.editor || {channels: [], favorites: [], profiles: {}, epg_urls: []};
const favoritesSet = new Set(editor.favorites || []);
let profileDefinitions = {...(editor.profiles || {})};
const selectedOptions = id => [...$(id).selectedOptions].map(option => option.value);
function selectOptions(id, values) {
  for (const option of $(id).options) option.selected = values.includes(option.value);
}
for (const key of ['country', 'language']) {
  for (const value of [...new Set((editor.channels || []).map(row => row[key]).filter(Boolean))].sort()) {
    const option = node('option', value); option.value = value; $('edit-' + key).append(option);
  }
}
function profileMenu() {
  const select = $('edit-profile'); select.replaceChildren(node('option', 'پروفایل جدید'));
  select.options[0].value = '';
  for (const name of Object.keys(profileDefinitions).sort()) { const option = node('option', name); option.value = name; select.append(option); }
}
function editorOptions() {
  const current = profileDefinitions[$('edit-profile').value] || {};
  return {languages: selectedOptions('edit-language'), countries: selectedOptions('edit-country'),
    max_resolution: Number($('edit-resolution').value), max_bitrate: Math.round(Number($('edit-bitrate').value || 0) * 1000),
    include_unknown_resolution: $('edit-unknown-quality').checked, include_unknown_bitrate: $('edit-unknown-bitrate').checked,
    favorites_only: $('edit-favorites-only').checked,
    ...(current.preferred_sources ? {preferred_sources: current.preferred_sources} : {})};
}
function preferenceRank(row, preferences) {
  for (let index = 0; index < preferences.length; index++) {
    const pref = preferences[index];
    for (const raw of [row.source, row.request_url]) {
      try { const url = new URL(raw); if (pref.includes('://')) { const prefix = new URL(pref); if (prefix.origin === url.origin && url.pathname.startsWith(prefix.pathname)) return index; }
      else if ([url.host, url.hostname].includes(pref.toLowerCase())) return index; } catch (_) { /* non-URL source label */ }
    }
  }
  return preferences.length;
}
function personalSelection() {
  const options = editorOptions(), choices = new Map(), omitted = new Set();
  const preferences = options.preferred_sources || [];
  const candidates = [...(editor.channels || [])].sort((a, b) =>
    (a.status === 'healthy' ? 0 : 1) - (b.status === 'healthy' ? 0 : 1) ||
    preferenceRank(a, preferences) - preferenceRank(b, preferences) ||
    b.stability - a.stability || b.resolution - a.resolution || a.m3u.localeCompare(b.m3u));
  for (const row of candidates) {
    if (options.favorites_only && !favoritesSet.has(row.identity)) continue;
    if (options.languages.length && !options.languages.includes(row.language)) continue;
    if (options.countries.length && !options.countries.includes(row.country)) continue;
    if ((row.status !== 'healthy' && !$('edit-unchecked').checked) ||
      (options.max_resolution && (row.resolution > options.max_resolution || (!row.resolution && !options.include_unknown_resolution))) ||
      (options.max_bitrate && (row.bitrate > options.max_bitrate || (!row.bitrate && !options.include_unknown_bitrate)))) { omitted.add(row.identity); continue; }
    if (!choices.has(row.identity)) choices.set(row.identity, row);
  }
  // Match the builder's one-URL rule, also across different channel IDs.
  const urls = new Set(), selected = [];
  for (const row of choices.values()) {
    const url = row.m3u.trim().split('\n').at(-1);
    if (!urls.has(url)) { urls.add(url); selected.push(row); }
  }
  const included = new Set(selected.map(row => row.identity));
  return {selected, omitted: [...omitted].filter(id => !included.has(id)).length};
}
function updateEditor() {
  const selection = personalSelection();
  const unknown = selection.selected.filter(row => !row.bitrate).length;
  const unchecked = selection.selected.filter(row => row.status !== 'healthy').length;
  $('editor-status').textContent = 'خروجی شخصی: ' + selection.selected.length + ' کانال · کنارگذاشته‌شده به‌علت سقف/نبود داده/بررسی‌نشده: ' + selection.omitted + ' · بیت‌ریت نامشخص در خروجی: ' + unknown + ' · بررسی‌نشده در خروجی: ' + unchecked + ' · علاقه‌مندی انتخاب‌شده: ' + favoritesSet.size;
  $('download-personal').disabled = selection.selected.length === 0;
}
function favoriteButton(row) {
  const button = node('button', favoritesSet.has(row.identity) ? '★' : '☆');
  button.type = 'button'; button.setAttribute('aria-label', 'علاقه‌مندی: ' + row.name);
  button.setAttribute('aria-pressed', String(favoritesSet.has(row.identity)));
  button.addEventListener('click', () => { if (favoritesSet.has(row.identity)) favoritesSet.delete(row.identity); else favoritesSet.add(row.identity); draw(); updateEditor(); });
  return button;
}
function downloadLocal(filename, text, type) {
  const blob = new Blob([text], {type});
  const url = URL.createObjectURL(blob), link = node('a'); link.href = url; link.download = filename;
  document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);
}
function personalPlaylist() {
  const guides = (editor.epg_urls || []).filter(url => /^https?:\/\//.test(url) && !/["\s,]/.test(url));
  const header = guides.length ? '#EXTM3U url-tvg="' + guides.join(',') + '"\n' : '#EXTM3U\n';
  return header + personalSelection().selected.map(row => row.m3u).join('');
}
$('download-favorites').addEventListener('click', () => downloadLocal('favorites.json', JSON.stringify({channels: [...favoritesSet].sort()}, null, 2) + '\n', 'application/json'));
$('download-profile').addEventListener('click', () => {
  const name = $('edit-name').value.trim();
  if (!/^[a-z0-9][a-z0-9-]{0,39}$/.test(name)) { $('editor-error').textContent = 'نام پروفایل باید ۱ تا ۴۰ حرف کوچک انگلیسی، رقم یا خط تیره باشد.'; return; }
  if (!Number.isFinite(editorOptions().max_bitrate) || editorOptions().max_bitrate < 0 || editorOptions().max_bitrate > 1000000000) { $('editor-error').textContent = 'سقف بیت‌ریت باید بین صفر و یک میلیون کیلوبیت در ثانیه باشد.'; return; }
  profileDefinitions[name] = editorOptions();
  if (Object.keys(profileDefinitions).length > 20) { delete profileDefinitions[name]; $('editor-error').textContent = 'حداکثر ۲۰ پروفایل پشتیبانی می‌شود.'; return; }
  $('editor-error').textContent = '';
  downloadLocal('profiles.json', JSON.stringify({profiles: profileDefinitions}, null, 2) + '\n', 'application/json'); profileMenu(); $('edit-profile').value = name;
});
$('download-personal').addEventListener('click', () => downloadLocal('personal.m3u', personalPlaylist(), 'audio/x-mpegurl'));
$('edit-profile').addEventListener('change', () => {
  const name = $('edit-profile').value, options = profileDefinitions[name] || {};
  $('edit-name').value = name || 'personal'; selectOptions('edit-language', options.languages || []); selectOptions('edit-country', options.countries || []);
  const height = String(options.max_resolution || 0);
  if (![...$('edit-resolution').options].some(option => option.value === height)) { const option = node('option', height + 'p'); option.value = height; $('edit-resolution').append(option); }
  $('edit-resolution').value = height; $('edit-bitrate').value = String((options.max_bitrate || 0) / 1000);
  for (const [id, key] of [['edit-unknown-quality','include_unknown_resolution'], ['edit-unknown-bitrate','include_unknown_bitrate'], ['edit-favorites-only','favorites_only']]) $(id).checked = Boolean(options[key]);
  updateEditor();
});
for (const id of ['edit-language','edit-country','edit-resolution','edit-bitrate','edit-unknown-quality','edit-unknown-bitrate','edit-favorites-only','edit-unchecked']) $(id).addEventListener('change', updateEditor);
$('edit-bitrate').addEventListener('input', updateEditor);
$('import-favorites').addEventListener('change', async () => {
  try {
    const file = $('import-favorites').files[0]; if (!file) return;
    if (file.size > 1048576) throw new Error('فایل بزرگ‌تر از یک مگابایت است.');
    const data = JSON.parse(await file.text());
    if (!data || Array.isArray(data) || Object.keys(data).length !== 1 || !Array.isArray(data.channels) || !data.channels.every(id => typeof id === 'string' && id.trim() && !/[\u0000-\u001f]/.test(id))) throw new Error('ساختار favorites.json معتبر نیست.');
    favoritesSet.clear();
    for (let id of data.channels) { id = id.trim().toLowerCase(); if (!id.startsWith('name:')) { id = id.replace(/^id:/,'').replace(/\.gb(?=@|$)/,'.uk').replace(/@(sd|hd|fhd|uhd|4k|\d{3,4}p)$/,''); id = 'id:' + id; } favoritesSet.add(id); }
    $('editor-error').textContent = ''; draw(); updateEditor();
  } catch (error) { $('editor-error').textContent = 'ورود فایل ناموفق: ' + error.message; }
});
profileMenu(); updateEditor();
// Exposed for offline regression tests and local inspection, never sent anywhere.
window.iptvEditor = {personalPlaylist, personalSelection, editorOptions, favoritesSet};
