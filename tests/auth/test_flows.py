# Copyright 2023 D-Wave Systems Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import time
import threading
import unittest
from functools import partial
from unittest import mock
from urllib.parse import urlsplit, parse_qsl, urljoin

import requests
import requests_mock

from dwave.cloud.auth.flows import AuthFlow, LeapAuthFlow
from dwave.cloud.auth.config import OCEAN_SDK_CLIENT_ID, OCEAN_SDK_SCOPES
from dwave.cloud.auth.creds import Credentials
from dwave.cloud.config import ClientConfig
from dwave.cloud.api.constants import DEFAULT_LEAP_API_ENDPOINT


class TestAuthFlow(unittest.TestCase):

    def setUp(self):
        self.client_id = '123'
        self.scopes = ('scope-a', 'scope-b')
        self.redirect_uri_oob = 'oob'
        self.authorization_endpoint = 'https://example.com/authorize'
        self.token_endpoint = 'https://example.com/token'
        self.token = dict(access_token='123', refresh_token='456', id_token='789')
        self.creds = Credentials(create=False)
        self.leap_api_endpoint = 'https://example.com/leap/api'

        self.test_args = dict(
            client_id=self.client_id,
            scopes=self.scopes,
            redirect_uri=self.redirect_uri_oob,
            authorization_endpoint=self.authorization_endpoint,
            token_endpoint=self.token_endpoint,
            leap_api_endpoint=self.leap_api_endpoint,
            creds=self.creds)

    def test_auth_url(self):
        flow = AuthFlow(**self.test_args)

        url = flow.get_authorization_url()
        q = dict(parse_qsl(urlsplit(url).query))

        self.assertTrue(url.startswith(self.authorization_endpoint))
        self.assertEqual(q['response_type'], 'code')
        self.assertEqual(q['client_id'], self.client_id)
        self.assertEqual(q['redirect_uri'], self.redirect_uri_oob)
        self.assertEqual(q['scope'], ' '.join(self.scopes))
        self.assertIn('state', q)
        # pkce
        self.assertIn('code_challenge', q)
        self.assertEqual(q['code_challenge_method'], 'S256')

    def test_fetch_token_state(self):
        flow = AuthFlow(**self.test_args)

        # generate auth request (state)
        _ = flow.get_authorization_url()

        # try exchanging the code with wrong state
        with self.assertRaisesRegex(ValueError, "State mismatch"):
            flow.fetch_token(code='not important', state='invalid')

    @requests_mock.Mocker()
    def test_fetch_token(self, m):
        # mock the token_endpoint
        code = '123456'
        expected_params = dict(
            grant_type='authorization_code', client_id=self.client_id,
            redirect_uri=self.redirect_uri_oob, code=code)

        def post_body_matcher(request):
            params = dict(parse_qsl(request.text))
            return params == expected_params

        m.get(requests_mock.ANY, status_code=404)
        m.post(requests_mock.ANY, status_code=404)
        m.post(self.token_endpoint, additional_matcher=post_body_matcher, json=self.token)

        # reset creds
        self.creds.clear()

        # verify token fetch flow
        flow = AuthFlow(**self.test_args)

        response = flow.fetch_token(code=code)
        self.assertEqual(response, self.token)

        # verify token proxy to oauth2 session
        self.assertEqual(flow.token, self.token)

        # verify token saved to creds
        self.assertEqual(flow.creds[flow.leap_api_endpoint], self.token)

    def test_token_setter(self):
        flow = AuthFlow(**self.test_args)

        self.assertIsNone(flow.session.token)

        self.creds.clear()

        flow.token = self.token

        self.assertIsNotNone(flow.session.token)
        self.assertEqual(flow.session.token['access_token'], self.token['access_token'])

        # verify token is persisted
        self.assertEqual(self.creds[self.leap_api_endpoint], self.token)

    def test_refresh_token(self):
        flow = AuthFlow(**self.test_args)
        self.creds.clear()

        with mock.patch.object(flow.session, 'refresh_token',
                               return_value=self.token) as m:
            flow.refresh_token()

        m.assert_called_once_with(url=flow.token_endpoint)

        # verify token is persisted
        self.assertEqual(self.creds[self.leap_api_endpoint], self.token)

    def test_ensure_active_token(self):
        flow = AuthFlow(**self.test_args)
        flow.token = self.token

        with mock.patch.object(flow.session, 'ensure_active_token') as m:
            flow.ensure_active_token()

        m.assert_called_once_with(token=self.token)

    def test_session_config(self):
        config = dict(
            cert='/path/to/cert',
            headers={'X-Field': 'Value'},
            proxies=dict(https='socks5://localhost'),
            timeout=60,
            verify=False)

        def verify(session, config):
            self.assertEqual(session.cert, config['cert'])
            self.assertEqual({**session.headers, **config['headers']}, session.headers)
            self.assertEqual(session.proxies, config['proxies'])
            self.assertEqual(session.default_timeout, config['timeout'])
            self.assertEqual(session.verify, config['verify'])

        with self.subTest('on construction'):
            flow = AuthFlow(session_config=config, **self.test_args)
            verify(flow.session, config)

        with self.subTest('post-construction'):
            flow = AuthFlow(**self.test_args)
            flow.update_session(config)
            verify(flow.session, config)

    def test_token_expires_soon(self):
        flow = AuthFlow(**self.test_args)
        now = time.time()

        with mock.patch.object(flow.session.token_auth, 'token',
                               dict(expires_at=now + 59)):
            self.assertTrue(flow.token_expires_soon())
            self.assertTrue(flow.token_expires_soon(within=60))
            self.assertFalse(flow.token_expires_soon(within=0))


