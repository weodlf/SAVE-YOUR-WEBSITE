'use strict';
const $ = selector => document.querySelector(selector);
const toolInfo = {
  'Port Scanner': {name:'포트 스캐너', color:'#c3f475', badge:''},
  'Header Check': {name:'보안 헤더', color:'#80b3ff', badge:'blue'},
  'Password Analysis': {name:'패스워드', color:'#b5a0f1', badge:'purple'},
  'Crypto': {name:'해시 · 인코딩', color:'#eeb280', badge:'orange'}
};
let logsCache = [], logsLoaded = false, logVersion = 0, statsVersion = 0;
let toolChart, dateChart, toastTimer;
function toast(message) {
  clearTimeout(toastTimer); $('#toast').textContent = message; $('#toast').hidden = false;
  toastTimer = setTimeout(() => { $('#toast').hidden = true; }, 4000);
}
async function request(url, options = {}) {
  const response = await fetch(url, {...options, credentials:'same-origin'});
  let data;
  try { data = await response.json(); } catch { throw new Error('서버 응답을 읽지 못했습니다. 다시 시도해 주세요.'); }
  if (!response.ok) {
    if (response.status === 401) throw new Error('인증이 만료되었습니다. 페이지를 새로고침해 로그인하세요.');
    const detail = data.detail;
    throw new Error(typeof detail === 'string' ? detail : Array.isArray(detail) ? detail.map(item => `${item.loc?.slice(1).join('.') || '입력'}: ${item.msg}`).join(' / ') : '요청을 처리하지 못했습니다.');
  }
  return data;
}
function renderLogs() {
  if (!logsLoaded) return;
  const search = $('#log-search').value.trim().toLocaleLowerCase();
  const filter = $('#log-filter').value;
  const visible = logsCache.filter(log => (!filter || log.tool_type === filter) &&
    [log.tool_type, toolInfo[log.tool_type]?.name || '', log.target, log.result_summary].some(value => value.toLocaleLowerCase().includes(search)));
  const body = $('#logs'); body.replaceChildren();
  visible.forEach(log => {
    const row = document.createElement('tr');
    const values = [`#${String(log.id).padStart(4,'0')}`, log.tool_type, log.target, log.result_summary,
      new Date(log.created_at).toLocaleString('ko-KR', {month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit', hour12:false})];
    values.forEach((value, index) => {
      const cell = document.createElement('td');
      if (index === 1) {
        const badge = document.createElement('span'); badge.className = `tool-badge ${toolInfo[log.tool_type]?.badge || ''}`;
        badge.textContent = toolInfo[log.tool_type]?.name || value; cell.append(badge);
      } else {
        cell.textContent = value;
        if (index === 2 && value === '[REDACTED]') { cell.textContent = '비공개 입력'; cell.title = '민감정보 보호를 위해 원문을 저장하지 않습니다.'; }
      }
      if (index === 4) cell.title = new Date(log.created_at).toLocaleString('ko-KR');
      row.append(cell);
    });
    body.append(row);
  });
  $('#empty-logs').hidden = visible.length > 0;
  $('#empty-logs strong').textContent = logsCache.length ? '검색 결과가 없어요' : '아직 기록이 없어요';
  $('#empty-logs p').textContent = logsCache.length ? '다른 검색어나 도구 필터로 다시 확인해 보세요.' : '첫 번째 진단을 실행하면 여기에 학습 기록이 쌓입니다.';
  $('#status').textContent = logsCache.length ? `최근 ${logsCache.length}개 중 ${visible.length}개 표시 · 최신순 · 시각은 기기 현지 시간` : '저장된 진단 기록 0개';
  $('#clear').disabled = logsCache.length === 0;
}
async function refreshLogs() {
  const version = ++logVersion;
  try {
    const logs = await request('/api/logs');
    if (version !== logVersion) return;
    logsCache = logs; logsLoaded = true; renderLogs();
  } catch (error) {
    if (version === logVersion) $('#status').textContent = `이력 조회 실패: ${error.message}${logsLoaded ? ' (아래는 이전 데이터입니다.)' : ''}`;
  }
}
function renderLegend(tools, counts, total) {
  const legend = $('#tool-legend'); legend.replaceChildren();
  if (!total) { const p = document.createElement('p'); p.className = 'legend-empty'; p.textContent = '첫 진단을 기다리고 있어요.'; legend.append(p); return; }
  tools.forEach((tool, index) => {
    const row = document.createElement('div'); row.className = 'legend-row';
    const dot = document.createElement('i'); dot.className = 'legend-dot'; dot.style.background = toolInfo[tool]?.color || '#9da9bb';
    const name = document.createElement('span'); name.textContent = toolInfo[tool]?.name || tool;
    const count = document.createElement('b'); count.textContent = `${counts[index]}회`;
    row.title = `${tool}: ${(counts[index] / total * 100).toFixed(1)}%`; row.append(dot, name, count); legend.append(row);
  });
}
const centerText = {
  id:'centerText',
  afterDraw(chart, args, options) {
    const {ctx, chartArea} = chart;
    if (!chartArea) return;
    const x = (chartArea.left + chartArea.right) / 2, y = (chartArea.top + chartArea.bottom) / 2;
    ctx.save(); ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
    ctx.fillStyle = '#edf2f6'; ctx.font = '600 23px system-ui'; ctx.fillText(options.total.toLocaleString(), x, y - 7);
    ctx.fillStyle = '#778496'; ctx.font = '9px system-ui'; ctx.fillText('전체 진단', x, y + 16); ctx.restore();
  }
};
function renderCharts(data) {
  const tools = Object.keys(data.by_tool), counts = Object.values(data.by_tool);
  const dates = Object.keys(data.by_date), dateCounts = Object.values(data.by_date);
  const total = counts.reduce((sum, count) => sum + count, 0);
  if (typeof Chart === 'undefined') throw new Error('차트 라이브러리를 불러오지 못했습니다. 아래 통계 데이터 보기에서 수치를 확인할 수 있습니다.');
  if (toolChart) toolChart.destroy();
  if (dateChart) dateChart.destroy();
  const common = {responsive:true, maintainAspectRatio:false, animation:false,
    plugins:{legend:{display:false}, tooltip:{backgroundColor:'#222833', titleColor:'#eef2f6', bodyColor:'#bdc6d3', padding:12, displayColors:false}}};
  toolChart = new Chart($('#tool-chart'), {
    type:'doughnut', plugins:[centerText],
    data:{labels:total ? tools.map(tool => toolInfo[tool]?.name || tool) : ['기록 없음'], datasets:[{data:total ? counts : [1], backgroundColor:total ? tools.map(tool => toolInfo[tool]?.color || '#9da9bb') : ['#2b333f'], borderWidth:0, hoverOffset:3, spacing:total ? 4 : 0}]},
    options:{...common, cutout:'79%', plugins:{...common.plugins, centerText:{total}, tooltip:{...common.plugins.tooltip, enabled:total > 0, callbacks:{label:context => `${context.raw}회 · ${(context.raw / total * 100).toFixed(1)}%`}}}}
  });
  const ctx = $('#date-chart').getContext('2d');
  const gradient = ctx.createLinearGradient(0,0,0,230); gradient.addColorStop(0,'#b5e57a29'); gradient.addColorStop(1,'#b5e57a00');
  dateChart = new Chart(ctx, {
    type:'line',
    data:{labels:dates, datasets:[{label:'진단 횟수', data:dateCounts, borderColor:'#c3f475', backgroundColor:gradient, fill:true, tension:0.28, borderWidth:2, pointRadius:2, pointHoverRadius:5, pointBackgroundColor:'#c3f475'}]},
    options:{...common, interaction:{intersect:false, mode:'index'}, layout:{padding:{top:8,right:6}},
      scales:{x:{grid:{display:false}, border:{display:false}, ticks:{color:'#66758a', font:{size:9}, maxRotation:0, maxTicksLimit:7, callback:function(value){return this.getLabelForValue(value).slice(5).replace('-','.');}}},
        y:{beginAtZero:true, suggestedMax:4, border:{display:false}, grid:{color:'#29313c66', drawTicks:false}, ticks:{precision:0,color:'#66758a',font:{size:9},padding:12,maxTicksLimit:5}}}}
  });
}
async function refreshStats() {
  const version = ++statsVersion;
  $('#stats-status').textContent = '통계 갱신 중…';
  try {
    const data = await request('/api/stats');
    if (version !== statsVersion) return;
    const tools = Object.keys(data.by_tool), counts = Object.values(data.by_tool);
    const dates = Object.keys(data.by_date), dateCounts = Object.values(data.by_date);
    const total = counts.reduce((sum, count) => sum + count, 0);
    const recent = dateCounts.reduce((sum, count) => sum + count, 0);
    $('#metric-total').textContent = total.toLocaleString();
    $('#metric-today').textContent = (dateCounts.at(-1) || 0).toLocaleString();
    $('#metric-recent').textContent = recent.toLocaleString();
    $('#metric-tools').textContent = tools.length;
    $('#stats-summary').textContent = `도구별: ${tools.map(tool => `${tool} ${data.by_tool[tool]}회`).join(', ') || '없음'} · 날짜별 (UTC): ${dates.map(date => `${date} ${data.by_date[date]}회`).join(', ')}`;
    renderLegend(tools, counts, total);
    renderCharts(data);
    document.querySelectorAll('.chart-fallback').forEach(element => {element.hidden = true;});
    $('#stats-status').textContent = `마지막 업데이트 ${new Date().toLocaleTimeString('ko-KR', {hour:'2-digit',minute:'2-digit',hour12:false})} · UTC 기준 집계 · 오류 이력 포함`;
  } catch (error) {
    if (version !== statsVersion) return;
    $('#stats-status').textContent = `통계 갱신 실패: ${error.message} (이전 데이터가 표시될 수 있습니다.)`;
    if (typeof Chart === 'undefined') document.querySelectorAll('.chart-fallback').forEach(element => {element.hidden = false;});
  }
}
async function refreshDashboard() { await Promise.all([refreshLogs(), refreshStats()]); }
function showResult(form, result) {
  const heading = form.querySelector('.result-heading'), body = form.querySelector('.result-body');
  heading.textContent = '진단 완료 · 이력에 저장했어요'; body.replaceChildren();
  if (result.open_ports) {
    body.textContent = result.open_ports.length ? `열린 포트: ${result.open_ports.join(', ')}` : '지정한 포트에서 열린 연결을 발견하지 못했습니다.';
  } else if (typeof result.score === 'number') {
    const score = document.createElement('strong'); score.textContent = `${result.score} / 100`;
    const track = document.createElement('div'); track.className = 'score-track';
    const fill = document.createElement('div'); fill.className = 'score-fill'; fill.style.width = `${result.score}%`; track.append(fill); body.append(score, track);
    if (result.headers) {
      Object.entries(result.headers).forEach(([header, present]) => {
        const row = document.createElement('div'); row.className = 'result-item';
        const name = document.createElement('span'); name.textContent = header;
        const state = document.createElement('span'); state.className = present ? 'present' : 'missing'; state.textContent = present ? '✓ 설정됨' : '미설정';
        row.append(name, state); body.append(row);
      });
    } else { const note = document.createElement('p'); note.textContent = '길이 및 문자 구성 기반의 참고 점수입니다.'; body.append(note); }
  } else if (typeof result.output === 'string') {
    const output = document.createElement('div'); output.className = 'result-output'; output.textContent = result.output;
    const copy = document.createElement('button'); copy.type = 'button'; copy.className = 'button copy-output'; copy.textContent = '결과 복사';
    copy.onclick = async () => {
      try { await navigator.clipboard.writeText(result.output); toast('결과를 클립보드에 복사했어요.'); }
      catch { toast('복사 권한을 확인하거나 결과를 직접 선택해 복사해 주세요.'); }
    }; body.append(output, copy);
  }
  form.querySelector('pre').textContent = JSON.stringify(result, null, 2);
}
document.querySelectorAll('form[data-api]').forEach(form => form.addEventListener('submit', async event => {
  event.preventDefault();
  const button = form.querySelector('[type="submit"]'), resultBox = form.querySelector('.tool-result');
  const original = button.innerHTML;
  button.disabled = true; button.textContent = '진단하는 중…'; form.setAttribute('aria-busy','true');
  resultBox.hidden = false; resultBox.classList.remove('error');
  form.querySelector('.result-heading').textContent = '결과를 기다리고 있어요…';
  form.querySelector('.result-body').replaceChildren(); form.querySelector('pre').textContent = '';
  let sent = false;
  try {
    const data = Object.fromEntries(new FormData(form));
    if (data.ports) {
      const parts = data.ports.split(',').map(port => port.trim());
      if (parts.length > 32 || parts.some(port => !/^\d+$/.test(port) || Number(port) < 1 || Number(port) > 65535)) throw new Error('1~65535 범위의 포트를 쉼표로 구분해 최대 32개 입력하세요.');
      data.ports = parts.map(Number);
    }
    sent = true;
    const result = await request(`/api/${form.dataset.api}`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(data)});
    showResult(form, result); toast('진단 결과가 활동 기록에 저장되었어요.');
  } catch (error) {
    resultBox.classList.add('error'); form.querySelector('.result-heading').textContent = '진단을 완료하지 못했어요';
    form.querySelector('.result-body').textContent = error.message;
  } finally {
    button.disabled = false; button.innerHTML = original; form.removeAttribute('aria-busy');
    if (sent) await refreshDashboard();
  }
}));
$('.password-toggle').onclick = function () {
  const input = this.parentElement.querySelector('input'), show = input.type === 'password';
  input.type = show ? 'text' : 'password'; this.textContent = show ? '숨김' : '표시';
  this.setAttribute('aria-label', show ? '패스워드 숨기기' : '패스워드 표시'); this.setAttribute('aria-pressed', String(show));
};
$('#refresh-stats').onclick = refreshStats;
$('#refresh').onclick = refreshDashboard;
$('#log-search').addEventListener('input', renderLogs);
$('#log-filter').addEventListener('change', renderLogs);
$('#clear').onclick = async function () {
  if (!confirm('저장된 모든 진단 이력을 영구 삭제할까요? 삭제한 기록은 복구할 수 없습니다.')) return;
  this.disabled = true;
  try { const result = await request('/api/logs', {method:'DELETE'}); toast(`${result.deleted}개의 기록을 삭제했어요.`); await refreshDashboard(); }
  catch (error) { toast(`삭제 실패: ${error.message}`); }
  finally { this.disabled = logsLoaded && logsCache.length === 0; }
};
function closeMenu() { document.body.classList.remove('menu-open'); $('#menu-toggle').setAttribute('aria-expanded','false'); }
$('#menu-toggle').onclick = () => {
  const open = document.body.classList.toggle('menu-open'); $('#menu-toggle').setAttribute('aria-expanded',String(open));
};
document.addEventListener('keydown', event => { if (event.key === 'Escape') closeMenu(); });
document.addEventListener('click', event => { if (document.body.classList.contains('menu-open') && !event.target.closest('#sidebar, #menu-toggle')) closeMenu(); });
document.querySelectorAll('.nav-item').forEach(link => link.addEventListener('click', () => {closeMenu();}));
const navLinks = [...document.querySelectorAll('.nav-item')];
function updateNavigation() {
  let current = 'overview';
  for (const link of navLinks) { if ($(link.getAttribute('href')).getBoundingClientRect().top <= 180) current = link.hash.slice(1); }
  navLinks.forEach(link => { const active = link.hash === `#${current}`; link.classList.toggle('active',active); if (active) link.setAttribute('aria-current','location'); else link.removeAttribute('aria-current'); });
}
window.addEventListener('scroll',updateNavigation,{passive:true}); updateNavigation();
request('/healthz').then(() => { $('#service-health').classList.add('ready'); $('#service-health').lastChild.textContent = '워크스페이스 연결됨'; }).catch(() => { $('#service-health').classList.add('error'); $('#service-health').lastChild.textContent = '연결 확인 필요'; });
refreshDashboard();
