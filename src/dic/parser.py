"""Conservative extraction: template assertions and labelled prose candidates.

No template expansion or guessed stemming. Immediate sources are kept separate
from deeper ancestors and comparisons. Raw etymologies remain available for audit.
"""
from dataclasses import dataclass, field
import re
import unicodedata

import mwparserfromhell as mw


def normalize(value):
    return unicodedata.normalize("NFC", value).replace("ş", "ș").replace("ţ", "ț").replace("Ş", "Ș").replace("Ţ", "Ț")


def plain(value):
    return normalize(str(mw.parse(str(value)).strip_code()).replace("''", "").strip())


ALIASES = {"lat": "la", "latină": "la", "latin": "la", "fra": "fr", "eng": "en",
           "ron": "ro", "română": "ro", "deu": "de", "ger": "de", "ita": "it",
           "rus": "ru", "bul": "bg", "tur": "tr", "hun": "hu", "grc": "grc",
           "slav": "sla", "sl": "sl", "slavă": "sla", "slavonă": "cu"}
SLAVIC = {"sla", "sla-pro", "cu", "ru", "bg", "uk", "pl", "cs", "sk", "sr", "hr", "sh", "sl", "mk", "be", "orv"}
GROUPS = {"la": "Latin", "fr": "French", "de": "German", "it": "Italian", "tr": "Turkish",
          "hu": "Hungarian", "el": "Greek", "grc": "Greek", "en": "English", "ro": "Romanian formations"}


def language(code):
    code = normalize(code.strip().lower())
    return ALIASES.get(code, code)


def group(code):
    return "Slavic" if code in SLAVIC else GROUPS.get(code, "Other")


@dataclass(frozen=True)
class Origin:
    language: str
    ancestor: str
    relation: str
    evidence: str
    direct: bool = True


@dataclass
class Entry:
    word: str
    etymologies: list[str] = field(default_factory=list)
    origins: list[tuple[int, Origin]] = field(default_factory=list)
    forms: set[tuple[str, str]] = field(default_factory=set)
    form_of: set[str] = field(default_factory=set)
    lemma: bool = True


LANGUAGE_HEADING = re.compile(r"^==\s*([^=\n]+?)\s*==\s*$", re.M)
ROMANIAN = re.compile(r"^(?:Romanian|Română|română|\{\{\s*limba\s*\|\s*(?:ron|ro)\s*\}\})$", re.I)
ETY_MARKER = re.compile(r"\{\{\s*-etimologie-\s*\}\}|^={3,}\s*(?:Etymology|Etimologie)(?:\s+\d+)?\s*={3,}\s*$", re.M | re.I)
SECTION_END = re.compile(r"\{\{\s*-[^{}\n]+?-\s*(?:\|[^{}]*)?\}\}|^={3,}[^\n]+?={3,}\s*$", re.M)


def romanian_sections(text):
    headings = list(LANGUAGE_HEADING.finditer(text))
    for index, heading in enumerate(headings):
        if ROMANIAN.fullmatch(heading.group(1).strip()):
            yield text[heading.end():headings[index + 1].start() if index + 1 < len(headings) else len(text)]


def param(template, number):
    return plain(template.get(str(number)).value) if template.has(str(number)) else ""


# Longest labels first. These labels are only accepted after an explicit "Din".
LABELS = {"latină": "la", "latina vulgară": "la", "latină vulgară": "la", "franceză": "fr",
          "germană": "de", "italiană": "it", "engleză": "en", "turcă": "tr", "maghiară": "hu",
          "neogreacă": "el", "greacă veche": "grc", "greacă": "el", "rusă": "ru", "bulgară": "bg",
          "slavă (veche)": "cu", "slavă veche": "cu", "slavonă": "cu", "slavă": "sla",
          "sârbocroată": "sh", "sârbă": "sr", "poloneză": "pl", "ucraineană": "uk",
          "cehă": "cs", "slovenă": "sl", "spaniolă": "es", "portugheză": "pt",
          "albaneză": "sq", "arabă": "ar", "persană": "fa", "română": "ro"}
LABEL_PATTERN = "|".join(re.escape(x) for x in sorted(LABELS, key=len, reverse=True))
SOURCE_CLAUSE = re.compile(rf"\b({LABEL_PATTERN})\s+((?:''|\[\[).+?)(?=(?:,\s*(?:{LABEL_PATTERN})\b)|$)", re.I)


def ancestor_term(value):
    """Prefer a linked dictionary headword to its inflected/marked display."""
    code = mw.parse(value)
    links = code.filter_wikilinks()
    if len(links) == 1 and str(code).strip() == str(links[0]):
        target = str(links[0].title).strip()
        return normalize(target.split('#')[0]) if not target.startswith('#') else plain(links[0].text or target[1:])
    return plain(value)


