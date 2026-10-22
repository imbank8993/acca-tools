import time
import pytest
from flask import Flask
from routes.auth import bp

@pytest.fixture
def client():
    app = Flask(__name__)
    app.config.update(TESTING=True, SECRET_KEY='logout-test-only', SSO_ENABLED=True,
                      ACCA_APP_URL='https://acca.icgowa.sch.id', SSO_MAX_AGE_SECONDS=900)
    app.register_blueprint(bp)
    app.add_url_rule('/protected', view_func=lambda: 'private')
    with app.test_client() as client:
        with client.session_transaction() as saved:
            saved['user'] = {'name': 'Fixture', 't': int(time.time())}
        yield client

def test_acca_logout_clears_the_current_tools_session(client):
    response = client.post('/auth/single-logout', headers={'Origin': 'https://acca.icgowa.sch.id'})
    assert response.status_code == 200 and response.json == {'ok': True}
    assert response.headers['Access-Control-Allow-Origin'] == 'https://acca.icgowa.sch.id'
    assert response.headers['Access-Control-Allow-Credentials'] == 'true'
    assert 'no-store' in response.headers['Cache-Control']
    with client.session_transaction() as saved:
        assert 'user' not in saved
    assert client.get('/protected').location == 'https://acca.icgowa.sch.id/api/sso/tools'

@pytest.mark.parametrize('origin', [None, 'https://evil.invalid', 'https://acca.icgowa.sch.id.evil.invalid'])
def test_untrusted_origins_preserve_the_session(client, origin):
    response = client.post('/auth/single-logout', headers={'Origin': origin} if origin else {})
    assert response.status_code == 403
    with client.session_transaction() as saved:
        assert saved['user']['name'] == 'Fixture'

def test_preflight_preserves_the_session(client):
    response = client.options('/auth/single-logout', headers={'Origin': 'https://acca.icgowa.sch.id'})
    assert response.status_code == 204
    with client.session_transaction() as saved:
        assert 'user' in saved

def test_get_cannot_mutate_the_session(client):
    assert client.get('/auth/single-logout').status_code == 405
    with client.session_transaction() as saved:
        assert 'user' in saved

def test_already_logged_out_browser_is_acknowledged(client):
    with client.session_transaction() as saved:
        saved.clear()
    response = client.post('/auth/single-logout', headers={'Origin': 'https://acca.icgowa.sch.id'})
    assert response.status_code == 200 and response.json['ok']
