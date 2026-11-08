'use strict';
const $ = id => document.getElementById(id);
const el = (tag, cls, text) => { const x=document.createElement(tag); if(cls)x.className=cls;if(text!==undefined)x.textContent=text;return x; };
const sampling=['temperature','top_p','top_k','min_p','repeat_penalty','presence_penalty','frequency_penalty'];
let token='', profiles=[], current=null, timer=null, active=null,comparisonExport=null;
let savedResults=[];const selectedResults=new Set();
let loadedDefinition=null,coverageData={tests:[],models:[],cells:[]},coverageFilter=null;
let lemonadeBase='http://10.138.6.1:13305/api/v1',connectionState=null;
let currentPage='tests';
let lastDeletion=null,pendingDelete=null,deleteVersion=0;
const profile=(name='Model')=>({name,provider:'demo',model:'demo-model',url:lemonadeBase,endpoint:'',settings:{},writing_output_tokens:2048,extraction_output_tokens:1024});
const pages={tests:['STEP 1 OF 3','Start with a test.','A test is a task and some source material. Save it once, then try it with different AI models.'],run:['STEP 2 OF 3','Choose who answers.','Pick a model, review what will run, and press Run test. Your answer will be saved automatically.'],results:['STEP 3 OF 3','What worked best?','Read your saved answers, compare them, and decide which model works for your task.']};
function updatePageCopy(){const mode=document.documentElement.dataset.mode;let copy=pages[currentPage];if(mode==='learned')copy={tests:['STEP 1 OF 3','Choose a test.','Choose a saved test or create one.'],run:['STEP 2 OF 3','Run a model.','Choose a model and run the saved test.'],results:['STEP 3 OF 3','Review results.','View and compare saved answers.']}[currentPage];if(mode==='expert')copy={tests:['TESTS','Tests',''],run:['RUN','Run',''],results:['RESULTS','Results','']}[currentPage];[$('page-kicker').textContent,$('page-title').textContent,$('page-description').textContent]=copy;window.JUICRAppearance?.refresh();}
document.addEventListener('juicr-appearance',updatePageCopy);
function setPage(page){currentPage=page;['tests','run','results'].forEach(name=>{$('view-'+name).hidden=name!==page;const nav=$('nav-'+name);if(name===page)nav.setAttribute('aria-current','step');else nav.removeAttribute('aria-current');});updatePageCopy();window.scrollTo({top:0,behavior:'smooth'});}
['tests','run','results'].forEach(page=>$('nav-'+page).onclick=()=>setPage(page));
function openEditor(fresh=false){if(fresh){loadedDefinition=null;profiles=[profile()];['title','instructions','content','context','criteria'].forEach(id=>$(id).value='');$('task').value='writing';$('repeats').value=1;$('seed').value=17;$('timeout').value=180;drawColumns();}$('test-editor').hidden=false;$('editor-heading').textContent=loadedDefinition?'Edit this test':'Create a test';setPage('tests');updateDefinitionStatus();$('test-editor').scrollIntoView({behavior:'smooth',block:'start'});}
$('editor-close').onclick=()=>{$('test-editor').hidden=true;};
$('edit-test').onclick=()=>openEditor(false);
$('delete-test').onclick=()=>{if(loadedDefinition)requestDelete({kind:'test',id:loadedDefinition.id});};
$('back-tests').onclick=()=>setPage('tests');
$('continue-unsaved').onclick=()=>{if(!checkTestFields())return;loadedDefinition=null;updateDefinitionStatus();setPage('run');};
function checkTestFields(){if(!['title','instructions','content'].every(id=>$(id).value.trim())){note('Give your test a name, describe the task, and add some source material before continuing.');return false;}return true;}
$('example').onclick=()=>{openEditor(true);$('title').value='Practice: write a project announcement';$('instructions').value='Write a short, clear announcement for nontechnical staff. Preserve the facts. Do not invent details.';$('content').value='Cedar is an internal document-search pilot for 12 analysts. It begins May 2 and has a $40,000 budget. Human citation checking is required. No production rollout date or guaranteed accuracy has been provided.';estimate();updateDefinitionStatus();note('This practice test uses Demo. Save it, then run a simulated answer at no cost.');};
function note(s){$('notice').textContent=s;}
async function api(path, data){const r=await fetch('/api/'+path,data===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-JUICR-Token':token},body:JSON.stringify(data)});const j=await r.json();if(!r.ok)throw Error(j.error||'Request failed');return j;}
function field(parent,label,value,change,type='text',placeholder=''){const l=el('label','',label), input=el('input');input.type=type;input.setAttribute('aria-label',label);input.value=value??'';input.placeholder=placeholder;if(type==='number')input.step='any';input.addEventListener('input',()=>change(input.value));l.append(input);parent.append(l);return input;}
let helpCounter=0;
function explain(input,text,shortText){const help=el('small','setting-help',text);help.id='field-help-'+(++helpCounter);help.dataset.guidanceLong=text;const range=text.match(/Accepted range:.*$/)?.[0]||'';help.dataset.guidanceShort=(shortText||text.split('. ')[0]+'.')+(range?' '+range:'');input.title=text;input.setAttribute('aria-describedby',help.id);input.parentElement.append(help);}
const catalogs=new Map();
function modelPicker(card,p){
 const area=el('div','model-picker'), searchLabel=el('label','','Search models'), search=el('input');
 search.type='search';search.placeholder='Filter by model name or ID';searchLabel.append(search);
 const modelLabel=el('label','','Available models'), select=el('select');modelLabel.append(select);
 const bottom=el('div','model-picker-foot'), status=el('span','','Loading models…'), refresh=el('button','','Refresh');refresh.type='button';bottom.append(status,refresh);area.append(searchLabel,modelLabel,bottom);card.append(area);
 let rows=[],loaded=false,generation=0;
 const render=()=>{
  const term=search.value.trim().toLocaleLowerCase(), filtered=rows.filter(m=>(m.name+' '+m.id).toLocaleLowerCase().includes(term));
  select.replaceChildren();const placeholder=el('option','','Choose a model…');placeholder.value='';select.append(placeholder);
  if(p.model&&!rows.some(m=>m.id===p.model)){const saved=el('option','',p.model+' (saved selection; not in catalog)');saved.value=p.model;select.append(saved);}
  filtered.forEach(m=>{const option=el('option','',m.name===m.id?m.id:`${m.name} · ${m.id}`);option.value=m.id;select.append(option);});
  if(p.model&&rows.some(m=>m.id===p.model)&&!filtered.some(m=>m.id===p.model)){const selected=el('option','',p.model+' (selected; outside filter)');selected.value=p.model;select.append(selected);}
  select.value=p.model||'';if(loaded)status.textContent=rows.length?`${filtered.length} of ${rows.length} models`:'No models returned by this endpoint';
 };
 search.oninput=render;select.onchange=()=>{p.model=select.value;render();estimate();};
 const load=async(force=false)=>{
  const version=++generation;
  const key=JSON.stringify([p.provider,p.provider==='lemonade'?p.url.trim():'']);
  refresh.disabled=true;loaded=false;status.textContent='Loading models…';
  try{
   if(force)catalogs.delete(key);
   if(!catalogs.has(key))catalogs.set(key,api('models',{provider:p.provider,url:p.url}).then(r=>r.models));
   const fetched=await catalogs.get(key);if(version!==generation)return;
   rows=fetched;loaded=true;render();
  }catch(e){if(version!==generation)return;catalogs.delete(key);rows=[];render();status.textContent=e.message;}
  finally{if(version===generation)refresh.disabled=false;}
 };
 refresh.onclick=()=>load(true);render();
 if(token)load();else status.textContent='Connecting…';
 return ()=>load();
}
function estimate(){const n=profiles.length*Number($('repeats').value||1),task=$('task').value,tokens=profiles.reduce((a,p)=>a+Number(p[task+'_output_tokens']||0),0)*Number($('repeats').value||1);$('estimate').textContent=`${n} answer${n===1?'':'s'} will be requested · up to ${tokens.toLocaleString()} output tokens`;$('runhint').textContent=profiles.every(p=>p.provider==='demo')?'Demo only: simulated answers, no model usage or charges.':'This sends your test material to the selected service. Hosted models may charge for input and output.';$('chars').textContent=`${$('content').value.length.toLocaleString()} characters`;
const hasTest=['title','instructions','content'].every(id=>$(id).value.trim()),hasModel=profiles.every(p=>p.model.trim()),changed=!!loadedDefinition&&!draft().test_id;
const validNumbers=[...document.querySelectorAll('#view-run input[type=number]')].every(input=>input.validity.valid)&&profiles.every(p=>['writing_output_tokens','extraction_output_tokens'].every(k=>Number.isInteger(p[k])&&p[k]>=1&&p[k]<=32768))&&Number($('repeats').value)>=1&&Number($('timeout').value)>=5&&Number($('seed').value)>=0&&Number($('seed').value)<=2147483647&&n<=100;
$('run').disabled=!!active||!hasTest||!hasModel||changed||!validNumbers;$('run-ready').textContent=active?'A test is already running. You can review saved results while you wait.':!hasTest?'Choose or create a test in step 1 first.':!hasModel?'Choose an available model before running.':!validNumbers?'Check the numbers in the options. Use the ranges shown beside each setting, with at most 100 answers per run.':changed?'Save your changed settings as a new test version to continue.':'Ready. Your existing results will be kept.';
$('run-test-name').textContent=$('title').value||'Choose a test first';$('run-test-summary').textContent=hasTest?`${$('instructions').value.slice(0,190)}${$('instructions').value.length>190?'…':''}`:'Go to step 1 to choose or create a test.';$('changed-test').hidden=!changed;}
const settingHelp={temperature:['Temperature','Controls variation in the answer. Lower values favor more likely choices; higher values allow more variety. Zero does not guarantee identical answers.'],top_p:['Top P','Limits choices to the most likely tokens whose combined probability reaches this value. Lower values generally reduce variety.'],top_k:['Top K','Limits how many likely token choices the model considers at each step. Support and the meaning of zero depend on the service.'],min_p:['Min P','Filters out choices much less likely than the most likely token. Higher values keep fewer choices.'],repeat_penalty:['Repeat penalty','Discourages repeating tokens. A value of 1 means no penalty. Service support varies.'],presence_penalty:['Presence penalty','Discourages reusing tokens that have already appeared, regardless of how often they appeared.'],frequency_penalty:['Frequency penalty','Discourages tokens in proportion to how often they have already appeared.']};
const shortSettingHelp={temperature:'Lower favors more likely choices; higher allows more variety.',top_p:'Lower values keep a smaller pool of likely choices.',top_k:'Number of candidate tokens considered at each step.',min_p:'Higher values filter out more unlikely choices.',repeat_penalty:'Discourages repetition; 1 is neutral.',presence_penalty:'Discourages tokens already used.',frequency_penalty:'Penalizes tokens more when they appear often.'};
function drawColumns(){const box=$('columns');box.replaceChildren();profiles.forEach((p,i)=>{
 const card=el('article','model-card'),top=el('div','card-top');top.append(el('h3','',profiles.length===1?'Choose a model':`Model ${i+1}`));
 if(profiles.length>1){const actions=el('div','batch-actions'),clone=el('button','','Duplicate'),remove=el('button','','Remove');clone.onclick=()=>{if(profiles.length>=6)return note('You can run up to six models at once.');profiles.splice(i+1,0,structuredClone(p));drawColumns();};remove.onclick=()=>{profiles.splice(i,1);drawColumns();};actions.append(clone,remove);top.append(actions);}card.append(top);
 const label=el('label','','Where should it run?'),select=el('select');[['demo','Demo — practice without using a model'],['lemonade','Lemonade — your local model server'],['openrouter','OpenRouter — hosted models']].forEach(([v,t])=>{const option=el('option','',t);option.value=v;select.append(option);});select.value=p.provider;select.onchange=()=>{p.provider=select.value;p.model='';drawColumns();};label.append(select);card.append(label);
 const providerText={demo:'Demo copies your source into a clearly marked practice answer. Use it to learn the workflow; it does not test model quality.',lemonade:'Lemonade uses your configured server. Add its key under Connections & keys if needed.',openrouter:'OpenRouter sends your content to hosted model providers. Add your key under Connections & keys. Running a test may use paid credits.'};const providerNote=el('p','provider-note',providerText[p.provider]);providerNote.dataset.guidanceLong=providerText[p.provider];providerNote.dataset.guidanceShort={demo:'Simulated answers only; no model quality measurement.',lemonade:'Your configured local server.',openrouter:'Hosted models; requests may use paid credits.'}[p.provider];card.append(providerNote);
 const reloadModels=modelPicker(card,p);
 const advanced=el('details','advanced-settings');advanced.append(el('summary','','Advanced model settings — optional'),el('p','hint','You can leave these alone. Empty sampling fields use the service’s defaults, which may differ between models. To study one setting, change only that setting and compare saved answers.'));
 let urlField;if(p.provider==='lemonade')urlField=field(advanced,'Server API address',p.url,v=>p.url=v);if(p.provider==='openrouter')field(advanced,'Specific provider (optional)',p.endpoint,v=>p.endpoint=v,'text','Leave blank to let OpenRouter choose');
 if(urlField)urlField.addEventListener('change',()=>{p.model='';reloadModels();estimate();});
 const defaults=el('button','','Use service defaults for sampling');defaults.onclick=()=>{p.settings={};drawColumns();note('Sampling values cleared. Save a new test version if you changed a saved test.');};advanced.append(defaults);
 const grid=el('div','setting-grid');sampling.forEach(k=>{const input=field(grid,settingHelp[k][0],p.settings[k],v=>{if(v==='')delete p.settings[k];else p.settings[k]=Number(v);estimate();updateDefinitionStatus();},'number','Service default');const ranges={temperature:[0,2],top_p:[0,1],top_k:[0,100000],min_p:[0,1],repeat_penalty:[0,2],presence_penalty:[-2,2],frequency_penalty:[-2,2]};input.min=ranges[k][0];input.max=ranges[k][1];input.step=k==='top_k'?'1':'any';explain(input,settingHelp[k][1]+` Accepted range: ${input.min} to ${input.max}.`,shortSettingHelp[k]);});advanced.append(grid);
 const caps=el('div','setting-grid');['writing','extraction'].forEach(task=>{const input=field(caps,`${task==='writing'?'Writing':'Extraction'} answer limit (tokens)`,p[task+'_output_tokens'],v=>{p[task+'_output_tokens']=Number(v);estimate();updateDefinitionStatus();},'number');input.min=1;input.max=32768;input.step=1;explain(input,'A token is a piece of a word. This is a maximum, not a target length. Reasoning may use some of this allowance too.','Maximum generated tokens; reasoning may use part of the allowance.');});advanced.append(caps);card.append(advanced);box.append(card);
});estimate();updateDefinitionStatus();window.JUICRAppearance?.refresh();}
function testSignature(d){return JSON.stringify([d.content,d.context||'',d.instructions,d.criteria||'',d.task,d.repeats,d.seed,d.timeout,d.profiles.map(p=>[Object.entries(p.settings).sort(([a],[b])=>a.localeCompare(b)),p.writing_output_tokens,p.extraction_output_tokens])]);}
function draft(){const d={title:$('title').value,task:$('task').value,instructions:$('instructions').value,content:$('content').value,context:$('context').value,criteria:$('criteria').value,repeats:Number($('repeats').value),seed:Number($('seed').value),timeout:Number($('timeout').value),profiles:structuredClone(profiles)};if(loadedDefinition&&testSignature(d)===testSignature(loadedDefinition.draft))d.test_id=loadedDefinition.id;return d;}
function loadDraft(d){['title','task','instructions','content','context','criteria','repeats','seed','timeout'].forEach(k=>$(k).value=d[k]??'');profiles=structuredClone(d.profiles);loadedDefinition=coverageData.tests.find(t=>t.id===d.test_id)||null;drawColumns();updateDefinitionStatus();}
function updateDefinitionStatus(){const linked=loadedDefinition&&draft().test_id;$('definition-status').textContent=loadedDefinition?`Saved version ${loadedDefinition.revision}. ${linked?'Your original task and settings are unchanged.':'Saving creates a new version. Earlier tests and answers stay intact.'}`:'This saves the task, source material and settings so you can repeat the same test later.';$('delete-test').hidden=!loadedDefinition;$('delete-test').disabled=!!active;$('save-test-definition').textContent=loadedDefinition?'Save new version & choose model →':'Save test & choose a model →';estimate();}
async function history(){const s=await api('state');token=s.token;active=s.active;showConnections(s.connections);showUndo(s.last_deletion);$('connection').textContent=s.openrouter_ready?'OpenRouter key ready':'Local session';const box=$('history');box.replaceChildren();s.items.forEach(item=>{const b=el('button','',item.title);b.append(el('small','',item.kind==='draft'?'Saved setup':item.status.replaceAll('_',' ')));b.onclick=()=>openItem(item.id,item.kind);box.append(b);});$('cancel').hidden=!active;if(active&&!timer){timer=setInterval(poll,1000);}await refreshLibrary();estimate();return s;}
function showUndo(deletion){lastDeletion=deletion;$('undo-bar').hidden=!deletion;$('undo-message').textContent=deletion?.description||'';$('undo-delete').disabled=!!active;}
function clearDisplayedResults(){current=null;comparisonExport=null;selectedResults.clear();$('results').replaceChildren(el('p','hint','Choose a saved answer above to view it.'));$('status').textContent='No answer selected';$('output-title').textContent='Your answer';$('export').disabled=true;}
async function deletePreview(){
 if(!pendingDelete)return;const version=++deleteVersion,request={...pendingDelete,include_results:pendingDelete.kind==='test'&&$('delete-related').checked};$('delete-confirm').disabled=true;
 try{const plan=await api('delete-preview',request);if(version!==deleteVersion||!pendingDelete)return;
  pendingDelete={...request,expected:plan.token};$('delete-title').textContent=plan.kind==='test'?'Delete this test?':`Delete ${plan.result_count===1?'this saved answer':'these saved answers'}?`;
  $('delete-description').textContent=plan.kind==='test'?`The test “${plan.title}” will be deleted, including ${plan.versions===1?'its saved version':`all ${plan.versions} saved versions`}. ${request.include_results?`Its ${plan.related_results} saved answer${plan.related_results===1?'':'s'} will also be deleted.`:`Its ${plan.related_results} saved answer${plan.related_results===1?'':'s'} will be kept.`}`:`${plan.result_count} saved answer${plan.result_count===1?'':'s'} will be removed from history and comparisons. Your tests will be kept.`;
  $('delete-related-label').hidden=plan.kind!=='test'||plan.related_results===0;$('delete-related-text').textContent=plan.related_results===1?'Also delete the saved answer from this test':`Also delete all ${plan.related_results} saved answers from every version of this test`;
  $('delete-confirm').textContent=plan.kind==='test'?'Delete test':`Delete ${plan.result_count===1?'answer':'answers'}`;$('delete-confirm').disabled=false;
 }catch(e){if(version===deleteVersion)$('delete-error').textContent=e.message;}
}
function requestDelete(request){pendingDelete=request;$('delete-related').checked=false;$('delete-related').disabled=false;$('delete-related-label').hidden=true;$('delete-error').textContent='';$('delete-title').textContent='Review deletion';$('delete-description').textContent='Checking which items will be affected…';$('delete-confirm').disabled=true;$('delete-dialog').showModal();$('delete-cancel').focus();deletePreview();}
$('delete-dialog').addEventListener('close',()=>{pendingDelete=null;deleteVersion++;});
$('delete-cancel').onclick=()=>$('delete-dialog').close();
$('delete-related').onchange=deletePreview;
$('delete-confirm').onclick=async()=>{
 if(!pendingDelete?.expected)return;const request={...pendingDelete};$('delete-confirm').disabled=true;$('delete-cancel').disabled=true;$('delete-related').disabled=true;
 try{const deleted=await api('delete',request);$('delete-dialog').close();clearDisplayedResults();if(deleted.family_id&&loadedDefinition?.family_id===deleted.family_id){loadedDefinition=null;profiles=[profile()];['title','instructions','content','context','criteria'].forEach(id=>$(id).value='');$('test-editor').hidden=true;drawColumns();setPage('tests');}coverageFilter=null;await history();note('');}
 catch(e){$('delete-error').textContent=e.message;await deletePreview();}
 finally{$('delete-cancel').disabled=false;$('delete-related').disabled=false;}
};
$('delete-selected').onclick=()=>requestDelete({kind:'results',ids:[...selectedResults]});
$('undo-delete').onclick=async()=>{if(!lastDeletion)return;try{await api('undo-delete',{id:lastDeletion.id});clearDisplayedResults();await history();note('Deleted items restored.');}catch(e){note(e.message);}};
function showConnections(s){
 if(!s)return;const first=!connectionState;connectionState=s;
 const previous=lemonadeBase;lemonadeBase=s.lemonade_url;
 if(first){$('lemonade-url').value=lemonadeBase;profiles.forEach(p=>{if(p.url===previous)p.url=lemonadeBase;});}
 ['lemonade','openrouter'].forEach(provider=>{
  const state=s[provider];$(provider+'-key-status').textContent=state.configured?'Configured · '+state.source:'Not configured';
  $(provider+'-remember').disabled=!s.can_remember;
  if(first)$(provider+'-remember').checked=state.source==='saved';
 });
}
function toggleConnections(open){$('connections-panel').hidden=!open;$('connections-toggle').setAttribute('aria-expanded',String(open));if(open)$('connections-panel').scrollIntoView({behavior:'smooth',block:'start'});else{$('lemonade-key').value='';$('openrouter-key').value='';}}
$('connections-toggle').onclick=()=>toggleConnections($('connections-panel').hidden);
$('connections-close').onclick=()=>toggleConnections(false);
['lemonade','openrouter'].forEach(provider=>{
 const feedback=$(provider+'-feedback');
 const save=async(remove=false)=>{
  const old=lemonadeBase;
  const payload={provider,key:remove?'':$(provider+'-key').value,remember:$(provider+'-remember').checked,remove};
  if(provider==='lemonade')payload.url=$('lemonade-url').value;
  try{
   const state=await api('connections',payload);$(provider+'-key').value='';showConnections(state);
   if(provider==='lemonade')profiles.forEach(p=>{if(p.provider==='lemonade'&&p.url===old)p.url=lemonadeBase;});
   catalogs.clear();drawColumns();await history();
   feedback.textContent=remove?'Entered key removed. Environment fallback, if configured, remains available.':'Connection saved. You can test it now.';
  }catch(e){feedback.textContent=e.message;}
 };
 $(provider+'-form').onsubmit=e=>{e.preventDefault();save();};
 $(provider+'-remove').onclick=()=>save(true);
 $(provider+'-test').onclick=async()=>{
  if($(provider+'-key').value.trim()){feedback.textContent='Save the entered key before testing.';return;}
  const button=$(provider+'-test');button.disabled=true;feedback.textContent='Checking model availability…';
  try{const result=await api('test-connection',{provider});feedback.textContent=`Connected · ${result.count} models available. No inference run.`;catalogs.clear();drawColumns();}
  catch(e){feedback.textContent=e.message;}finally{button.disabled=false;}
 };
});
async function openItem(id,kind){try{const v=await api('item/'+id);if(kind==='draft'){loadDraft(v);setPage('run');note('Saved setup loaded.');}else{current=v;loadDraft(v.draft);renderResults();setPage('results');} }catch(e){note(e.message);}}
function settingsText(p){return Object.entries(p.settings).map(([k,v])=>`${settingHelp[k]?.[0]||k} ${v}`).join(' · ')||'Service defaults';}
async function refreshLibrary(){const [data,coverage]=await Promise.all([api('results'),api('coverage')]);savedResults=data.results;coverageData=coverage;drawTestCards();drawCoverage();drawLibrary();}
function drawTestCards(){const box=$('test-list');box.replaceChildren();const seen=new Set(),tests=coverageData.tests.filter(t=>{if(seen.has(t.family_id))return false;seen.add(t.family_id);return true;});if(!tests.length){const empty=el('div','empty');empty.append(el('h3','','Your first test can be simple.'),el('p','','Try the practice example above, or create a test with a task and a short piece of text.'));box.append(empty);}
tests.forEach(test=>{const card=el('article','test-card'),count=coverageData.cells.filter(c=>c.test_id===test.id).reduce((n,c)=>n+c.results.length,0);const family=new Set(coverageData.tests.filter(t=>t.family_id===test.family_id).map(t=>t.id)),when=ms=>new Date(ms*1000).toLocaleString([],{dateStyle:'medium',timeStyle:'short'}),runs=savedResults.filter(r=>family.has(r.test_id)),last=runs.reduce((m,r)=>Math.max(m,r.created||0),0),tried=[...new Set(coverageData.cells.filter(c=>family.has(c.test_id)&&c.attempts>0).map(c=>coverageData.models.find(m=>m.id===c.model_id)?.title).filter(Boolean))],clip=(t,n)=>{t=t.replace(/\s+/g,' ').trim();return t.length>n?t.slice(0,n)+'…':t;},source=el('p','test-source'),sourceLabel=el('strong','','Source: ');source.append(sourceLabel,document.createTextNode(clip(test.draft.content,110)));
 card.append(el('h3','',test.title),el('p','test-meta',`${test.draft.task==='writing'?'Writing task':'Fact extraction'} · version ${test.revision} · ${count} saved answer${count===1?'':'s'}`),el('p','test-meta',`Saved ${when(test.created)} · ${last?'Last run '+when(last):'Not run yet'} · ${tried.length?'Models tried: '+clip(tried.join(', '),70):'No models tried'}`),el('p','',clip(test.draft.instructions,160)),source);const actions=el('div','actions'),choose=el('button','primary','Choose this test →'),edit=el('button','','Edit test');choose.setAttribute('aria-label','Choose test '+test.title);choose.onclick=()=>{loadDraft({...test.draft,test_id:test.id});$('test-editor').hidden=true;setPage('run');};edit.onclick=()=>{loadDraft({...test.draft,test_id:test.id});openEditor(false);};actions.append(choose,edit);card.append(actions);box.append(card);});}
function drawCoverage(){
 const filter=$('coverage-model'),value=filter.value;filter.replaceChildren();const all=el('option','','All tracked models');all.value='';filter.append(all);coverageData.models.forEach(m=>{const option=el('option','',`${m.provider} / ${m.model}${m.endpoint?' / '+m.endpoint:''}`);option.value=m.id;filter.append(option);});filter.value=value;
 const models=coverageData.models.filter(m=>!filter.value||m.id===filter.value),term=$('test-filter').value.toLocaleLowerCase(),seen=new Set();
 const tests=coverageData.tests.filter(t=>{const latest=!seen.has(t.family_id);seen.add(t.family_id);return ($('show-revisions').checked||latest)&&t.title.toLocaleLowerCase().includes(term);});
 const box=$('test-coverage');box.replaceChildren();
 if(!tests.length){box.append(el('p','hint','Save a reusable test above to start tracking coverage.'));return;}
 if(!models.length){box.append(el('p','hint','Choose a model and click Track configured models to add a coverage column.'));return;}
 const table=el('table','coverage-table'),head=el('thead'),hr=el('tr');hr.append(el('th','','Test definition'));models.forEach(m=>{const th=el('th');th.append(el('strong','',m.model),el('small','',`${m.provider}${m.url?' · '+m.url:m.endpoint?' · '+m.endpoint:''}`));hr.append(th);});head.append(hr);table.append(head);const body=el('tbody');
 tests.forEach(test=>{const tr=el('tr'),name=el('th');name.append(el('strong','',test.title),el('small','',`Revision ${test.revision} · ${test.draft.task}`));tr.append(name);models.forEach(model=>{
  const cell=coverageData.cells.find(c=>c.test_id===test.id&&c.model_id===model.id),td=el('td');
  td.append(el('div',cell.status==='results'?'coverage-present':'coverage-missing',cell.status==='not_run'?'No results · not run':cell.status==='errors_only'?`No successful results · ${cell.attempts} failed attempts`:`${cell.results.length} saved result${cell.results.length===1?'':'s'}`));
  if(cell.attempts){const view=el('button','','View answers');view.onclick=()=>{coverageFilter={test_id:test.id,model_id:model.id};$('result-filter').value='';drawLibrary();setPage('results');$('saved-results').scrollIntoView({behavior:'smooth',block:'start'});};td.append(view);}
  if(cell.results.length>=2){const compare=el('button','','Compare repeats');compare.onclick=async()=>{try{selectedResults.clear();cell.results.slice(0,6).forEach(id=>selectedResults.add(id));drawLibrary();showComparison(await api('compare',{ids:[...selectedResults]}));}catch(e){$('coverage-notice').textContent=e.message;}};td.append(compare);}
  const quick=el('button','',cell.status==='not_run'?'Quick test':'Try again');quick.setAttribute('aria-label',`${cell.status==='not_run'?'Quick test':'Try again'} ${test.title} version ${test.revision} on ${model.model}`);quick.onclick=async()=>{try{const d=await api('tests/load',{test_id:test.id,model_id:model.id});loadDraft(d);setPage('run');note('Test and model loaded. Review the summary, then choose Run test when you’re ready.');}catch(e){$('coverage-notice').textContent=e.message;}};td.append(quick);tr.append(td);
 });body.append(tr);});table.append(body);box.append(table);
}
$('save-test-definition').onclick=async()=>{if(!checkTestFields())return;try{const definition=await api('tests/save',{draft:draft(),previous:loadedDefinition?.id});await refreshLibrary();loadDraft({...definition.draft,test_id:definition.id});$('test-editor').hidden=true;setPage('run');note(`Test saved as version ${definition.revision}. Next: choose the model you want to try.`);}catch(e){note(e.message);}};
$('save-run-version').onclick=()=>$('save-test-definition').click();
$('track-models').onclick=async()=>{try{for(const p of profiles)await api('models/track',{profile:p});await refreshLibrary();$('coverage-notice').textContent='Configured models added. No inference was run.';}catch(e){$('coverage-notice').textContent=e.message;}};
['test-filter','coverage-model','show-revisions'].forEach(id=>$(id).addEventListener('input',drawCoverage));
$('clear-coverage-filter').onclick=()=>{coverageFilter=null;drawLibrary();};
function drawLibrary(){
 const box=$('saved-results'),term=$('result-filter').value.toLocaleLowerCase();box.replaceChildren();
 const coverageIds=coverageFilter?new Set(coverageData.cells.find(c=>c.test_id===coverageFilter.test_id&&c.model_id===coverageFilter.model_id)?.all_results||[]):null;
 const visible=savedResults.filter(e=>(!coverageIds||coverageIds.has(e.id))&&(e.title+' '+e.profile.model+' '+e.profile.provider+' '+settingsText(e.profile)).toLocaleLowerCase().includes(term));
 $('clear-coverage-filter').hidden=!coverageFilter;
 $('result-count').textContent=String(savedResults.length);
 if(!visible.length)box.append(el('p','hint',savedResults.length?'No matching results.':'No results yet. Run a single model to start your collection.'));
 visible.forEach(e=>{
  const row=el('div','saved-result-row'+(selectedResults.has(e.id)?' selected':'')),check=el('input');check.type='checkbox';check.checked=selectedResults.has(e.id);check.setAttribute('aria-label',`Select result ${e.title} · ${e.profile.model} · ${e.id}`);
  check.onchange=()=>{if(check.checked){if(selectedResults.size>=6){check.checked=false;$('library-notice').textContent='Select up to six results.';return;}selectedResults.add(e.id);}else selectedResults.delete(e.id);drawLibrary();};
  const detail=el('div','saved-detail');detail.append(el('strong','',`${e.title} · ${e.profile.model}`),el('small','',`${new Date(e.created*1000).toLocaleString()} · ${e.profile.provider} · ${e.status==='done'?'Answer saved':'Request failed'}${e.profile.provider==='demo'?' · Practice only':''}`),el('small','',settingsText(e.profile)));
  const actions=el('div','actions'),view=el('button','','View'),remove=el('button','danger','Delete');view.onclick=()=>viewResult(e);remove.setAttribute('aria-label',`Delete answer ${e.title} · ${e.id}`);remove.disabled=!!active;remove.onclick=()=>requestDelete({kind:'results',ids:[e.id]});actions.append(view,remove);row.append(check,detail,actions);box.append(row);
 });
 $('compare-results').textContent=`Compare selected (${selectedResults.size})`;$('compare-results').disabled=selectedResults.size<2;
 $('delete-selected').disabled=selectedResults.size===0||!!active;
 $('library-notice').textContent=`${visible.length} of ${savedResults.length} results · ${selectedResults.size} selected`;
}
function outputCard(p,result,caption=''){
 const card=el('article','output'+(result?.status==='error'?' error':''));card.append(el('h3','',p.name),el('div','badge',`${p.provider} / ${p.model}`));
 if(caption)card.append(el('p','saved-caption',caption));
 card.append(el('p','metrics','Settings: '+settingsText(p)));
 if(result?.deleted){card.append(el('p','hint','This answer was deleted. Use Undo deletion to restore it.'));return card;}
 if(!result){card.append(el('p','hint',current?.status==='running'?'Waiting for the answer…':'This request was not sent'));return card;}
 const u=result.usage,usage=u?`${u.prompt_tokens??'?'} input · ${u.completion_tokens??'?'} output tokens`:'The service did not report token usage.';
 card.append(el('p','metrics',p.provider==='demo'?'Practice answer · no real model was used':`${result.seconds.toFixed(2)} seconds · Reported cost ${u?.cost!=null?'$'+u.cost:'not available'}`));
 if(result.finish_reason==='length')card.append(el('p','badge','The answer reached its length limit and may be unfinished.'));
 card.append(el('pre','',result.error||result.text||'(No visible output)'));
 if(result.reasoning){const detail=el('details');detail.append(el('summary','','Reasoning supplied by the model'),el('pre','',typeof result.reasoning==='string'?result.reasoning:JSON.stringify(result.reasoning)));card.append(detail);}
 const evidence=el('details');evidence.append(el('summary','','Settings, usage & technical details'),el('p','hint',settingsText(p)),el('p','hint',`Output limit: ${result.request?.max_tokens??'unknown'} tokens · seed ${result.seed}. ${usage}`),el('pre','',JSON.stringify(result,null,2)));card.append(evidence);return card;
}
function renderResults(){
 if(!current)return;comparisonExport=null;const r=current,box=$('results');box.replaceChildren();$('output-title').textContent='Your answer';
 $('status').textContent=`${r.status==='completed'?'Saved':r.status.replaceAll('_',' ')} · ${r.results.length} of ${r.request_limit} requests finished`;$('export').disabled=false;
 if(r.note)box.append(el('p','hint',r.note));
 for(let n=0;n<r.draft.repeats;n++){if(r.draft.repeats>1)box.append(el('div','repeat-title',`ANSWER ${n+1} OF ${r.draft.repeats}`));const grid=el('div','output-grid');r.draft.profiles.forEach((p,i)=>grid.append(outputCard(p,r.results.find(x=>x.column===i&&x.repeat===n))));box.append(grid);}
}
function showComparison(data){
 setPage('results');
 comparisonExport=data;const box=$('results');box.replaceChildren();$('output-title').textContent=data.entries.length>1?'Compare saved results':'Saved result';$('status').textContent=`${data.entries.length} saved results · no model calls`;$('export').disabled=false;
 if(data.entries.length>1)box.append(el('p','comparison-input-note'+(data.same_input?'':' different'),data.same_input?'Same source, context, criteria, instructions and task. Review settings, seeds and output limits below.':'Inputs differ: '+data.differences.join(', ')+'. These results are not a controlled comparison.'));
 const grid=el('div','output-grid');
 data.entries.forEach(e=>{const card=outputCard(e.profile,e.result,`${e.title} · ${new Date(e.created*1000).toLocaleString()} · repeat ${e.result.repeat+1} · seed ${e.result.seed}`);const inputs=el('details');inputs.append(el('summary','','Source, context & criteria'),el('pre','',`${e.task}\n\n${e.instructions}\n\nContext: ${e.context||''}\n\nCriteria: ${e.criteria||''}\n\n${e.content}`));card.append(inputs);grid.append(card);});box.append(grid);$('results').scrollIntoView({behavior:'smooth',block:'start'});
}
async function viewResult(e){try{const run=await api('item/'+e.run_id),result=run.results[Number(e.id.split(':')[1])];showComparison({kind:'saved_result',same_input:true,differences:[],entries:[{id:e.id,run_id:e.run_id,title:e.title,created:e.created,profile:e.profile,task:run.draft.task,instructions:run.draft.instructions,context:run.draft.context,criteria:run.draft.criteria,content:run.draft.content,result}]});}catch(e){$('library-notice').textContent=e.message;}}
$('result-filter').oninput=drawLibrary;
$('refresh-results').onclick=()=>refreshLibrary().catch(e=>$('library-notice').textContent=e.message);
$('clear-selection').onclick=()=>{selectedResults.clear();drawLibrary();};
$('compare-results').onclick=async()=>{try{showComparison(await api('compare',{ids:[...selectedResults]}));}catch(e){$('library-notice').textContent=e.message;}};
async function poll(){try{if(active){current=await api('item/'+active);if(!comparisonExport)renderResults();if(current.status!=='running'){clearInterval(timer);timer=null;await history();}}else{clearInterval(timer);timer=null;}}catch(e){note(e.message);}}
$('save').onclick=async()=>{try{await api('save',draft());await history();note('Setup saved locally, including content and settings.');}catch(e){note(e.message);}};
$('run').onclick=async()=>{try{$('run').disabled=true;note('');const r=await api('run',draft());current=await api('item/'+r.id);renderResults();setPage('results');await history();if(!active){current=await api('item/'+r.id);renderResults();}$('results').scrollIntoView({behavior:'smooth',block:'start'});}catch(e){note(e.message);await history();}};
$('cancel').onclick=async()=>{try{await api('cancel',{});note('Stopped. Queued requests will not be sent. A request already in progress is abandoned here, but the provider may still finish it and charge for it.');}catch(e){note(e.message);}};
$('add').onclick=()=>{if(profiles.length>=6)return note('Maximum six columns');profiles.push(profile('Column '+String.fromCharCode(65+profiles.length)));drawColumns();};
$('new').onclick=()=>{current=null;openEditor(true);note('');};
$('export').onclick=()=>{const data=comparisonExport||current;if(!data)return;const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'})),a=el('a');a.href=url;a.download='juicr-'+(comparisonExport?'saved-results':current.id)+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
['task','repeats','content'].forEach(k=>$(k).addEventListener('input',estimate));
document.querySelector('main').addEventListener('input',updateDefinitionStatus);
profiles=[profile('Model configuration')];drawColumns();history().then(()=>{drawColumns();if(active)poll();}).catch(e=>note(e.message));
updatePageCopy();
