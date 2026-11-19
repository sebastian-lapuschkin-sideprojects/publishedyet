"""
publishedyet: has the preprint you cite been published by now?

This file holds the core logic. It is shared by the command line tool (cli.py) and
the web page (web.py), which runs this very same file inside the browser via Pyodide.
Everything that touches the network goes through a `get_json(url)` coroutine that the
caller hands in, so nothing in here cares about where it runs.

The database is one json file per preprint (see db/ and the README for the format).
"""

import re
import html
import difflib
import unicodedata
from urllib.parse import quote

OPENALEX = 'https://api.openalex.org'
CROSSREF = 'https://api.crossref.org'
BIORXIV = 'https://api.biorxiv.org'


# ---------------------------------------------------------------------------------------
# preprint identifiers
# ---------------------------------------------------------------------------------------
# Preprints are named by prefixed ids: "arxiv:2402.05602", "arxiv:hep-th/9901001" or
# "doi:10.1101/2020.01.01.890160" (bioRxiv / medRxiv). Version suffixes (v1, v2, ...)
# are dropped: all versions of a preprint share one database record.

ARXIV_NEW = r'\d{4}\.\d{4,5}'                        # since 2007, e.g. 2402.05602
ARXIV_OLD = r'[a-z][a-z-]*(?:\.[a-z]{2})?/\d{7}'      # before, e.g. hep-th/9901001, math.GT/0309136
BIORXIV_DOI = r'10\.1101/(?:\d{4}\.\d{2}\.\d{2}\.)?\d{6,}'

# a bare id, as typed into the input field
BARE_ARXIV = re.compile(r'^(?:arxiv:)?({}|{})(?:v\d+)?$'.format(ARXIV_NEW, ARXIV_OLD), re.IGNORECASE)
# an arXiv id somewhere in a bibtex entry or a link: arXiv:2402.05602, arxiv.org/abs/2402.05602,
# 10.48550/arXiv.2402.05602, abs/2402.05602 (DBLP), eprint = {2402.05602}
ARXIV_IN_TEXT = re.compile(r'(?:arxiv(?:\.org/(?:abs|pdf))?[\s:./]*|abs/|eprint\s*=\s*"?\s*)({}|{})(?:v\d+)?'
                           .format(ARXIV_NEW, ARXIV_OLD), re.IGNORECASE)
BIORXIV_IN_TEXT = re.compile('(' + BIORXIV_DOI + ')', re.IGNORECASE)


def canonical_arxiv(arxiv_id):
    # old style ids may carry a subject class (math.GT/0309136), which is not part of the id
    if '/' in arxiv_id:
        archive, number = arxiv_id.split('/')
        return 'arxiv:{}/{}'.format(archive.split('.')[0].lower(), number)
    return 'arxiv:' + arxiv_id


def find_preprint_id(text):
    # returns the (first) preprint id mentioned in text, or None
    text = text.replace('{', '').replace('}', '')
    match = BARE_ARXIV.match(text.strip())
    if match:
        return canonical_arxiv(match.group(1))
    match = ARXIV_IN_TEXT.search(text)
    if match:
        return canonical_arxiv(match.group(1))
    match = BIORXIV_IN_TEXT.search(text)
    if match:
        return 'doi:' + match.group(1)
    return None


def db_path(pid):
    # where the record of a preprint lives inside the database folder
    kind, ident = pid.split(':', 1)
    if kind == 'arxiv' and '/' in ident:
        return 'arxiv/{}.json'.format(ident)                      # arxiv/hep-th/9901001.json
    if kind == 'arxiv':
        return 'arxiv/{}/{}.json'.format(ident[:4], ident)        # arxiv/2402/2402.05602.json
    prefix, suffix = ident.split('/', 1)
    return 'doi/{}/{}.json'.format(prefix, suffix.replace('/', '_'))  # doi/10.1101/2020.01.01.890160.json


def preprint_doi(pid):
    kind, ident = pid.split(':', 1)
    return '10.48550/arXiv.' + ident if kind == 'arxiv' else ident


