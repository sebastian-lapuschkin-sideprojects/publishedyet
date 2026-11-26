"""
Offline tests: python -m unittest discover tests
Online sources are replaced by saved responses in tests/data.
"""

import os
import sys
import json
import asyncio
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import publishedyet as py

with open(os.path.join(HERE, 'testlibrary.bib'), encoding='utf-8') as f:
    LIBRARY = f.read()


def data(name):
    with open(os.path.join(HERE, 'data', name), encoding='utf-8') as f:
        return json.load(f)


RECORD = {  # a made-up record, only for testing
    'preprint': {'id': 'arxiv:1512.03385', 'title': 'Deep Residual Learning for Image Recognition',
                 'authors': ['Kaiming He', 'Xiangyu Zhang'], 'year': '2015', 'primary_class': 'cs.CV'},
    'publications': [
        {'relation': 'same', 'venue_type': 'workshop', 'archival': False,
         'bibtex': {'type': 'inproceedings', 'author': ['He, Kaiming'], 'title': 'A Workshop Version',
                    'booktitle': 'Some Workshop', 'year': '2015'}},
        {'relation': 'same', 'venue_type': 'conference', 'archival': True, 'preferred': True, 'dblp': 'conf/x/HeZ16',
         'bibtex': {'type': 'inproceedings', 'author': ['He, Kaiming', 'Zhang, Xiangyu'],
                    'title': 'Deep Residual Learning for Image Recognition', 'booktitle': 'CVPR', 'year': '2016'}},
    ],
}


class Ids(unittest.TestCase):

    def test_find_preprint_id(self):
        cases = {
            '2402.05602': 'arxiv:2402.05602',
            '2402.05602v3': 'arxiv:2402.05602',
            'arXiv:hep-th/9901001': 'arxiv:hep-th/9901001',
            'math.GT/0309136v2': 'arxiv:math/0309136',
            'https://arxiv.org/pdf/2103.00020v1.pdf': 'arxiv:2103.00020',
            'journal = {{arXiv} preprint {arXiv}:2504.19475}': 'arxiv:2504.19475',
            'doi = {10.48550/arXiv.1802.03426}': 'arxiv:1802.03426',
            'volume = {abs/2402.05602}': 'arxiv:2402.05602',
            'eprint = {1802.03426 [stat]}': 'arxiv:1802.03426',
            '10.1101/2020.01.01.890160': 'doi:10.1101/2020.01.01.890160',
            'https://www.biorxiv.org/content/10.1101/2020.01.01.890160v2': 'doi:10.1101/2020.01.01.890160',
            '10.1101/gr.123456.111': None,     # a Cold Spring Harbor journal, not bioRxiv
            '2020.07.23.217034v2': 'doi:10.1101/2020.07.23.217034',
            'pages = {1006--1019}': None,
            'https://openreview.net/forum?id=AV5BgHyVdw': 'openreview:AV5BgHyVdw',
            'url = {https://openreview.net/pdf?id=F76bwRSLeK}': 'openreview:F76bwRSLeK',
        }
        for text, expected in cases.items():
            self.assertEqual(py.find_preprint_id(text), expected, text)

    def test_db_path(self):
        self.assertEqual(py.db_path('arxiv:2402.05602'), 'arxiv/2402/2402.05602.json')
        self.assertEqual(py.db_path('arxiv:hep-th/9901001'), 'arxiv/hep-th/9901001.json')
        self.assertEqual(py.db_path('doi:10.1101/2020.01.01.890160'), 'doi/10.1101/2020.01.01.890160.json')

    def test_preprint_year(self):
        self.assertEqual(py.preprint_year('arxiv:2402.05602'), '2024')
        self.assertEqual(py.preprint_year('arxiv:hep-th/9901001'), '1999')
        self.assertEqual(py.preprint_year('doi:10.1101/2021.02.19.431935'), '2021')


