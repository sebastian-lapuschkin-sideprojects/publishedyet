"""
The web page. Runs inside the browser (Pyodide); index.html loads it and calls start().

One card per preprint found in the input. Its tiles are the options: the preprint itself
and each known publication. Clicking a tile chooses what goes into the export; "edit" opens
all fields of the chosen option, and "+ add" adds a publication by hand.
"""

import re
import json
import asyncio
from html import escape
from urllib.parse import urlencode

import js
from pyodide.ffi import create_proxy, to_js
from pyodide.http import pyfetch

import publishedyet as py

REPO = 'https://github.com/sebastian-lapuschkin-sideprojects/publishedyet'
DB = 'db/'
STATUS = {  # status of a preprint: (label, css class); edits add " / edited"
    'looking': ('looking up', 'busy'), 'searching': ('searching', 'busy'), 'db': ('in database', 'db'),
    'online': ('found online', 'online'), 'none': ('nothing found', 'none'), 'unknown': ('waiting', 'busy'),
}
LINK_ICON = ('<svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/>'
             '<path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>')   # the "link" icon of Feather (MIT)
TIMEOUT = 30   # seconds a source may take to answer
URL_LIMIT = 8000   # GitHub refuses longer links (proposals of papers with very many authors)
SOURCES = {'sparql.dblp.org': 'DBLP', 'api.openalex.org': 'OpenAlex', 'api.crossref.org': 'Crossref',
           'api.biorxiv.org': 'bioRxiv', 'proceedings.mlr.press': 'PMLR'}
FIELDS = ['author', 'title', 'venue', 'year', 'volume', 'number', 'pages', 'publisher', 'series', 'editor', 'month', 'doi', 'url']
WIDE = ('author', 'title', 'venue', 'editor', 'url')

# what the page knows. Per preprint id: status, records, choices, originals (database records as
# loaded), edited (ids changed by the user), problems (of the online search). Per card number:
# editing (panel open), left_out (not exported).
state = {'text': '', 'items': [], 'records': {}, 'status': {}, 'choices': {}, 'originals': {}, 'edited': set(),
         'problems': {}, 'editing': set(), 'left_out': set()}


def el(name):
    return js.document.getElementById(name)


async def fetch(url, kind, problems=None):
    # kind 'json' or 'text'. Returns None if there is nothing. Rate limits, blocks, time-outs and
    # sources that do not answer are noted in problems, so the card can say why it found nothing.
    # (404 is no problem: the database or a source simply does not have this id.)
    try:
        response = await asyncio.wait_for(pyfetch(url), TIMEOUT)
        if response.ok:
            return await asyncio.wait_for(response.json() if kind == 'json' else response.string(), TIMEOUT)
        if response.status == 404:
            return None
        problem = {429: 'rate limited', 403: 'blocked'}.get(response.status, 'error {}'.format(response.status))
    except asyncio.TimeoutError:
        problem = 'timed out'
    except Exception:
        problem = 'no answer'     # also how refusals without CORS headers and bot walls look from here
    js.console.warn('{}: {}'.format(problem, url))
    if problems is not None:
        problems.append('{}: {}'.format(SOURCES.get(url.split('/')[2], url.split('/')[2]), problem))
    return None


async def get_text(url):
    return await fetch(url, 'text')


async def get_json(url):
    return await fetch(url, 'json')


def venue_field(bibtype):
    return py.VENUE_FIELD.get(bibtype, 'howpublished')


def preprint_of(item, record):
    return record['preprint'] if record else py.preprint_from_fields(item['id'], item['fields'])


def editable(item):
    # the record of an item, created from what we know if there is none yet
    pid = item['id']
    if state['records'].get(pid) is None:
        state['records'][pid] = {'preprint': preprint_of(item, None), 'publications': []}
    return state['records'][pid]


# ----- running ---------------------------------------------------------------------------

async def run(event=None):
    text = el('input').value
    state.update(text=text, items=py.scan(text), records={}, status={}, choices={}, originals={}, edited=set(),
                 problems={}, editing=set(), left_out=set())
    for item in state['items']:
        if item['id']:
            state['status'][item['id']] = 'looking'
    show()
    for pid in list(state['status']):
        record = await get_json(DB + py.db_path(pid))
        state['records'][pid] = record
        state['originals'][pid] = json.loads(json.dumps(record))
        state['status'][pid] = 'db' if record else 'unknown'
        state['choices'][pid] = py.preferred(record)
    show()
    await search([pid for pid, status in state['status'].items() if status == 'unknown'])