def preprint_url(pid):
    kind, ident = pid.split(':', 1)
    return 'https://arxiv.org/abs/' + ident if kind == 'arxiv' else 'https://doi.org/' + ident


def preprint_year(pid):
    # the year a preprint first appeared, as far as its id tells: arXiv ids start with YYMM,
    # newer bioRxiv DOIs with the date (OpenAlex gives the year of the published version, if merged)
    kind, ident = pid.split(':', 1)
    if kind == 'arxiv':
        yy = int(ident.split('/')[-1][:2])
        return str(1900 + yy if yy > 90 else 2000 + yy)
    match = re.match(r'10\.1101/(\d{4})\.', ident)
    return match.group(1) if match else None


def preprint_label(pid):
    kind, ident = pid.split(':', 1)
    return 'arXiv:' + ident if kind == 'arxiv' else ident


# ---------------------------------------------------------------------------------------
# reading bibtex
# ---------------------------------------------------------------------------------------

ENTRY_START = re.compile(r'@\s*([a-zA-Z]+)\s*([{(])')
FIELD_NAME = re.compile(r'\s*,?\s*([a-zA-Z][\w.:+-]*)\s*=\s*')
BARE_VALUE = re.compile(r'[^,#{}()"\s]+')
SPECIAL = ('comment', 'string', 'preamble')


def closing_brace(text, pos):
    # index of the brace closing the one at text[pos] (or the end of text)
    depth = 0
    for i in range(pos, len(text)):
        if text[i] == '{':
            depth += 1
        elif text[i] == '}':
            depth -= 1
            if depth == 0:
                return i
    return len(text) - 1


def split_entries(text):
    """
    Find the entries of a bibtex file. Returns a list of dicts with type, key, the position
    of the entry in text (start, end) and the entry text. Text between entries is skipped,
    just like BibTeX does.
    """
    entries = []
    pos = 0
    while True:
        match = ENTRY_START.search(text, pos)
        if not match:
            return entries
        if match.group(2) == '{':
            end = closing_brace(text, match.end() - 1) + 1
        else:  # entries may also be written as @article( ... )
            depth, end = 0, match.end()
            while end < len(text) and not (text[end] == ')' and depth == 0):
                depth += {'{': 1, '}': -1}.get(text[end], 0)
                end += 1
            end = min(end + 1, len(text))
        kind = match.group(1).lower()
        key = None if kind in SPECIAL else text[match.end():end].split(',', 1)[0].strip()
        entries.append({'type': kind, 'key': key, 'start': match.start(), 'end': end,
                        'text': text[match.start():end]})
        pos = end


def read_value(text, pos):
    # reads {...}, "..." or a bare word (and parts joined with #). Returns value and new position.
    parts = []
    while pos < len(text):
        if text[pos] == '{':
            end = closing_brace(text, pos)
            parts.append(text[pos + 1:end])
            pos = end + 1
        elif text[pos] == '"':
            end, depth = pos + 1, 0
            while end < len(text) and not (text[end] == '"' and depth == 0):
                depth += {'{': 1, '}': -1}.get(text[end], 0)
                end += 1
            parts.append(text[pos + 1:end])
            pos = end + 1
        else:
            match = BARE_VALUE.match(text, pos)
            if not match:
                break
            parts.append(match.group(0))
            pos = match.end()
        joined = re.compile(r'\s*#\s*').match(text, pos)
        if not joined:
            break
        pos = joined.end()
    return ' '.join(''.join(parts).split()), pos


def parse_fields(entry_text):
    # the fields of one entry as {name: value}, names in lower case, outer braces/quotes removed
    fields = {}
    match = ENTRY_START.match(entry_text)
    pos = entry_text.find(',', match.end() if match else 0)
    if pos < 0:
        return fields
    while True:
        match = FIELD_NAME.match(entry_text, pos)
        if not match:
            return fields
        fields[match.group(1).lower()], pos = read_value(entry_text, match.end())


