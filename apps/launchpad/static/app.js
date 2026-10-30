'use strict';
const $ = selector => document.querySelector(selector);
const statusNames = {created:'등록됨',building:'빌드 중',starting:'시작 중',running:'실행 중',stopped:'중지됨',failed:'배포 실패',degraded:'확인 필요'};
let services = [], selectedService = null, refreshing = false, toastTimer;
function toast(text) {clearTimeout(toastTimer); $('#toast').textContent = text; $('#toast').hidden = false; toastTimer = setTimeout(() => {$('#toast').hidden = true;},5000);}
async function request(url, options={}) {
  const response = await fetch(url, {credentials:'same-origin',...options});
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail));
  return data;
}
function node(tag, text, className) {const element=document.createElement(tag); if(text!==undefined)element.textContent=text; if(className)element.className=className; return element;}
async function action(service, type, button) {
  if (type==='logs') {selectedService=service; $('#logs-title').textContent=service.name; $('#logs-dialog').showModal(); await loadLogs();return;}
  if(type==='delete' && !confirm(`${service.name} 서비스를 삭제할까요? 컨테이너와 등록 정보는 삭제되며, /data 영구 볼륨은 유지됩니다.`)) return;
  if(type==='stop' && !confirm(`${service.name} 서비스를 중지할까요?`)) return;
  button.disabled=true;
  try {
    await request(`/api/services/${service.id}${type==='delete'?'':`/${type}`}`,{method:type==='delete'?'DELETE':'POST'});
    toast(type==='deploy'?'빌드를 시작했어요. 로그에서 진행 상황을 확인하세요.':type==='stop'?'서비스를 중지했어요.':type==='start'?'서비스를 시작했어요.':'서비스를 삭제했어요.');
    await refresh();
  } catch(error){toast(`작업 실패: ${error.message}`);}
  finally {button.disabled=false;}
}
function render() {
  $('#total').textContent=services.length;
  $('#running').textContent=services.filter(s=>s.status==='running').length;
  $('#building').textContent=services.filter(s=>['building','starting'].includes(s.status)).length;
  $('#failed').textContent=services.filter(s=>['failed','degraded'].includes(s.status)).length;
  $('#empty').hidden=services.length>0;
  const list=$('#service-list');list.replaceChildren();
  services.forEach(service=>{
    const card=node('article',undefined,'service-card');card.dataset.id=service.id;
    const title=node('div',undefined,'service-title');title.append(node('span','↗','app-icon'));
    const name=node('div');name.append(node('h3',service.name),node('small','Docker web service'));title.append(name,node('span',statusNames[service.status]||service.status,`badge ${service.status}`));card.append(title);
    card.append(node('div',service.project,'project-path'));
    const meta=node('div',undefined,'service-meta');meta.append(node('span',`컨테이너 포트 ${service.port}`),node('span',`환경변수 ${service.environment_keys.length}개`),node('span','256 MB · 0.5 CPU'));card.append(meta);
    if(service.host_port && service.status==='running'){
      const link=node('a',`↗ 127.0.0.1:${service.host_port}`,'service-url');link.href=`http://127.0.0.1:${service.host_port}/`;link.target='_blank';link.rel='noopener noreferrer';link.title='서버 자체에서 접속하는 로컬 주소입니다.';card.append(link);
    }
    const buttons=node('div',undefined,'service-actions');
    const busy=['building','starting'].includes(service.status);
    [['deploy',service.status==='created'?'↗ 배포하기':'↻ 다시 빌드'],['start','▷ 시작'],['stop','□ 중지'],['logs','⌘ 로그'],['delete','삭제']].forEach(([type,label])=>{
      if(type==='start' && !['stopped','degraded'].includes(service.status))return;
      if(type==='stop' && !['running','degraded','failed'].includes(service.status))return;
      const button=node('button',label,`button ${type==='delete'?'remove':type==='deploy'?'primary':'subtle'}`);button.type='button';button.disabled=busy&&type!=='logs';button.onclick=()=>action(service,type,button);buttons.append(button);
    });card.append(buttons);list.append(card);
  });
}
async function refresh(){
  if(refreshing)return;refreshing=true;
  try{services=await request('/api/services');render();$('#page-status').textContent=`최근 확인 ${new Date().toLocaleTimeString('ko-KR')} · 4초마다 자동 갱신`;}
  catch(error){$('#page-status').textContent=`상태 갱신 실패: ${error.message} (기존 데이터가 표시될 수 있어요.)`;}
  finally{refreshing=false;}
}
async function loadLogs(){
  if(!selectedService)return;
  const id=selectedService.id;$('#logs-status').textContent='로그를 불러오는 중…';
  try{
    const [logs,history]=await Promise.all([request(`/api/services/${id}/logs`),request(`/api/services/${id}/deployments`)]);
    if(selectedService?.id!==id)return;
    $('#build-logs').textContent=logs.build||'아직 빌드를 실행하지 않았습니다.';
    $('#runtime-logs').textContent=logs.runtime||'실행 로그가 없습니다.';
    $('#deploy-history').replaceChildren();
    history.forEach(job=>{const row=node('div',undefined,'history-row');row.append(node('span',new Date(job.started_at).toLocaleString('ko-KR')),node('span',statusNames[job.status]||job.status));$('#deploy-history').append(row);if(job.error)$('#deploy-history').append(node('p',job.error,'history-error'));});
    $('#logs-status').textContent=`${new Date().toLocaleTimeString('ko-KR')} 갱신 · 빌드 로그 최근 64 KB / 실행 로그 150줄`;
  }catch(error){$('#logs-status').textContent=`로그 조회 실패: ${error.message}`;}
}
function openProject(){ $('#create-error').textContent='';$('#project-dialog').showModal(); }
$('#new-project').onclick=openProject;$('#empty-create').onclick=openProject;
document.querySelectorAll('[data-close]').forEach(button=>button.onclick=()=>document.getElementById(button.dataset.close).close());
$('#logs-dialog').addEventListener('close',()=>{selectedService=null;});
$('#project-form').addEventListener('submit',async event=>{
  event.preventDefault();const form=event.currentTarget;const button=form.querySelector('[type="submit"]');button.disabled=true;$('#create-error').textContent='';
  try{
    const fields=Object.fromEntries(new FormData(form));const environment={};
    for(const line of fields.environment.split(/\r?\n/)){if(!line.trim())continue;const index=line.indexOf('=');if(index<1)throw new Error('환경변수는 KEY=value 형식으로 입력하세요.');const key=line.slice(0,index).trim();if(Object.hasOwn(environment,key))throw new Error(`중복된 환경변수: ${key}`);Object.defineProperty(environment,key,{value:line.slice(index+1),enumerable:true});}
    await request('/api/services',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...fields,port:Number(fields.port),environment})});
    $('#project-dialog').close();form.reset();toast('프로젝트를 등록했어요. 배포하기 버튼으로 빌드를 시작하세요.');await refresh();
  }catch(error){$('#create-error').textContent=error.message;}
  finally{button.disabled=false;}
});
$('#refresh').onclick=async()=>{await Promise.all([refresh(),loadSystem()]);};$('#refresh-logs').onclick=loadLogs;
async function loadSystem(){
  try{const data=await request('/api/system');$('#connection').textContent=data.docker_ready?'● Docker 연결됨':'● Docker 연결 확인 필요';$('#connection').classList.toggle('ready',data.docker_ready);$('#server-details').textContent=`프로젝트 루트: ${data.project_root} · 동시 배포 ${data.max_parallel_deployments}개 · 서비스 주소: localhost`;}
  catch(error){$('#connection').textContent='● 서버 응답 없음';$('#server-details').textContent=error.message;}
}
loadSystem();refresh();
setInterval(()=>{if(!document.hidden){refresh();if($('#logs-dialog').open)loadLogs();}},4000);
