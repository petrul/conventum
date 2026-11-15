// Pure ranking rules shared by the standalone page and the regression suite.
export const TURKIC = new Set(['tr','ota','az','azb','crh','tt','ba','tk','kk','ky','uz','ug','sah','cv','gag']);

export function originGroup(origin) {
  return TURKIC.has(origin.language) ? 'Turkic' : origin.group;
}

export function visibleOrigins(entry, state) {
  return entry.origins.filter(origin =>
    (origin.direct || state.ancestors) &&
    (state.scope === 'All' || originGroup(origin) === state.scope) &&
    (state.evidence === 'all' || (state.evidence === 'strict' ? origin.relation === 'inherited' : origin.relation !== 'prose'))
  );
}

export function entryScore(entry, state) {
  const key = state.metric === 'definitions' ? 'definition_distance' : 'word_distance';
  const scored = visibleOrigins(entry, state).filter(origin => Number.isFinite(origin[key]));
  // Each direction finds an actual pair in this scope. Never average unrelated
  // homographs or let a different origin tab determine a word's ranking.
  scored.sort((a,b) => (state.sort === 'asc' ? a[key]-b[key] : b[key]-a[key]) ||
    a.sense-b.sense || a.ancestor.localeCompare(b.ancestor, 'ro'));
  return scored.length ? {distance:scored[0][key], origin:scored[0]} : {distance:null, origin:null};
}

export function rankEntries(entries, state) {
  const key = state.metric === 'definitions' ? 'definition_distance' : 'word_distance';
  const fold = text => text.toLocaleLowerCase('ro').normalize('NFD').replace(/[\u0300-\u036f]/g, '');
  // Index both î/â spellings without changing dictionary headwords, meanings,
  // or model inputs. Normalize decomposed Unicode before exchanging letters.
  const searchText = text => {
    const canonical = text.normalize('NFC').toLocaleLowerCase('ro');
    const variant = canonical.replace(/[âî]/g, letter => letter === 'â' ? 'î' : 'â');
    return fold(canonical) + '\n' + fold(variant);
  };
  const query = fold(state.query || '').trim();
  const queries = [...new Set([query,query.replace(/^a\s+/,'')])].filter(Boolean);
  const ranked = [];
  for (const entry of entries) {
    const origins = visibleOrigins(entry, state);
    const classified = entry.origins.some(o => o.direct || state.ancestors);
    if (state.scope === 'Unclassified' ? classified : state.scope !== 'All' && !origins.length) continue;
    if (state.evidence !== 'all' && !origins.length) continue;
    if (queries.length) {
      const text = searchText([entry.word, ...entry.forms, ...origins.map(o => o.ancestor)].join('\n'));
      if (!queries.some(query=>text.includes(query))) continue;
    }
    const score = entryScore(entry, state);
    if (state.scoredOnly && score.distance === null) continue;
    // Distance 0 means identical word–ancestor strings: same vector, i.e. the
    // word has a same-form etymon and was imported unchanged (e.g. academic
    // Latin imports), not inherited.
    if (state.removeZero && origins.some(o => o[key] === 0)) continue;
    ranked.push({entry, ...score});
  }
  ranked.sort((a,b) => {
    if (state.sort !== 'alpha') {
      if (a.distance === null && b.distance !== null) return 1;
      if (b.distance === null && a.distance !== null) return -1;
      if (a.distance !== null && b.distance !== null && a.distance !== b.distance)
        return state.sort === 'asc' ? a.distance-b.distance : b.distance-a.distance;
    }
    return a.entry.word.localeCompare(b.entry.word, 'ro') || a.entry.word.localeCompare(b.entry.word);
  });
  return ranked;
}
