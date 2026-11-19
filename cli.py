"""
publishedyet on the command line. Examples:

    python cli.py show 2402.05602 10.1101/2020.01.01.890160
    python cli.py fix refs.bib --preprint-style scholar -o refs_fixed.bib
    python cli.py add 2402.05602 --doi 10.1234/some.doi          (maintainers: add to the database)
    python cli.py check                                          (maintainers: check the database)

Run `python cli.py <command> -h` for all options.
"""

import os
import re
import sys
import json
import asyncio
import argparse
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from urllib.parse import quote

import publishedyet as py

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'db')
USER_AGENT = 'publishedyet (https://github.com/sebastian-lapuschkin-sideprojects/publishedyet)'


def fetch(url):
    request = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read().decode('utf-8')


async def get_json(url):
    # json from a url or a local file; None if there is none (or the request fails)
    if not url.startswith('http'):
        if not os.path.exists(url):
            return None
        with open(url, encoding='utf-8') as f:
            return json.load(f)
    try:
        return json.loads(fetch(url))
    except urllib.error.HTTPError as error:
        if error.code != 404:
            print('warning: {} for {}'.format(error, url), file=sys.stderr)
    except Exception as error:
        print('warning: {} for {}'.format(error, url), file=sys.stderr)
    return None


async def get_text(url):
    # a web page as text; None if the request fails
    try:
        return fetch(url)
    except Exception as error:
        print('warning: {} for {}'.format(error, url), file=sys.stderr)
        return None


async def lookup(pid, args, item=None):
    # the database record of a preprint; searched online if it is not in the database and --online is set
    db = args.db.rstrip('/') + '/'
    record = await get_json(db + py.db_path(pid))
    if record is None and args.online:
        known = py.preprint_from_fields(pid, item['fields']) if item and item['fields'] else None
        record = await py.discover(pid, get_json, known, get_text)
    return record


def status(record):
    if record is None:
        return 'not in the database'
    if not record['publications']:
        return 'no published version found online'
    found = ', '.join('{} {}'.format(p['bibtex'].get('journal') or p['bibtex'].get('booktitle') or '?',
                                     p['bibtex'].get('year', '')) for p in record['publications'])
    return ('FOUND ONLINE, please check: ' if record.get('unconfirmed') else 'in database: ') + found


async def show(args):
    for item in py.scan(' '.join(args.ids)):
        if item['id'] is None:
            print('{}  is not an arXiv, bioRxiv or OpenReview id\n'.format(item['text']))
            continue
        record = await lookup(item['id'], args)
        print('{}  {}'.format(py.preprint_label(item['id']), status(record)))
        choice = py.preferred(record)
        text = py.replacement(item, record, choice, args.preprint_style, args.key_style) if record else None
        if text:
            print(text)
        print()


async def fix(args):
    with open(args.file, encoding='utf-8') as f:
        text = f.read()
    changes = []
    for item in py.scan(text):
        record = await lookup(item['id'], args, item)
        print('{:<35} {:<22} {}'.format(item['key'], py.preprint_label(item['id']), status(record)), file=sys.stderr)
        choice = py.preferred(record)
        new = py.replacement(item, record, choice, args.preprint_style)
        if new:
            note = py.describe(record, choice) + (' (found online, unconfirmed)' if record and record.get('unconfirmed') else '')
            changes.append((item['entry'], new, note))
    fixed = py.fix_bib(text, changes)
    if args.output:
        with open(args.output, 'w', encoding='utf-8') as f:
            f.write(fixed)
        print('\n'.join(py.diff_lines(text, fixed)))
    else:
        print(fixed)


# ----- maintainers ---------------------------------------------------------------------

def arxiv_metadata(pid):
    # title, authors, year and primary category from the arXiv API
    ident = pid.split(':', 1)[1]
    root = ET.fromstring(fetch('https://export.arxiv.org/api/query?id_list=' + quote(ident)))
    ns = {'a': 'http://www.w3.org/2005/Atom', 'arxiv': 'http://arxiv.org/schemas/atom'}
    entry = root.find('a:entry', ns)
    if entry is None or entry.find('a:title', ns) is None:
        sys.exit('arXiv does not know ' + ident)
    category = entry.find('arxiv:primary_category', ns)
    return {'id': pid,
            'title': ' '.join(entry.findtext('a:title', '', ns).split()),
            'authors': [a.findtext('a:name', '', ns).strip() for a in entry.findall('a:author', ns)],
            'year': entry.findtext('a:published', '', ns)[:4],
            'primary_class': category.get('term') if category is not None else None}


