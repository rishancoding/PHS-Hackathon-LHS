"""Protocol, error and dashboard tests for address suggestions without billable requests."""
from io import BytesIO
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from address_autocomplete import address_predictions
from roofready_app import create_app
from solar_address import InputError


class PredictionTests(unittest.TestCase):
    @patch('address_autocomplete.urlopen')
    def test_predictions_use_server_key_country_bounds_and_narrow_fields(self, open_url):
        prediction = {'placePrediction': {'text': {'text': '123 Main St, Boston, MA, USA'},
                      'structuredFormat': {'mainText': {'text': '123 Main St'},
                                           'secondaryText': {'text': 'Boston, MA, USA'}}}}
        open_url.return_value.__enter__.return_value = BytesIO(json.dumps({'suggestions': [prediction]}).encode())
        items = address_predictions('123 Main', 'test-key')
        self.assertEqual(items[0]['address'], '123 Main St, Boston, MA, USA')
        self.assertEqual(items[0]['label'], '123 Main St')
        req = open_url.call_args.args[0]
        self.assertEqual(req.method, 'POST')
        self.assertNotIn('test-key', req.full_url)
        self.assertEqual(req.get_header('X-goog-api-key'), 'test-key')
        self.assertEqual(json.loads(req.data)['includedRegionCodes'], ['us'])
        self.assertIn('locationRestriction', json.loads(req.data))
        self.assertNotIn('test-key', json.dumps(items))

    @patch('address_autocomplete.urlopen')
    def test_invalid_queries_never_call_google(self, open_url):
        for query in (None, '', '12', 'x'*201):
            with self.subTest(query=query), self.assertRaises(InputError):
                address_predictions(query, 'test-key')
        open_url.assert_not_called()

    @patch('address_autocomplete.urlopen')
    def test_google_failure_is_actionable_and_does_not_echo_credentials(self, open_url):
        open_url.side_effect = HTTPError('https://places.googleapis.com/', 403, 'secret-test-key', {}, None)
        with self.assertRaises(InputError) as caught:
            address_predictions('123 Main', 'secret-test-key')
        self.assertIn('Places API (New)', str(caught.exception))
        self.assertNotIn('secret-test-key', str(caught.exception))
        open_url.side_effect = URLError('secret-test-key')
        with self.assertRaises(InputError) as caught:
            address_predictions('123 Main', 'secret-test-key')
        self.assertIn('full address', str(caught.exception))
        self.assertNotIn('secret-test-key', str(caught.exception))

    @patch('address_autocomplete.urlopen')
    def test_empty_or_malformed_predictions_are_handled(self, open_url):
        open_url.return_value.__enter__.return_value = BytesIO(json.dumps({'suggestions': [None, {}, {'placePrediction': 3}]}).encode())
        self.assertEqual(address_predictions('123 Main', 'test-key'), [])
        open_url.return_value.__enter__.return_value = BytesIO(b'not-json')
        with self.assertRaises(InputError):
            address_predictions('123 Main', 'test-key')


class AddressRouteTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        saved = Path(self.folder.name)/'saved'; saved.mkdir()
        result = {'request': {'address': '123 Main St, Boston, MA 02110, USA', 'radius_meters': 100},
                  'analysis': {'flux_coverage_percent': 100}, 'summary': {}, 'roofs': []}
        (saved/'results.json').write_text(json.dumps(result))
        self.app = create_app(self.folder.name); self.client = self.app.test_client()
        self.origin = {'Origin': 'http://localhost'}

    def tearDown(self):
        self.app.extensions['roofready_executor'].shutdown(wait=True)
        self.folder.cleanup()

    @patch('roofready_app.address_predictions')
    def test_origin_validation_success_and_static_assets(self, predict):
        self.assertEqual(self.client.post('/api/address-suggestions', json={'query': 'Main'}).status_code, 403)
        for body in ([], {'query': 'a'}, {'query': 'x'*201}, {'query': 'Main', 'api_key': []}):
            self.assertEqual(self.client.post('/api/address-suggestions', headers=self.origin, json=body).status_code, 400)
        predict.assert_not_called()
        predict.return_value = [{'address': '123 Main St, Boston, MA, USA', 'label': '123 Main St', 'detail': 'Boston, MA', 'source': 'google'}]
        response = self.client.post('/api/address-suggestions', headers=self.origin, json={'query': 'Main', 'api_key': 'test-key'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['suggestions'][0]['source'], 'google')
        predict.assert_called_once_with('Main', 'test-key')
        self.assertNotIn('test-key', response.get_data(as_text=True))
        for asset in ('roofready-mark.svg', 'address.js', 'loading.js'):
            response = self.client.get('/static/'+asset)
            self.assertEqual(response.status_code, 200); response.close()
        page = self.client.get('/').get_data(as_text=True)
        self.assertIn('role="combobox"', page)
        self.assertIn('id="loading-screen"', page)

    @patch('roofready_app.address_predictions', side_effect=InputError('Live suggestions unavailable; type a full address.'))
    def test_service_failure_keeps_recent_address_fallback(self, predict):
        response = self.client.post('/api/address-suggestions', headers=self.origin, json={'query': 'Main', 'api_key': 'test-key'})
        data = response.get_json()
        self.assertEqual(data['suggestions'][0]['source'], 'recent')
        self.assertIn('full address', data['notice'])
        self.assertEqual(self.client.get('/api/jobs/saved').get_json()['status'], 'complete')

    @patch.dict(os.environ, {'GOOGLE_MAPS_API_KEY': ''})
    @patch('roofready_app.DEFAULT_API_KEY', '')
    @patch('roofready_app.address_predictions')
    def test_without_a_key_only_saved_addresses_are_used(self, predict):
        data = self.client.post('/api/address-suggestions', headers=self.origin, json={'query': 'Main'}).get_json()
        self.assertEqual(data['suggestions'][0]['address'], '123 Main St, Boston, MA 02110, USA')
        predict.assert_not_called()


if __name__ == '__main__':
    unittest.main()
