import socket
import threading
import time

import pytest
import requests
import uvicorn

from app import quant_server


@pytest.fixture(scope='module')
def client():
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(quant_server.app, log_level='error'))
    thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(.01)
    assert server.started
    class Client:
        def get(self, path):
            return requests.get(f'http://127.0.0.1:{port}{path}', timeout=10)
        def post(self, path, **kwargs):
            return requests.post(f'http://127.0.0.1:{port}{path}', timeout=10, **kwargs)
    try:
        yield Client()
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        sock.close()


def test_entrypoint_serves_scripts_and_css_without_external_network(client):
    response = client.get('/')
    assert response.status_code == 200
    assert './style.css' in response.text and './app.js' in response.text
    for path, content_type in [('/style.css','text/css'),('/app.js','application/javascript')]:
        asset = client.get(path)
        assert asset.status_code == 200
        assert asset.headers['content-type'].startswith(content_type)


def test_invalid_dates_and_empty_selection_return_validation_errors(client):
    assert client.post('/api/analyze', json={'dataset_ids':[], 'factors':[{'id':'ma'}], 'start':'2025-01-01','end':'2025-02-01'}).status_code == 422
    assert client.post('/api/analyze', json={'dataset_ids':['x'], 'factors':[{'id':'ma'}], 'start':'2025-02-01','end':'2025-01-01'}).status_code == 422


def test_divergence_available_in_catalog_manual_and_radar(client, monkeypatch):
    from dataclasses import replace
    from test_quant_analysis import sample
    data = sample(length=180)
    data.dataset = replace(data.dataset, filters={'symbol':'000001'}, source='baostock_history_k_data')
    duplicate = replace(data.dataset, id='duplicate', source='openbb_yfinance', filters={'symbol':'000001.SZ'})
    monkeypatch.setattr(quant_server, 'catalog', lambda: [data.dataset, duplicate])
    monkeypatch.setattr(quant_server.repository, 'load', lambda *args: data)
    monkeypatch.setattr(quant_server.repository, 'calendar', lambda: data.frame.index)
    monkeypatch.setattr(quant_server, 'database_metadata', lambda repository: ([], []))
    response = client.get('/api/catalog').json()
    assert any(factor['id']=='macd_divergence' for factor in response['factors'])
    assert client.get('/api/divergence-defaults').json()['dataset_ids'] == ['test']
    request={'dataset_ids':['test','duplicate'], 'factors':[{'id':'macd_divergence'}],
             'start':data.dataset.first_date,'end':data.dataset.last_date}
    manual = client.post('/api/analyze',json=request)
    radar = client.post('/api/scan',json=request)
    assert manual.status_code == radar.status_code == 200
    assert len(manual.json()['results']) == len(radar.json()['results']) == 1
    assert manual.json()['results'][0]['signals'] == radar.json()['results'][0]['signals']
    assert manual.json()['results'][0]['history']
    assert radar.json()['results'][0]['history'] == []