def split_authors(value):
    # 'A and B and C' -> ['A', 'B', 'C']
    return [name.strip() for name in re.split(r'\s+and\s+', value or '') if name.strip()]


def plain(text):
    # lower case ascii words without latex, braces or punctuation, for comparing and searching
    text = re.sub(r'\\.', '', text or '')                       # {\"u} -> {u}
    text = unicodedata.normalize('NFKD', text).encode('ascii', 'ignore').decode()
    return ' '.join(re.sub(r'[^a-z0-9]+', ' ', text.lower()).split())


def last_name_words(author):
    # 'Montavon, Grégoire' -> ['montavon'], 'Laurens van der Maaten' -> ['van', 'der', 'maaten']
    author = author.replace('{', '').replace('}', '')
    if ',' in author:
        words = author.split(',')[0].split()
    else:
        words = author.split()
        particles = [i for i, word in enumerate(words[1:-1], 1) if word[:1].islower()]
        words = words[particles[0]:] if particles else words[-1:]
    return [plain(word).replace(' ', '') for word in words if plain(word)]


# ---------------------------------------------------------------------------------------
# writing bibtex
# ---------------------------------------------------------------------------------------

FIELD_ORDER = ['author', 'title', 'editor', 'journal', 'booktitle', 'series', 'volume', 'number', 'pages',
               'year', 'month', 'publisher', 'address', 'howpublished', 'doi', 'url',
               'eprinttype', 'archiveprefix', 'eprint', 'primaryclass', 'note']
STOPWORDS = {'a', 'an', 'and', 'as', 'at', 'by', 'for', 'from', 'in', 'into', 'of', 'on', 'or',
             'the', 'to', 'via', 'with', 'without'}
PREPRINT_STYLES = {'scholar': 'Google Scholar', 'arxiv': 'arXiv (@misc)', 'dblp': 'DBLP (CoRR)'}


def render_entry(entry_type, key, fields):
    # one bibtex entry; fields is {name: value} and 'author' may be a list. Empty fields are left out.
    rank = lambda name: FIELD_ORDER.index(name.lower()) if name.lower() in FIELD_ORDER else len(FIELD_ORDER)
    names = sorted((name for name in fields if fields[name]), key=rank)
    width = max([len(name) for name in names] + [0])
    lines = ['@{}{{{},'.format(entry_type, key)]
    for name in names:
        value = fields[name]
        if isinstance(value, list):
            value = ' and '.join(value)
        lines.append('  {} = {{{}}},'.format(name.ljust(width), value))
    lines.append('}')
    return '\n'.join(lines)


def scholar_key(authors, year, title):
    # Google Scholar style: last name of the first author, year, first proper word of the title
    name = last_name_words(authors[0]) if authors else []
    words = [plain(word).replace(' ', '') for word in re.split(r'[\s\-:/]+', title or '')]
    words = [word for word in words if word]
    word = next((w for w in words if w not in STOPWORDS), words[0] if words else '')
    return '{}{}{}'.format(name[0] if name else 'anonymous', str(year or '')[:4], word)


def preprint_dblp_key(pid):
    # DBLP's key for an arXiv preprint, if we can tell it without asking DBLP
    kind, ident = pid.split(':', 1)
    if kind == 'arxiv' and '/' not in ident:
        return 'DBLP:journals/corr/abs-' + ident.replace('.', '-')
    return None


def new_key(record, choice, key_style='scholar'):
    # a key for an entry that is not in the user's bibtex file yet
    if choice == 'preprint':
        pre = record['preprint']
        if key_style == 'dblp' and preprint_dblp_key(pre['id']):
            return preprint_dblp_key(pre['id'])
        return scholar_key(pre.get('authors'), pre.get('year'), pre.get('title'))
    pub = record['publications'][choice]
    if key_style == 'dblp' and pub.get('dblp'):
        return 'DBLP:' + pub['dblp']
    bib = pub['bibtex']
    return scholar_key(bib.get('author'), bib.get('year'), bib.get('title'))


def render_publication(pub, key):
    fields = dict(pub['bibtex'])
    entry_type = fields.pop('type')
    return render_entry(entry_type, key, fields)


