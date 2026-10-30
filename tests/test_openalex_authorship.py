import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
import zlib

from sblp_enrich.cli.main_openalex_authorship import (
    BASE, VOCAB, cached_matches, resolve_authors, title_key, work_triples,
)


class AuthorshipTests(unittest.TestCase):
    def test_title_ambiguity_and_newest_cached_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'cache.sqlite'
            with sqlite3.connect(path) as db:
                db.execute('CREATE TABLE work_cache (k TEXT, v BLOB, t INTEGER)')
                for key, wid, timestamp in [('old', 'W1', 1), ('other', 'W2', 2), ('new', 'W1', 3)]:
                    work = dict(id='https://openalex.org/' + wid, title='A Paper', version=key)
                    db.execute('INSERT INTO work_cache VALUES (?, ?, ?)', (key, zlib.compress(json.dumps(work).encode()), timestamp))
            matches = cached_matches(path, {title_key('A—PAPER!')})['apaper']
            self.assertEqual(len(matches), 2)
            self.assertEqual(matches['https://openalex.org/W1']['version'], 'new')

    def test_author_order_is_not_used_and_collisions_are_skipped(self):
        work = {'authorships': [dict(author=dict(id='https://openalex.org/A1', display_name='G. Engels'))]}
        people = ['https://example.org/other', 'https://example.org/gregor']
        records = {people[0]: dict(ids=set(), names={'Other, Person'}), people[1]: dict(ids=set(), names={'Engels, Gregor'})}
        mapping, _ = resolve_authors(work, people, records, {})
        self.assertEqual(mapping['https://openalex.org/A1'], people[1])
        records[people[0]]['names'] = {'Engels, Georg'}
        mapping, reports = resolve_authors(work, people, records, {})
        self.assertEqual(mapping, {})
        self.assertEqual(reports[0][2], 'ambiguous')

    def test_affiliations_are_paper_scoped_and_missing_stays_unknown(self):
        aid, person = 'https://openalex.org/A1', BASE + 'id/person/uni/1'
        work = dict(id='https://openalex.org/W1', authorships=[dict(author=dict(id=aid), institutions=[
            dict(ror='https://ror.org/058kzsd48'), dict(id='https://openalex.org/I2')])])
        first = work_triples(BASE + 'paper/1', work, {aid: person}, mapped_only=True)
        second = work_triples(BASE + 'paper/2', work, {aid: person}, mapped_only=True)
        affiliations = [line for line in first if '<' + VOCAB + 'affiliation>' in line]
        self.assertEqual(len(affiliations), 2)
        self.assertTrue(all(line.startswith('<' + BASE + 'paper/1/authorship/1>') for line in affiliations))
        self.assertTrue(all(not line.startswith('<' + person + '>') for line in affiliations))
        self.assertFalse(set(affiliations) & second)
        self.assertEqual(first, work_triples(BASE + 'paper/1', work, {aid: person}, mapped_only=True))
        work['authorships'][0]['institutions'] = []
        self.assertFalse(any('<' + VOCAB + 'affiliation>' in line for line in work_triples(BASE + 'paper/1', work, {aid: person})))


if __name__ == '__main__':
    unittest.main()