async def search(pids):
    # search online for preprints not in the database, three at a time
    queue = list(pids)

    async def worker():
        while queue:
            pid = queue.pop(0)
            state['status'][pid] = 'searching'
            problems = state['problems'][pid] = []
            show()
            item = next(item for item in state['items'] if item['id'] == pid)
            known = py.preprint_from_fields(pid, item['fields']) if item['fields'] else None

            def fetcher(kind):   # as get_json and get_text, but problems show on the card at once
                async def get(url):
                    count = len(problems)
                    result = await fetch(url, kind, problems)
                    if len(problems) > count:
                        refresh(pid)
                    return result
                return get

            try:
                record = await py.discover(pid, fetcher('json'), known, fetcher('text'))
            except Exception as error:   # a bug or an unexpected answer: say so instead of searching forever
                js.console.error('search for {} failed: {}'.format(pid, error))
                problems.append('search: failed ({})'.format(error))
                record = {'preprint': known or {'id': pid}, 'publications': []}
            state['records'][pid] = record
            state['status'][pid] = 'online' if record['publications'] else 'none'
            state['choices'][pid] = py.preferred(record)
            show()

    await asyncio.gather(*[worker() for _ in range(3)])


# ----- showing ---------------------------------------------------------------------------

def show():
    if not state['items']:
        el('results').innerHTML = '<p class="hint">No arXiv, bioRxiv or OpenReview preprints found in the input.</p>'
        el('export').hidden = True
        return
    counts = '{} preprint(s), {} in the database'.format(
        len(state['status']), sum(1 for status in state['status'].values() if status == 'db'))
    unreadable = sum(1 for item in state['items'] if item['id'] is None)
    if unreadable:
        counts += ', {} not readable'.format(unreadable)
    button = '<button class="small" data-action="share" title="copy a link that opens this page with the ticked preprints">{} share link</button>'.format(LINK_ICON)
    everything = ('<label class="pick" title="tick or untick all entries for the export and share links">'
                  'export/share all: <input type="checkbox" id="pick-all" data-action="pick-all"{}></label>').format(' checked' if all_picked() else '')
    el('results').innerHTML = '<div class="bar-top"><span class="cap">{}</span>{}{}</div>'.format(counts, button, everything) + \
        ''.join(card(i) for i in range(len(state['items'])))
    show_export()


def card(i):
    if state['items'][i]['id'] is None:
        return unreadable_card(i)
    edit = '<div class="edit" id="edit-{}">{}</div>'.format(i, edit_panel(i)) if i in state['editing'] else ''
    classes = ' alt' if i % 2 else ''
    classes += ' left-out' if i in state['left_out'] else ''
    return '<div class="card{4}" id="card-{0}"><div class="top" id="top-{0}">{1}</div><div class="opts" id="opts-{0}">{2}</div>{3}</div>'.format(
        i, card_top(i), tiles(i), edit, classes)


def unreadable_card(i):
    # something in the input that is no id we know; shown so it does not get lost silently
    return ('<div class="card{0}"><div class="top"><div class="who"><span class="id" title="{1}">{1}</span></div>'
            '<div class="title warn">could not read this as an arXiv, bioRxiv or OpenReview id</div>'
            '<span class="pill warn">not an id</span><span class="actions"></span></div></div>').format(
        ' alt' if i % 2 else '', escape(state['items'][i]['text']))


def refresh(pid):
    # redraw headers and tiles of the cards of one preprint (not the edit panels, which may have the focus)
    for i, item in enumerate(state['items']):
        if item['id'] == pid:
            el('top-{}'.format(i)).innerHTML = card_top(i)
            el('opts-{}'.format(i)).innerHTML = tiles(i)
    show_export()


