import io
import json
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs

from sblp_enrich.cli.main_authorship_missing_affiliations import existing_institutions


class MissingAffiliationsTests(unittest.TestCase):
    def test_query_is_posted_without_expanding_endpoint_url(self):
        endpoint = 'https://example.org/sparql?dataset=kg'
        org = 'https://example.org/institution/1'
        alias = 'https://ror.org/example'
        institutions = {org: {'aliases': {org, alias}}}
        response = {'results': {'bindings': [{'institution': {'value': org}}]}}
        with patch('sblp_enrich.cli.main_authorship_missing_affiliations.urlopen',
                   return_value=io.BytesIO(json.dumps(response).encode())) as mocked:
            self.assertEqual(existing_institutions(endpoint, institutions), {org})
        request = mocked.call_args.args[0]
        self.assertEqual(request.get_method(), 'POST')
        self.assertEqual(request.full_url, endpoint)
        self.assertEqual(request.get_header('Content-type'), 'application/x-www-form-urlencoded')
        query = parse_qs(request.data.decode())['query'][0]
        self.assertIn(f'(<{org}> <{alias}>)', query)
        self.assertIn('SELECT DISTINCT ?institution', query)


if __name__ == '__main__':
    unittest.main()