class Bibtex(unittest.TestCase):

    def test_split_and_parse(self):
        entries = py.split_entries(LIBRARY)
        self.assertEqual(len(entries), 45)
        he = next(e for e in entries if e['key'] == 'he_deep_2015')
        fields = py.parse_fields(he['text'])
        self.assertEqual(fields['title'], 'Deep Residual Learning for Image Recognition')
        self.assertEqual(py.split_authors(fields['author'])[0], 'He, Kaiming')

    def test_tricky_values(self):
        entry = '@misc{k, title = "A {"}quoted{"} title", year = 2020, note = {a} # " b", author={X and Y}}'
        fields = py.parse_fields(entry)
        self.assertEqual(fields['title'], 'A {"}quoted{"} title')
        self.assertEqual(fields['year'], '2020')
        self.assertEqual(fields['note'], 'a b')
        self.assertEqual(py.split_authors(fields['author']), ['X', 'Y'])

    def test_scan_library(self):
        items = py.scan(LIBRARY)
        ids = [item['id'] for item in items]
        self.assertEqual(len(items), 14)
        self.assertIn('arxiv:1512.03385', ids)
        self.assertIn('arxiv:2504.19475', ids)

    def test_scan_ids(self):
        items = py.scan('2402.05602, https://arxiv.org/abs/2402.05602v2\n10.1101/2020.01.01.890160 nonsense')
        self.assertEqual([item['id'] for item in items], ['arxiv:2402.05602', 'doi:10.1101/2020.01.01.890160', None])
        self.assertEqual(items[2]['text'], 'nonsense')

    def test_published_entries_are_not_preprints(self):
        entry = '@article{k, title={T}, journal={Nature Communications}, eprint={1902.10178}, archivePrefix={arXiv}}'
        self.assertEqual(py.scan(entry), [])

    def test_scholar_key(self):
        self.assertEqual(py.scholar_key(['Bach, Sebastian'], 2015, 'On Pixel-Wise Explanations'), 'bach2015pixel')
        self.assertEqual(py.scholar_key(['Laurens van der Maaten'], 2008, 'Visualizing Data using t-SNE'), 'van2008visualizing')
        self.assertEqual(py.scholar_key(['M{\\"u}ller, Klaus-Robert'], '2019', 'From attribution maps'), 'muller2019attribution')


class Fixing(unittest.TestCase):

    def test_keys_and_choices(self):
        item = {'id': 'arxiv:1512.03385', 'key': None, 'entry': None, 'fields': None}
        self.assertEqual(py.preferred(RECORD), 1)
        self.assertTrue(py.replacement(item, RECORD, 1).startswith('@inproceedings{he2016deep,'))
        self.assertTrue(py.replacement(item, RECORD, 1, key_style='dblp').startswith('@inproceedings{DBLP:conf/x/HeZ16,'))
        self.assertTrue(py.replacement(item, RECORD, 0, key_style='dblp').startswith('@inproceedings{he2015workshop,'))
        preprint = py.replacement(item, RECORD, 'preprint', key_style='dblp')
        self.assertTrue(preprint.startswith('@article{DBLP:journals/corr/abs-1512-03385,'))

    def test_preprint_styles(self):
        pre = RECORD['preprint']
        self.assertIn('journal = {arXiv preprint arXiv:1512.03385}', py.render_preprint(pre, 'k', 'scholar'))
        self.assertIn('primaryClass  = {cs.CV}', py.render_preprint(pre, 'k', 'arxiv'))
        self.assertIn('volume     = {abs/1512.03385}', py.render_preprint(pre, 'k', 'dblp'))

    def test_fix_library(self):
        changes = []
        for item in py.scan(LIBRARY):
            if item['id'] == 'arxiv:1512.03385':
                choice = py.preferred(RECORD)
                changes.append((item['entry'], py.replacement(item, RECORD, choice), py.describe(RECORD, choice)))
            else:  # all other preprints rewritten in one style, from what the user's entry says
                changes.append((item['entry'], py.replacement(item, None, 'preprint', 'scholar'), 'restyled'))
        fixed = py.fix_bib(LIBRARY, changes)
        entries = py.split_entries(fixed)
        # same number of entries: each old one is commented out (its '@' removed), the new one takes its place
        self.assertEqual(len(entries), 45)
        he = [e for e in entries if e['key'] == 'he_deep_2015']
        self.assertEqual(len(he), 1)
        self.assertEqual(py.parse_fields(he[0]['text'])['booktitle'], 'CVPR')
        self.assertIn('% publishedyet: replaced by the published version: CVPR 2016', fixed)
        self.assertIn('% article{he_deep_2015,', fixed)
        self.assertTrue(py.diff_lines(LIBRARY, fixed))


