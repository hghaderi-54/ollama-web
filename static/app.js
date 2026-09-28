'use strict';
const $ = (id) => document.getElementById(id);
const token = document.querySelector('meta[name="ollama-web-token"]').content;
const STORE = 'ollama-web.history.v1';
let chats = [], current = null, installed = [], online = false, chatController = null, pullController = null;
let refreshing = false, toastTimer, lifecycleBusy = false;

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}
function toast(message, failure = false) {
  $('toast').textContent = message;
  $('toast').className = 'toast' + (failure ? ' failure' : '');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => $('toast').classList.add('hidden'), failure ? 10000 : 4500);
}
function save() {
  try { localStorage.setItem(STORE, JSON.stringify(chats)); }
  catch { toast('Browser storage is full or unavailable. Export your conversations now; new changes may not survive a reload.', true); }
}
function load() {
  try {
    const value = JSON.parse(localStorage.getItem(STORE) || '[]');
    if (!Array.isArray(value)) throw new Error();
    chats = value.filter(c => typeof c.id === 'string' && typeof c.title === 'string' && Array.isArray(c.messages));
    for (const c of chats) c.messages = c.messages.filter(m => ['user', 'assistant'].includes(m.role) && typeof m.content === 'string');
  } catch { toast('Saved history could not be read. Existing browser data has not been changed.', true); }
}
async function api(path, payload, signal) {
  const response = await fetch(path, {method: payload === undefined ? 'GET' : 'POST',
    headers: {'Content-Type': 'application/json', 'X-Ollama-Web-Token': token},
    body: payload === undefined ? undefined : JSON.stringify(payload), signal});
  if (!response.ok) {
    let data; try { data = await response.json(); } catch { data = {}; }
    const detail = typeof data.detail === 'string' ? data.detail : 'Invalid request. Check the model name or start a shorter conversation.';
    throw new Error(detail || `Request failed (${response.status}).`);
  }
  return response;
}
async function json(path, payload) { return (await api(path, payload)).json(); }
async function stream(path, payload, signal, onEvent) {
  const response = await api(path, payload, signal);
  const reader = response.body.getReader(), decoder = new TextDecoder();
  let buffer = '';
  function dispatch(line) {
    if (!line.trim()) return;
    const event = JSON.parse(line);
    if (event.error) throw new Error(event.error);
    onEvent(event);
  }
  try {
    while (true) {
      const {value, done} = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, {stream: true});
      let newline;
      while ((newline = buffer.indexOf('\n')) >= 0) {
        dispatch(buffer.slice(0, newline)); buffer = buffer.slice(newline + 1);
      }
    }
    buffer += decoder.decode();
    dispatch(buffer);
  } finally { await reader.cancel().catch(() => {}); reader.releaseLock(); }
}
function confirmAction(title, description, label = 'Confirm') {
  if ($('confirm-dialog').open) return Promise.resolve(false);
  $('confirm-title').textContent = title; $('confirm-description').textContent = description;
  $('confirm-button').textContent = label;
  $('confirm-dialog').returnValue = 'cancel'; $('confirm-dialog').showModal();
  return new Promise(resolve => $('confirm-dialog').addEventListener('close', () => resolve($('confirm-dialog').returnValue === 'confirm'), {once:true}));
}
function bytes(n) {
  if (!Number.isFinite(n)) return '—';
  if (n < 1024 ** 3) return `${(n / 1024 ** 2).toFixed(0)} MB`;
  return `${(n / 1024 ** 3).toFixed(1)} GB`;
}
function view(name) {
  for (const section of document.querySelectorAll('.view')) section.classList.toggle('hidden', section.id !== `${name}-view`);
  for (const button of document.querySelectorAll('.nav')) button.classList.toggle('active', button.dataset.view === name);
  $('page-subtitle').textContent = {chat:'Chat', models:'Model library', system:'System'}[name];
  $('sidebar').classList.remove('open');
}
function history() {
  $('history').replaceChildren();
  if (!chats.length) $('history').append(el('p', 'history-empty', 'A fresh start. Your conversations will appear here.'));
  for (const c of [...chats].sort((a,b) => b.updated - a.updated)) {
    const button = el('button', c.id === current?.id ? 'selected' : '', c.title);
    button.title = c.title;
    button.onclick = () => {
      if (chatController) return toast('Stop the current response before switching conversations.');
      current = c; selectModel(c.model); renderMessages(); history(); view('chat');
    };
    $('history').append(button);
  }
}
function selectModel(name) {
  if ([...$('model-select').options].some(o => o.value === name)) $('model-select').value = name;
}
function newChat() {
  if (chatController) return toast('Stop the current response before starting a new conversation.');
  current = null; $('prompt').value = ''; renderMessages(); history(); view('chat'); $('prompt').focus();
}
// A deliberately small Markdown renderer: HTML, images and links are rendered as text.
// Every text fragment uses textContent, never innerHTML, including model output.
function inline(node, text) {
  const parts = text.split(/(`[^`]+`|\*\*[^*]+\*\*)/g);
  for (const part of parts) {
    if (part.startsWith('`') && part.endsWith('`')) node.append(el('code', '', part.slice(1,-1)));
    else if (part.startsWith('**') && part.endsWith('**')) node.append(el('strong', '', part.slice(2,-2)));
    else node.append(document.createTextNode(part));
  }
}
function markdown(node, text) {
  node.replaceChildren();
  const blocks = text.split('```');
  blocks.forEach((block, index) => {
    if (index % 2) {
      const newline = block.indexOf('\n');
      const code = newline >= 0 && /^[\w+-]*$/.test(block.slice(0,newline)) ? block.slice(newline+1) : block;
      const pre = el('pre'); pre.append(el('code', '', code)); node.append(pre);
    } else {
      for (const paragraph of block.split(/\n\n+/)) {
        if (!paragraph) continue;
        let tag = 'p', content = paragraph;
        if (/^#{1,3} /.test(content)) { tag = 'h3'; content = content.replace(/^#{1,3} /, ''); }
        if (content.startsWith('> ')) { tag = 'blockquote'; content = content.slice(2); }
        const p = el(tag); inline(p, content); node.append(p);
      }
    }
  });
}
function renderMessages() {
  $('welcome').classList.toggle('hidden', !!current?.messages.length);
  $('messages').replaceChildren();
  for (const message of current?.messages || []) $('messages').append(messageNode(message));
  $('delete-chat').disabled = !current || !!chatController;
}
function messageNode(message) {
  const article = el('article', `message ${message.role}${message.error ? ' error' : ''}`);
  const head = el('div', 'message-head'); head.append(el('span', 'avatar', message.role === 'user' ? '↑' : '✳'), document.createTextNode(message.role === 'user' ? 'You' : (message.model || 'Ollama')));
  article.append(head);
  if (message.thinking) {
    const details = el('details'); details.append(el('summary', '', 'Reasoning'), el('div', 'thinking', message.thinking)); article.append(details);
  }
  const body = el('div', 'message-body'); markdown(body, message.content || (chatController ? 'Thinking…' : 'No response text.')); article.append(body);
  const meta = el('div', 'message-meta');
  if (message.role === 'assistant') {
    if (message.error) meta.append(el('span', '', message.error));
    else if (message.stopped) meta.append(el('span', '', 'Stopped · partial response'));
    else if (message.tokens) meta.append(el('span', '', `${message.tokens} tokens${message.speed ? ' · ' + message.speed + ' tokens/s' : ''}`));
    const copy = el('button', '', 'Copy'); copy.onclick = () => navigator.clipboard.writeText(message.content).then(() => toast('Copied response.')).catch(() => toast('Could not access the clipboard.', true)); meta.append(copy);
  }
  article.append(meta); return article;
}
function busyChat(busy) {
  $('send').classList.toggle('hidden', busy); $('stop').classList.toggle('hidden', !busy);
  $('model-select').disabled = busy; $('delete-chat').disabled = busy || !current;
  $('new-chat').disabled = busy;
}
async function send(event) {
  event.preventDefault();
  if (chatController) return;
  const content = $('prompt').value.trim(), model = $('model-select').value;
  if (!content) return;
  if (!online || !model) return toast('Start Ollama and choose an installed local model first.', true);
  if (!current) { current = {id:crypto.randomUUID(), title:content.slice(0,55), model, updated:Date.now(), messages:[]}; chats.push(current); }
  const conversation = current;
  if (conversation.messages.length >= 198) return toast('This conversation is full. Start a new chat.', true);
  conversation.model = model; conversation.updated = Date.now();
  conversation.messages.push({role:'user', content});
  const payload = {model, messages: conversation.messages.filter(m => m.content).map(m => ({role:m.role, content:m.content}))};
  const answer = {role:'assistant', content:'', thinking:'', model}; conversation.messages.push(answer);
  $('prompt').value = ''; chatController = new AbortController(); busyChat(true); save(); history(); renderMessages();
  let node = $('messages').lastElementChild, done = false, lastSave = 0;
  $('chat-scroll').scrollTop = $('chat-scroll').scrollHeight;
  try {
    await stream('/api/chat', payload, chatController.signal, data => {
      answer.content += data.message?.content || ''; answer.thinking += data.message?.thinking || '';
      if (data.done) { done = true; answer.tokens = data.eval_count; answer.speed = data.eval_duration ? (data.eval_count / (data.eval_duration/1e9)).toFixed(1) : null; }
      const scroll = $('chat-scroll'), pinned = scroll.scrollHeight - scroll.scrollTop - scroll.clientHeight < 140;
      const replacement = messageNode(answer); node.replaceWith(replacement); node = replacement;
      if (pinned) scroll.scrollTop = scroll.scrollHeight;
      if (Date.now()-lastSave > 1500) { save(); lastSave = Date.now(); }
    });
    if (!done) throw new Error('Connection ended before the response was complete. You can retry with another message.');
  } catch (error) {
    if (error.name === 'AbortError') answer.stopped = true;
    else { answer.error = error.message; toast(error.message, true); }
  } finally {
    chatController = null; busyChat(false); save(); renderMessages(); refresh();
  }
}
function modelsUI(data, runningData) {
  const selected = $('model-select').value || current?.model;
  installed = data.models || [];
  $('model-select').replaceChildren();
  for (const model of installed.filter(m => !m.cloud)) { const option = el('option', '', model.name); option.value = model.name; $('model-select').append(option); }
  if (!$('model-select').options.length) { const option = el('option', '', 'No local models — open Model library'); option.value = ''; $('model-select').append(option); }
  selectModel(selected);
  $('model-count').textContent = installed.length;
  $('installed-total').textContent = `(${installed.length})`;
  $('model-list').replaceChildren();
  if (!installed.length) $('model-list').append(el('div', 'empty', 'No installed models yet. Download your first model above.'));
  for (const model of installed) {
    const card = el('div', 'model-card'), icon = el('div', 'model-icon', '▧');
    icon.append(el('span', 'model-tag', model.cloud ? 'CLOUD · DISABLED' : model.details?.parameter_size || 'LOCAL'));
    card.append(icon, el('h3','',model.name), el('p','',`${bytes(model.size)} · ${model.details?.quantization_level || model.details?.family || 'Ollama model'}`));
    if (model.modified_at) card.append(el('p','',`Updated ${new Date(model.modified_at).toLocaleDateString()}`));
    const actions = el('div','model-actions'), use = el('button','quiet','Open in chat'), remove = el('button','danger','Delete');
    use.disabled = model.cloud || !!chatController;
    use.onclick = () => { if (chatController) return; selectModel(model.name); view('chat'); $('prompt').focus(); };
    remove.onclick = () => modelAction('delete', model.name, remove);
    actions.append(use, remove); card.append(actions); $('model-list').append(card);
  }
  $('running-list').replaceChildren();
  if (runningData.error) $('running-list').append(el('div','empty',runningData.error));
  else if (!runningData.models?.length) $('running-list').append(el('div','empty','Nothing loaded. A model loads into memory when you send it a message.'));
  for (const model of runningData.models || []) {
    const row = el('div','running-row'), info = el('div','',model.name);
    const ratio = model.size ? Math.round((model.size_vram || 0)/model.size*100) : 0;
    info.append(el('small','',`${bytes(model.size)} memory · ${bytes(model.size_vram || 0)} VRAM · ${ratio}% GPU${model.expires_at ? ' · expires ' + new Date(model.expires_at).toLocaleTimeString() : ''}`));
    const unload = el('button','quiet','Unload'); unload.onclick = () => modelAction('unload',model.name,unload); row.append(info,unload); $('running-list').append(row);
  }
}
async function modelAction(action, name, button) {
  const deleting = action === 'delete';
  if (!await confirmAction(`${deleting ? 'Delete' : 'Unload'} ${name}?`, deleting ? 'This removes the installed model from disk. To use it again, you will need to download it. Conversation history is kept.' : 'This frees the model’s memory and may interrupt active requests using it.', deleting ? 'Delete model' : 'Unload model')) return;
  button.disabled = true;
  try { toast((await json(`/api/models/${action}`, {model:name, confirm:true})).message); await refresh(); }
  catch (error) { toast(error.message,true); }
  finally { button.disabled = false; }
}
async function pull(event) {
  event.preventDefault(); if (pullController) return;
  const model = $('pull-name').value.trim(); if (!model) return;
  pullController = new AbortController(); $('pull-button').disabled = true; $('pull-name').disabled = true;
  $('cancel-pull').classList.remove('hidden'); $('pull-progress').classList.remove('hidden');
  $('pull-bar').removeAttribute('value'); $('pull-status').textContent = 'Connecting to Ollama…';
  let success = false;
  try {
    await stream('/api/models/pull', {model}, pullController.signal, data => {
      success ||= data.status === 'success';
      if (data.total) { $('pull-bar').value = Math.min(100,(data.completed || 0)/data.total*100); }
      else if (success) $('pull-bar').value = 100;
      else $('pull-bar').removeAttribute('value');
      $('pull-status').textContent = `${data.status || 'Downloading'}${data.total ? ' · ' + bytes(data.completed || 0) + ' / ' + bytes(data.total) + ' (current layer)' : ''}`;
    });
    if (!success) throw new Error('Download stream ended early. Retry the same model to resume.');
    toast(`${model} is ready.`);
  } catch (error) {
    $('pull-status').textContent = error.name === 'AbortError' ? 'Download request cancelled. Ollama may retain partial layers; retry to resume.' : error.message;
    if (error.name !== 'AbortError') toast(error.message,true);
  } finally {
    pullController = null; $('pull-button').disabled = false; $('pull-name').disabled = false; $('cancel-pull').classList.add('hidden'); refresh();
  }
}
function systemUI(data) {
  online = data.running;
  $('status-text').textContent = online ? 'Ollama is running' : 'Ollama is stopped';
  $('status-dot').className = 'dot ' + (online ? 'online' : 'offline');
  $('engine-state').textContent = online ? 'Running smoothly' : 'Ready when you are';
  $('engine-version').textContent = `${data.version ? 'Version ' + data.version + ' · ' : ''}127.0.0.1:11434${!data.executable_found ? ' · Ollama executable not found' : ''}`;
  $('system-metrics').replaceChildren();
  const totalSize = installed.reduce((sum,m) => sum + (m.size || 0),0);
  for (const [label,value,note] of [['CPU',`${data.cpu_percent}%`,`${data.cpu_count} logical processors · sampled every 5s`],['MEMORY',`${data.ram_percent}%`,`${bytes(data.ram_used)} of ${bytes(data.ram_total)}`],['MODEL SIZES',online ? bytes(totalSize) : '—','Logical total; shared layers may be counted twice']]) {
    const card = el('div','metric'); card.append(el('span','tiny-label',label),el('strong','',value),el('small','',note)); $('system-metrics').append(card);
  }
  $('process-list').replaceChildren();
  if (!data.processes.length) $('process-list').append(el('div','empty','No matching Ollama processes found for this user.'));
  for (const proc of data.processes) { const row = el('div','running-row'); row.append(el('span','',`${proc.name} · PID ${proc.pid}`),el('span','muted',bytes(proc.ram))); $('process-list').append(row); }
  $('platform').textContent = data.platform;
  for (const button of document.querySelectorAll('[data-control]')) button.disabled = lifecycleBusy || !data.lifecycle_supported || (button.dataset.control === 'start' && online);
}
async function refresh() {
  if (refreshing) return; refreshing = true;
  try {
    const status = await json('/api/status');
    if (status.running) {
      const [models, running] = await Promise.all([json('/api/models'),json('/api/running').catch(error => ({error:error.message}))]);
      modelsUI(models,running);
    } else {
      installed = [];
      $('model-list').replaceChildren(el('div','empty','Start Ollama from System to discover your installed models.'));
      $('running-list').replaceChildren(el('div','empty','Ollama is stopped.'));
      $('model-count').textContent = '—'; $('installed-total').textContent = '';
    }
    systemUI(status);
  } catch (error) {
    online = false; $('status-text').textContent = 'Connection unavailable'; $('status-dot').className = 'dot offline';
    $('engine-state').textContent = 'Connection unavailable';
    $('engine-version').textContent = error.message;
  } finally { refreshing = false; }
}
async function control(action) {
  if (lifecycleBusy) return;
  if (action !== 'start' && !await confirmAction(`${action === 'stop' ? 'Stop' : 'Restart'} Ollama?`, 'This closes Ollama and its tray app for your Windows user, interrupting active chats and downloads, including other applications.', action === 'stop' ? 'Stop Ollama' : 'Restart Ollama')) return;
  lifecycleBusy = true;
  for (const button of document.querySelectorAll('[data-control]')) button.disabled = true;
  try { toast((await json('/api/control', {action, confirm:action !== 'start'})).message); }
  catch (error) { toast(error.message,true); }
  finally { lifecycleBusy = false; await refresh(); }
}
document.querySelectorAll('[data-view]').forEach(button => button.onclick = () => view(button.dataset.view));
document.querySelectorAll('[data-control]').forEach(button => button.onclick = () => control(button.dataset.control));
document.querySelectorAll('[data-prompt]').forEach(button => button.onclick = () => { $('prompt').value = button.dataset.prompt; $('prompt').focus(); });
$('new-chat').onclick = newChat;
$('menu').onclick = () => $('sidebar').classList.toggle('open');
$('status-pill').onclick = () => view('system');
$('chat-form').onsubmit = send;
$('prompt').onkeydown = event => { if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); $('chat-form').requestSubmit(); } };
$('stop').onclick = () => chatController?.abort();
$('pull-form').onsubmit = pull;
$('cancel-pull').onclick = () => pullController?.abort();
$('refresh-models').onclick = refresh;
$('delete-chat').onclick = async () => {
  if (!current || chatController) return;
  if (await confirmAction('Delete this conversation?', 'This removes the conversation from this browser. Export a backup first if you want to keep it.', 'Delete conversation')) { chats = chats.filter(c => c.id !== current.id); save(); newChat(); }
};
$('export').onclick = () => {
  const url = URL.createObjectURL(new Blob([JSON.stringify(chats,null,2)],{type:'application/json'}));
  const link = el('a'); link.href = url; link.download = 'ollama-web-conversations.json'; link.click(); setTimeout(() => URL.revokeObjectURL(url),1000);
};
window.addEventListener('beforeunload', event => { if (chatController || pullController) { event.preventDefault(); event.returnValue = ''; } });
window.addEventListener('storage', event => { if (event.key === STORE) toast('History changed in another tab. Use one tab to avoid overwriting conversations.',true); });
document.addEventListener('keydown', event => { if (event.key.toLowerCase() === 'n' && !event.ctrlKey && !event.metaKey && !['INPUT','TEXTAREA','SELECT'].includes(document.activeElement.tagName) && !$('confirm-dialog').open) newChat(); });
load(); history(); renderMessages(); refresh(); setInterval(refresh,5000);