def card_top(i):
    item = state['items'][i]
    pid = item['id']
    record = state['records'].get(pid)
    status = state['status'][pid]
    choice = state['choices'].get(pid, 'preprint')
    title = preprint_of(item, record).get('title', '').replace('{', '').replace('}', '')
    who = '<a class="id" href="{0}" target="_blank" title="{1}">{1}</a>'.format(py.preprint_url(pid), escape(py.preprint_label(pid)))
    if item['key']:
        who += '<div class="key" title="key in your file">{}</div>'.format(escape(item['key']))
    actions = []
    if status not in ('looking', 'searching', 'unknown'):
        actions.append('<button class="small" data-action="edit" data-i="{}">{}</button>'.format(i, 'close' if i in state['editing'] else 'edit'))
    if status == 'online' or pid in state['edited'] or (status == 'db' and choice != py.preferred(record)):
        url = proposal_url(proposal(pid, record, choice))
        if len(url) <= URL_LIMIT:
            actions.append('<a class="act" href="{}" target="_blank" title="send this entry to the database (opens a GitHub issue)">propose</a>'
                           .format(escape(url)))
        else:   # too long for a link to GitHub: copy and paste instead
            actions.append('<button class="small" data-action="propose-long" data-i="{}" title="send this entry to the database '
                           '(too long for a link: copy it and paste it into a GitHub issue)">propose</button>'.format(i))
    actions.append('<label class="pick" title="include this entry in the export and in share links">export/share: <input type="checkbox" data-action="pick" data-i="{}"{}></label>'
                   .format(i, '' if i in state['left_out'] else ' checked'))
    label, kind = STATUS[status]
    if pid in state['edited']:
        label, kind = (label + ' / edited' if status in ('db', 'online') else 'edited'), 'online'
    # sources that were rate limited, blocked or did not answer during the online search
    problems = state['problems'].get(pid) if pid not in state['edited'] else None
    tip = 'problems during the search: ' + '; '.join(problems) if problems else ''
    if problems and status == 'searching':
        label, kind = 'searching · ' + problems[-1].split(': ', 1)[1], 'warn'
    elif problems and status == 'none':
        label, kind = 'search incomplete', 'warn'
        tip += '. Nothing found, but the search was not complete; try again later.'
        actions.insert(0, '<button class="small" data-action="retry" data-i="{}" title="search online again">retry</button>'.format(i))
    title_class = 'title'
    if status == 'none' and not title and pid not in state['edited'] and not problems:   # no source knows this id at all
        label, kind, title_class = 'unknown id', 'warn', 'title warn'
        title = 'no source knows this preprint; please check the id'
    return '<div class="who">{}</div><div class="{}" title="{}">{}</div><span class="pill {}" title="{}">{}</span><span class="actions">{}</span>'.format(
        who, title_class, escape(title), escape(title), kind, escape(tip), label, ''.join(actions))


def tiles(i):
    item = state['items'][i]
    pid = item['id']
    record = state['records'].get(pid)
    choice = state['choices'].get(pid, 'preprint')
    pre = preprint_of(item, record)
    server = {'arxiv': 'arXiv', 'openreview': 'OpenReview'}.get(pid.split(':')[0], pre.get('server', 'bioRxiv'))
    out = [tile(i, 'preprint', server + ' preprint', ['preprint', pre.get('year')], py.preprint_url(pid), choice == 'preprint',
                '{}\n{}'.format(' and '.join(pre.get('authors') or []), pre.get('title', '')))]
    for n, pub in enumerate((record or {}).get('publications') or []):
        bib = pub['bibtex']
        venue = bib.get(venue_field(bib['type'])) or bib.get('title') or 'new publication'
        link = bib.get('url') or ('https://doi.org/' + bib['doi'] if bib.get('doi') else '')
        short = pub.get('venue_short')   # 'ICML 2024' reads faster than the full proceedings title
        meta = [pub.get('venue_type'), None if short else bib.get('year'), None if pub.get('archival', True) else 'non-archival',
                'extended' if pub.get('relation') == 'extended' else None]
        preview = '{}\n{}\n{} {}'.format(' and '.join(bib.get('author', [])), bib.get('title', ''), venue, bib.get('year', ''))
        missing = py.missing_fields(pub)
        if missing:
            preview += '\n\nmissing: ' + ', '.join(missing)
        name = '{} {}'.format(short, bib.get('year', '')).strip() if short else venue
        out.append(tile(i, n, name, meta, link, choice == n, preview, incomplete=bool(missing)))
    if state['status'][pid] not in ('looking', 'searching', 'unknown'):
        out.append('<div class="tile ghost" data-action="add" data-i="{}">+ add publication</div>'.format(i))
    return ''.join(out)


