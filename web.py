"""
The web page. Runs inside the browser (Pyodide); index.html loads it and calls start().
The page shows one row per preprint found in the input, with a box per known publication.
Clicking a box chooses what goes into the export.
"""

import json
from html import escape
from urllib.parse import urlencode

import js
from pyodide.ffi import create_proxy, to_js
from pyodide.http import pyfetch

import publishedyet as py

REPO = 'https://github.com/sebastian-lapuschkin-sideprojects/publishedyet'
DB = 'db/'

# what the page knows. status per preprint id: looking, db, unknown, searching, online, none
state = {'text': '', 'items': [], 'records': {}, 'status': {}, 'choices': {}}


def el(name):
    return js.document.getElementById(name)


async def get_json(url):
    try:
        response = await pyfetch(url)
        if response.ok:
            return await response.json()
    except Exception as error:
        js.console.warn('could not load {}: {}'.format(url, error))
    return None


# ----- running ---------------------------------------------------------------------------

async def run(event=None):
    text = el('input').value
    state.update(text=text, items=py.scan(text), records={}, status={}, choices={})
    for item in state['items']:
        state['status'][item['id']] = 'looking'
    show()
    for pid in list(state['status']):
        record = await get_json(DB + py.db_path(pid))
        state['records'][pid] = record
        state['status'][pid] = 'db' if record else 'unknown'
        state['choices'][pid] = py.preferred(record)
    show()
    # a single preprint is searched online right away; for more, the user presses a button
    if len(state['status']) == 1 and state['status'][state['items'][0]['id']] == 'unknown':
        await search([state['items'][0]['id']])


async def search(pids):
    for pid in pids:
        state['status'][pid] = 'searching'
        show()
        item = next(item for item in state['items'] if item['id'] == pid)
        known = py.preprint_from_fields(pid, item['fields']) if item['fields'] else None
        record = await py.discover(pid, get_json, known)
        state['records'][pid] = record
        state['status'][pid] = 'online' if record['publications'] else 'none'
        state['choices'][pid] = py.preferred(record)
        show()


# ----- showing ---------------------------------------------------------------------------

def show():
    if not state['items']:
        el('results').innerHTML = '<p class="hint">No arXiv or bioRxiv preprints found in the input.</p>'
        el('export').hidden = True
        return
    unknown = [pid for pid, status in state['status'].items() if status == 'unknown']
    counts = 'found {} preprint(s), {} in the database'.format(
        len(state['status']), sum(1 for status in state['status'].values() if status == 'db'))
    button = ' <button class="small" data-action="search-all">search online for the {} unknown</button>'.format(len(unknown)) if unknown else ''
    el('results').innerHTML = '<div class="bar-top"><span class="cap">{}</span>{}</div>'.format(counts, button) + ''.join(row(item) for item in state['items'])
    show_export()


def row(item):
    pid = item['id']
    record = state['records'].get(pid)
    status = state['status'][pid]
    choice = state['choices'].get(pid, 'preprint')
    pre = record['preprint'] if record else py.preprint_from_fields(pid, item['fields'])
    left = '<a class="id" href="{}" target="_blank">{}</a><div class="title">{}</div>'.format(
        py.preprint_url(pid), escape(py.preprint_label(pid)), escape(pre.get('title', '').replace('{', '').replace('}', '')))
    if item['key']:
        left += '<div class="key">{}</div>'.format(escape(item['key']))
    boxes = [publication_box(pid, i, pub, choice == i) for i, pub in enumerate((record or {}).get('publications') or [])]
    if boxes:
        boxes.append('<div class="box preprint{}" data-action="choose" data-pid="{}" data-choice="preprint">keep the preprint</div>'
                     .format(' chosen' if choice == 'preprint' else '', escape(pid)))
    return '<div class="row"><div class="pre">{}</div><div class="pubs">{}<div class="status {}">{}</div></div></div>'.format(
        left, ''.join(boxes), status, status_line(pid, record, status, choice))


