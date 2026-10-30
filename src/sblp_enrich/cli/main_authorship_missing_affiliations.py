"""List exported affiliation institutions absent from a SPARQL endpoint."""
import argparse
import csv
import json
from pathlib import Path
import re
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from sblp_enrich.cli.main_openalex_authorship import ROOT, SCHEMA, VOCAB, iri


def read_institutions(path):
    triples = []
    pattern = re.compile(r'^<([^>]+)>\s+<([^>]+)>\s+(.+)\s+\.\s*$')
    with path.open(encoding='utf-8') as stream:
        for line in stream:
            if not line.strip() or line.lstrip().startswith('#'):
                continue
            match = pattern.match(line)
            if not match:
                raise ValueError('Expected N-Triples from the authorship exporter')
            triples.append(match.groups())
    institutions = {obj[1:-1]: {'names': set(), 'aliases': {obj[1:-1]}, 'authorships': set()}
                    for _, predicate, obj in triples if predicate == VOCAB + 'affiliation' and obj.startswith('<')}
    for subject, predicate, obj in triples:
        if predicate == VOCAB + 'affiliation' and obj.startswith('<'):
            institutions[obj[1:-1]]['authorships'].add(subject)
        if subject in institutions:
            if predicate == SCHEMA + 'name':
                institutions[subject]['names'].add(json.JSONDecoder().raw_decode(obj)[0])
            elif predicate == SCHEMA + 'sameAs' and obj.startswith('<'):
                institutions[subject]['aliases'].add(obj[1:-1])
    return institutions


def existing_institutions(endpoint, institutions, batch_size=50):
    found = set()
    items = sorted(institutions)
    for start in range(0, len(items), batch_size):
        values = '\n'.join(f'({iri(org)} {iri(alias)})'
                           for org in items[start:start + batch_size]
                           for alias in sorted(institutions[org]['aliases']))
        # Any occurrence counts as present, including an identity link from a
        # differently named KG node. Search default and named graphs.
        query = '''SELECT DISTINCT ?institution WHERE {
          VALUES (?institution ?candidate) { %s }
          { ?candidate ?p ?o } UNION { ?s ?p ?candidate }
          UNION { GRAPH ?g { ?candidate ?p ?o } }
          UNION { GRAPH ?g { ?s ?p ?candidate } }
        }''' % values
        request = Request(endpoint, data=urlencode({'query': query}).encode('utf-8'),
                          headers={'Accept': 'application/sparql-results+json',
                                   'Content-Type': 'application/x-www-form-urlencoded'},
                          method='POST')
        with urlopen(request, timeout=90) as response:
            result = json.load(response)
        bindings = result['results']['bindings']
        found.update(row['institution']['value'] for row in bindings)
    return found


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input-nt', type=Path, default=ROOT / 'data/authorship/authorships.nt')
    parser.add_argument('--endpoint', default='http://upbkg.data.dice-research.org/sparql')
    parser.add_argument('--out-csv', type=Path, default=ROOT / 'data/authorship/missing_affiliations.csv')
    args = parser.parse_args()
    if args.input_nt.resolve() == args.out_csv.resolve():
        parser.error('Output must differ from input')
    institutions = read_institutions(args.input_nt)
    present = existing_institutions(args.endpoint, institutions)
    # Only write after every query succeeded; errors must not imply absence.
    args.out_csv.parent.mkdir(parents=True, exist_ok=True)
    with args.out_csv.open('w', encoding='utf-8', newline='') as stream:
        writer = csv.writer(stream)
        writer.writerow(['institution', 'name', 'external_ids', 'authorships'])
        for org in sorted(institutions.keys() - present):
            data = institutions[org]
            writer.writerow([org, '; '.join(sorted(data['names'])),
                             '; '.join(sorted(data['aliases'] - {org})),
                             '; '.join(sorted(data['authorships']))])
    print(f'Checked {len(institutions)} institutions: {len(present)} present, {len(institutions) - len(present)} missing.')
    print(f'Missing affiliations: {args.out_csv}')


if __name__ == '__main__':
    main()
