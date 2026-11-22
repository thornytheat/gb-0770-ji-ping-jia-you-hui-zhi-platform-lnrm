const status = document.querySelector('#dispatch-message');

async function post(path,body) {
  const response = await fetch(path, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const data = await response.json();
  if (!response.ok) throw new Error(data.message || '交班提交失败');
  return data;
}

function bind(selector, submit) {
  const form = document.querySelector(selector);
  form?.addEventListener('submit', async event => {
    event.preventDefault();
    const button = form.querySelector('button');
    button.disabled = true;
    status.textContent = '正在核量…';
    try { await submit(form); }
    catch (error) { status.textContent = error.message; }
    finally { button.disabled = false; }
  });
}

bind('#archive', async form => {
  const files = Object.fromEntries(['tickets','receipts','stock'].map(id => [id,document.getElementById(id).value]));
  const values = Object.values(files);
  if (values.some(Boolean) && !values.every(Boolean)) throw new Error('请填全三份 CSV');
  const sample = form.dataset.sample;
  const ack = await post('/api/shifts', values.every(Boolean) ? files : {sample});
  location.assign('/ops/sign?' + new URLSearchParams({sample,batch:ack.batch}));
});

const pick = document.querySelector('#batch');
const hash = document.querySelector('#checksum');
const exportLink = document.querySelector('#export-slip');
pick?.addEventListener('change', () => { hash.value = ''; exportLink.hidden = true; });
bind('#signature', async () => {
  const batch = pick.value;
  const result = await post(`/api/shifts/${batch}/sign`,{});
  if (pick.value !== batch) return;
  hash.value = result.checksum;
  exportLink.href = `/api/shifts/${batch}/export`;
  exportLink.hidden = false;
  status.textContent = `证据已签发 · 待复核 ${result.open_items} 票`;
});

const handoverChecksum = document.querySelector('#handover-checksum');
const handoverLink = document.querySelector('#handover-detail');
bind('#handover', async form => {
  const previous_batch = form.querySelector('#previous-batch').value;
  const incoming_batch = form.querySelector('#incoming-batch').value;
  const result = await post('/api/handovers', {previous_batch, incoming_batch});
  handoverChecksum.value = `交接 ${result.reused ? '复用' : '入账'} · 净差 ${result.total_difference_g/1000} kg · 短少 ${result.short_count} 辆 / 溢余 ${result.over_count} 辆`;
  handoverLink.href = `/api/handovers/${result.digest}`;
  handoverLink.hidden = false;
  status.textContent = '交接差已入账一次，证据单签发时随件带出两班原件';
});
