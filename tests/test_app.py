import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app as web
from ollama_manager import OllamaManager, OllamaError


@pytest.fixture
def client():
    with TestClient(web.app, base_url="http://127.0.0.1:8080") as c:
        c.headers["X-Ollama-Web-Token"] = web.TOKEN
        yield c


def test_home_and_assets(client):
    response = client.get("/")
    assert response.status_code == 200
    assert web.TOKEN in response.text
    assert "frame-ancestors 'none'" in response.headers['content-security-policy']
    assert client.get('/static/app.js').status_code == 200


@pytest.mark.parametrize('headers', [
    {'Host':'evil.example'}, {'Origin':'https://evil.example'},
    {'Origin':'http://localhost:8080'},
])
def test_cross_origin_and_rebinding_blocked(client, headers):
    assert client.get('/api/status',headers=headers).status_code == 403


def test_mutation_requires_session_token(client):
    client.headers.pop('X-Ollama-Web-Token')
    assert client.post('/api/control',json={'action':'start'}).status_code == 403


@pytest.mark.parametrize('endpoint,body', [
    ('/api/models/delete',{'model':'llama3.2:latest'}),
    ('/api/models/unload',{'model':'llama3.2:latest','confirm':False}),
    ('/api/control',{'action':'stop'}),
    ('/api/control',{'action':'restart'}),
    ('/api/control',{'action':'cmd /c whoami'}),
    ('/api/models/pull',{'model':'model & whoami'}),
    ('/api/models/pull',{'model':'$(whoami)'}),
])
def test_confirmation_and_input_validation(client, endpoint, body):
    assert client.post(endpoint,json=body).status_code in (400,422)


def test_model_discovery_preserves_dynamic_names(client):
    with patch.object(web.manager,'request',AsyncMock(return_value={'models':[{'name':'custom-model:7b'},{'name':'x:cloud'}]})):
        models = client.get('/api/models').json()['models']
        assert models[0]['name'] == 'custom-model:7b'
        assert not models[0]['cloud'] and models[1]['cloud']


def test_delete_and_unload_use_fixed_api(client):
    with patch.object(web.manager,'request',AsyncMock(return_value={})) as request:
        assert client.post('/api/models/delete',json={'model':'test:1','confirm':True}).status_code == 200
        request.assert_awaited_with('DELETE','/api/delete',{'model':'test:1'})
        assert client.post('/api/models/unload',json={'model':'test:1','confirm':True}).status_code == 200
        request.assert_awaited_with('POST','/api/generate',{'model':'test:1','keep_alive':0,'stream':False})


def test_offline_error_is_actionable(client):
    with patch.object(web.manager,'request',AsyncMock(side_effect=OllamaError('Start Ollama and try again.'))):
        result = client.get('/api/models')
        assert result.status_code == 503 and 'Start Ollama' in result.json()['detail']


def test_unknown_and_cloud_models_rejected(client):
    with patch.object(web.manager,'request',AsyncMock(return_value={'models':[{'name':'x:cloud'}]})):
        for name in ['missing','x:cloud']:
            assert client.post('/api/chat',json={'model':name,'messages':[{'role':'user','content':'hello'}]}).status_code == 400


def test_chat_stream_and_thinking(client):
    async def events(path, payload):
        assert path == '/api/chat' and payload['messages'][0]['content'] == 'hello'
        for event in [{'message':{'thinking':'reason'}},{'message':{'content':'Hi'}},{'done':True,'eval_count':1}]:
            yield json.dumps(event)+'\n'
    with patch.object(web.manager,'request',AsyncMock(return_value={'models':[{'name':'local:1'}]})), patch.object(web.manager,'stream',events):
        response = client.post('/api/chat',json={'model':'local:1','messages':[{'role':'user','content':'hello'}]})
        events_out = [json.loads(line) for line in response.text.splitlines()]
        assert events_out[0]['message']['thinking'] == 'reason' and events_out[-1]['done']