def render_preprint(pre, key, style='scholar'):
    # a preprint written in one of the PREPRINT_STYLES. pre is the 'preprint' part of a record.
    kind, ident = pre['id'].split(':', 1)
    common = {'author': pre.get('authors'), 'title': pre.get('title'), 'year': pre.get('year')}
    if kind == 'arxiv':
        url = 'https://arxiv.org/abs/' + ident
        if style == 'scholar':
            return render_entry('article', key, dict(common, journal='arXiv preprint arXiv:' + ident))
        if style == 'arxiv':
            return render_entry('misc', key, dict(common, eprint=ident, archivePrefix='arXiv',
                                                  primaryClass=pre.get('primary_class'), url=url))
        return render_entry('article', key, dict(common, journal='CoRR', volume='abs/' + ident,
                                                 eprinttype='arXiv', eprint=ident, url=url))
    server = pre.get('server', 'bioRxiv')
    url = 'https://doi.org/' + ident
    if style == 'scholar':
        return render_entry('article', key, dict(common, journal=server, doi=ident))
    if style == 'arxiv':
        return render_entry('misc', key, dict(common, howpublished=server, doi=ident, url=url))
    return render_entry('article', key, dict(common, journal=server, doi=ident, url=url))


def preprint_from_fields(pid, fields):
    # what a user's bibtex entry tells about a preprint, in the format of a record's 'preprint' part
    fields = fields or {}
    pre = {'id': pid, 'title': fields.get('title', ''), 'authors': split_authors(fields.get('author')),
           'year': (fields.get('year') or fields.get('date') or '')[:4]}
    category = fields.get('primaryclass') or re.search(r'\[([\w.-]+)\]', fields.get('eprint', ''))
    if category:
        pre['primary_class'] = category if isinstance(category, str) else category.group(1)
    return pre


# ---------------------------------------------------------------------------------------
# going through the user's input
# ---------------------------------------------------------------------------------------

NOT_PREPRINT_TYPES = ('book', 'inbook', 'incollection', 'proceedings', 'phdthesis', 'mastersthesis')


def looks_like_preprint(fields):
    # an entry with a venue other than a preprint server is a publication already
    venue = fields.get('journal') or fields.get('journaltitle') or fields.get('booktitle') or ''
    return not venue or bool(re.search(r'arxiv|corr|rxiv|preprint', venue, re.IGNORECASE))


def scan(text):
    """
    Find the preprints in the user's input: a bibtex file or a list of ids / links.
    Returns a list of items {'id', 'key', 'entry', 'fields'}. key, entry and fields are
    None for bare ids, which are not part of a bibtex file.
    """
    items = []
    entries = split_entries(text)
    for entry in entries:
        if entry['key'] is None or entry['type'] in NOT_PREPRINT_TYPES:
            continue
        fields = parse_fields(entry['text'])
        pid = find_preprint_id(entry['text'])
        if pid and looks_like_preprint(fields):
            items.append({'id': pid, 'key': entry['key'], 'entry': entry, 'fields': fields})
    if entries:
        return items
    for token in re.split(r'[\s,;]+', text):
        pid = find_preprint_id(token) if token else None
        if pid and pid not in [item['id'] for item in items]:
            items.append({'id': pid, 'key': None, 'entry': None, 'fields': None})
    return items


def preferred(record):
    # index of the preferred publication of a record, or 'preprint' if it has none
    publications = (record or {}).get('publications') or []
    for i, pub in enumerate(publications):
        if pub.get('preferred'):
            return i
    return 0 if publications else 'preprint'


def replacement(item, record, choice, preprint_style=None, key_style='scholar'):
    """
    The bibtex entry for one item: the chosen publication, or the preprint (restyled if
    preprint_style is set). Entries from the user's file keep their key. Returns None if
    the user's entry stays as it is or if there is nothing to write.
    """
    key = item['key']
    if choice != 'preprint':
        return render_publication(record['publications'][choice], key or new_key(record, choice, key_style))
    if item['entry'] is not None and not preprint_style:
        return None
    if record is None and item['fields'] is None:
        return None
    pre = record['preprint'] if record else preprint_from_fields(item['id'], item['fields'])
    return render_preprint(pre, key or new_key({'preprint': pre}, 'preprint', key_style), preprint_style or 'scholar')