async def preprint_metadata(pid):
    if pid.startswith('arxiv:'):
        return arxiv_metadata(pid)
    record = await py.discover(pid, get_json)    # for bioRxiv: title, authors, year, server
    return record['preprint']


def tidy(data, order):
    # the same keys in the same order in every file, so that diffs show only real changes
    result = dict((key, data[key]) for key in order if key in data)
    result.update((key, value) for key, value in data.items() if key not in result)
    return result


def save(record, db):
    record = {'preprint': tidy(record['preprint'], ['id', 'title', 'authors', 'year', 'primary_class', 'server']),
              'publications': [tidy(pub, ['relation', 'venue_type', 'venue_short', 'archival', 'preferred', 'dblp', 'source', 'bibtex'])
                               for pub in record['publications']]}
    path = os.path.join(db, py.db_path(record['preprint']['id']))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(json.dumps(record, indent=2, ensure_ascii=False) + '\n')
    return path


async def add(args):
    pid = py.find_preprint_id(args.preprint)
    if not pid:
        sys.exit('not a preprint id: ' + args.preprint)
    record = await get_json(os.path.join(args.db, py.db_path(pid)))
    if record is None:
        record = {'preprint': await preprint_metadata(pid), 'publications': []}
    if args.doi:
        found = await get_json('{}/works/{}'.format(py.CROSSREF, quote(args.doi)))
        if not found or found['message'].get('type') not in py.CROSSREF_TYPES:
            sys.exit('Crossref has no journal / proceedings / chapter for ' + args.doi)
        pub = py.from_crossref(found['message'])
    else:
        with open(args.bib, encoding='utf-8') as f:
            pub = py.from_bibtex(f.read())
        if pub is None:
            sys.exit('no bibtex entry in ' + args.bib)
    pub['relation'] = args.relation
    pub['venue_type'] = args.venue_type or pub['venue_type']
    pub['archival'] = not args.non_archival
    if args.dblp:
        pub['dblp'] = args.dblp
    if args.preferred:
        for other in record['publications']:
            other.pop('preferred', None)
        pub['preferred'] = True
    record['publications'].append(pub)
    problems = py.check_record(record)
    if problems:
        sys.exit('not saved:\n  ' + '\n  '.join(problems))
    print('saved', save(record, args.db))


async def check(args):
    bad = 0
    for folder, _, names in os.walk(args.db):
        for name in sorted(n for n in names if n.endswith('.json')):
            path = os.path.join(folder, name)
            record = await get_json(path)
            problems = py.check_record(record)
            pid = (record.get('preprint') or {}).get('id') or ''
            if not problems and os.path.relpath(path, args.db) != py.db_path(pid):
                problems.append('file should be at {}'.format(py.db_path(pid)))
            for problem in problems:
                print('{}: {}'.format(path, problem))
            bad += bool(problems)
    print('{} problem record(s)'.format(bad))
    sys.exit(1 if bad else 0)


async def from_issue(args):
    # used by the GitHub action: take the json entry from an issue body and write it into the database
    body = os.environ.get('ISSUE_BODY', '')
    match = re.search(r'```(?:json)?\s*\n(.*?)\n```', body, re.DOTALL)
    try:
        record = json.loads(match.group(1) if match else body)
    except ValueError as error:
        sys.exit('The entry is not valid json: {}'.format(error))
    problems = py.check_record(record)
    if problems:
        sys.exit('The entry has problems:\n- ' + '\n- '.join(problems))
    path = os.path.join(args.db, py.db_path(record['preprint']['id']))
    existed = os.path.exists(path)
    save(record, args.db)
    with open(os.environ.get('PR_BODY', 'pr_body.md'), 'w', encoding='utf-8') as f:
        f.write(summary(record, os.path.relpath(path), existed))
    print(os.path.relpath(path))


def readable(names):
    # 'Achtibat, Reduan' -> 'Reduan Achtibat', joined with commas, braces removed
    names = [' '.join(reversed([part.strip() for part in name.split(',', 1)])) for name in names or []]
    return ', '.join(names).replace('{', '').replace('}', '')


