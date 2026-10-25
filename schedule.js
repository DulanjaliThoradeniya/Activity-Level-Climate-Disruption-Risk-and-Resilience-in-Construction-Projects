/* Calendar schedule prototype. Dates are local calendar dates (YYYY-MM-DD);
   finishes and relationship lags/leads use inclusive working-day calendars. */

(() => {

  const $=s=>document.querySelector(s), esc=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

  const key='c-resist-schedule-v1', headers=['id','name','stage','durationDays','predecessors','dependencies','exposure','heatSensitivity','rainSensitivity'];

  let activities=[], selected=null, computed=null, intervention=null, persistenceWarning='', preserveBadSave=false, scenarioActive=false;

  const readCal=()=>({weekdays:($('#workdays').value||'1,2,3,4,5').split(',').map(x=>+x.trim()).filter(x=>x>=1&&x<=7),holidays:new Set(($('#holidays').value||'').split(',').map(x=>x.trim()).filter(Boolean))});

  const validDate=s=>/^\d{4}-\d\d-\d\d$/.test(s)&&!Number.isNaN(Date.parse(s+'T00:00:00Z'))&&new Date(s+'T00:00:00Z').toISOString().slice(0,10)===s;

  const add=(s,n)=>window.CResistScheduleEngine.add(s,n), schedule=window.CResistScheduleEngine.calculate, parseCSV=window.CResistScheduleEngine.parseCSV;

  function currentScenario(factor=+$('#productivity').value,recovery=+$('#recoveryDays').value){const start=$('#scenarioStart').value,end=$('#scenarioEnd').value;if(!validDate(start)||!validDate(end)||end<start)throw Error('Scenario dates must be valid and end on or after start.');if(!Number.isFinite(factor)||factor<0||factor>1||!Number.isInteger(recovery)||recovery<0)throw Error('Productivity must be 0–1 and recovery must be a non-negative whole number.');return {start,end,hazard:$('#scheduleHazard').value,factor,recovery}}

  const dependenciesOf=a=>Array.isArray(a.dependencies)?a.dependencies:(a.predecessors||'').split(',').map(predecessorId=>predecessorId.trim()).filter(Boolean).map(predecessorId=>({predecessorId,type:a.relationship||'FS',lag:Number(a.lag)||0}));
  const dependencySummary=id=>{const incoming=activities.find(a=>a.id===id),inCount=incoming?dependenciesOf(incoming).length:0,outCount=activities.reduce((count,a)=>count+dependenciesOf(a).filter(link=>link.predecessorId===id).length,0);return `${inCount} predecessor${inCount===1?'':'s'} · ${outCount} successor${outCount===1?'':'s'}`};

  function drawEditor(){const el=$('#activityEditor');el.innerHTML=`<table><thead><tr>${['ID','Activity','Stage','Duration (workdays)','Dependencies','Exposure','Heat','Rain',''].map(x=>`<th>${x}</th>`).join('')}</tr></thead><tbody>${activities.map(a=>`<tr data-id="${esc(a.id)}"><td><input data-f="id" value="${esc(a.id)}"></td><td><input data-f="name" value="${esc(a.name)}"></td><td><input data-f="stage" value="${esc(a.stage)}"></td><td><input data-f="durationDays" type="number" min="1" step="1" value="${a.durationDays}"></td><td><button type="button" data-dependencies="${esc(a.id)}">Edit links</button><small class="dependency-summary">${dependencySummary(a.id)}</small></td><td><select data-f="exposure">${['outdoor','partially sheltered','indoor','not confirmed'].map(x=>`<option ${a.exposure===x?'selected':''}>${x}</option>`)}</select></td><td>${sens(a,'heatSensitivity')}</td><td>${sens(a,'rainSensitivity')}</td><td><button type="button" data-remove="${esc(a.id)}" aria-label="Delete ${esc(a.name)}">×</button></td></tr>`).join('')}</tbody></table>`;

    el.querySelectorAll('[data-f]').forEach(input=>input.addEventListener('change',()=>{const tr=input.closest('tr'),a=activities.find(x=>x.id===tr.dataset.id),field=input.dataset.f;if(field==='id'&&input.value!==a.id){const oldId=a.id,newId=input.value;activities.forEach(item=>{item.dependencies=dependenciesOf(item).map(link=>({...link,predecessorId:link.predecessorId===oldId?newId:link.predecessorId}));if(item.id===oldId)item.id=newId;item.predecessors=dependenciesOf(item).map(link=>link.predecessorId).join(',')});if(selected===oldId)selected=newId}else a[field]=input.value;render()}));
    el.querySelectorAll('[data-dependencies]').forEach(button=>button.onclick=()=>openDependencies(button.dataset.dependencies));
    el.querySelectorAll('[data-remove]').forEach(button=>button.onclick=()=>{const id=button.dataset.remove;activities=activities.filter(a=>a.id!==id).map(a=>{a.dependencies=dependenciesOf(a).filter(link=>link.predecessorId!==id);a.predecessors=a.dependencies.map(link=>link.predecessorId).join(',');return a});render()})}

  function openDependencies(activityId){
    const activity=activities.find(item=>item.id===activityId||item.catalogueId===activityId);if(!activity)return;
    activityId=activity.id;
    let dialog=$('#activityDependenciesDialog');
    if(!dialog){dialog=document.createElement('dialog');dialog.id='activityDependenciesDialog';dialog.className='schedule-dialog dependency-dialog';document.body.append(dialog)}
    const draft={incoming:dependenciesOf(activity).map(link=>({...link})),outgoing:activities.flatMap(successor=>dependenciesOf(successor).filter(link=>link.predecessorId===activityId).map(link=>({activityId:successor.id,type:link.type||'FS',lag:Number(link.lag)||0})))};
    const activityName=item=>`${item.catalogueCode||item.id} · ${item.name}`;
    const edgeRows=(direction,links)=>links.map((link,index)=>{const otherId=direction==='incoming'?link.predecessorId:link.activityId,options=activities.filter(item=>item.id!==activityId);return `<div class="dependency-row" data-dependency-row="${direction}" data-index="${index}"><label>${direction==='incoming'?'Predecessor activity':'Successor activity'}<select data-edge-activity>${options.map(item=>`<option value="${esc(item.id)}" ${item.id===otherId?'selected':''}>${esc(activityName(item))}</option>`).join('')}</select></label><label>Relationship<select data-edge-type>${['FS','SS','FF','SF'].map(type=>`<option ${type===(link.type||'FS')?'selected':''}>${type}</option>`).join('')}</select></label><label>Lag / lead (working days)<input data-edge-lag type="number" step="1" value="${Number(link.lag)||0}" aria-label="Lag or lead in working days; negative values are leads"></label><button type="button" data-edge-remove aria-label="Remove dependency">Remove</button></div>`}).join('');
    function renderDialog(){
      dialog.innerHTML=`<div class="dialog-head"><div><p class="eyebrow">SCHEDULE DEPENDENCIES</p><h2>${esc(activityName(activity))}</h2></div><button type="button" class="dialog-close" data-dependency-close aria-label="Close dependency editor">Close</button></div><p class="stage-dialog-note">Add any number of predecessor and successor links. Set a separate FS, SS, FF or SF relationship and working-day lag for every link. Use a negative lag for lead.</p><section class="dependency-section"><h3>Predecessors</h3>${draft.incoming.length?edgeRows('incoming',draft.incoming):'<p>No predecessors added.</p>'}<button type="button" data-edge-add="incoming">+ Add predecessor</button></section><section class="dependency-section"><h3>Successors</h3>${draft.outgoing.length?edgeRows('outgoing',draft.outgoing):'<p>No successors added.</p>'}<button type="button" data-edge-add="outgoing">+ Add successor</button></section><div class="catalogue-actions"><button type="button" data-dependency-save>Save dependencies</button></div>`;
      dialog.querySelector('[data-dependency-close]').onclick=()=>dialog.close();
      dialog.querySelectorAll('[data-edge-add]').forEach(button=>button.onclick=()=>{const direction=button.dataset.edgeAdd,options=activities.filter(item=>item.id!==activityId&&(direction==='incoming'?!draft.incoming.some(link=>link.predecessorId===item.id):!draft.outgoing.some(link=>link.activityId===item.id)));if(!options.length){$('#scheduleMessage').textContent=`No more selected activities can be added as ${direction==='incoming'?'predecessors':'successors'}.`;$('#scheduleMessage').className='schedule-error';return}draft[direction].push(direction==='incoming'?{predecessorId:options[0].id,type:'FS',lag:0}:{activityId:options[0].id,type:'FS',lag:0});renderDialog()});
      dialog.querySelectorAll('[data-edge-remove]').forEach(button=>button.onclick=()=>{const row=button.closest('[data-dependency-row]');draft[row.dataset.dependencyRow].splice(+row.dataset.index,1);renderDialog()});
      dialog.querySelectorAll('[data-dependency-row]').forEach(row=>{const direction=row.dataset.dependencyRow,index=+row.dataset.index,link=draft[direction][index];row.querySelector('[data-edge-activity]').onchange=event=>{if(direction==='incoming')link.predecessorId=event.target.value;else link.activityId=event.target.value};row.querySelector('[data-edge-type]').onchange=event=>link.type=event.target.value;row.querySelector('[data-edge-lag]').onchange=event=>link.lag=event.target.value});
      dialog.querySelector('[data-dependency-save]').onclick=()=>{
        const incoming=draft.incoming.map(link=>({...link,lag:Number(link.lag)})),outgoing=draft.outgoing.map(link=>({...link,lag:Number(link.lag)}));
        if([...incoming,...outgoing].some(link=>!Number.isInteger(link.lag))){$('#scheduleMessage').textContent='Dependency lag/lead must be a whole number of working days.';$('#scheduleMessage').className='schedule-error';return}
        if(new Set(incoming.map(link=>link.predecessorId)).size!==incoming.length||new Set(outgoing.map(link=>link.activityId)).size!==outgoing.length){$('#scheduleMessage').textContent='Each activity pair can have only one dependency link. Remove duplicate links before saving.';$('#scheduleMessage').className='schedule-error';return}
        activities.forEach(item=>{item.dependencies=dependenciesOf(item).filter(link=>item.id!==activityId&&link.predecessorId!==activityId);if(item.id===activityId)item.dependencies=incoming;item.predecessors=item.dependencies.map(link=>link.predecessorId).join(',')});
        outgoing.forEach(link=>{const successor=activities.find(item=>item.id===link.activityId);if(successor)successor.dependencies.push({predecessorId:activityId,type:link.type,lag:link.lag});successor.predecessors=successor.dependencies.map(edge=>edge.predecessorId).join(',')});
        render();dialog.close();
      };
    }
    renderDialog();dialog.showModal();
  }

  function sens(a,k){return `<select data-f="${k}">${['low','medium','high'].map(x=>`<option ${a[k]===x?'selected':''}>${x}</option>`)}</select>`}

  function render(){
    drawEditor();
    if(!activities.length){
      computed=null;intervention=null;$('#phaseSummary').textContent='';
      $('#activityGantt').innerHTML='<p class="gantt-empty">Choose a project stage and add activities to build the timeline.</p>';
      $('#scheduleMessage').textContent='';$('#impactCount').textContent='No activities selected';
      $('#activityCount').textContent='Add activities from a project stage';
      $('#scheduleResults').innerHTML='<p class="impact-empty">No activities added yet.</p>';
      window.updateProjectCompletion?.('Not calculated yet');return;
    }
    if(activities.some(a=>!Number.isInteger(+a.durationDays)||+a.durationDays<1)){
      computed=null;intervention=null;$('#phaseSummary').textContent='';
      $('#activityGantt').innerHTML=`<div class="gantt-unscheduled"><strong>Enter planned durations</strong><p>Open a project stage, select activities, and enter each duration in working days.</p><ul>${activities.map(a=>`<li><b>${esc(a.catalogueCode||a.id)}</b> ${esc(a.name)}</li>`).join('')}</ul></div>`;
      $('#scheduleMessage').textContent='';$('#impactCount').textContent=`${activities.length} selected · unscheduled`;
      $('#activityCount').textContent=`${activities.length} activities · duration needed`;
      $('#scheduleResults').innerHTML='<p class="impact-empty">Set a duration for each selected activity to calculate climate impacts.</p>';
      window.updateProjectCompletion?.('Not calculated yet');return;
    }
    try{
      const workHours=+$('#workHours').value;
      if(!Number.isFinite(workHours)||workHours<=0||workHours>24)throw Error('Working hours per day must be greater than 0 and no more than 24. Durations remain in workdays.');
      const scenario=currentScenario(),scheduleScenario=scenarioActive?scenario:{...scenario,factor:1,recovery:0};
      computed=schedule(activities,$('#projectStart').value,readCal(),scheduleScenario);
      const interventionInputs=currentScenario(+$('#interventionProductivity').value,+$('#interventionRecoveryDays').value);
      const interventionScenario=scenarioActive?interventionInputs:{...interventionInputs,factor:1,recovery:0};
      intervention=schedule(activities,$('#projectStart').value,readCal(),interventionScenario);
      $('#scheduleMessage').textContent='';$('#scheduleMessage').className='';drawChart();drawResults();
      if(!preserveBadSave){
        try{localStorage.setItem(key,JSON.stringify({schemaVersion:1,activities,projectStart:$('#projectStart').value,workdays:$('#workdays').value,workHours,holidays:$('#holidays').value,scenario,intervention:{factor:interventionInputs.factor,recovery:interventionInputs.recovery}}))}
        catch{persistenceWarning=' · Browser storage unavailable; project not saved.';$('#scheduleMessage').textContent=persistenceWarning}
      }
    }catch(e){
      computed=null;intervention=null;$('#scheduleMessage').textContent=e.message;$('#scheduleMessage').className='schedule-error';
      $('#activityGantt').replaceChildren();$('#scheduleResults').innerHTML=`<p class="impact-empty">${esc(e.message)}</p>`;
      $('#activityCount').textContent='Check schedule inputs';window.updateProjectCompletion?.('Not calculated yet');
    }
  }

  function drawChart(){const c=computed,ids=activities.map(a=>a.id),min=$('#projectStart').value,max=[c.finishProject,c.adjustedFinish].sort().at(-1),span=Math.max(1,(Date.parse(max+'T00:00:00Z')-Date.parse(min+'T00:00:00Z'))/86400000+1),unit=Math.max(5,Math.min(18,740/span)),label=250,svgW=label+span*unit+30,row=34,height=62+ids.length*row;let s=`<svg xmlns="http://www.w3.org/2000/svg" width="${svgW}" height="${height}" role="img" aria-label="Activity Gantt chart comparing planned dates with climate-adjusted dates"><rect width="${svgW}" height="${height}" fill="#fff"/>`;

    for(let d=0;d<span;d++){const date=add(min,d);if(date.slice(-2)==='01'||d%7===0)s+=`<text x="${label+d*unit}" y="19" font-size="9" fill="#66736b">${date.slice(5)}</text><line x1="${label+d*unit}" x2="${label+d*unit}" y1="28" y2="${height-8}" stroke="#e5eae5"/>`}

    ids.forEach((id,i)=>{const a=activities.find(x=>x.id===id),b=c.base[id],r=c.adj[id],x0=(Date.parse(b.start+'T00:00:00Z')-Date.parse(min+'T00:00:00Z'))/86400000,x1=(Date.parse(b.finish+'T00:00:00Z')-Date.parse(min+'T00:00:00Z'))/86400000,ax=(Date.parse(r.start+'T00:00:00Z')-Date.parse(min+'T00:00:00Z'))/86400000,az=(Date.parse(r.finish+'T00:00:00Z')-Date.parse(min+'T00:00:00Z'))/86400000,y=39+i*row;s+=`<text x="8" y="${y+8}" font-size="10" font-weight="600" fill="#26362e">${esc(a.catalogueCode||a.id)} · ${esc(a.name.slice(0,25))}</text><rect x="${label+x0*unit}" y="${y}" width="${Math.max(unit,(x1-x0+1)*unit)}" height="8" rx="4" fill="#54866a"><title>Planned ${b.start} to ${b.finish}</title></rect><rect x="${label+ax*unit}" y="${y+12}" width="${Math.max(unit,(az-ax+1)*unit)}" height="8" rx="4" fill="${r.direct||r.inherited?'#d8894e':'#5486a8'}"><title>Actual with climate effect ${r.start} to ${r.finish}</title></rect>`});s+='</svg>';$('#activityGantt').innerHTML=s;$('#activityCount').textContent=`${activities.length} activities · planned completion ${c.finishProject} · scenario completion ${c.adjustedFinish}`}

  function drawResults(){const c=computed,windowStart=$('#scenarioStart').value,windowEnd=new Date(Date.parse(windowStart+'T00:00:00Z')+13*86400000).toISOString().slice(0,10),impacted=c.order.map(id=>{const activity=activities.find(item=>item.id===id),result=c.adj[id],baseline=c.base[id],delay=Math.max(0,Math.round((Date.parse(result.finish+'T00:00:00Z')-Date.parse(baseline.finish+'T00:00:00Z'))/86400000));return {activity,result,baseline,delay}}).filter(item=>(item.result.direct||item.result.inherited)&&item.result.finish>=windowStart&&item.result.start<=windowEnd).sort((a,b)=>b.delay-a.delay||b.result.loss-a.result.loss),risk=days=>days>3?'High':days>0?'Moderate':'Low',cards=impacted.length?impacted.map(item=>`<li class="impact-activity"><div><strong>${esc(item.activity.catalogueCode||item.activity.id)} · ${esc(item.activity.name)}</strong><small>Planned finish ${item.baseline.finish} · climate-adjusted finish ${item.result.finish}</small></div><span class="impact-delay">${item.delay} d delay</span><span class="impact-risk risk-${risk(item.delay).toLowerCase()}">${risk(item.delay)}</span></li>`).join(''):'<li class="impact-empty">No selected activities are modelled as impacted in this 14-day window. Check task exposure and rainfall sensitivity, activity timing, and scenario assumptions.</li>';$('#scheduleResults').innerHTML=`<p class="impact-period">Assessment window: ${windowStart} to ${windowEnd}</p><ul class="impacted-activity-list">${cards}</ul><p class="impact-band-note">Indicative schedule-shift bands: Low = 0 days; Moderate = 1–3 days; High = more than 3 days. These are modelled delay screens, not validated risk probabilities.</p><div class="completion-result"><span>Calculated project completion</span><strong>${c.adjustedFinish}</strong></div>`;window.updateProjectCompletion?.(c.adjustedFinish)}

  function selectCatalogue(id,checked){const c=(window.CResistActivityCatalogue||[]).find(x=>x.id===id);if(!c)return;const ix=activities.findIndex(a=>a.catalogueId===id);if(checked&&ix<0)activities.push({id:`${id}#1`,catalogueId:id,catalogueCode:c.code,name:c.name,stage:`${c.phaseCode} ${c.phaseName}`,durationDays:'',predecessors:'',dependencies:[],exposure:'outdoor',heatSensitivity:'low',rainSensitivity:'medium',scope:c.scope,resources:c.links});if(!checked&&ix>=0){const removedId=activities[ix].id;activities=activities.filter(a=>a.id!==removedId).map(a=>{a.dependencies=dependenciesOf(a).filter(link=>link.predecessorId!==removedId);a.predecessors=a.dependencies.map(link=>link.predecessorId).join(',');return a})}render();window.dispatchEvent(new Event('c-resist-schedule-change'))}
  function configure(catalogueId,field,value){const a=activities.find(x=>x.catalogueId===catalogueId);if(!a)return;a[field]=value;render();window.dispatchEvent(new Event('c-resist-schedule-change'))}
  window.CResistSchedule={calculate:schedule,parseCSV,select:selectCatalogue,configure,editDependencies:openDependencies,has:id=>activities.some(a=>a.catalogueId===id),get:id=>activities.find(a=>a.catalogueId===id)};
  window.renderCalendarSchedule=render;window.activateClimateScenario=()=>{scenarioActive=true;render()};window.resetClimateScenario=()=>{scenarioActive=false;render()};$('#openActivitySchedule').onclick=()=>$('#activityScheduleDialog').showModal();$('#openCalendarSettings').onclick=()=>$('#calendarSettingsDialog').showModal();$('#openScenarioSettings').onclick=()=>$('#scenarioSettingsDialog').showModal();document.querySelectorAll('[data-close]').forEach(b=>b.onclick=()=>$('#'+b.dataset.close).close());$('#fitProject').onclick=()=>{const c=$('#mapCanvas');c.scrollLeft=0;c.scrollTop=0};$('#activityEditor').addEventListener('click',e=>{if(e.target.closest('tr')){selected=e.target.closest('tr').dataset.id;if(computed)drawResults()}});

  ['projectStart','workdays','workHours','holidays','scenarioStart','scenarioEnd','scheduleHazard','productivity','recoveryDays','interventionProductivity','interventionRecoveryDays'].forEach(id=>$('#'+id).addEventListener('change',render));$('#addActivity').onclick=()=>{const n=Math.max(0,...activities.map(a=>+(a.id.match(/\d+/)?.[0]||0)))+1;activities.push({id:'A'+n,name:'New activity',stage:'New stage',durationDays:3,predecessors:'',exposure:'outdoor',heatSensitivity:'medium',rainSensitivity:'medium'});render()};

  $('#csvImport').onchange=async e=>{try{const rows=parseCSV(await e.target.files[0].text()),head=rows.shift().map(x=>x.trim()),required=['id','name','stage','durationDays','predecessors','exposure','heatSensitivity','rainSensitivity'];if(required.some(h=>!head.includes(h)))throw Error('CSV headers required: '+required.join(', '));const nextRows=rows.map(row=>{const item=Object.fromEntries(head.map((h,i)=>[h,row[i]??'']));if(item.dependencies){try{item.dependencies=JSON.parse(item.dependencies)}catch{throw Error(`Activity ${item.id}: dependencies must be a valid JSON array.`)}}return item});schedule(nextRows,$('#projectStart').value,readCal(),currentScenario());activities=nextRows;render()}catch(err){$('#scheduleMessage').textContent='Import rejected; existing programme kept. '+err.message;$('#scheduleMessage').className='schedule-error'}e.target.value=''};

  $('#downloadTemplate').onclick=()=>download('c-resist-activity-template.csv',headers.join(',')+'\n'+['A1','Site survey','Preliminaries',3,'','','outdoor','medium','low'].map(csvCell).join(',')+'\n'+['A2','Excavate','Groundworks',5,'A1',JSON.stringify([{predecessorId:'A1',type:'FS',lag:0}]),'outdoor','high','high'].map(csvCell).join(',')+'\n');

  $('#exportCsv').onclick=()=>{if(!computed)return;const lines=[['id','name','baselineStart','baselineFinish','baselineFloatWorkingDays','scenarioStart','scenarioFinish','scenarioFinishShiftCalendarDays','directWorkLossProductiveDays','directlyDisrupted','inheritedShift','interventionStart','interventionFinish'].join(',')];for(const id of computed.order){const a=activities.find(x=>x.id===id),b=computed.base[id],r=computed.adj[id],ir=intervention.adj[id],sh=Math.round((Date.parse(r.finish+'T00:00:00Z')-Date.parse(b.finish+'T00:00:00Z'))/86400000);lines.push([a.id,a.name,b.start,b.finish,b.float,r.start,r.finish,sh,r.loss.toFixed(2),r.direct,r.inherited,ir.start,ir.finish].map(csvCell).join(','))}download('c-resist-schedule-results.csv',lines.join('\n'))};

  $('#exportJson').onclick=()=>download('c-resist-project.json',JSON.stringify({schemaVersion:1,activities,projectStart:$('#projectStart').value,workdays:$('#workdays').value,workHours:+$('#workHours').value,holidays:$('#holidays').value,scenario:currentScenario(),intervention:{factor:+$('#interventionProductivity').value,recovery:+$('#interventionRecoveryDays').value}},null,2));

  $('#jsonImport').onchange=async e=>{try{const obj=JSON.parse(await e.target.files[0].text());if(obj.schemaVersion!==1||!Array.isArray(obj.activities))throw Error('Unsupported project JSON schema.');schedule(obj.activities,obj.projectStart,{weekdays:(obj.workdays||'1,2,3,4,5').split(',').map(Number),holidays:new Set((obj.holidays||'').split(',').filter(Boolean))},obj.scenario);if(obj.workHours!==undefined&&(!Number.isFinite(+obj.workHours)||+obj.workHours<=0||+obj.workHours>24))throw Error('Invalid working hours per day.');if(obj.intervention&&(!Number.isFinite(+obj.intervention.factor)||obj.intervention.factor<0||obj.intervention.factor>1||!Number.isInteger(+obj.intervention.recovery)||obj.intervention.recovery<0))throw Error('Invalid intervention assumptions.');activities=obj.activities;$('#projectStart').value=obj.projectStart;$('#workdays').value=obj.workdays||'1,2,3,4,5';$('#workHours').value=obj.workHours||8;$('#holidays').value=obj.holidays||'';Object.entries(obj.scenario||{}).forEach(([k,v])=>{const el=$('#'+({start:'scenarioStart',end:'scenarioEnd',hazard:'scheduleHazard',factor:'productivity',recovery:'recoveryDays'}[k]||''));if(el)el.value=v});if(obj.intervention){$('#interventionProductivity').value=obj.intervention.factor;$('#interventionRecoveryDays').value=obj.intervention.recovery}render()}catch(err){$('#scheduleMessage').textContent='Import rejected; existing programme kept. '+err.message;$('#scheduleMessage').className='schedule-error'}e.target.value=''};

  function csvCell(x){return '"'+String(x).replaceAll('"','""')+'"'}function download(name,txt){const a=document.createElement('a'),url=URL.createObjectURL(new Blob([txt],{type:'text/plain'}));a.href=url;a.download=name;a.click();URL.revokeObjectURL(url)}

  try{const saved=JSON.parse(localStorage.getItem(key)||'null');if(saved!==null&&(saved?.schemaVersion!==1||!Array.isArray(saved.activities)))throw Error('Saved project uses an unsupported schema.');if(saved){schedule(saved.activities,saved.projectStart,{weekdays:(saved.workdays||'1,2,3,4,5').split(',').map(Number),holidays:new Set((saved.holidays||'').split(',').filter(Boolean))},saved.scenario);activities=saved.activities;$('#projectStart').value=saved.projectStart;$('#workdays').value=saved.workdays||'1,2,3,4,5';$('#workHours').value=saved.workHours||8;$('#holidays').value=saved.holidays||'';if(saved.scenario){$('#scenarioStart').value=saved.scenario.start;$('#scenarioEnd').value=saved.scenario.end;$('#scheduleHazard').value=saved.scenario.hazard;$('#productivity').value=saved.scenario.factor;$('#recoveryDays').value=saved.scenario.recovery}if(saved.intervention){$('#interventionProductivity').value=saved.intervention.factor;$('#interventionRecoveryDays').value=saved.intervention.recovery}}}catch{persistenceWarning='Saved project was invalid or incompatible and was left untouched.';preserveBadSave=true}

  render();if(persistenceWarning){$('#scheduleMessage').textContent+=' '+persistenceWarning;$('#scheduleMessage').className='schedule-error'}

})();