def describe(record, choice):
    # a short note on what replaced an entry, for the comment above it
    if choice == 'preprint':
        return 'preprint entry rewritten in a uniform style'
    bib = record['publications'][choice]['bibtex']
    venue = bib.get('journal') or bib.get('booktitle') or bib.get('howpublished') or 'published version'
    return 'replaced by the published version: {} {}'.format(venue, bib.get('year', '')).strip()


def comment_out(entry_text, note):
    # BibTeX has no comment character: it reads an entry wherever it meets an '@'.
    # So besides adding '%' we remove the '@'s, and BibTeX skips the old entry as plain text.
    lines = entry_text.replace('@', '', 1).replace('@', '(at)').split('\n')
    return '% publishedyet: {}\n'.format(note) + '\n'.join('% ' + line for line in lines)


def fix_bib(text, changes):
    # apply changes [(entry, new entry text, note), ...] to a bibtex file, keeping old entries as comments
    for entry, new_text, note in sorted(changes, key=lambda change: change[0]['start'], reverse=True):
        old = text[entry['start']:entry['end']]
        text = text[:entry['start']] + comment_out(old, note) + '\n' + new_text + text[entry['end']:]
    return text


def diff_lines(old, new):
    return list(difflib.unified_diff(old.split('\n'), new.split('\n'), 'original', 'fixed', n=2, lineterm=''))


# ---------------------------------------------------------------------------------------
# searching online for published versions (OpenAlex, Crossref, bioRxiv)
# ---------------------------------------------------------------------------------------

CROSSREF_TYPES = {  # crossref type: (bibtex type, venue field, venue type)
    'journal-article': ('article', 'journal', 'journal'),
    'proceedings-article': ('inproceedings', 'booktitle', 'conference'),
    'book-chapter': ('incollection', 'booktitle', 'book'),
}
OPENALEX_TYPES = {  # openalex source type: (bibtex type, venue field, venue type)
    'journal': ('article', 'journal', 'journal'),
    'conference': ('inproceedings', 'booktitle', 'conference'),
}


def strip_tags(text):
    return ' '.join(re.sub(r'<[^>]+>', '', html.unescape(text or '')).split())


def doi_of(url):
    # 'https://doi.org/10.1038/xyz' -> '10.1038/xyz'
    match = re.search(r'(10\.\d{4,}/\S+)', url or '')
    return match.group(1) if match else None


def work_doi(work):
    # the DOI of an OpenAlex work; sometimes it is only given as the landing page of its venue
    return doi_of(work.get('doi')) or doi_of((work.get('primary_location') or {}).get('landing_page_url'))


def is_preprint_doi(doi):
    return doi.lower().startswith(('10.48550/', '10.1101/'))


def from_crossref(item):
    # a Crossref work as a publication of our database format
    bibtype, venue_field, venue_type = CROSSREF_TYPES[item['type']]
    bib = {'type': bibtype,
           'author': [', '.join(n for n in [a.get('family'), a.get('given')] if n) or a.get('name', '')
                      for a in item.get('author', [])],
           'title': strip_tags((item.get('title') or [''])[0]),
           venue_field: strip_tags((item.get('container-title') or [''])[0])}
    for date in ('published-print', 'issued', 'published-online'):
        year = ((item.get(date) or {}).get('date-parts') or [[None]])[0][0]
        if year:
            bib['year'] = str(year)
            break
    bib.update({'volume': item.get('volume'), 'number': item.get('issue'),
                'pages': (item.get('page') or '').replace('-', '--'), 'publisher': item.get('publisher'),
                'doi': item['DOI'], 'url': 'https://doi.org/' + item['DOI']})
    bib = dict((name, value) for name, value in bib.items() if value)
    return {'relation': 'same', 'venue_type': venue_type, 'archival': True, 'source': 'crossref', 'bibtex': bib}