def summary(record, path, existed):
    # the text of the pull request: what merging it does to the database, with titles, authors and links to check it
    pre = record['preprint']
    unbrace = lambda text: (text or '').replace('{', '').replace('}', '')
    lines = ['**{}** `{}`'.format('Changes the database entry' if existed else 'Adds a new file to the database:', path), '',
             '**Preprint:** [{}]({}) ({})'.format(py.preprint_label(pre['id']), py.preprint_url(pre['id']), pre.get('year')),
             '- Title: ' + unbrace(pre.get('title')),
             '- Authors: ' + readable(pre.get('authors')),
             '', '**Publications:**']
    for i, pub in enumerate(record['publications']):
        bib = pub['bibtex']
        venue = bib.get(py.VENUE_FIELD.get(bib['type'], 'howpublished')) or ''
        notes = [pub.get('venue_type'), 'archival' if pub.get('archival', True) else 'non-archival',
                 'extended version' if pub.get('relation') == 'extended' else None,
                 'preferred' if py.preferred(record) == i else None]
        name = '{} {}'.format(pub.get('venue_short') or '', bib.get('year', '')).strip()
        lines.append('- **{}**: {} ({})'.format(name, venue, ', '.join(note for note in notes if note)))
        lines.append('  - Title: ' + unbrace(bib.get('title')))
        lines.append('  - Authors: ' + readable(bib.get('author')))
        links = []
        if bib.get('url'):
            links.append('[paper]({})'.format(bib['url']))
        if bib.get('doi') and 'doi.org' not in (bib.get('url') or ''):
            links.append('[doi:{0}](https://doi.org/{0})'.format(bib['doi']))
        if pub.get('dblp'):
            links.append('[DBLP](https://dblp.org/rec/{})'.format(pub['dblp']))
        if links:
            lines.append('  - Links: ' + ' · '.join(links))
    if not record['publications']:
        lines.append('- none')
    if existed:
        lines += ['', 'The diff under "Files changed" shows what changes.']
    lines += ['', 'Merging puts this into the database; the page uses it a few minutes later.']
    return '\n'.join(lines) + '\n'


def main():
    parser = argparse.ArgumentParser(description='Has the preprint you cite been published by now?')
    parser.add_argument('--db', default=DB, help='database folder or url (default: the db folder next to this file)')
    commands = parser.add_subparsers(dest='command', required=True)

    p = commands.add_parser('show', help='show what is known about some preprints')
    p.add_argument('ids', nargs='+', help='arXiv ids, bioRxiv DOIs or links')
    p.add_argument('--online', action='store_true', help='search online for preprints not in the database')
    p.add_argument('--key-style', choices=['scholar', 'dblp'], default='scholar')
    p.add_argument('--preprint-style', choices=list(py.PREPRINT_STYLES), default='scholar')
    p.set_defaults(run=show)

    p = commands.add_parser('fix', help='replace preprints in a bibtex file by their published versions')
    p.add_argument('file')
    p.add_argument('-o', '--output', help='write the fixed file here and print the changes (default: print the fixed file)')
    p.add_argument('--online', action='store_true', help='search online for preprints not in the database')
    p.add_argument('--preprint-style', choices=list(py.PREPRINT_STYLES), help='rewrite remaining preprints in this style')
    p.set_defaults(run=fix)

    p = commands.add_parser('add', help='add a publication of a preprint to the database')
    p.add_argument('preprint', help='arXiv id or bioRxiv DOI')
    source = p.add_mutually_exclusive_group(required=True)
    source.add_argument('--doi', help='DOI of the publication (details come from Crossref)')
    source.add_argument('--bib', help='bibtex file holding the publication (for papers without DOI)')
    p.add_argument('--relation', choices=py.RELATIONS, default='same')
    p.add_argument('--venue-type', choices=py.VENUE_TYPES)
    p.add_argument('--non-archival', action='store_true')
    p.add_argument('--preferred', action='store_true', help='make this the preferred publication')
    p.add_argument('--dblp', help="DBLP key, e.g. conf/icml/Name24 (for DBLP style keys)")
    p.set_defaults(run=add)

    p = commands.add_parser('check', help='check all database records')
    p.set_defaults(run=check)

    p = commands.add_parser('from-issue', help='(GitHub action) write the entry in $ISSUE_BODY into the database')
    p.set_defaults(run=from_issue)

    args = parser.parse_args()
    asyncio.run(args.run(args))


if __name__ == '__main__':
    main()
