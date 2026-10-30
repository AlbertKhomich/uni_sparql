import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from rdflib import Literal, RDF, URIRef, XSD

from sblp_enrich.cli import main_missing_affiliations_openalex as cli
from sblp_enrich.cache_db import SqliteTableCache


class MissingAffiliationsExportTests(unittest.TestCase):
    def setUp(self):
        self.org = 'http://upbkg.data.dice-research.org/ror/04tsk2644'
        self.aid = 'https://openalex.org/I904495901'
        self.row = dict(institution=self.org, name='Fallback',
                        aliases={self.aid, 'https://ror.org/04tsk2644'}, openalex_id=self.aid)
        self.inst = dict(id=self.aid, ror='https://ror.org/04tsk2644',
                         display_name='Ruhr University Bochum', geo=dict(city='Bochum',
                         country='Germany', country_code='DE', latitude=51.48165, longitude=7.21648))
        self.payload = dict(status='ok', openalex_sameas=self.aid, institution=self.inst)

    def test_cached_export_preserves_subject_and_all_example_fields(self):
        with patch.object(cli.openalex, 'get_cached_affiliation_geo_payload', return_value=self.payload), \
             patch.object(cli, '_fetch_or_cache_affiliation_payload') as fetch:
            graph, report = cli.export([self.row], '', cache_only=True)
        fetch.assert_not_called()
        s = URIRef(self.org)
        self.assertEqual(set(graph.subjects()), {s})
        self.assertEqual(len(graph), 10)
        self.assertIn((s, RDF.type, cli.SCHEMA.Organization), graph)
        for country in ['Germany', 'DE']:
            self.assertIn((s, cli.SCHEMA.addressCountry, Literal(country)), graph)
        self.assertIn((s, cli.SCHEMA.continent, Literal('Europe')), graph)
        self.assertIn((s, cli.SCHEMA.latitude, Literal(51.48165, datatype=XSD.double)), graph)
        self.assertEqual(report[0]['status'], 'ok')

    def test_fetch_uses_existing_cache_workflow(self):
        with patch.object(cli, '_fetch_or_cache_affiliation_payload', return_value=(self.payload, False)) as fetch:
            graph, report = cli.export([self.row], 'test-key', sleep=0)
        fetch.assert_called_once_with(self.org, self.aid, 'test-key')
        self.assertEqual(len(graph), 10)
        self.assertFalse(report[0]['cache_hit'])

    def test_cache_miss_exports_no_invented_data(self):
        with patch.object(cli.openalex, 'get_cached_affiliation_geo_payload', return_value=None):
            graph, report = cli.export([self.row], '', cache_only=True)
        self.assertEqual(len(graph), 0)
        self.assertEqual(report[0]['status'], 'cache_miss')

    def test_fetched_response_is_persisted_and_reused(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = SqliteTableCache(str(Path(tmp) / 'cache.sqlite'), 'affiliation_geo_cache')
            try:
                with patch.object(cli.openalex, '_affiliation_geo_cache', cache), \
                     patch.object(cli.openalex, 'get_institution_by_openalex_id', return_value=self.inst) as fetch:
                    first, _ = cli.export([self.row], 'test-key', sleep=0)
                    second, report = cli.export([self.row], 'test-key', sleep=0)
                    fetch.assert_called_once()
                    self.assertEqual(set(first), set(second))
                    self.assertTrue(report[0]['cache_hit'])
                    self.assertEqual(cache.get(self.org)['institution'], self.inst)
            finally:
                cache.close()

    def test_not_found_is_reported(self):
        with patch.object(cli, '_fetch_or_cache_affiliation_payload', return_value=({'status': 'not_found'}, False)):
            graph, report = cli.export([self.row], 'test-key', sleep=0)
        self.assertEqual(len(graph), 0)
        self.assertEqual(report[0]['status'], 'not_found')


if __name__ == '__main__':
    unittest.main()