def test_pull_stream_progress(client):
    async def events(path, payload):
        assert path == '/api/pull' and payload['stream'] is True
        yield '{"status":"pulling","total":100,"completed":50}\n'
        yield '{"status":"success"}\n'
    with patch.object(web.manager,'stream',events):
        response = client.post('/api/models/pull',json={'model':'local:1'})
        assert json.loads(response.text.splitlines()[0])['completed'] == 50


def test_manager_http_error_and_stream_error():
    manager = OllamaManager()
    transport = httpx.MockTransport(lambda request: httpx.Response(404,json={'error':'model missing'}))
    def client(streaming=False):
        return httpx.AsyncClient(transport=transport,base_url='http://127.0.0.1:11434')
    async def run():
        with patch.object(manager,'client',client):
            with pytest.raises(OllamaError,match='model missing'):
                await manager.request('GET','/api/tags')
            events = [json.loads(line) async for line in manager.stream('/api/chat',{})]
            assert 'model missing' in events[0]['error']
    asyncio.run(run())


def test_stream_close_releases_upstream():
    class ResponseStream(httpx.AsyncByteStream):
        closed = False
        async def __aiter__(self):
            yield b'{"message":{"content":"first"}}\n'
            await asyncio.sleep(100)
        async def aclose(self):
            self.closed = True
    upstream = ResponseStream()
    manager = OllamaManager()
    transport = httpx.MockTransport(lambda request: httpx.Response(200,stream=upstream))
    async def run():
        with patch.object(manager,'client',lambda streaming=False: httpx.AsyncClient(transport=transport,base_url='http://127.0.0.1:11434')):
            generator = manager.stream('/api/chat',{})
            await anext(generator)
            await generator.aclose()
            assert upstream.closed
    asyncio.run(run())


def test_process_matching_excludes_other_users_and_unrelated_paths():
    manager = OllamaManager()
    root = Path(__file__).resolve().parent
    executable = root/'ollama.exe'
    def proc(name, path, owner):
        result = MagicMock()
        result.info = {'pid':1,'name':name,'exe':str(path),'username':owner}
        return result
    valid = proc('ollama.exe',executable,'me')
    candidates = [valid,proc('ollama.exe',root.parent/'ollama.exe','me'),
                  proc('ollama.exe',executable,'someone else'),proc('other.exe',root/'other.exe','me')]
    with patch.object(manager,'executable',return_value=executable), patch('ollama_manager.psutil.Process') as own, patch('ollama_manager.psutil.process_iter',return_value=candidates):
        own.return_value.username.return_value = 'me'
        assert manager.processes() == [valid]


def test_start_uses_fixed_argv_no_shell_and_loopback_environment(tmp_path):
    manager = OllamaManager()
    executable = tmp_path/'ollama.exe'
    with patch.object(manager,'executable',return_value=executable), patch.object(manager,'alive',AsyncMock(side_effect=[None,{'version':'test'}])), patch('ollama_manager.subprocess.Popen') as popen:
        assert asyncio.run(manager.start()) == 'Ollama started.'
        args, kwargs = popen.call_args
        assert args[0] == [str(executable),'serve'] and kwargs['shell'] is False
        assert kwargs['env']['OLLAMA_HOST'] == '127.0.0.1:11434'
        assert kwargs['env']['OLLAMA_NO_CLOUD'] == '1'


def test_executable_permission_error_is_not_fatal():
    with patch('ollama_manager.shutil.which',return_value=None), patch('ollama_manager.Path.is_file',side_effect=PermissionError()):
        assert OllamaManager().executable() is None


def test_invalid_api_json_has_actionable_error():
    manager = OllamaManager()
    transport = httpx.MockTransport(lambda request: httpx.Response(200,text='not json'))
    async def run():
        with patch.object(manager,'client',lambda streaming=False: httpx.AsyncClient(transport=transport,base_url='http://127.0.0.1:11434')):
            with pytest.raises(OllamaError,match='invalid response'):
                await manager.request('GET','/api/tags')
    asyncio.run(run())