def tile(i, choice, name, meta, link, chosen, preview, incomplete=False):
    go = ' <a class="go" href="{}" target="_blank" title="open">↗</a>'.format(escape(link)) if link else ''
    flag = ' · <span class="incomplete">incomplete</span>' if incomplete else ''
    return '<div class="tile{}" data-action="choose" data-i="{}" data-choice="{}" title="{}"><div class="name">{}{}</div><div class="meta">{}{}</div></div>'.format(
        ' chosen' if chosen else '', i, choice, escape(preview), escape(name), go, escape(' · '.join(m for m in meta if m)), flag)


# ----- the edit panel --------------------------------------------------------------------

def edit_panel(i):
    item = state['items'][i]
    pid = item['id']
    record = state['records'].get(pid)
    choice = state['choices'].get(pid, 'preprint')
    if choice == 'preprint':
        pre = preprint_of(item, record)
        out = [field(i, 'pre', 'title', pre.get('title', '')),
               field(i, 'pre', 'authors', '\n'.join(pre.get('authors') or []), rows=3),
               field(i, 'pre', 'year', pre.get('year', ''))]
        if pid.startswith('arxiv:'):
            out.append(field(i, 'pre', 'primary_class', pre.get('primary_class', '')))
        return ''.join(out) + '<p class="hint wide">These fields describe the preprint itself.</p>'
    pub = record['publications'][choice]
    bib = pub['bibtex']
    empty = not (bib.get('year') or bib.get(venue_field(bib['type'])))   # just added: open the paste box
    paste = ('<details class="wide paste"{0}><summary>paste a BibTeX entry to fill in the fields below</summary>'
             '<textarea id="paste-{1}" rows="6" spellcheck="false" placeholder="@inproceedings{{key, author = {{...}}, title = {{...}}, ...}}"></textarea>'
             '<div class="controls"><button class="small" data-action="from-bibtex" data-i="{1}">fill in</button> '
             '<span class="hint" id="paste-note-{1}"></span></div></details>').format(' open' if empty else '', i)
    out = [paste,
           select(i, 'bib', 'type', bib['type'], py.BIBTEX_TYPES),
           select(i, 'meta', 'venue_type', pub.get('venue_type'), py.VENUE_TYPES),
           select(i, 'meta', 'relation', pub.get('relation'), py.RELATIONS),
           select(i, 'meta', 'archival', 'yes' if pub.get('archival', True) else 'no', ['yes', 'no']),
           field(i, 'meta', 'venue_short', pub.get('venue_short', ''), wide=False, note='e.g. ICML, shown on the tile')]
    names = [venue_field(bib['type']) if name == 'venue' else name for name in FIELDS]
    names += [name for name in bib if name not in names and name != 'type']
    for name in names:
        value = bib.get(name, '')
        if name == 'author':
            out.append(field(i, 'bib', name, '\n'.join(value), rows=max(2, min(len(value), 6)), note='one per line'))
        else:
            out.append(field(i, 'bib', name, value, wide=name in WIDE or name == venue_field(bib['type'])))
    missing = py.missing_fields(pub)
    if missing:
        out.append('<p class="problems wide">missing: {}</p>'.format(escape(', '.join(missing))))
    out.append('<div class="wide controls"><button class="small" data-action="remove" data-i="{}">remove this publication</button></div>'.format(i))
    return ''.join(out)


def field(i, target, name, value, rows=0, wide=None, note=''):
    wide = name in WIDE or name == 'authors' if wide is None else wide
    attrs = 'id="f-{0}-{1}" data-i="{0}" data-target="{2}" data-field="{1}" spellcheck="false"'.format(i, name, target)
    if rows:
        control = '<textarea {} rows="{}">{}</textarea>'.format(attrs, rows, escape(value))
    else:
        control = '<input {} value="{}">'.format(attrs, escape(value))
    label = '{}{}'.format(name, ' <span class="note">({})</span>'.format(note) if note else '')
    return '<div class="fld{}"><label class="cap" for="f-{}-{}">{}</label>{}</div>'.format(' wide' if wide else '', i, name, label, control)