def publication_box(pid, i, pub, chosen):
    bib = pub['bibtex']
    venue = bib.get('journal') or bib.get('booktitle') or bib.get('howpublished') or bib.get('title')
    link = bib.get('url') or ('https://doi.org/' + bib['doi'] if bib.get('doi') else '')
    tags = [pub.get('venue_type'), bib.get('year'), None if pub.get('archival', True) else 'non-archival',
            'extended version' if pub.get('relation') == 'extended' else None]
    preview = '{}\n{}\n{} {}'.format(' and '.join(bib.get('author', [])), bib.get('title', ''), venue, bib.get('year', ''))
    name = '<a href="{}" target="_blank">{}</a>'.format(escape(link), escape(venue)) if link else escape(venue)
    return '<div class="box{}" data-action="choose" data-pid="{}" data-choice="{}" title="{}">{}<div class="meta">{}</div></div>'.format(
        ' chosen' if chosen else '', escape(pid), i, escape(preview), name, escape(' · '.join(t for t in tags if t)))


def status_line(pid, record, status, choice):
    add = '<a href="{}" target="_blank">add a publication by hand</a>'.format(escape(proposal_url(pid, record, choice, by_hand=True)))
    if status == 'looking':
        return 'looking up ...'
    if status == 'searching':
        return 'searching online ...'
    if status == 'unknown':
        return 'not in the database <button class="small" data-action="search" data-pid="{}">search online</button> {}'.format(escape(pid), add)
    if status == 'none':
        return 'no newer publication found ' + add
    if status == 'online':
        return '<span class="badge">found online, not confirmed yet</span> <a href="{}" target="_blank">confirm for the database</a> {}'.format(
            escape(proposal_url(pid, record, choice)), add)
    return 'from the database <a href="{}" target="_blank">suggest a change</a>'.format(escape(proposal_url(pid, record, choice)))


def show_export():
    preprint_style = el('preprint-style').value or None
    key_style = el('key-style').value
    entries, changes = [], []
    for item in state['items']:
        pid = item['id']
        if state['status'][pid] in ('looking', 'searching'):
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

def proposal_url(pid, record, choice, by_hand=False):
    # a link to a prefilled GitHub issue holding the record as json; a GitHub action turns it into a pull request
    record = json.loads(json.dumps(record)) if record else {'preprint': {'id': pid}, 'publications': []}
    record.pop('unconfirmed', None)
    for i, pub in enumerate(record['publications']):
        pub.pop('preferred', None)
        if i == choice:
            pub['preferred'] = True
    if by_hand:
        record['publications'].append({
            'relation': 'same', 'venue_type': 'conference', 'archival': True,
            'bibtex': {'type': 'inproceedings', 'author': ['Last, First'], 'title': '', 'booktitle': '', 'year': '', 'url': ''}})
    title = '[db] ' + py.preprint_label(pid)
    return REPO + '/issues/new?' + urlencode({'template': 'db-entry.yml', 'title': title,
                                              'entry': json.dumps(record, indent=2, ensure_ascii=False)})


# ----- events ----------------------------------------------------------------------------

async def on_results_click(event):
    if event.target.closest('a'):
        return  # links simply open
    target = event.target.closest('[data-action]')
    if not target:
        return
    action = target.getAttribute('data-action')
    if action == 'choose':
        choice = target.getAttribute('data-choice')
        state['choices'][target.getAttribute('data-pid')] = choice if choice == 'preprint' else int(choice)
        show()
    elif action == 'search':
        await search([target.getAttribute('data-pid')])
    elif action == 'search-all':
        await search([pid for pid, status in state['status'].items() if status == 'unknown'])


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


async def on_file(event):
    files = el('file').files
    if files.length:
        el('input').value = await files.item(0).text()
        await run()


async def start():
    for name, kind, handler in [('run', 'click', run), ('results', 'click', on_results_click),
                                ('export', 'click', on_export_click), ('file', 'change', on_file),
                                ('preprint-style', 'change', lambda event: show_export()),
                                ('key-style', 'change', lambda event: show_export())]:
        el(name).addEventListener(kind, create_proxy(handler))
    el('run').disabled = False
    el('run').textContent = 'run'