class Discovery(unittest.TestCase):

    def test_merged_openalex_work(self):
        # OpenAlex merges the CRP preprint with its journal version; details then come from Crossref
        responses = {
            py.OPENALEX + '/works/doi:10.48550/arXiv.2206.03208': data('openalex_arxiv_2206.03208.json'),
            py.CROSSREF + '/works/10.1038/s42256-023-00711-8': data('crossref_10.1038_s42256-023-00711-8.json'),
        }

        async def get_json(url):
            return responses.get(url)

        record = asyncio.run(py.discover('arxiv:2206.03208', get_json))
        self.assertTrue(record['unconfirmed'])
        self.assertEqual(record['preprint']['year'], '2022')
        self.assertEqual(len(record['publications']), 1)
        bib = record['publications'][0]['bibtex']
        self.assertEqual(bib['journal'], 'Nature Machine Intelligence')
        self.assertEqual(bib['pages'], '1006--1019')
        self.assertEqual(bib['year'], '2023')


class Dblp(unittest.TestCase):

    def test_short_booktitle(self):
        # DBLP's proceedings titles without place and dates
        self.assertEqual(py.short_booktitle('Findings of the Association for Computational Linguistics: ACL 2023, Toronto, Canada, July 9-14, 2023.'),
                         'Findings of the Association for Computational Linguistics: ACL 2023')
        self.assertEqual(py.short_booktitle('Advances in Neural Information Processing Systems 38: Annual Conference on Neural Information '
                                            'Processing Systems 2025, NeurIPS 2025, San Diego, CA, USA, December 2-7, 2025.'),
                         'Advances in Neural Information Processing Systems 38')
        kept = 'Machine Learning, Optimization, and Data Science - 9th International Conference, LOD 2023, Grasmere, UK, September 22-26, 2023, Part I'
        self.assertEqual(py.short_booktitle(kept), kept)   # the dates are not at the end: leave it

    def test_from_dblp(self):
        # a row as DBLP_DETAILS returns it (taken from sparql.dblp.org)
        row = {'pub': 'https://dblp.org/rec/journals/tnn/SamekBMLM17', 'type': 'http://purl.org/net/nknouf/ns/bibtex#Article',
               'title': 'Evaluating the Visualization of What a Deep Neural Network Has Learned.', 'year': '2017',
               'journal': 'IEEE Trans. Neural Networks Learn. Syst.', 'volume': '28', 'number': '11', 'pages': '2660-2673',
               'doi': 'https://doi.org/10.1109/TNNLS.2016.2599820', 'url': 'https://doi.org/10.1109/TNNLS.2016.2599820',
               'short': 'IEEE Trans. Neural Networks Learn. Syst.',
               'authors': '2:Alexander Binder|1:Wojciech Samek|5:Klaus-Robert Müller|3:Grégoire Montavon|4:Sebastian Lapuschkin'}
        pub = py.from_dblp(row)
        self.assertEqual(pub['dblp'], 'journals/tnn/SamekBMLM17')
        self.assertEqual(pub['venue_short'], 'IEEE Trans. Neural Networks Learn. Syst.')
        self.assertEqual(pub['venue_type'], 'journal')
        bib = pub['bibtex']
        self.assertEqual(bib['author'][0], 'Wojciech Samek')
        self.assertEqual(bib['author'][4], 'Klaus-Robert Müller')
        self.assertEqual(bib['title'], 'Evaluating the Visualization of What a Deep Neural Network Has Learned')
        self.assertEqual(bib['pages'], '2660--2673')
        self.assertEqual(bib['doi'], '10.1109/TNNLS.2016.2599820')