def select(i, target, name, value, options):
    choices = ''.join('<option{}>{}</option>'.format(' selected' if option == value else '', option) for option in options)
    return '<div class="fld"><label class="cap">{}</label><select data-i="{}" data-target="{}" data-field="{}">{}</select></div>'.format(
        name.replace('_', ' '), i, target, name, choices)


def on_results_change(event):
    target = event.target
    if target.getAttribute('data-field') is None:
        return
    item = state['items'][int(target.getAttribute('data-i'))]
    pid = item['id']
    record = editable(item)
    choice = state['choices'].get(pid, 'preprint')
    name = target.getAttribute('data-field')
    where = target.getAttribute('data-target')
    value = target.value.strip()
    if name == 'archival':
        value = value == 'yes'   # stored as true / false
    if name in ('author', 'authors'):
        value = [n.strip() for n in re.split(r'\n|\s+and\s+', value) if n.strip()]
    if where == 'pre':
        place = record['preprint']
    elif where == 'meta':
        place = record['publications'][choice]
    else:
        place = record['publications'][choice]['bibtex']
    if name == 'type':   # the venue moves along: journal <-> booktitle
        old, new = venue_field(place['type']), venue_field(value)
        if old != new and old in place:
            place[new] = place.pop(old)
    if value or value is False:
        place[name] = value
    else:
        place.pop(name, None)
    state['edited'].add(pid)
    refresh(pid)
    if name == 'type':
        for i, other in enumerate(state['items']):
            if other['id'] == pid and i in state['editing']:
                el('edit-{}'.format(i)).innerHTML = edit_panel(i)


# ----- export ----------------------------------------------------------------------------

def show_export():
    preprint_style = el('preprint-style').value or None
    key_style = el('key-style').value
    entries, changes = [], []
    for i, item in enumerate(state['items']):
        pid = item['id']
        if pid is None or state['status'][pid] in ('looking', 'searching', 'unknown') or i in state['left_out']:
            continue
        record = state['records'].get(pid)
        choice = state['choices'].get(pid, 'preprint')
        new = py.replacement(item, record, choice, preprint_style, key_style)
        if new:
            entries.append(new)
            if item['entry']:
                changes.append((item['entry'], new, py.describe(record, choice)))
    el('export').hidden = False
    el('entries').value = '\n\n'.join(entries)
    from_file = any(item['entry'] for item in state['items'])
    el('fixed-box').hidden = not from_file
    if from_file:
        fixed = py.fix_bib(state['text'], changes)
        el('fixed').value = fixed
        el('diff').innerHTML = diff_html(py.diff_lines(state['text'], fixed)) or '<div>no changes</div>'


def diff_html(lines):
    html = []
    for line in lines:
        kind = 'hunk' if line.startswith(('@@', '---', '+++')) else {'+': 'add', '-': 'del'}.get(line[:1], '')
        html.append('<div class="{}">{}</div>'.format(kind, escape(line) or ' '))
    return ''.join(html)


# ----- proposing entries for the database ----------------------------------------------

def proposal_url(query):
    # a link to the prefilled GitHub issue (longer than URL_LIMIT for papers of large teams: see show_long_proposal)
    return REPO + '/issues/new?' + urlencode(query)


def show_long_proposal(query):
    # too long for any link: the entry goes to the clipboard, and the user pastes it into the GitHub form
    js.navigator.clipboard.writeText(query['entry'])
    form = dict((name, value) for name, value in query.items() if name != 'entry')
    if len(REPO + '/issues/new?' + urlencode(form)) > URL_LIMIT:
        form.pop('notes', None)
    authors = max([len(json.loads(query['entry'])['preprint'].get('authors') or [])] +
                  [len(pub['bibtex'].get('author') or []) for pub in json.loads(query['entry'])['publications']])
    dialog = el('dialog')
    dialog.innerHTML = (
        '<h3>This entry is too long for a link</h3>'
        '<p>GitHub accepts links of up to about {limit:,} characters; this entry needs more (it lists {authors} authors). '
        'So it goes the other way round:</p><ol>'
        '<li>The entry is now in your clipboard (or copy it from the box below).</li>'
        '<li><a href="{url}" target="_blank">Open the proposal form on GitHub</a>; the title is filled in already.</li>'
        '<li>Paste the entry into the field <b>Entry</b> and submit.</li></ol>'
        '<textarea readonly rows="8" spellcheck="false">{entry}</textarea>'
        '<form method="dialog"><button class="small">close</button></form>').format(
        limit=URL_LIMIT, authors=authors, url=escape(REPO + '/issues/new?' + urlencode(form)), entry=escape(query['entry']))
    dialog.showModal()


