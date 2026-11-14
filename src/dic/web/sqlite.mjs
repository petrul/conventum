// Runtime SQLite queries shared with tests. No server API is required.
export function queryRows(database, sql, parameters=[]) {
  const statement = database.prepare(sql);
  try {
    statement.bind(parameters);
    const rows = [];
    while (statement.step()) rows.push(statement.getAsObject());
    return rows;
  } finally {statement.free();}
}

export function hydrateDictionary(database) {
  const metadata = Object.fromEntries(queryRows(database,'SELECT key,value FROM metadata').map(row=>[row.key,JSON.parse(row.value)]));
  const rows = new Map(queryRows(database,'SELECT id,word,source_url,revision_id FROM entries WHERE is_lemma=1 ORDER BY word').map(row=>[
    row.id,{word:row.word,url:row.source_url,revision:row.revision_id,forms:[],etymologies:[],origins:[]}
  ]));
  for (const row of queryRows(database,'SELECT DISTINCT lemma_id,form FROM forms ORDER BY form')) {
    rows.get(row.lemma_id)?.forms.push(row.form);
  }
  for (const row of queryRows(database,'SELECT t.entry_id,t.sense,t.raw,d.text FROM etymologies t LEFT JOIN etymology_display d ON d.etymology_id=t.id')) {
    if (row.raw) rows.get(row.entry_id)?.etymologies.push({sense:row.sense,raw:row.raw,text:row.text||row.raw});
  }
  for (const row of queryRows(database,`SELECT t.entry_id,t.sense,o.language,o.ancestor,o.origin_group,o.relation,o.is_direct,
      s.word_distance,s.definition_distance,s.word_definition,s.ancestor_definition,s.definition_source
      FROM origins o JOIN etymologies t ON t.id=o.etymology_id LEFT JOIN origin_scores s ON s.origin_id=o.id`)) {
    rows.get(row.entry_id)?.origins.push({sense:row.sense,language:row.language,ancestor:row.ancestor,group:row.origin_group,
      relation:row.relation,direct:!!row.is_direct,word_distance:row.word_distance,definition_distance:row.definition_distance,
      word_definition:row.word_definition,ancestor_definition:row.ancestor_definition,definition_source:row.definition_source});
  }
  return {metadata,entries:[...rows.values()]};
}

export function autocomplete(database, query, limit=12) {
  const key = query.normalize('NFC').toLocaleLowerCase('ro').trim().replace(/^a\s+/,'').normalize('NFD').replace(/[\u0300-\u036f]/g,'');
  if (!key) return [];
  return queryRows(database,`SELECT e.word,min(s.kind) AS kind FROM search_aliases s JOIN entries e ON e.id=s.entry_id
    WHERE s.search_key>=? AND s.search_key<? GROUP BY e.id
    ORDER BY min(CASE WHEN s.search_key=? THEN 0 ELSE 1 END),e.word LIMIT ?`,[key,key+'\u{10ffff}',key,limit]);
}