class VenueBibtex(unittest.TestCase):

    def test_pmlr_page(self):
        page = ('<html><code class="citecode" id="bibtex">@InProceedings{pmlr-v1-x,\n  title = \t {A {CLIP} Paper},\n'
                '  author = {M&uuml;ller, Klaus-Robert and Doe, Jane},\n  booktitle = {Proceedings of the 42nd International '
                'Conference on Machine Learning},\n  pages = {1--9},\n  year = {2025},\n  pdf = {x.pdf},\n}</code></html>')

        async def get_text(url):
            return page

        pub = {'dblp': 'conf/icml/X25', 'bibtex': {'type': 'inproceedings', 'title': 'A CLIP Paper', 'booktitle': 'ICML 2025, ...',
                                                   'url': 'https://proceedings.mlr.press/v1/x.html'}}
        asyncio.run(py.venue_bibtex(pub, get_text))
        self.assertEqual(pub['source'], 'pmlr')
        self.assertEqual(pub['dblp'], 'conf/icml/X25')
        self.assertEqual(pub['bibtex']['booktitle'], 'Proceedings of the 42nd International Conference on Machine Learning')
        self.assertEqual(pub['bibtex']['author'], ['Müller, Klaus-Robert', 'Doe, Jane'])
        self.assertNotIn('pdf', pub['bibtex'])
        self.assertEqual(pub['bibtex']['url'], 'https://proceedings.mlr.press/v1/x.html')

    def test_iclr_as_on_openreview(self):
        pub = {'dblp': 'conf/iclr/HubenCRES24', 'bibtex': {
            'type': 'inproceedings', 'author': ['Robert Huben'], 'title': 'Sparse Autoencoders', 'year': '2024',
            'booktitle': 'The Twelfth International Conference on Learning Representations, ICLR 2024, Vienna, Austria, May 7-11, 2024',
            'publisher': 'OpenReview.net', 'url': 'https://openreview.net/forum?id=F76bwRSLeK'}}
        asyncio.run(py.venue_bibtex(pub, None))
        self.assertEqual(pub['bibtex']['booktitle'], 'The Twelfth International Conference on Learning Representations')
        self.assertNotIn('publisher', pub['bibtex'])


    def test_tmlr_as_on_openreview(self):
        pub = {'dblp': 'journals/tmlr/BereskaG24', 'venue_short': 'Trans. Mach. Learn. Res.', 'bibtex': {
            'type': 'article', 'author': ['Leonard F. Bereska', 'Stratis Gavves'], 'title': 'Mechanistic Interpretability for AI Safety - A Review',
            'journal': 'Trans. Mach. Learn. Res.', 'volume': '2024', 'year': '2024', 'url': 'https://openreview.net/forum?id=ePUVetPKu6'}}
        asyncio.run(py.venue_bibtex(pub, None))
        self.assertEqual(pub['bibtex']['journal'], 'Transactions on Machine Learning Research')
        self.assertEqual(pub['bibtex']['issn'], '2835-8856')
        self.assertNotIn('volume', pub['bibtex'])
        self.assertEqual(pub['venue_short'], 'Transactions on Machine Learning Research')
        self.assertEqual(py.missing_fields(pub), [])

    def test_jmlr_as_on_jmlr_org(self):
        pub = {'dblp': 'journals/jmlr/AlberLSHSMSMDK19', 'bibtex': {
            'type': 'article', 'author': ['Maximilian Alber'], 'title': 'iNNvestigate Neural Networks!',
            'journal': 'J. Mach. Learn. Res.', 'volume': '20', 'pages': '93:1-93:8', 'year': '2019'}}
        asyncio.run(py.venue_bibtex(pub, None))
        self.assertEqual(pub['bibtex']['journal'], 'Journal of Machine Learning Research')
        self.assertEqual((pub['bibtex']['number'], pub['bibtex']['pages']), ('93', '1--8'))
        old = {'dblp': 'journals/jmlr/LeCun03', 'bibtex': {'type': 'article', 'journal': 'J. Mach. Learn. Res.', 'pages': '1--48'}}
        asyncio.run(py.venue_bibtex(old, None))
        self.assertEqual(old['bibtex']['pages'], '1--48')
        self.assertNotIn('number', old['bibtex'])


