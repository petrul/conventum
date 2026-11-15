'use strict';
(async () => {
const boot = JSON.parse(document.getElementById('dic-data').textContent);
const SQL = await initSqlJs({locateFile:file=>new URL('vendor/'+file, location.href).href});
const response = await fetch(new URL(boot.database,location.href), {cache:'no-cache'});
if (!response.ok) throw new Error('Cannot load dictionary SQLite: HTTP '+response.status);
const database = new SQL.Database(new Uint8Array(await response.arrayBuffer()));
const {entries,metadata} = hydrateDictionary(database);
window.dicDatabase = database;
window.dicEntries = entries;
const entriesByWord = new Map(entries.map(entry=>[entry.word,entry]));
const $ = id => document.getElementById(id);
const fmt = n => n.toLocaleString('en');
const el = (tag, text, cls) => {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (cls) node.className = cls;
  return node;
};
const preferred = ['Latin','Slavic','Turkic','French','Greek','German','Hungarian','Italian','English','Romanian formations','Other','Unclassified'];
const relationNames = {'prose':'Prose candidate','inherited':'Inherited','borrowed':'Borrowed','derived':'Derived','etymology-template':'Etymology template'};
let active = 'All', page = 0, matches = [], timer;
const size = 80;
const hasEmbeddings = !!metadata.embeddings;

function currentState() {
  return {scope:active, evidence:$('relation').value, ancestors:$('ancestors').checked,
    metric:$('metric').value, sort:$('sort').value, query:$('search').value, scoredOnly:$('scored-only').checked, removeZero:$('remove-zero').checked};
}

function readHash() {
  const hash = new URLSearchParams(location.hash.slice(1));
  const requested = hash.get('origin') === 'Turkish' ? 'Turkic' : hash.get('origin');
  active = preferred.includes(requested) || requested === 'All' ? requested : 'All';
  $('search').value = hash.get('q') || '';
  $('relation').value = ['all','strict','template'].includes(hash.get('evidence')) ? hash.get('evidence') : 'all';
  $('metric').value = hash.get('metric') === 'definitions' ? 'definitions' : 'words';
  $('sort').value = ['alpha','asc','desc'].includes(hash.get('sort')) ? hash.get('sort') : hasEmbeddings ? 'desc' : 'alpha';
  $('ancestors').checked = hash.get('ancestors') !== '0';
  $('scored-only').checked = hash.get('scored') === '1';
  $('remove-zero').checked = hash.get('zero') === '1';
}
readHash();
$('lemma-count').textContent = fmt(entries.length);
$('forms-count').textContent = fmt(metadata.grouped_forms);
$('origin-count').textContent = new Set(entries.flatMap(e => e.origins.filter(o => o.direct).map(originGroup))).size;
$('source-info').textContent = `${metadata.edition.toUpperCase()} Wiktionary · Data build ${metadata.imported_at.slice(0,10)} · Dump SHA-1 ${metadata.sha1} · ${fmt(metadata.strict_latin_pairs)} explicit Latin inheritance pairs`;
$('embedding-info').textContent = hasEmbeddings
  ? `${metadata.embeddings.model} · ${metadata.embeddings.dimensions} dimensions · ${fmt(metadata.embeddings.pairs)} scored pairs · ${fmt(metadata.embeddings.definition_pairs)} with attested definitions · Computed ${metadata.embeddings.completed_at.slice(0,10)}`
  : 'This export has no embedding scores yet.';

function setTab(name) {active = name; page = 0; filter();}

function updateTabs() {
  const counts = new Map();
  for (const entry of entries) {
    const groups = [...new Set(entry.origins.filter(o => o.direct || $('ancestors').checked).map(originGroup))];
    if (!groups.length) groups.push('Unclassified');
    for (const group of groups) counts.set(group, (counts.get(group) || 0) + 1);
  }
  const names = ['All', ...preferred.filter(g => counts.has(g))];
  if (!names.includes(active)) active = 'All';
  $('tabs').replaceChildren();
  for (const [i,name] of names.entries()) {
    const button = el('button', undefined, 'tab');
    button.id = 'tab-' + i;
    button.type = 'button';
    button.setAttribute('role', 'tab');
    button.setAttribute('aria-controls', 'results');
    button.setAttribute('aria-selected', String(name === active));
    button.tabIndex = name === active ? 0 : -1;
    button.append(el('span', name), el('span', fmt(name === 'All' ? entries.length : counts.get(name)), 'count'));
    button.onclick = () => {setTab(name); $('tab-' + i)?.focus({preventScroll:true});};
    button.onkeydown = event => {
      const indexes = {ArrowRight:(i+1)%names.length, ArrowLeft:(i-1+names.length)%names.length, Home:0, End:names.length-1};
      if (event.key in indexes) {
        event.preventDefault();
        const next = indexes[event.key];
        setTab(names[next]);
        $('tab-' + next)?.focus();
      }
    };
    $('tabs').append(button);
  }
  $('results').setAttribute('aria-labelledby', 'tab-' + names.indexOf(active));
}

function filter() {
  updateTabs();
  const state = currentState();
  matches = rankEntries(entries, state);
  const params = new URLSearchParams({origin:active, q:state.query, evidence:state.evidence,
    metric:state.metric, sort:state.sort, ancestors:state.ancestors?'1':'0', scored:state.scoredOnly?'1':'0', zero:state.removeZero?'1':'0'});
  history.replaceState(null, '', '#' + params);
  const scored = matches.filter(row => row.distance !== null).length;
  $('ranking-note').textContent = `${state.metric === 'words' ? 'Bare-word embeddings' : 'First attested dictionary definitions'} · ${fmt(scored)} of ${fmt(matches.length)} words scored in this scope. ${state.sort === 'asc' ? 'Smallest' : 'Largest'} eligible word–ancestor distance per word; missing scores appear last. Cosine distance: 0 = similar, 2 = opposite vectors.`;
  render();
}

function renderOrigins(entry, selected, state) {
  const cell = el('div');
  const seen = new Set();
  for (const origin of visibleOrigins(entry, state)) {
    const key = [origin.language, origin.ancestor, origin.relation, origin.sense, origin.direct].join('|');
    if (seen.has(key)) continue;
    seen.add(key);
    const block = el('div', undefined, origin === selected ? 'origin-item selected-origin' : 'origin-item');
    block.append(el('div', origin.ancestor, 'ancestors'));
    const meta = el('div', undefined, 'origin-meta');
    meta.append(el('span', originGroup(origin), 'tag'), document.createTextNode(`${origin.language} · ${relationNames[origin.relation] || origin.relation} · sense ${origin.sense}${origin.direct?'':' · deeper ancestor'}`));
    const value = state.metric === 'definitions' ? origin.definition_distance : origin.word_distance;
    if (Number.isFinite(value)) meta.append(document.createTextNode(' · ' + value.toFixed(3)));
    block.append(meta);
    cell.append(block);
  }
  if (!seen.size) cell.append(el('span', 'No structured source extracted; see the etymology text.', 'origin-meta'));
  return cell;
}

function renderScore(row, state) {
  const cell = el('div', undefined, 'distance-cell');
  cell.append(el('div', row.distance === null ? '—' : row.distance.toFixed(3), 'distance'));
  if (row.origin) {
    cell.append(el('div', '↔ ' + row.origin.ancestor, 'origin-meta'));
    const details = el('details', undefined, 'score-details');
    details.append(el('summary', 'Compare inputs'));
    if (state.metric === 'definitions') {
      details.append(el('div', `${row.entry.word}: ${row.origin.word_definition}`, 'forms'),
        el('div', `${row.origin.ancestor}: ${row.origin.ancestor_definition}`, 'forms'));
      if (row.origin.definition_source) {
        const source = el('a', 'Source meaning ↗');
        source.href = row.origin.definition_source;
        source.target = '_blank'; source.rel = 'noopener noreferrer';
        details.append(source);
      }
    } else {
      details.append(el('div', `${row.entry.word} ↔ ${row.origin.ancestor}`, 'forms'));
    }
    cell.append(details);
  } else {
    cell.append(el('div', state.metric === 'definitions' ? 'Definitions unavailable for this pair' : 'No scored pair', 'origin-meta'));
  }
  return cell;
}

function originRows(result, state) {
  const key = state.metric === 'definitions' ? 'definition_distance' : 'word_distance';
  const seen = new Set();
  let origins = visibleOrigins(result.entry, state).filter(origin => {
    const k = [origin.language, origin.ancestor, origin.relation, origin.sense, origin.direct].join('|');
    if (seen.has(k)) return false;
    seen.add(k);
    return true;
  });
  if (state.scoredOnly) origins = origins.filter(o => Number.isFinite(o[key]));
  origins.sort((x, y) => {
    if (state.sort !== 'alpha') {
      const xd = x[key], yd = y[key];
      if (!Number.isFinite(xd)) return 1;
      if (!Number.isFinite(yd)) return -1;
      if (xd !== yd) return state.sort === 'asc' ? xd - yd : yd - xd;
    }
    return x.sense - y.sense || x.ancestor.localeCompare(y.ancestor, 'ro');
  });
  if (!origins.length) return [{entry: result.entry, origin: null, distance: null}];
  return origins.map(origin => ({entry: result.entry, origin, distance: Number.isFinite(origin[key]) ? origin[key] : null}));
}
function render() {
  const container = $('results');
  container.replaceChildren();
  const state = currentState(), total = matches.length, start = page * size;
  $('result-count').textContent = `${fmt(total)} ${total===1?'word':'words'}${active==='All'?'':' · '+active}`;
  $('page-label').textContent = total ? `${fmt(start+1)}–${fmt(Math.min(start+size,total))} of ${fmt(total)}` : '0 words';
  $('prev').disabled = page === 0;
  $('next').disabled = start + size >= total;
  if (!total) {container.append(el('div', 'No words match these filters. Try another origin, use all evidence, or shorten your search.', 'empty')); return;}
  for (const result of matches.slice(start, start + size)) for (const line of originRows(result, state)) {
    const entry = line.entry, origin = line.origin, row = el('article', undefined, 'word-row');
    row.dataset.word = entry.word;
    const word = el('div');
    word.append(el('div', entry.word, 'word'));
    const source = el('a', '↗', 'wiktionary-link');
    source.href = entry.url + (metadata.edition==='en'?'#Romanian':'');
    source.target = '_blank'; source.rel = 'noopener noreferrer';
    source.title = 'View ' + entry.word + ' on Wiktionary';
    source.setAttribute('aria-label', 'View ' + entry.word + ' on Wiktionary');
    const sourceLine = el('div', undefined, 'origin-meta');
    sourceLine.append(source); word.append(sourceLine); row.append(word);
    row.append(renderOrigins(origin ? {...entry, origins:[origin]} : entry, origin, state), renderScore({entry, distance:line.distance, origin}, state));
    const forms = el('div', undefined, 'forms-cell');
    if (entry.forms.length) {
      forms.append(el('span', `${entry.forms.length} ${entry.forms.length===1?'form':'forms'}`, 'tag'), el('div', entry.forms.slice(0,8).join(' · '), 'forms'));
      if (entry.forms.length > 8) {
        const more = el('details');
        more.append(el('summary', 'See all forms'), el('div', entry.forms.join(' · '), 'forms'));
        forms.append(more);
      }
    } else forms.append(el('span', 'No explicit forms recorded', 'origin-meta'));
    word.append(forms);
    const etymology = el('div', undefined, 'etymology-cell');
    for (const item of entry.etymologies) etymology.append(el('p',`Etymology ${item.sense}: ${item.text||item.raw}`));
    if (!entry.etymologies.length) etymology.append(el('p','No etymology supplied by the source.'));
    row.append(etymology);
    if (entry.etymologies.length) {
      const details = el('details', undefined, 'details');
      details.append(el('summary', 'Read source etymology'));
      for (const etymology of entry.etymologies) details.append(el('div', `Etymology ${etymology.sense}\n${etymology.raw}`, 'evidence'));
      const revision = el('a', 'Source revision ' + entry.revision);
      revision.href = `https://${metadata.edition}.wiktionary.org/w/index.php?oldid=${encodeURIComponent(entry.revision)}`;
      revision.target = '_blank'; revision.rel = 'noopener noreferrer';
      details.append(revision); row.append(details);
    }
    container.append(row);
  }
}
function updateSuggestions() {
  const options = autocomplete(database,$('search').value,100);
  const state = currentState();
  $('word-suggestions').replaceChildren();
  for (const suggestion of options) {
    const entry = entriesByWord.get(suggestion.word);
    if (!entry || !rankEntries([entry],{...state,query:'',scoredOnly:false}).length) continue;
    const option = el('option'); option.value=suggestion.word;
    option.label=suggestion.kind==='ancestor'?'Source-word match':suggestion.kind==='form'?'Grouped form':'Dictionary word';
    $('word-suggestions').append(option);
    if ($('word-suggestions').children.length>=12) break;
  }
}
$('search').addEventListener('input', () => {clearTimeout(timer); timer = setTimeout(() => {page=0;updateSuggestions();filter();},120);});
document.addEventListener('keydown',event=>{
  if (event.key==='/' && !event.ctrlKey && !event.metaKey && !event.altKey && !event.target.closest('input,textarea,select,[contenteditable=true]')) {
    event.preventDefault(); $('search').focus(); $('search').select();
  }
});
for (const id of ['relation','sort','metric','ancestors','scored-only','remove-zero']) $(id).onchange = () => {page=0;filter();};
$('prev').onclick = () => {page--;render();};
$('next').onclick = () => {page++;render();};
function csvField(value) {
  const text = value === null || value === undefined ? '' : String(value);
  return /[",\n\r]/.test(text) ? '"' + text.replace(/"/g, '""') + '"' : text;
}
function downloadCsv() {
  const state = currentState();
  const metricLabel = state.metric === 'definitions' ? 'attested first definitions' : 'bare-word embeddings';
  const stamp = new Date().toISOString();
  const name = boot.name || 'export';
  const comments = [
    `# ${name} — Romanian etymology explorer`,
    `# Generated ${stamp}`,
    `# Provenance: ${metadata.edition}.wiktionary.org under CC BY-SA 4.0; XML dump SHA-1 ${metadata.sha1}; data build ${metadata.imported_at.slice(0,10)}`,
    `# Filter: tab ${active} · evidence ${state.evidence} · deeper ancestors ${state.ancestors?'yes':'no'} · metric ${metricLabel} · sort ${state.sort} · scored only ${state.scoredOnly?'yes':'no'} · remove zero distances ${state.removeZero?'yes':'no'} · query ${state.query ? '"'+state.query+'"' : '(none)'}`,
    `# Lines: one per word–source-word pair in the current filter across ALL pages, not only the visible page of ${size}.`,
    '# Columns: word, comparison_term (the source word this line compares against), distance',
    '# distance is the cosine distance (1 − dot of normalized embeddings; 0 = identical vectors, 2 = opposite) for that pair; blank when the pair is unscored.',
  ];
  const header = ['word','comparison_term','distance'];
  const rows = [];
  for (const result of matches) for (const line of originRows(result, state)) {
    rows.push([line.entry.word, line.origin ? line.origin.ancestor : '', line.distance === null ? '' : line.distance.toFixed(6)]);
  }
  const csv = comments.join('\n') + '\n' + [header, ...rows].map(fields => fields.map(csvField).join(',')).join('\n') + '\n';
  const blob = new Blob([csv], {type: 'text/csv;charset=utf-8'});
  const link = el('a');
  link.href = URL.createObjectURL(blob);
  link.download = `${name}-${(active === 'All' ? 'all' : active).toLowerCase().replace(/[^a-z0-9]+/g, '-')}-${stamp.slice(0,10)}.csv`;
  document.body.append(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(link.href);
}
if ($('download-csv')) $('download-csv').onclick = downloadCsv;
window.addEventListener('hashchange', () => {readHash();page=0;filter();});
filter();

window.dicReady = true;
})().catch(error=>{
  document.getElementById('result-count').textContent='Unable to load the dictionary';
  document.getElementById('results').textContent=error.message+' · Serve the complete site folder over HTTP, including dic.sqlite and vendor assets.';
  document.getElementById('results').setAttribute('role','alert');
  console.error(error);
});
