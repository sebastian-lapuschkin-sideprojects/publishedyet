import re
import requests
from urllib.parse import urlencode

DBLP_API = 'https://dblp.org/search/publ/api'
def dblp_search(query, hits=10, format='json'):
    # searches dblp for a bunch of queries,
    # query should be a string based identifier of stuff to look for.
    # returns the  discovered hits in a list of specified format if any are found, or None otherwise.

    # build proper query options
    queryoptions = {'q':query, 'format':format, 'h':hits  }

    # request a reply
    r = '{}?{}'.format(DBLP_API,urlencode(queryoptions))
    print(r)
    r = requests.get(r).json()
    hits = r['result']['hits'].get('hit')

    print(hits, (len(hits) if not hits is None else None))

    return(hits)


ARXIV_PATTERN       = re.compile('^\d{4}\.\d+(?:v\d+)?$' , re.IGNORECASE)
ARXIV_PATTERN_OLD   = re.compile('^[a-zA-Z-]+(?:\.[a-zA-Z-]+)?\/\d+(?:v\d+)?$' , re.IGNORECASE)
def dblp_resolve_arxiv(arxiv_id):
    # receives an arxiv id of a paper and resolves its dblp entry, if possible.
    # returns author list, paper title and type, or None otherwise.

    # make sure we have a valid arxiv id pattern first and retu
    if not (
        re.match(ARXIV_PATTERN, arxiv_id) # not None if valid new arxiv pattern from 2007+
        or
        re.match(ARXIV_PATTERN_OLD, arxiv_id) # not None if valid for old arxiv pattern pre 2007
        ):
        print('Given arxiv paper id {} is not valid'.format(arxiv_id))
        return None
    
    # since we have not rejected the given id yet, we can call DBLP.
    hits = dblp_search(arxiv_id)
    # TODO: CHECK: DO WE HAVE 0, 1 or more hits?
    # DO ALL OTHER SHENANINGANS AS RESOLVING AUTHORS TITLE ETC. MAYBE PARSE TO BIBTEX lATER?
    
    






# import requests
# import pandas as pd

# from urllib.parse import urlencode
# from importlib.resources import open_binary

# BASE_URL = 'https://dblp.org/search/publ/api'


# def search(queries: list[str]) -> list[dict | None]:
#     results = []
#     for query in queries:
#         options = {
#             'q': query,
#             'format': 'json',
#             'h': 1,
#         }
#         r = requests.get(f'{BASE_URL}?{urlencode(options)}').json()
#         hit = r['result']['hits'].get('hit')
#         if hit is not None:
#             info = hit[0].get('info')
#             info['authors'] = [author['text'] for author in info['authors']['author']]
#             results.append(info)
#         else:
#             results.append(None)
#     return results