def from_openalex(work):
    # an OpenAlex work as a publication of our database format (for papers without a Crossref DOI)
    source = (work.get('primary_location') or {}).get('source') or {}
    bibtype, venue_field, venue_type = OPENALEX_TYPES.get(source.get('type'), ('misc', 'howpublished', 'other'))
    biblio = work.get('biblio') or {}
    doi = work_doi(work)
    bib = {'type': bibtype,
           'author': [a['author']['display_name'] for a in work.get('authorships', [])],
           'title': strip_tags(work.get('title')), venue_field: source.get('display_name'),
           'year': str(work.get('publication_year') or ''), 'volume': biblio.get('volume'),
           'number': biblio.get('issue'),
           'pages': '--'.join(p for p in [biblio.get('first_page'), biblio.get('last_page')] if p),
           'publisher': source.get('host_organization_name'), 'doi': doi,
           'url': 'https://doi.org/' + doi if doi else (work.get('primary_location') or {}).get('landing_page_url')}
    bib = dict((name, value) for name, value in bib.items() if value)
    return {'relation': 'same', 'venue_type': venue_type, 'archival': True, 'source': 'openalex', 'bibtex': bib}


def same_paper(pre, title, authors):
    # does a search hit look like the preprint? Same title, and the first author is among its authors.
    if difflib.SequenceMatcher(None, plain(pre.get('title')), plain(title)).ratio() < 0.9:
        return False
    if not pre.get('authors') or not authors:
        return True
    first = ''.join(last_name_words(pre['authors'][0]))
    return first in [''.join(last_name_words(name)) for name in authors]


async def discover(pid, get_json, known=None):
    """
    Search online for published versions of a preprint: OpenAlex (which often merges a
    preprint with its published version), title searches in OpenAlex and Crossref, and
    the bioRxiv API, which links bioRxiv preprints to their journal articles.
    known: what we already know about the preprint, e.g. from the user's bibtex entry.
    Returns a record in database format, marked as unconfirmed.
    """
    pre = dict(known or {'id': pid})
    dois, works = [], []      # found publications: DOIs (details come from Crossref), OpenAlex works

    work = await get_json('{}/works/doi:{}'.format(OPENALEX, preprint_doi(pid)))
    if work:
        pre['title'] = pre.get('title') or strip_tags(work.get('title'))
        pre['authors'] = pre.get('authors') or [a['author']['display_name'] for a in work.get('authorships', [])]
        pre['year'] = pre.get('year') or preprint_year(pid) or str(work.get('publication_year') or '')
        source = (work.get('primary_location') or {}).get('source') or {}
        if work.get('type') != 'preprint' and source.get('type') in OPENALEX_TYPES:
            doi = work_doi(work)
            if doi and not is_preprint_doi(doi):
                dois.append(doi)
            elif not doi:
                works.append(work)

    if pid.startswith('doi:10.1101/'):
        for server in ('biorxiv', 'medrxiv'):
            data = await get_json('{}/details/{}/{}'.format(BIORXIV, server, pid[4:]))
            versions = (data or {}).get('collection') or []
            if versions:
                latest = versions[-1]
                pre['server'] = 'bioRxiv' if server == 'biorxiv' else 'medRxiv'
                if not (known or {}).get('title'):   # bioRxiv's own title, not the one of the published version
                    pre['title'] = latest.get('title') or pre.get('title')
                pre['authors'] = pre.get('authors') or [a.strip() for a in latest.get('authors', '').split(';') if a.strip()]
                pre['year'] = preprint_year(pid) or versions[0].get('date', '')[:4]
                if doi_of(latest.get('published')):
                    dois.append(doi_of(latest['published']))
                break

    items = {}   # doi -> crossref work
    if pre.get('title'):
        query = quote(plain(pre['title']))
        found = await get_json('{}/works?filter=title.search:{}&per-page=10'.format(OPENALEX, query))
        for hit in (found or {}).get('results', []):
            source = (hit.get('primary_location') or {}).get('source') or {}
            authors = [a['author']['display_name'] for a in hit.get('authorships', [])]
            if hit.get('type') != 'preprint' and source.get('type') in OPENALEX_TYPES and same_paper(pre, hit.get('title'), authors):
                doi = work_doi(hit)
                if doi and not is_preprint_doi(doi):
                    dois.append(doi)
                elif not doi:
                    works.append(hit)
        found = await get_json('{}/works?rows=10&query.bibliographic={}'.format(CROSSREF, query))
        for hit in ((found or {}).get('message') or {}).get('items', []):
            authors = [', '.join(n for n in [a.get('family'), a.get('given')] if n) for a in hit.get('author', [])]
            if hit.get('type') in CROSSREF_TYPES and same_paper(pre, (hit.get('title') or [''])[0], authors):
                items[hit['DOI'].lower()] = hit

    for doi in dois:
        if doi.lower() not in items:
            found = await get_json('{}/works/{}'.format(CROSSREF, quote(doi)))
            hit = (found or {}).get('message')
            if hit and hit.get('type') in CROSSREF_TYPES:
                items[doi.lower()] = hit

    publications = [from_crossref(hit) for hit in items.values()]
    seen = [(plain(p['bibtex'].get('title')), p['bibtex'].get('year')) for p in publications]
    for work in works:
        pub = from_openalex(work)
        if (plain(pub['bibtex'].get('title')), pub['bibtex'].get('year')) not in seen:
            publications.append(pub)
            seen.append((plain(pub['bibtex'].get('title')), pub['bibtex'].get('year')))
    return {'preprint': pre, 'publications': publications, 'unconfirmed': True}