def prose_origins(raw):
    # Read only an explicit source clause. Explanations/comparisons following
    # it are evidence, but do not themselves supply additional donor words.
    start = re.match(r"\s*[*:]?\s*(?:(?:Împrumutat|Preluat|Provine|Provenit)\s+)?din\s+(.+)", raw, re.S | re.I)
    if not start:
        return []
    text = start.group(1)
    text = re.split(r"(?:\.\s|;|\b(?:Confer|Înrudit|Compară|Cf\.|prin)\b)", text, maxsplit=1, flags=re.I)[0]
    if re.search(r"\b(?:probabil|poate|posibil|nesigur|incert)\b", text, re.I):
        return []
    text = re.sub(r"\[\[([^]|]+)\]\]", lambda m: m[1] if m[1].lower() in LABELS else m[0], text)
    text = re.sub(r"''(latină|franceză|germană|italiană)''", r"\1", text)
    results = []
    for branch in re.split(rf',\s*(?=(?:{LABEL_PATTERN})\b)', text):
        last_code = None
        for depth, segment in enumerate(branch.split('<')):
            # A chain can omit a repeated language: paumentum < pavimentum.
            clauses = list(SOURCE_CLAUSE.finditer(segment.strip()))
            items = [(LABELS[m[1].lower()], m[2]) for m in clauses]
            if not items and depth and last_code:
                items = [(last_code, segment)]
            for code, clause in items:
                last_code = code
                equivalents = re.findall(r'\(\s*=\s*([^()]*)\)', clause)
                # Inline parenthetical glosses are recorded separately, not as roots.
                clause = re.sub(r"\([^()]*\)", '', clause)
                for term in re.findall(r"''(.*?)''", clause, re.S):
                    term = ancestor_term(term)
                    if term and not any(c in term for c in '{}'):
                        results.append(Origin(code, term, "prose", raw.strip(), depth == 0))
                for equivalent in equivalents:
                    for term in re.findall(r"''(.*?)''", equivalent):
                        term = ancestor_term(term)
                        if term and not any(c in term for c in '{}'):
                            results.append(Origin(code, term, "prose", raw.strip(), False))
    return results


def extract_origins(raw):
    result = []
    for template in mw.parse(raw).filter_templates(recursive=False):
        name = str(template.name).strip().lower().replace('_', ' ')
        if name in {"inh", "inherited", "bor", "borrowed", "der", "derived"} and language(param(template, 1)) == "ro":
            code, term = language(param(template, 2)), param(template, 3)
            relation = {"inh": "inherited", "inherited": "inherited", "bor": "borrowed", "borrowed": "borrowed"}.get(name, "derived")
        elif name in {"etimologie", "etim"}:
            code, term, relation = language(param(template, 1)), param(template, 2), "etymology-template"
        else:
            continue
        if code and term and term != "-":
            # In English prose, a template after '<' or 'from' following another
            # source template describes deeper ancestry, not an immediate donor.
            before = raw.split(str(template), 1)[0]
            # Multiple parallel donor templates are valid. A second template
            # following "from"/"<" is instead a deeper step in the chain.
            previous = before.rsplit('}}', 1)[-1] if result else before
            direct = '<' not in previous and not (result and re.search(r'\bfrom\b', previous, re.I))
            result.append(Origin(code, term, relation, str(template), direct))
    return result or prose_origins(raw)


FORM_TEMPLATES = {"inflection of", "infl of", "plural of", "feminine of", "masculine of", "form of",
                  "alternative form of", "alternative spelling of", "past participle of", "present participle of"}
PARADIGM_KEY = re.compile(r"^(?:nom|art|dat|gen|voc|ac|acc|sg|pl|f|m|n|pres|imperfect|perfect|participiu)(?:-|$)")
FORM_PROSE = re.compile(r"^#\s*''(?:forma|formă|pluralul|femininul)\b[^\n]*?(?:pentru|a[l]?(?:e)?|de la)\s*''?\s*(?:'''\s*)?\[\[([^]|#]+)", re.M | re.I)


def is_form_definition(raw):
    templates = '|'.join(re.escape(name) for name in FORM_TEMPLATES)
    if re.search(r'\{\{\s*(?:' + templates + r')\s*\|', raw, re.I):
        return True
    # A mention of the verb [[forma]] inside a definition is not an
    # inflection declaration. These formulae must begin the definition.
    return bool(FORM_PROSE.match('#' + raw))


def parse_entry(title, text):
    sections = list(romanian_sections(normalize(text)))
    if not sections:
        return None
    entry = Entry(normalize(title))
    body = '\n'.join(sections)
    for marker in ETY_MARKER.finditer(body):
        rest = body[marker.end():]
        end = SECTION_END.search(rest)
        raw = rest[:end.start() if end else len(rest)].strip()
        entry.etymologies.append(raw)
        index = len(entry.etymologies)
        entry.origins.extend((index, origin) for origin in extract_origins(raw))
    for template in mw.parse(body).filter_templates():
        name = str(template.name).strip().lower().replace('_', ' ')
        if name in FORM_TEMPLATES and language(param(template, 1)) == "ro":
            target = param(template, 3 if name == "form of" else 2)
            if target:
                entry.form_of.add(target)
        if name.endswith('-ron') or name in {'ro-noun', 'ro-adj', 'ro-verb', 'ro-pronoun'}:
            for parameter in template.params:
                key = str(parameter.name).strip()
                if PARADIGM_KEY.match(key):
                    value = plain(parameter.value)
                    for form in re.split(r",\s*|\s*/\s*|\s+;\s+", value):
                        if form and form != entry.word and not re.search(r"[{}<>\n]|\b(?:sau|vezi)\b", form) and len(form) < 100:
                            entry.forms.add((form, key))
    entry.form_of.update(normalize(m[1]) for m in FORM_PROSE.finditer(body))
    definitions = re.findall(r"^#(?![:*#])\s*(.+)$", body, re.M)
    # A homograph can be both an inflection and an independent lemma.
    nonform = [d for d in definitions if not is_form_definition(d)]
    entry.lemma = not entry.form_of or bool(nonform)
    return entry