def proposal(pid, record, choice):
    # the fields of a GitHub issue holding the record as json; a GitHub action turns it into a pull request
    record = json.loads(json.dumps(record))
    record.pop('unconfirmed', None)
    for i, pub in enumerate(record['publications']):
        pub.pop('preferred', None)
        if i == choice:
            pub['preferred'] = True
    query = {'template': 'db-entry.yml', 'title': proposal_title(pid, record), 'entry': json.dumps(record, indent=2, ensure_ascii=False)}
    original = json.loads(json.dumps(state['originals'].get(pid)))
    if original:
        for i, pub in enumerate(original['publications']):   # the database's implicit preferred one, made explicit
            pub.pop('preferred', None)
            if i == py.preferred(state['originals'][pid]):
                pub['preferred'] = True
        query['notes'] = 'Changes to the database entry:\n' + '\n'.join('- ' + line for line in changes(original, record))
    return query


def proposal_title(pid, record):
    # e.g. '[db] arXiv:1512.00172 -> CVPR 2016: Analyzing Classifiers: Fisher Vectors and Deep Neural Networks'
    venues = []
    for pub in record['publications']:
        bib = pub['bibtex']
        venue = pub.get('venue_short') or bib.get(venue_field(bib['type'])) or 'new publication'
        venues.append('{} {}'.format(venue if len(venue) <= 40 else venue[:37] + '...', bib.get('year', '')).strip())
    paper = (record['preprint'].get('title') or '').replace('{', '').replace('}', '')
    if len(paper) > 70:
        paper = paper[:67] + '...'
    return '[db] {} -> {}: {}'.format(py.preprint_label(pid), ', '.join(venues) or 'no publication', paper).rstrip(': ')


def changes(old, new):
    # what differs between two records, one line per field, for the proposal
    show = lambda value: json.dumps(value, ensure_ascii=False)
    lines = ['preprint {}: {} -> {}'.format(name, show(old['preprint'].get(name)), show(new['preprint'].get(name)))
             for name in sorted(set(old['preprint']) | set(new['preprint']))
             if old['preprint'].get(name) != new['preprint'].get(name)]
    flat = lambda pub: dict(list(pub['bibtex'].items()) + [(k, v) for k, v in pub.items() if k != 'bibtex'])
    for n in range(max(len(old['publications']), len(new['publications']))):
        if n >= len(new['publications']):
            lines.append('publication {} removed'.format(n + 1))
        elif n >= len(old['publications']):
            lines.append('publication {} added'.format(n + 1))
        else:
            a, b = flat(old['publications'][n]), flat(new['publications'][n])
            lines += ['publication {} {}: {} -> {}'.format(n + 1, name, show(a.get(name)), show(b.get(name)))
                      for name in sorted(set(a) | set(b)) if a.get(name) != b.get(name)]
    return lines or ['none (only the preferred publication)']


# ----- events ----------------------------------------------------------------------------