class TestLeapAuthFlow(unittest.TestCase):

    def test_from_default_config(self):
        config = ClientConfig()

        flow = LeapAuthFlow.from_config_model(config)

        # endpoint urls are generated?
        prefix = urljoin(DEFAULT_LEAP_API_ENDPOINT, '/')
        self.assertTrue(flow.authorization_endpoint.startswith(prefix))
        self.assertTrue(flow.token_endpoint.startswith(prefix))

    def test_from_minimal_config(self):
        config = ClientConfig(leap_api_endpoint='https://example.com/leap')

        flow = LeapAuthFlow.from_config_model(config)

        # endpoint urls are generated?
        self.assertTrue(flow.authorization_endpoint.startswith(config.leap_api_endpoint))
        self.assertTrue(flow.token_endpoint.startswith(config.leap_api_endpoint))
        # Leap-specific:
        self.assertTrue(flow.authorization_endpoint.endswith('authorize'))
        self.assertTrue(flow.token_endpoint.endswith('token'))
        self.assertEqual(flow.leap_api_endpoint, config.leap_api_endpoint)

        self.assertEqual(flow.client_id, OCEAN_SDK_CLIENT_ID)
        self.assertEqual(flow.scopes, ' '.join(OCEAN_SDK_SCOPES))
        self.assertEqual(flow.redirect_uri, LeapAuthFlow._OOB_REDIRECT_URI)
        self.assertIsNotNone(flow.creds)

    def test_from_minimal_config_with_overrides(self):
        config = ClientConfig(leap_api_endpoint='https://example.com/leap')
        client_id = '123'
        scopes = ['email']
        redirect_uri = 'https://example.com/callback'

        flow = LeapAuthFlow.from_config_model(
            config=config, client_id=client_id,
            scopes=scopes, redirect_uri=redirect_uri)

        self.assertEqual(flow.client_id, client_id)
        self.assertEqual(flow.scopes, ' '.join(scopes))
        self.assertEqual(flow.redirect_uri, redirect_uri)

    def test_from_common_config(self):
        config = ClientConfig(leap_api_endpoint='https://example.com/leap',
                              headers=dict(injected='value'), request_timeout=10)

        flow = LeapAuthFlow.from_config_model(config)

        self.assertEqual(flow.session.headers.get('injected'), 'value')
        self.assertEqual(flow.session.default_timeout, 10)

    def test_client_id_from_config(self):
        client_id = '123'
        config = ClientConfig(leap_api_endpoint='https://example.com/leap',
                              leap_client_id=client_id)

        flow = LeapAuthFlow.from_config_model(config)

        self.assertEqual(flow.client_id, client_id)


class TestLeapAuthFlowRunners(unittest.TestCase):

    @mock.patch('click.echo', return_value=None)
    def test_oob(self, m):
        config = ClientConfig(leap_api_endpoint='https://example.com/leap')
        flow = LeapAuthFlow.from_config_model(config)

        mock_code = '1234'

        with mock.patch('click.prompt', return_value=mock_code):
            with mock.patch.object(flow, 'fetch_token') as fetch_token:
                flow.run_oob_flow()
                fetch_token.assert_called_once_with(code=mock_code)

    @mock.patch('click.echo', return_value=None)
    def test_redirect(self, m):
        config = ClientConfig(leap_api_endpoint='https://example.com/leap')
        flow = LeapAuthFlow.from_config_model(config)

        mock_code = '1234'

        ctx = {}
        ready = threading.Event()
        def url_open(url, *args, **kwargs):
            ctx.update(parse_qsl(urlsplit(url).query))
            ready.set()

        with mock.patch.object(flow, 'fetch_token') as fetch_token:
            f = threading.Thread(
                target=partial(flow.run_redirect_flow, open_browser=url_open))
            f.start()

            ready.wait()
            response = requests.get(ctx['redirect_uri'],
                                    params=dict(code=mock_code, state=ctx['state'])).text
            self.assertEqual(response, flow._REDIRECT_DONE_MSG)

            f.join()
            fetch_token.assert_called_once_with(code=mock_code, state=ctx['state'])