# ---------------------------------------------------------------------------------------
# checking database records
# ---------------------------------------------------------------------------------------

BIBTEX_TYPES = ('article', 'inproceedings', 'incollection', 'book', 'misc', 'techreport', 'phdthesis')
VENUE_FIELD = {'article': 'journal', 'inproceedings': 'booktitle', 'incollection': 'booktitle'}
VENUE_TYPES = ('journal', 'conference', 'workshop', 'book', 'other')
RELATIONS = ('same', 'extended')


def check_record(record):
    # returns a list of problems with a database record (empty if all is fine)
    problems = []
    pre = record.get('preprint') or {}
    pid = pre.get('id') or ''
    if ':' not in pid or find_preprint_id(pid.split(':', 1)[1]) != pid:
        problems.append('preprint id {!r} is not a valid id (like arxiv:2402.05602 or doi:10.1101/...)'.format(pid))
    for name in ('title', 'authors', 'year'):
        if not pre.get(name):
            problems.append('preprint: {} is missing'.format(name))
    publications = record.get('publications')
    if not isinstance(publications, list):
        return problems + ['publications must be a list']
    for i, pub in enumerate(publications, 1):
        bib = pub.get('bibtex') or {}
        if bib.get('type') not in BIBTEX_TYPES:
            problems.append('publication {}: bibtex type {!r} is not one of {}'.format(i, bib.get('type'), BIBTEX_TYPES))
        for name in ['author', 'title', 'year'] + [VENUE_FIELD.get(bib.get('type'))]:
            if name and not bib.get(name):
                problems.append('publication {}: {} is missing'.format(i, name))
        if pub.get('relation') not in RELATIONS:
            problems.append('publication {}: relation must be one of {}'.format(i, RELATIONS))
        if pub.get('venue_type') not in VENUE_TYPES:
            problems.append('publication {}: venue_type must be one of {}'.format(i, VENUE_TYPES))
        if not isinstance(pub.get('archival'), bool):
            problems.append('publication {}: archival must be true or false'.format(i))
    if sum(1 for pub in publications if pub.get('preferred')) > 1:
        problems.append('more than one publication is marked as preferred')
    if record.get('unconfirmed'):
        problems.append('record is marked as unconfirmed')
    return problems