class CrossrefNames(unittest.TestCase):

    def test_volume_not_series(self):
        item = {'type': 'book-chapter', 'DOI': '10.1007/978-3-319-44781-0_8', 'title': ['LRP with Local Renormalization Layers'],
                'container-title': ['Lecture Notes in Computer Science', 'Artificial Neural Networks and Machine Learning – ICANN 2016'],
                'author': [{'family': 'Binder', 'given': 'Alexander'}], 'issued': {'date-parts': [[2016]]}, 'page': '63-71'}
        bib = py.from_crossref(item)['bibtex']
        self.assertEqual(bib['booktitle'], 'Artificial Neural Networks and Machine Learning – ICANN 2016')
        self.assertEqual(bib['series'], 'Lecture Notes in Computer Science')


class Completeness(unittest.TestCase):

    def test_missing_fields(self):
        article = {'bibtex': {'type': 'article', 'author': ['A'], 'title': 'T', 'journal': 'J', 'year': '2020', 'doi': '10.1/x'}}
        self.assertEqual(py.missing_fields(article), ['volume', 'pages'])
        iclr = {'bibtex': {'type': 'inproceedings', 'author': ['A'], 'title': 'T', 'booktitle': 'ICLR', 'year': '2024',
                           'url': 'https://openreview.net/forum?id=x'}}
        self.assertEqual(py.missing_fields(iclr), [])
        iclr26 = {'bibtex': {'type': 'inproceedings', 'author': ['A'], 'title': 'T', 'booktitle': 'ICLR', 'year': '2026',
                             'url': 'https://proceedings.iclr.cc/paper_files/paper/2026/hash/x-Abstract-Conference.html'}}
        self.assertEqual(py.missing_fields(iclr26), [])
        workshop = {'archival': False, 'bibtex': {'type': 'inproceedings', 'author': ['A'], 'title': 'T', 'booktitle': 'W', 'year': '2024'}}
        self.assertEqual(py.missing_fields(workshop), ['doi or url'])


class Demo(unittest.TestCase):

    def test_demo_is_up_to_date(self):
        # demo/refs_fixed.bib is what `cli.py fix demo/refs.bib --preprint-style scholar` makes with the database
        root = os.path.dirname(HERE)
        with open(os.path.join(root, 'demo', 'refs.bib'), encoding='utf-8') as f:
            text = f.read()
        changes = []
        for item in py.scan(text):
            path = os.path.join(root, 'db', py.db_path(item['id']))
            record = json.load(open(path, encoding='utf-8')) if os.path.exists(path) else None
            choice = py.preferred(record)
            new = py.replacement(item, record, choice, 'scholar')
            if new:
                changes.append((item['entry'], new, py.describe(record, choice)))
        with open(os.path.join(root, 'demo', 'refs_fixed.bib'), encoding='utf-8') as f:
            self.assertEqual(py.fix_bib(text, changes), f.read())


class Database(unittest.TestCase):

    def test_all_records(self):
        db = os.path.join(os.path.dirname(HERE), 'db')
        for folder, _, names in os.walk(db):
            for name in [n for n in names if n.endswith('.json')]:
                path = os.path.join(folder, name)
                with open(path, encoding='utf-8') as f:
                    record = json.load(f)
                self.assertEqual(py.check_record(record), [], path)
                self.assertEqual(os.path.relpath(path, db), py.db_path(record['preprint']['id']))

    def test_check_finds_problems(self):
        bad = {'preprint': {'id': 'arxiv:nonsense'}, 'publications': [{'bibtex': {'type': 'article'}}], 'unconfirmed': True}
        self.assertGreater(len(py.check_record(bad)), 5)


if __name__ == '__main__':
    unittest.main()