async def on_results_click(event):
    if event.target.closest('a') or event.target.closest('.edit input, .edit textarea, .edit select, .edit label'):
        return  # links open, form fields are handled on change
    target = event.target.closest('[data-action]')
    if not target:
        return
    action = target.getAttribute('data-action')
    if action == 'share':
        link = share_link()
        if link:
            js.navigator.clipboard.writeText(link)
        target.innerHTML = LINK_ICON + (' link copied' if link else ' nothing ticked')
        return
    if action == 'pick-all':
        state['left_out'] = set() if target.checked else {i for i, item in enumerate(state['items']) if item['id']}
        show()
        return
    i = int(target.getAttribute('data-i'))
    item = state['items'][i]
    pid = item['id']
    if action == 'choose':
        choice = target.getAttribute('data-choice')
        state['choices'][pid] = choice if choice == 'preprint' else int(choice)
    elif action == 'pick':
        state['left_out'] ^= {i}
        el('card-{}'.format(i)).classList.toggle('left-out', i in state['left_out'])
        el('pick-all').checked = all_picked()
        show_export()
        return
    elif action == 'retry':
        await search([pid])
        return
    elif action == 'propose-long':
        show_long_proposal(proposal(pid, state['records'][pid], state['choices'].get(pid, 'preprint')))
        return
    elif action == 'from-bibtex':   # the fields of the chosen publication from a pasted entry
        parsed = py.from_bibtex(el('paste-{}'.format(i)).value)
        if not parsed:
            el('paste-note-{}'.format(i)).innerText = 'no BibTeX entry found in the text'
            return
        pub = editable(item)['publications'][state['choices'][pid]]
        pub.update(venue_type=parsed['venue_type'], source='bibtex, pasted', bibtex=parsed['bibtex'])
        state['edited'].add(pid)
    elif action == 'edit':
        state['editing'] ^= {i}
    elif action == 'add':
        record = editable(item)
        pre = record['preprint']
        record['publications'].append({
            'relation': 'same', 'venue_type': 'conference', 'archival': True, 'source': 'by hand',
            'bibtex': {'type': 'inproceedings', 'author': list(pre.get('authors') or []), 'title': pre.get('title', ''),
                       'booktitle': '', 'year': ''}})
        state['choices'][pid] = len(record['publications']) - 1
        state['edited'].add(pid)
        state['editing'].add(i)
    elif action == 'remove':
        record = state['records'][pid]
        record['publications'].pop(state['choices'][pid])
        state['choices'][pid] = py.preferred(record)
        state['edited'].add(pid)
    show()


def on_export_click(event):
    name = event.target.getAttribute('data-copy')
    if name:
        js.navigator.clipboard.writeText(el(name).value)
        event.target.textContent = 'copied'
    elif event.target.id == 'download':
        blob = js.Blob.new(to_js([el('fixed').value]), to_js({'type': 'text/plain'}, dict_converter=js.Object.fromEntries))
        link = js.document.createElement('a')
        link.href = js.URL.createObjectURL(blob)
        link.download = 'fixed.bib'
        link.click()


async def on_key(event):
    # cmd+enter (mac) or ctrl+enter in the input field runs, like the button
    if event.key == 'Enter' and (event.metaKey or event.ctrlKey):
        event.preventDefault()
        await run()


async def on_file(event):
    files = el('file').files
    if files.length:
        el('input').value = await files.item(0).text()
        await run()


def all_picked():
    # are all readable entries ticked for export/share?
    return not any(i in state['left_out'] for i, item in enumerate(state['items']) if item['id'])


def share_link():
    # this page with the ticked preprints in the address: .../publishedyet/#2402.05602,1509.06321
    ids = []
    for i, item in enumerate(state['items']):
        if item['id'] and i not in state['left_out'] and item['id'] not in ids:
            ids.append(item['id'])
    if not ids:
        return None
    plain_ids = [pid.split(':', 1)[1] if pid.startswith(('arxiv:', 'doi:')) else pid for pid in ids]
    return js.location.origin + js.location.pathname + '#' + ','.join(plain_ids)


async def start():
    for name, kind, handler in [('run', 'click', run), ('results', 'click', on_results_click),
                                ('results', 'change', on_results_change), ('export', 'click', on_export_click),
                                ('file', 'change', on_file), ('input', 'keydown', on_key),
                                ('preprint-style', 'change', lambda event: show_export()),
                                ('key-style', 'change', lambda event: show_export())]:
        el(name).addEventListener(kind, create_proxy(handler))
    el('run').disabled = False
    el('run').textContent = 'run'
    # opened from a shared link: show those preprints and search the unknown ones right away
    shared = js.decodeURIComponent(js.location.hash[1:])
    if shared:
        el('input').value = '\n'.join(part for part in re.split(r'[,\s]+', shared) if part)
        await run()
