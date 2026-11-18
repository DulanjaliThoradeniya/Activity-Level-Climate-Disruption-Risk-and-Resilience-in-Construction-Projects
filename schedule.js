/* Calendar schedule prototype. Dates are local calendar dates (YYYY-MM-DD);
   finishes and relationship lags/leads use inclusive working-day calendars. */

(() => {

  const $=s=>document.querySelector(s), esc=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

  const scheduleMenu=$('.more-menu'),scheduleMenuPopup=scheduleMenu.querySelector('.more-menu-pop');
  const positionScheduleMenu=()=>{const anchor=scheduleMenu.querySelector('summary').getBoundingClientRect(),width=scheduleMenuPopup.offsetWidth,height=scheduleMenuPopup.offsetHeight,maxTop=Math.max(8,window.innerHeight-height-8),top=Math.min(maxTop,Math.max(8,anchor.bottom+height<=window.innerHeight-8?anchor.bottom+4:anchor.top-height-4)),left=Math.min(Math.max(8,anchor.left),Math.max(8,window.innerWidth-width-8));scheduleMenuPopup.style.setProperty('--schedule-menu-top',`${top}px`);scheduleMenuPopup.style.setProperty('--schedule-menu-left',`${left}px`)};
  scheduleMenu.addEventListener('toggle',()=>{if(scheduleMenu.open){document.body.appendChild(scheduleMenuPopup);scheduleMenuPopup.classList.add('is-floating');positionScheduleMenu()}else{scheduleMenuPopup.classList.remove('is-floating');scheduleMenu.appendChild(scheduleMenuPopup)}});
  window.addEventListener('resize',()=>{if(scheduleMenu.open)positionScheduleMenu()});
  window.addEventListener('scroll',()=>{if(scheduleMenu.open)positionScheduleMenu()},true);

  const key='c-resist-schedule-v1', headers=['id','name','stage','durationDays','predecessors','dependencies','exposure','heatSensitivity','rainSensitivity'];

  let activities=[], selected=null, computed=null, intervention=null, prediction=null, predictionRequest=null, activityKnowledgePromise=null, persistenceWarning='', preserveBadSave=false, scenarioActive=false, predictionState='idle', predictionMessage='', predictionAttemptKey='', forecastBundlePromise=null, historyBundlePromise=null;

  const readCal=()=>({weekdays:($('#workdays').value||'1,2,3,4,5').split(',').map(x=>+x.trim()).filter(x=>x>=1&&x<=7),holidays:new Set(($('#holidays').value||'').split(',').map(x=>x.trim()).filter(Boolean))});

  const validDate=s=>/^\d{4}-\d\d-\d\d$/.test(s)&&!Number.isNaN(Date.parse(s+'T00:00:00Z'))&&new Date(s+'T00:00:00Z').toISOString().slice(0,10)===s;

  const add=(s,n)=>window.CResistScheduleEngine.add(s,n), schedule=window.CResistScheduleEngine.calculate, parseCSV=window.CResistScheduleEngine.parseCSV;
  const upcomingWindow=()=>{const now=new Date(),start=new Date(now.getTime()-now.getTimezoneOffset()*60000).toISOString().slice(0,10);return {start,end:add(start,13)}};
  const showScheduleResultMessage=message=>{$('#scheduleResults').innerHTML=`<p class="impact-empty">${esc(message)}</p><p class="impact-band-note">WSH risk is unassessed until site-specific ratings and an approved matrix are available.</p>`};

  const loadJSON=async path=>{const response=await fetch(path,{cache:'no-store'});if(!response.ok)throw Error(`${path} returned HTTP ${response.status}.`);return response.json()};
  const loadActivityKnowledge=()=>activityKnowledgePromise||(activityKnowledgePromise=loadJSON('data/activity-intervention-knowledge.json').then(bundle=>{if(bundle.schema_version!==1||bundle.bundle_type!=='STATIC_CANDIDATE_KNOWLEDGE_ONLY'||!bundle.activities)throw Error('The static WBS intervention knowledge bundle has an unsupported format.');return bundle}).catch(error=>{activityKnowledgePromise=null;throw error}));
  let assessmentInputs=null,assessmentInputsPromise=null;
  function loadAssessmentInputs(){
    if(!assessmentInputsPromise)assessmentInputsPromise=Promise.all([loadActivityKnowledge(),loadJSON('data/provisional-wsh-examples.json'),loadJSON('data/climate-multi-model-forecasts.json')]).then(([knowledge,examples,forecast])=>{
      if(examples.schema_version!==1||examples.bundle_type!=='SYNTHETIC_PROVISIONAL_WSH_EXAMPLES'||!examples.activities||!forecast.models)throw Error('Assessment input data has an unsupported format.');
      assessmentInputs={knowledge,examples,forecast};return assessmentInputs;
    }).catch(error=>{assessmentInputsPromise=null;throw error});
    return assessmentInputsPromise;
  }
  function singaporeToday(){
    const parts=new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Singapore',year:'numeric',month:'2-digit',day:'2-digit'}).formatToParts(new Date());
    const part=name=>parts.find(item=>item.type===name).value;
    return `${part('year')}-${part('month')}-${part('day')}`;
  }
  function assessmentForActivity(code,activity,planned,location,inputs){
    const knowledge=inputs.knowledge.activities[code],station=window.neaHistoricalStationByArea?.[location]?.code;
    if(!knowledge)throw Error(`No activity information is available for ${code}.`);
    if(!station)throw Error(`No NEA station is mapped to ${location||'the selected location'}.`);
    if(!activity||!planned)throw Error('Schedule this activity to match its dates to the forecast.');
    const today=singaporeToday(),end=add(today,13),start=planned.start>today?planned.start:today,last=planned.finish<end?planned.finish:end;
    const proxy=window.CResistRainfallStation?.resolve(inputs.forecast.models,station,today),sourceStation=proxy?.code||station;
    const days=start<=last?window.CResistAssessmentScreen.selectRainfall(inputs.forecast.models,sourceStation,start,last):[];
    const result=window.CResistAssessmentScreen.screen(knowledge,inputs.examples.activities[code],days);
    const expected=start<=last?dateDelta(start,last)+1:0;
    const selectedResults=days.map(day=>inputs.forecast.models?.[day.model]?.stations?.[sourceStation]?.targets?.rainfall);
    const provenance=[...new Set(selectedResults.map(item=>item?.input_provenance).filter(Boolean))];
    const periods=[...new Set(selectedResults.map(item=>item?.data_end&&item.forecast?.length?`${item.data_end}|${item.forecast[0].date}|${item.forecast[item.forecast.length-1].date}`:null).filter(Boolean))];
    return {code,knowledge,examples:inputs.examples.activities[code],station,proxy,sourceStation,days,result,expected,start,last,provenance,periods,wholeActivityInWindow:planned.start>=today&&planned.finish<=end,summary:window.CResistAssessmentScreen.summary(result.hazards)};
  }
  function renderWeatherAssessment(data){
    const {station,days,result,summary,expected}=data;
    const range=key=>summary?esc(summary[key]):'—';
    const rows=days.map(day=>{const hazards=window.CResistAssessmentScreen.screen(data.knowledge,data.examples,[day]).hazards;return hazards.map((h,index)=>`<tr>${index===0?`<th scope="rowgroup" rowspan="${hazards.length}">${esc(day.date)}</th>`:''}<td>${esc(h.hazard_id)} · ${esc(h.name)}</td><td>${h.score?h.score.severity:'—'}</td><td>${h.score?h.score.likelihood:'—'}</td><td>${h.score?h.score.rpn:'—'}</td><td>${esc(h.definition||'Check site conditions')}</td></tr>`).join('')}).join('');
    const controls=result.controls.map(c=>`<li><b>${esc(c.name)}</b> <small>(${esc(c.hierarchy||'Unclassified')})</small></li>`).join('');
    const dates=days.map(day=>`<tr><td>${esc(day.date)}</td><td>${day.rainfall.toFixed(1)} mm</td><td>${esc(day.model)}</td><td>${day.mae.toFixed(2)} mm</td></tr>`).join('');
    const status=result.status==='unavailable'?'No current forecast overlaps the planned activity. Hazard screening is unavailable.':result.status==='dry'?'No rain-related hazards identified by this forecast screen. Other hazards, residual wet ground and sudden rain may still occur.':'Possible rain-related hazards for the available forecast days are shown below.';
    const chartSummary=window.CResistAssessmentScreen.chartScore(data),band=window.CResistAssessmentScreen.riskBand(chartSummary);
    return `<div class="assessment-score-grid"><div><small>Illustrative severity range</small><strong>${range('severity')}</strong></div><div><small>Illustrative likelihood range</small><strong>${range('likelihood')}</strong></div><div class="assessment-rpn" style="--rpn-colour:${band.colour}"><small>Illustrative RPN range</small><strong>${range('rpn')}</strong><span>${chartSummary?`Average ${chartSummary.average.toFixed(1)} · ${band.label}`:'Gantt colour: unavailable / incomplete'}</span></div></div>
      <p class="assessment-rpn-legend">Gantt colour by average illustrative RPN: Low 1–4 · Medium &gt;4–12 · High &gt;12–25. Grey means incomplete, unavailable, or no rain-related hazards screened.</p>
      <p class="assessment-status-note">These are illustrative screening ranges, not verified or approved risk ratings. A competent supervisor and risk assessment team must verify the weather, hazards, exposure and controls on site before work. This display does not authorize work.</p>
      <h4>Forecast for planned dates</h4><p>NEA station ${esc(station)} · ${days.length} of ${expected} calendar days in the next 14 days have a forecast. The lowest available station-specific validation MAE selects a model for each day; past performance does not guarantee accuracy.</p>${data.proxy?`<p class="assessment-status-note">Rainfall proxy: ${esc(data.proxy.name)} (${esc(data.proxy.code)}), approximately ${data.proxy.distanceKm} km from ${esc(station)}. The forecast is for the proxy station, not a measurement at the selected station.</p>`:''}${(data.provenance||[]).map(note=>`<p class="assessment-status-note">${esc(note)}</p>`).join('')}${dates?`<div class="assessment-table-wrap"><table class="assessment-forecast-table"><thead><tr><th>Date</th><th>Daily rainfall</th><th>Selected model</th><th>Validation MAE</th></tr></thead><tbody>${dates}</tbody></table></div>`:''}
      <h4>Possible rain-related hazards</h4><p>${esc(status)} Each date groups the hazards screened for that forecast day (YYYY-MM-DD).</p>${days.length<expected?'<p class="assessment-status-note">Some planned dates have no forecast. Missing dates are not treated as dry or safe.</p>':''}
      ${!data.wholeActivityInWindow?'<p class="assessment-status-note">Part of this activity lies outside the 14-day forecast window and is not screened here.</p>':''}${rows?`<div class="assessment-table-wrap"><table class="assessment-forecast-table"><thead><tr><th>Forecast date</th><th>Hazard</th><th>Example severity</th><th>Example likelihood</th><th>Example RPN</th><th>Possible conditions</th></tr></thead><tbody>${rows}</tbody></table></div>`:''}
      ${result.status==='wet'?'<p>Illustrative screening: daily totals below 20 mm show a narrower set; 20 mm or more includes additional rain-related hazards. Daily totals do not indicate rainfall intensity, and excluded hazards may still occur. Scores are fixed examples, not weather-calibrated predictions; a dash means no example score is available.</p>':''}
      <h4>Priority candidate controls</h4>${controls?`<ol>${controls}</ol>`:'<p>No controls selected by this rain screen. The site team must still identify and apply all necessary controls.</p>'}`;
  }
  async function showProvisionalExamples(code,host){
    try{
      const bundle=await loadJSON('data/provisional-wsh-examples.json');
      if(bundle.schema_version!==1||bundle.bundle_type!=='SYNTHETIC_PROVISIONAL_WSH_EXAMPLES'||!bundle.activities||!Array.isArray(bundle.activities[code]))throw Error(`No valid provisional examples found for ${code}.`);
      const examples=bundle.activities[code];
      if(!examples.length)throw Error(`No provisional examples found for ${code}.`);
      const rows=examples.map(item=>{
        if(!item.hazard_id||!Number.isInteger(item.severity)||item.severity<1||item.severity>5||!Number.isInteger(item.likelihood)||item.likelihood<1||item.likelihood>5||item.rpn!==item.severity*item.likelihood||!['Low','Medium','High'].includes(item.example_class))throw Error(`Invalid provisional example for ${code}.`);
        return `<tr><td>${esc(item.hazard_id)}</td><td>${item.severity}</td><td>${item.likelihood}</td><td>${item.rpn}</td><td>${esc(item.example_class)}</td><td>${esc(item.assumed_conditions||'Not supplied')}</td></tr>`;
      }).join('');
      if(host.isConnected)host.innerHTML=`<h4>Hypothetical workbook examples — not an assessment</h4><p class="assessment-status-note"><b>Synthetic, desk-based assumptions; not surveyed, site-verified or approved.</b> These examples must not be used for safety decisions, work authorization or delay prediction. The risk class uses the workbook's unapproved example matrix. Actual WSH risk remains unassessed.</p><div class="assessment-table-wrap"><table><thead><tr><th>Hazard</th><th>Example severity</th><th>Example likelihood</th><th>Example RPN</th><th>Example class</th><th>Assumed conditions</th></tr></thead><tbody>${rows}</tbody></table></div>`;
    }catch(error){if(host.isConnected)host.innerHTML=`<p class="assessment-status-note"><b>Provisional examples unavailable.</b> ${esc(error.message)} WSH risk remains unassessed.</p>`}
  }
  let forecastContextVersion=0,forecastContextTimer=null;
  async function showExperimentalForecastContext(host,activity,planned,location){
    const version=++forecastContextVersion;
    host.innerHTML='<p>Loading forecast context…</p>';
    try{
      if(!activity||!planned)throw Error('A scheduled activity is required to match forecast dates.');
      if(!location)throw Error('Select a project location.');
      const forecastData=await loadJSON('data/climate-multi-model-forecasts.json');
      const request=buildPredictionRequest({location},forecastData,null);
      const overlap=request.forecast_days.filter(day=>day.date>=planned.start&&day.date<=planned.finish);
      const rows=overlap.map(day=>{
        for(const target of ['rainfall','mean_temperature']){
          const model=day.forecast_model_by_target[target],points=forecastData.models?.[model]?.stations?.[request.nea_station_id]?.targets?.[target]?.forecast;
          const point=Array.isArray(points)?points.find(item=>item.date===day.date):null;
          if(!point||typeof point.value!=='number'||!Number.isFinite(point.value))throw Error(`Invalid ${target} forecast for ${day.date}.`);
        }
        return `<tr><td>${esc(day.date)}</td><td>${esc(day.rainfall_mm)} mm · ${esc(day.forecast_model_by_target.rainfall)}</td><td>${esc(day.mean_temperature_c)} °C · ${esc(day.forecast_model_by_target.mean_temperature)}</td></tr>`;
      }).join('');
      if(version!==forecastContextVersion||!host.isConnected)return;
      const period=overlap.length?`<div class="assessment-table-wrap"><table><thead><tr><th>Date</th><th>Rainfall · model</th><th>Mean temperature · model</th></tr></thead><tbody>${rows}</tbody></table></div><p>Only dates available in the current forecast and activity calendar-date range are shown; missing dates are not treated as safe. Dates may include non-working days.</p>`:'<p>No current forecast dates overlap this activity’s planned dates. Risk cannot be inferred from missing weather.</p>';
      host.innerHTML=`<h4>Experimental forecast context — not a risk assessment</h4><p>Location: ${esc(location)} · station: ${esc(request.nea_station_id)} · planned: ${esc(planned.start)} to ${esc(planned.finish)}. Selected exposure: ${esc(activity.exposure)}; rain sensitivity: ${esc(activity.rainSensitivity)}; heat sensitivity: ${esc(activity.heatSensitivity)}.</p><p>Forecast models are selected separately by variable and date using the lowest available validation MAE. These values do not determine hazard likelihood or severity. The workbook examples below are not weather-specific predictions.</p>${period}`;
    }catch(error){if(version===forecastContextVersion&&host.isConnected)host.innerHTML=`<h4>Experimental forecast context — unavailable</h4><p class="assessment-status-note">${esc(error.message)} No WSH rating has been inferred.</p>`}
  }
  function refreshExperimentalForecastContext(){
    const dialog=$('#activityAssessmentDialog'),host=dialog.querySelector('[data-weather-assessment]');
    if(!dialog.open||!host)return;
    const version=++forecastContextVersion,activity=activities.find(item=>item.id===host.dataset.activityId);
    loadAssessmentInputs().then(inputs=>{
      if(version!==forecastContextVersion||!host.isConnected)return;
      const data=assessmentForActivity(host.dataset.code,activity,computed?.base?.[activity?.id],$('#location').value,inputs);
      host.innerHTML=renderWeatherAssessment(data);
    }).catch(error=>{if(version===forecastContextVersion&&host.isConnected)host.innerHTML=`<p class="assessment-status-note"><b>Forecast screening unavailable.</b> ${esc(error.message)} Site risk remains unassessed.</p>`});
  }
  const stableValue=value=>Array.isArray(value)?value.map(stableValue):value&&typeof value==='object'?Object.fromEntries(Object.keys(value).sort().map(key=>[key,stableValue(value[key])])):value,stableJSON=value=>JSON.stringify(stableValue(value));
  const projectProfile=()=>{try{return JSON.parse(localStorage.getItem('c-resist-project-profile-v1')||'null')}catch(error){throw Error(`Project profile could not be read: ${error.message}`)}};
  function predictionInputKey(){
    const profile=projectProfile(),location=profile?.location||$('#location')?.value||'',activitiesForModel=activities.filter(a=>a.catalogueCode).map(a=>({id:a.id,wbs_code:String(a.catalogueCode).toUpperCase(),exposure:a.exposure,heat_sensitivity:a.heatSensitivity,rain_sensitivity:a.rainSensitivity,stage:a.stage,mandatory_stop_work_dates:a.mandatoryStopWorkDates||a.mandatory_stop_work_dates||[]}));
    return JSON.stringify({project_id:profile?.name||`${location}-${$('#projectStart').value}`,location,work_hours_per_day:+$('#workHours').value,activities:activitiesForModel});
  }
  function buildPredictionRequest(profile,forecastData,historyData){
    const location=profile?.location||$('#location')?.value||'',station=window.neaHistoricalStationByArea?.[location],modelData=forecastData?.models;
    if(!location||!station?.code)throw Error(`No existing NEA station mapping is available for project location "${location||'not selected'}".`);
    if(!modelData||typeof modelData!=='object')throw Error('The saved five-model climate forecast data is unavailable.');
    const now=new Date(),today=new Date(now.getTime()-now.getTimezoneOffset()*60000).toISOString().slice(0,10),lastDate=add(today,13),targetKeys={rainfall:'rainfall',mean_temperature:'mean_temperature',maximum_temperature:'maximum_temperature',minimum_temperature:'minimum_temperature'},forecastsByTarget={};
    for(const [target,targetKey] of Object.entries(targetKeys)){
      const candidates=Object.entries(modelData).map(([modelId,model])=>({modelId,result:model?.stations?.[station.code]?.targets?.[target]})).filter(item=>item.result&&Array.isArray(item.result.forecast)&&item.result.validation?.mae!=null&&Number.isFinite(Number(item.result.validation.mae)));
      const byDate=new Map();
      for(const candidate of candidates)for(const point of candidate.result.forecast){if(!validDate(point.date)||!Number.isFinite(Number(point.value)))continue;const current=byDate.get(point.date),mae=Number(candidate.result.validation.mae);if(!current||mae<current.mae)byDate.set(point.date,{value:Number(point.value),lower:point.lower!=null&&Number.isFinite(Number(point.lower))?Number(point.lower):null,upper:point.upper!=null&&Number.isFinite(Number(point.upper))?Number(point.upper):null,model:candidate.modelId,mae})}
      forecastsByTarget[targetKey]=byDate;
    }
    const rainfall=forecastsByTarget.rainfall,mean=forecastsByTarget.mean_temperature,history=(historyData?.stations?.[station.code]||[]),estimated=historyData?.estimatedValues?.[station.code]||{},rainHistory=new Map(history.filter(row=>Array.isArray(row)&&row.length===5&&validDate(row[0])&&!estimated[row[0]]?.includes('rainfall')).map(row=>[row[0],row[1]]));
    const forecastDays=[...rainfall.keys()].filter(day=>day>=today&&day<=lastDate&&mean.has(day)).sort().map(day=>{
      const targetForecasts={rainfall:rainfall.get(day),mean_temperature:mean.get(day),maximum_temperature:forecastsByTarget.maximum_temperature.get(day),minimum_temperature:forecastsByTarget.minimum_temperature.get(day)},uncertainty={},models={};
      for(const [target,forecast] of Object.entries(targetForecasts)){if(forecast){models[target]=forecast.model;uncertainty[target]={lower:forecast.lower,upper:forecast.upper,validationMae:forecast.mae}}}
      const priorRain=[];for(let offset=7;offset>=1;offset--){const priorDate=add(day,-offset),observed=rainHistory.get(priorDate);if(observed==null||!Number.isFinite(Number(observed))){priorRain.length=0;break}priorRain.push(Number(observed))}
      return {date:day,rainfall_mm:targetForecasts.rainfall.value,mean_temperature_c:targetForecasts.mean_temperature.value,maximum_temperature_c:targetForecasts.maximum_temperature?.value??null,minimum_temperature_c:targetForecasts.minimum_temperature?.value??null,recent_cumulative_rainfall_mm:priorRain.length===7?priorRain.reduce((sum,value)=>sum+value,0):null,recent_rainfall_window_days:7,forecast_source:'NEA station-specific best validation-MAE model by target/date',forecast_model_by_target:models,forecast_uncertainty:uncertainty};
    });
    if(!forecastDays.length)throw Error(`No current 14-day forecast dates are available for NEA station ${station.code}.`);
    const selected=activities.filter(a=>a.catalogueCode).map(activity=>({id:activity.id,wbs_code:String(activity.catalogueCode).toUpperCase(),planned_productive_hours:+$('#workHours').value,exposure:activity.exposure,heat_sensitivity:activity.heatSensitivity,rain_sensitivity:activity.rainSensitivity,stage:activity.stage,site_drainage_condition:null,ground_condition:null,existing_controls:'[]',existing_controls_status:'NOT_PROVIDED',mandatory_stop_work_dates:activity.mandatoryStopWorkDates||activity.mandatory_stop_work_dates||[]})).sort((a,b)=>a.wbs_code<b.wbs_code?-1:a.wbs_code>b.wbs_code?1:a.id<b.id?-1:a.id>b.id?1:0);
    if(!selected.length)throw Error('Select one or more canonical WBS activities before requesting productivity predictions.');
    return {schema_version:1,project_id:profile?.name||`${location}-${$('#projectStart').value}`,location,nea_station_id:station.code,work_hours_per_day:+$('#workHours').value,activities:selected,forecast_days:forecastDays,approved_stop_work_source:''};
  }
  async function getActivityPrediction(request){
    let apiError='Local Python prediction API is unavailable.';
    try{
      const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),2500);
      try{
        const response=await fetch('http://127.0.0.1:8766/api/activity-disruption/predict',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(request),signal:controller.signal});
        if(!response.ok)throw Error(`Local prediction API returned HTTP ${response.status}: ${await response.text()}`);
        const result=await response.json();if(result.status!=='AVAILABLE')throw Error('Local prediction API did not return an available model.');result.modelSource='LOCAL_API';return result;
      }finally{clearTimeout(timer)}
    }catch(error){apiError=error.message||apiError}
    try{
      const bundle=await loadJSON('data/activity-disruption-predictions.json');
      if(!bundle.bundle_request||stableJSON(bundle.bundle_request)!==stableJSON(request))throw Error('The static prediction bundle does not match the current project, station, activities, or forecast dates.');
      if(bundle.status!=='AVAILABLE')throw Error('The static prediction bundle is not available.');
      bundle.modelSource='STATIC_BUNDLE';
      return bundle;
    }catch(error){throw Error(`${apiError} Static bundle unavailable or mismatched: ${error.message}`)}
  }
  function refreshActivityPredictions(){
    let signature;try{signature=predictionInputKey()}catch(error){prediction=null;predictionRequest=null;predictionState='unavailable';predictionMessage=error.message;return}
    if(signature===predictionAttemptKey)return;
    predictionAttemptKey=signature;prediction=null;predictionRequest=null;
    const selected=activities.some(activity=>activity.catalogueCode);
    if(!selected){predictionState='unavailable';predictionMessage='Select WBS catalogue activities to request activity-level predictions.';return}
    predictionState='loading';predictionMessage='Checking the local Python API, then a matching static prediction bundle.';
    forecastBundlePromise=forecastBundlePromise||loadJSON('data/climate-multi-model-forecasts.json');
    historyBundlePromise=historyBundlePromise||loadJSON('data/climate-history.json');
    let profile;try{profile=projectProfile()}catch(error){predictionState='unavailable';predictionMessage=error.message;return}
    Promise.all([forecastBundlePromise,historyBundlePromise]).then(([forecastData,historyData])=>{
      if(predictionInputKey()!==signature)return;
      const request=buildPredictionRequest(profile,forecastData,historyData);predictionRequest=request;
      return getActivityPrediction(request).then(result=>{
        if(predictionInputKey()!==signature)return;
        const activeIds=new Set(activities.map(activity=>activity.id));
        result.productivity_profiles=Object.fromEntries(Object.entries(result.productivity_profiles||{}).filter(([id])=>activeIds.has(id)));
        prediction=result;predictionState='available';predictionMessage='';
        render();
      });
    }).catch(error=>{
      if(predictionInputKey()!==signature)return;
      prediction=null;predictionRequest=null;predictionState='unavailable';predictionMessage=error.message;
      render();
    });
  }

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
    const activityName=item=>`${item.catalogueNumber?`${item.catalogueNumber} · `:''}${item.catalogueCode||item.id} · ${item.name}`;
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

  const dateDelta=(from,to)=>validDate(from)&&validDate(to)?Math.round((Date.parse(to+'T00:00:00Z')-Date.parse(from+'T00:00:00Z'))/86400000):null;
  const assessmentSourceLabel=source=>`${source.source_id}${source.reference?` · ${source.reference}`:''}${source.sections?` · ${source.sections}`:''}`;
  function activityAssessmentMarkup(code,activityId,bundle){
    const activity=activities.find(item=>item.id===activityId),knowledge=bundle.activities[code],profile=prediction?.productivity_profiles?.[activityId]||[],request=prediction?.bundle_request||predictionRequest,requestActivity=request?.activities?.find(item=>item.id===activityId),forecasts=request?.forecast_days||[],current=computed?.adj?.[activityId],baseline=computed?.base?.[activityId],manual=intervention?.adj?.[activityId],modelLabel=predictionState==='available'?`${prediction.model_status||'Model status unavailable'}${prediction.model_id?` · ${esc(prediction.model_id)}`:''}${Number.isFinite(Number(prediction.evaluation_mae_hours))?` · validation MAE ${Number(prediction.evaluation_mae_hours).toFixed(2)} h`:''}${prediction.modelSource==='LOCAL_API'?' · local API':prediction.modelSource==='STATIC_BUNDLE'?' · static bundle':''}`:predictionState==='loading'?'Loading productivity model…':`Unavailable: ${esc(predictionMessage||'use the existing illustrative scenario mode')}`,sources=new Map((knowledge.evidence||[]).map(source=>[source.source_id,source]));
    const activityDelay=current&&baseline?Math.max(0,dateDelta(baseline.finish,current.finish)):null,projectDelta=computed?dateDelta(computed.finishProject,computed.adjustedFinish):null;
    const hazardRows=(knowledge.hazards||[]).map(hazard=>`<li><strong>${esc(hazard.hazard_id)} · ${esc(hazard.name)}</strong>${hazard.definition?`<small>${esc(hazard.definition)}</small>`:''}</li>`).join('');
    const interventionRows=(knowledge.interventions||[]).map(item=>{
      const hazardNames=(item.hazard_ids||[]).map(id=>knowledge.hazards.find(hazard=>hazard.hazard_id===id)?.name||id).join(', ');
      const controlNames=(item.linked_control_ids||[]).map(id=>knowledge.controls.find(control=>control.control_id===id)?.name||id).join(', ');
      const sourceNames=(item.source_ids||[]).map(id=>sources.get(id)?assessmentSourceLabel(sources.get(id)):id).join(' · ');
      return `<li class="assessment-intervention"><div class="assessment-intervention-head"><strong>${esc(item.intervention_id)} · ${esc(item.name)}</strong><span class="assessment-candidate">Candidate only</span></div><p><b>Hazard:</b> ${esc(hazardNames||'Graph-linked; verify site applicability')}</p><p><b>Hierarchy:</b> ${esc(item.hierarchy||'Not specified')} · <b>Stage:</b> ${esc(item.stage||'Not specified')}</p>${item.mechanism_of_action?`<p><b>Reason:</b> ${esc(item.mechanism_of_action)}</p>`:''}${item.implementation_requirements?`<p><b>Actions / resources:</b> ${esc(item.implementation_requirements)}</p>`:''}${controlNames?`<p><b>Linked controls:</b> ${esc(controlNames)}</p>`:''}${item.expected_safety_benefit?`<p><b>Qualitative safety benefit:</b> ${esc(item.expected_safety_benefit)}</p>`:''}${item.potential_performance_benefit?`<p><b>Qualitative performance benefit:</b> ${esc(item.potential_performance_benefit)}</p>`:''}${item.residual_risk_considerations?`<p><b>Residual considerations:</b> ${esc(item.residual_risk_considerations)}</p>`:''}<p><b>Productivity effect:</b> Not quantified (${esc(item.effectiveness||'no numerical effectiveness assigned')}). No intervention-based delay reduction is estimated.</p>${item.primary_source_location?`<p><b>Source section:</b> ${esc(item.primary_source_location)}</p>`:''}${sourceNames?`<small><b>Evidence:</b> ${esc(sourceNames)}</small>`:''}</li>`;
    }).join('');
    const controlRows=(knowledge.controls||[]).map(item=>`<li><b>${esc(item.control_id)} · ${esc(item.name)}</b> <span>${esc(item.hierarchy||'Unclassified')} · candidate, not confirmed as implemented</span>${item.description?`<small>${esc(item.description)}</small>`:''}</li>`).join('');
    const forecastRows=profile.map(entry=>{
      const weather=forecasts.find(day=>day.date===entry.date),targetModels=entry.forecastModelByTarget||weather?.forecast_model_by_target||{};
      const source=Object.entries(targetModels).map(([target,model])=>`${target}: ${model}`).join(' · ');
      const recent=weather?.recent_cumulative_rainfall_mm==null?'unavailable (seven observed station days incomplete; forecasts were not substituted)':`${Number(weather.recent_cumulative_rainfall_mm).toFixed(1)} mm observed`;
      return `<tr><td>${esc(entry.date)}</td><td>${weather&&Number.isFinite(Number(weather.rainfall_mm))?`${Number(weather.rainfall_mm).toFixed(1)} mm`:'—'}</td><td>${weather&&Number.isFinite(Number(weather.mean_temperature_c))?`${Number(weather.mean_temperature_c).toFixed(1)} °C`:'—'}</td><td>${Number(entry.predictedLostHours).toFixed(2)} h</td><td>${Math.round(Number(entry.productivityFactor)*100)}%</td><td>${entry.mandatoryStopWork?'Mandatory stop':'No stop flag'}</td><td>${esc(source||entry.forecastSource||'Forecast source unavailable')}<br><small>Recent rainfall: ${esc(recent)}</small></td></tr>`;
    }).join('');
    const sourceNotice=sources.has('SRC-01')&&sources.has('SRC-02')?`<p class="assessment-source-note"><b>Primary references:</b> ${esc(assessmentSourceLabel(sources.get('SRC-01')))}<br>${esc(assessmentSourceLabel(sources.get('SRC-02')))}</p>`:'';
    return `<nav class="activity-assessment-tabs" role="tablist" aria-label="Activity assessment sections"><button type="button" role="tab" aria-selected="true" aria-controls="activityAssessmentOverview" data-assessment-tab="overview">Assessment</button><button type="button" role="tab" aria-selected="false" aria-controls="activityAssessmentSchedule" data-assessment-tab="schedule">Schedule impact</button><button type="button" role="tab" aria-selected="false" aria-controls="activityAssessmentInterventions" data-assessment-tab="interventions">Interventions</button></nav>
      <section id="activityAssessmentOverview" class="activity-assessment-panel" data-assessment-panel="overview"><div class="assessment-title-row"><div><p class="eyebrow">${esc(knowledge.stage||'WBS ACTIVITY')}</p><h3>${esc(code)} · ${esc(knowledge.name)}</h3></div><span class="assessment-candidate">Illustrative / RA review required</span></div><div class="assessment-score-grid"><div><small>Severity</small><strong>—</strong></div><div><small>Likelihood</small><strong>—</strong></div><div><small>RPN</small><strong>—</strong></div></div><p class="assessment-status-note"><b>${esc(knowledge.wsh_assessment.status||'REQUIRES_RA_REVIEW')}</b> · No approved organization risk matrix or approved site assessment is present. Severity and likelihood are not supplied, so no Low/Medium/High classification or RPN is claimed. High-risk or unresolved work is not authorized by this display.</p><p class="assessment-matrix-note">Matrix template: ${esc(knowledge.wsh_assessment.risk_matrix_version||'not configured')} · organization-approved: ${knowledge.wsh_assessment.organization_approved_matrix?'Yes':'No'}</p><h4>Knowledge-graph hazards</h4>${hazardRows?`<ul class="assessment-hazard-list">${hazardRows}</ul>`:'<p>No graph-linked hazard records are available; review the activity scope.'}<h4>Candidate controls</h4>${controlRows?`<ul class="assessment-control-list">${controlRows}</ul>`:'<p>No graph-linked candidate controls are available.'}${sourceNotice}</section>
      <section id="activityAssessmentSchedule" class="activity-assessment-panel" data-assessment-panel="schedule" hidden><div class="assessment-title-row"><div><p class="eyebrow">PRODUCTIVITY AND SCHEDULE / SEPARATE FROM WSH</p><h3>${activity?esc(activity.name):'Activity not currently scheduled'}</h3></div><span class="assessment-candidate">${esc(predictionState==='available'?(prediction.model_status||'Model'):'Model status')}</span></div><p class="prediction-status ${predictionState==='available'?'prediction-ready':predictionState==='loading'?'prediction-loading':'prediction-unavailable'}"><b>Activity productivity model:</b> ${modelLabel}${predictionState==='available'&&prediction.model_status==='SYNTHETIC_DEMO'?'<br><b>SYNTHETIC_DEMO — not empirically validated and not for safety decisions.</b>':''}</p><div class="assessment-schedule-grid"><div><small>Without activity profile</small><strong>${baseline?.finish?esc(baseline.finish):'Not scheduled'}</strong></div><div><small>Current adjusted schedule</small><strong>${current?.finish?esc(current.finish):'Not available'}</strong></div><div><small>Activity finish shift</small><strong>${activityDelay===null?'Not estimated':`${activityDelay} calendar day${activityDelay===1?'':'s'}`}</strong></div><div><small>Project completion change</small><strong>${projectDelta===null?'Not estimated':`${projectDelta>0?'+':''}${projectDelta} calendar day${Math.abs(projectDelta)===1?'':'s'}`}</strong></div></div>${current?`<p>Direct lost productive hours: ${Number(current.directLostProductiveHours||0).toFixed(2)} h · Direct disruption: ${current.direct?'Yes':'No'} · Inherited dependency delay: ${current.inherited?'Yes':'No'} · Adjusted activity finish: ${esc(current.finish)}.</p>`:'<p>Select and schedule this WBS activity to calculate activity-level dates and propagated project completion.</p>'}${manual&&scenarioActive?`<p class="assessment-manual-scenario"><b>Manual illustrative scenario only:</b> adjusted finish ${esc(manual.finish)}. This is not a measured intervention effect and does not override stop-work conditions.</p>`:''}${predictionState==='available'&&!profile.length?'<p>No daily profile is available for this selected activity.</p>':''}${profile.length?`<h4>Daily station forecast and activity prediction</h4><div class="assessment-table-wrap"><table class="assessment-forecast-table"><thead><tr><th>Date</th><th>Rain</th><th>Mean temp.</th><th>Predicted lost</th><th>Productivity</th><th>Safety gate</th><th>Input provenance</th></tr></thead><tbody>${forecastRows}</tbody></table></div>`:''}<p class="assessment-source-note">Forecast values are the selected station's best validation-MAE model by target/date. Historical rainfall is used only for the prior seven-day observed accumulation; missing observations remain missing. Productivity estimates are schedule inputs, not WSH classifications.</p></section>
      <section id="activityAssessmentInterventions" class="activity-assessment-panel" data-assessment-panel="interventions" hidden><p class="assessment-status-note"><b>Recommendations are candidates only.</b> Confirm the work method, site conditions, applicability, required resources, owner, implementation time, restart checks, and approval with the project RA/site team. Recommendations do not change residual WSH risk or authorize work.</p><div class="assessment-schedule-grid"><div><small>Current project completion</small><strong>${computed?.adjustedFinish?esc(computed.adjustedFinish):'Not scheduled'}</strong></div><div><small>Revised completion with intervention</small><strong>Not estimated</strong></div><div><small>Potential delay avoided</small><strong>Not quantified</strong></div></div><p>Workbook effectiveness is qualitative only; no numeric productivity gain is assigned. Revised schedule evaluation requires an explicit evidence-backed or clearly labeled scenario productivity profile and site-approved implementation/recovery inputs.</p>${interventionRows?`<h4>Possible interventions</h4><ul class="assessment-intervention-list">${interventionRows}</ul>`:'<p>No linked resilience intervention records are available for this activity.</p>'}${sourceNotice}</section>`;
  }
  async function openActivityAssessment(wbsCode,activityId=null){
    const code=String(wbsCode||'').trim().toUpperCase(),activity=activityId?activities.find(item=>item.id===activityId):activities.find(item=>String(item.catalogueCode||'').toUpperCase()===code);
    if(activity)selected=activity.id;
    const dialog=$('#activityAssessmentDialog'),content=$('#activityAssessmentContent');
    dialog.querySelector(':scope > .activity-assessment-tabs')?.remove();
    $('#activityAssessmentTitle').textContent=`${code} · ${activity?.name||'Activity assessment'}`;
    content.innerHTML='<p class="assessment-loading">Loading graph-linked activity evidence…</p>';
    if(!dialog.open)dialog.showModal();
    try{
      const bundle=await loadActivityKnowledge(),knowledge=bundle.activities[code];
      if(!knowledge)throw Error(`WBS activity ${code} is not present in the static graph bundle.`);
      const interventionRows=(knowledge.interventions||[]).slice(0,5).map(item=>`<li class="assessment-intervention"><strong>${esc(item.name)}</strong>${item.mechanism_of_action?`<p>${esc(item.mechanism_of_action)}</p>`:''}${item.implementation_requirements?`<p><b>Needs:</b> ${esc(item.implementation_requirements)}</p>`:''}</li>`).join('');
      content.innerHTML=`<nav class="activity-assessment-tabs" role="tablist" aria-label="Activity assessment sections"><button type="button" role="tab" aria-selected="true" aria-controls="activityAssessmentOverview" data-assessment-tab="overview">Assessment</button><button type="button" role="tab" aria-selected="false" aria-controls="activityAssessmentInterventions" data-assessment-tab="interventions">Interventions</button></nav><section id="activityAssessmentOverview" class="activity-assessment-panel" data-assessment-panel="overview"><div class="assessment-title-row"><div><p class="eyebrow">${esc(knowledge.stage||'WBS ACTIVITY')}</p><h3>${esc(code)} · ${esc(knowledge.name)}</h3></div><span class="assessment-candidate">Site review required</span></div><div data-weather-assessment data-code="${esc(code)}" data-activity-id="${esc(activity?.id||'')}"><p>Loading weather screening…</p></div></section><section id="activityAssessmentInterventions" class="activity-assessment-panel" data-assessment-panel="interventions" hidden><p class="assessment-status-note">Candidate interventions require site review, supervision and approval. Benefits and schedule effects are not quantified.</p>${interventionRows?`<h4>Possible interventions</h4><ul class="assessment-intervention-list">${interventionRows}</ul>`:'<p>No candidate interventions are available for this activity.</p>'}</section>`;
      refreshExperimentalForecastContext();
      if(forecastContextTimer!==null)clearInterval(forecastContextTimer);
      forecastContextTimer=setInterval(refreshExperimentalForecastContext,24*60*60*1000);
      content.querySelectorAll('[data-assessment-tab]').forEach(button=>button.addEventListener('click',()=>setAssessmentTab(button.dataset.assessmentTab)));
    }catch(error){
      content.innerHTML=`<section class="activity-assessment-panel"><p class="assessment-status-note"><b>Knowledge data unavailable.</b> ${esc(error.message)}</p><p>No WSH risk rating or intervention effectiveness has been inferred. Verify the bundled graph file and obtain a site-specific risk assessment.</p></section>`;
    }
  }
  function setAssessmentTab(name){
    const dialog=$('#activityAssessmentDialog');
    dialog.querySelectorAll('[data-assessment-tab]').forEach(button=>button.setAttribute('aria-selected',String(button.dataset.assessmentTab===name)));
    dialog.querySelectorAll('[data-assessment-panel]').forEach(panel=>{panel.hidden=panel.dataset.assessmentPanel!==name});
  }

  function sens(a,k){return `<select data-f="${k}">${['low','medium','high'].map(x=>`<option ${a[k]===x?'selected':''}>${x}</option>`)}</select>`}

  function render(){
    $('#ganttForecastPeriod').textContent='Forecast details will appear when activities are scheduled.';
    drawEditor();
    refreshActivityPredictions();
    $('#resetSchedule').disabled=!activities.length;
    if(!activities.length){
      computed=null;intervention=null;$('#phaseSummary').textContent='';
      $('#activityGantt').innerHTML='<p class="gantt-empty">Choose a project stage and add activities to build the timeline.</p>';
      $('#scheduleMessage').textContent='';$('#impactCount').textContent='No activities selected';
      $('#activityCount').textContent='Add activities from a project stage';
      showScheduleResultMessage('No activities are selected. Add activities to see any schedule delays in this period.');
      if(!preserveBadSave){
        const scenario={start:$('#scenarioStart').value,end:$('#scenarioEnd').value,hazard:$('#scheduleHazard').value,factor:+$('#productivity').value,recovery:+$('#recoveryDays').value};
        const interventionInputs={factor:+$('#interventionProductivity').value,recovery:+$('#interventionRecoveryDays').value};
        try{localStorage.setItem(key,JSON.stringify({schemaVersion:1,activities,projectStart:$('#projectStart').value,workdays:$('#workdays').value,workHours:+$('#workHours').value,holidays:$('#holidays').value,scenario,intervention:interventionInputs}))}
        catch{$('#scheduleMessage').textContent=' · Browser storage unavailable; project not saved.'}
      }
      window.updateProjectCompletion?.('Not calculated yet');refreshExperimentalForecastContext();return;
    }
    if(activities.some(a=>!Number.isInteger(+a.durationDays)||+a.durationDays<1)){
      computed=null;intervention=null;$('#phaseSummary').textContent='';
      $('#activityGantt').innerHTML=`<div class="gantt-unscheduled"><strong>Enter planned durations</strong><p>Open a project stage, select activities, and enter each duration in working days.</p><ul>${activities.map(a=>`<li><b>${esc(a.catalogueNumber?`${a.catalogueNumber} · `:'')}${esc(a.catalogueCode||a.id)}</b> ${esc(a.name)}</li>`).join('')}</ul></div>`;
      $('#scheduleMessage').textContent='';$('#impactCount').textContent=`${activities.length} selected · unscheduled`;
      $('#activityCount').textContent=`${activities.length} activities · duration needed`;
      showScheduleResultMessage('Set a duration for each selected activity to calculate the planned schedule.');
      window.updateProjectCompletion?.('Not calculated yet');refreshExperimentalForecastContext();return;
    }
    try{
      const workHours=+$('#workHours').value;
      if(!Number.isFinite(workHours)||workHours<=0||workHours>24)throw Error('Working hours per day must be greater than 0 and no more than 24. Durations remain in workdays.');
      const scenario=currentScenario(),scheduleScenario=scenarioActive?scenario:{...scenario,factor:1,recovery:0};
      const mandatoryStopWorkDates=Object.fromEntries(activities.filter(activity=>Array.isArray(activity.mandatoryStopWorkDates||activity.mandatory_stop_work_dates)).map(activity=>[activity.id,activity.mandatoryStopWorkDates||activity.mandatory_stop_work_dates]));
      computed=schedule(activities,$('#projectStart').value,readCal(),{...scheduleScenario,workHoursPerDay:workHours,activityProfiles:prediction?.productivity_profiles||{},mandatoryStopWorkDates});
      const interventionInputs=currentScenario(+$('#interventionProductivity').value,+$('#interventionRecoveryDays').value);
      const interventionScenario=scenarioActive?interventionInputs:{...interventionInputs,factor:1,recovery:0};
      intervention=schedule(activities,$('#projectStart').value,readCal(),{...interventionScenario,workHoursPerDay:workHours,mandatoryStopWorkDates});
      $('#scheduleMessage').textContent='';$('#scheduleMessage').className='';drawChart();drawResults();
      if(!preserveBadSave){
        try{localStorage.setItem(key,JSON.stringify({schemaVersion:1,activities,projectStart:$('#projectStart').value,workdays:$('#workdays').value,workHours,holidays:$('#holidays').value,scenario,intervention:{factor:interventionInputs.factor,recovery:interventionInputs.recovery}}))}
        catch{persistenceWarning=' · Browser storage unavailable; project not saved.';$('#scheduleMessage').textContent=persistenceWarning}
      }
    }catch(e){
      computed=null;intervention=null;$('#scheduleMessage').textContent=e.message;$('#scheduleMessage').className='schedule-error';
      $('#activityGantt').replaceChildren();showScheduleResultMessage(e.message);
      $('#activityCount').textContent='Check schedule inputs';window.updateProjectCompletion?.('Not calculated yet');
    }
    refreshExperimentalForecastContext();
  }

  function drawChart(){const c=computed,ids=activities.map(a=>a.id),windowStart=singaporeToday(),windowEnd=add(windowStart,13),projectStart=$('#projectStart').value,min=projectStart<windowStart?projectStart:windowStart,max=c.finishProject>windowEnd?c.finishProject:windowEnd,span=Math.max(1,(Date.parse(max+'T00:00:00Z')-Date.parse(min+'T00:00:00Z'))/86400000+1),unit=Math.max(5,Math.min(18,740/span)),label=250,svgW=label+span*unit+30,row=34,height=72+ids.length*row,periods=new Set(),todayX=label+dateDelta(min,windowStart)*unit;let s=`<svg xmlns="http://www.w3.org/2000/svg" width="${svgW}" height="${height}" role="img" aria-label="Planned activity Gantt chart with today's date and 14-day screening window"><rect width="${svgW}" height="${height}" fill="#fff"/><rect data-gantt-forecast-window="${windowStart}/${windowEnd}" x="${todayX}" y="35" width="${14*unit}" height="${height-43}" fill="#eaf4fb"><title>14-day screening window: ${windowStart} to ${windowEnd}; forecast availability varies by station and date</title></rect>`;

    for(let d=0;d<span;d++){const date=add(min,d);if(date.slice(-2)==='01'||d%7===0)s+=`<text x="${label+d*unit}" y="30" font-size="9" fill="#66736b">${date.slice(5)}</text><line x1="${label+d*unit}" x2="${label+d*unit}" y1="35" y2="${height-8}" stroke="#e5eae5"/>`}
    s+=`<line data-gantt-today="${windowStart}" x1="${todayX}" x2="${todayX}" y1="18" y2="${height-8}" stroke="#397daa" stroke-width="2" stroke-dasharray="4 3"><title>Today: ${windowStart} (Singapore)</title></line><text x="${todayX+4}" y="14" font-size="10" font-weight="700" fill="#28668c">Today · ${windowStart}</text>`;

    ids.forEach((id,i)=>{const a=activities.find(x=>x.id===id),b=c.base[id],x0=(Date.parse(b.start+'T00:00:00Z')-Date.parse(min+'T00:00:00Z'))/86400000,x1=(Date.parse(b.finish+'T00:00:00Z')-Date.parse(min+'T00:00:00Z'))/86400000,y=49+i*row,assessmentAttrs=a.catalogueCode?`role="button" tabindex="0" aria-label="Open WSH assessment for ${esc(a.catalogueCode)} ${esc(a.name)}" data-assess-code="${esc(a.catalogueCode)}" data-schedule-id="${esc(a.id)}" style="cursor:pointer"`:'';
      let screening=null;
      if(assessmentInputs&&a.catalogueCode){try{screening=assessmentForActivity(a.catalogueCode,a,b,$('#location').value,assessmentInputs)}catch(error){console.warn(`Assessment screen unavailable for ${a.catalogueCode}: ${error.message}`)}}
      if(screening)for(const period of screening.periods)periods.add(`${screening.sourceStation}|${period}`);
      const chartSummary=window.CResistAssessmentScreen.chartScore(screening),band=window.CResistAssessmentScreen.riskBand(chartSummary),actualColour=band.colour,description=chartSummary?`Illustrative ${band.label} average RPN ${chartSummary.average.toFixed(1)} (${screening.days.length} of ${screening.expected} forecast days); site risk unassessed`:'No complete illustrative RPN screen available; site risk unassessed';
      s+=`<text x="8" y="${y+8}" font-size="10" font-weight="600" fill="#26362e" ${assessmentAttrs}>${esc(a.catalogueNumber?`${a.catalogueNumber} · `:'')}${esc(a.catalogueCode||a.id)} · ${esc(a.name.slice(0,25))}</text><rect x="${label+x0*unit}" y="${y}" width="${Math.max(unit,(x1-x0+1)*unit)}" height="8" rx="4" fill="#2479b8" ${assessmentAttrs}><title>Planned ${b.start} to ${b.finish}</title></rect><rect x="${label+x0*unit}" y="${y+12}" width="${Math.max(unit,(x1-x0+1)*unit)}" height="8" rx="4" fill="${actualColour}" ${assessmentAttrs}><title>${esc(description)}</title></rect>`});s+='</svg>';$('#activityGantt').innerHTML=s;$('#activityCount').textContent=`${activities.length} activities · planned completion ${c.finishProject}`;
    const periodText=[...periods].sort().map(period=>{const [station,inputEnd,start,end]=period.split('|');return `${station}: model input through ${inputEnd}; forecast ${start} to ${end}`});
    $('#ganttForecastPeriod').textContent=`Screening window: ${windowStart} to ${windowEnd} (Singapore dates). ${periodText.length?periodText.join(' · '):assessmentInputs?'No forecast overlaps the scheduled activities.':'Loading forecast dates…'}`;
    if(!assessmentInputs&&!assessmentInputsPromise)loadAssessmentInputs().then(()=>{if(computed)drawChart()}).catch(error=>{console.error('Assessment screen data unavailable:',error);$('#ganttForecastPeriod').textContent=`Weather screening unavailable: ${error.message}`;$('#scheduleMessage').textContent=`Weather screening unavailable: ${error.message}`;$('#scheduleMessage').className='schedule-error';$('#scheduleMessage').hidden=false});
  }

  function drawResults(){
    $('#scheduleResults').innerHTML='<p class="impact-empty">Selected activities require a site-specific WSH assessment. High-risk activities cannot be identified until severity, likelihood and an approved risk matrix are supplied.</p>';
    window.updateProjectCompletion?.(computed.finishProject);
  }

  function selectCatalogue(id,checked){const c=(window.CResistActivityCatalogue||[]).find(x=>x.id===id);if(!c)return;const ix=activities.findIndex(a=>a.catalogueId===id);if(checked&&ix<0)activities.push({id:`${id}#1`,catalogueId:id,catalogueCode:c.code,catalogueNumber:c.subcategoryNumber,name:c.name,stage:`${c.stageNumber}. ${c.phaseCode} - ${c.phaseName}`,durationDays:'',predecessors:'',dependencies:[],exposure:'outdoor',heatSensitivity:'low',rainSensitivity:'medium',scope:c.scope,resources:c.links});if(!checked&&ix>=0){const removedId=activities[ix].id;activities=activities.filter(a=>a.id!==removedId).map(a=>{a.dependencies=dependenciesOf(a).filter(link=>link.predecessorId!==removedId);a.predecessors=a.dependencies.map(link=>link.predecessorId).join(',');return a})}render();window.dispatchEvent(new Event('c-resist-schedule-change'))}
  function configure(catalogueId,field,value){const a=activities.find(x=>x.catalogueId===catalogueId);if(!a)return;a[field]=value;render();window.dispatchEvent(new Event('c-resist-schedule-change'))}
  $('#resetSchedule').addEventListener('click',()=>{
    if(!activities.length)return;
    if(!window.confirm('Clear all selected schedule activities and start again? Project dates, calendar settings and scenario settings will be kept.'))return;
    activities=[];selected=null;preserveBadSave=false;render();
    window.dispatchEvent(new Event('c-resist-schedule-change'));
  });
  window.CResistSchedule={calculate:schedule,parseCSV,select:selectCatalogue,configure,editDependencies:openDependencies,openAssessment:openActivityAssessment,has:id=>activities.some(a=>a.catalogueId===id),get:id=>activities.find(a=>a.catalogueId===id)};
  window.addEventListener('c-resist-climate-risk-calculated',event=>{
    window.CResistClimateRisk={level:event.detail?.level||'Low',score:Number(event.detail?.score)||0};
    if(computed)drawChart();
  });
  $('#location').addEventListener('change',()=>{refreshExperimentalForecastContext();if(computed)drawChart()});
  window.addEventListener('focus',refreshExperimentalForecastContext);
  $('#activityAssessmentDialog').addEventListener('close',()=>{forecastContextVersion++;if(forecastContextTimer!==null){clearInterval(forecastContextTimer);forecastContextTimer=null}});
  window.renderCalendarSchedule=render;window.activateClimateScenario=()=>{scenarioActive=true;render()};window.resetClimateScenario=()=>{scenarioActive=false;render()};$('#openActivitySchedule').onclick=()=>$('#activityScheduleDialog').showModal();$('#openCalendarSettings').onclick=()=>$('#calendarSettingsDialog').showModal();$('#openScenarioSettings').onclick=()=>$('#scenarioSettingsDialog').showModal();document.querySelectorAll('[data-close]').forEach(b=>b.onclick=()=>$('#'+b.dataset.close).close());$('#activityAssessmentDialog').querySelector('[data-activity-assessment-close]').onclick=()=>$('#activityAssessmentDialog').close();$('#activityAssessmentDialog').addEventListener('click',event=>{if(event.target.id==='activityAssessmentDialog')event.target.close()});$('#fitProject').onclick=()=>{const c=$('#mapCanvas');c.scrollLeft=0;c.scrollTop=0};$('#activityGantt').addEventListener('click',event=>{const target=event.target.closest('[data-assess-code]');if(target)openActivityAssessment(target.dataset.assessCode,target.dataset.scheduleId)});$('#activityGantt').addEventListener('keydown',event=>{const target=event.target.closest('[data-assess-code]');if(target&&(event.key==='Enter'||event.key===' ')){event.preventDefault();openActivityAssessment(target.dataset.assessCode,target.dataset.scheduleId)}});$('#activityEditor').addEventListener('click',e=>{if(e.target.closest('tr')){selected=e.target.closest('tr').dataset.id;if(computed)drawResults()}});

  ['projectStart','workdays','workHours','holidays','scenarioStart','scenarioEnd','scheduleHazard','productivity','recoveryDays','interventionProductivity','interventionRecoveryDays'].forEach(id=>$('#'+id).addEventListener('change',render));$('#addActivity').onclick=()=>{const n=Math.max(0,...activities.map(a=>+(a.id.match(/\d+/)?.[0]||0)))+1;activities.push({id:'A'+n,name:'New activity',stage:'New stage',durationDays:3,predecessors:'',exposure:'outdoor',heatSensitivity:'medium',rainSensitivity:'medium'});render()};

  $('#csvImport').onchange=async e=>{try{const rows=parseCSV(await e.target.files[0].text()),head=rows.shift().map(x=>x.trim()),required=['id','name','stage','durationDays','predecessors','exposure','heatSensitivity','rainSensitivity'];if(required.some(h=>!head.includes(h)))throw Error('CSV headers required: '+required.join(', '));const nextRows=rows.map(row=>{const item=Object.fromEntries(head.map((h,i)=>[h,row[i]??'']));if(item.dependencies){try{item.dependencies=JSON.parse(item.dependencies)}catch{throw Error(`Activity ${item.id}: dependencies must be a valid JSON array.`)}}return item});schedule(nextRows,$('#projectStart').value,readCal(),currentScenario());activities=nextRows;render()}catch(err){$('#scheduleMessage').textContent='Import rejected; existing programme kept. '+err.message;$('#scheduleMessage').className='schedule-error'}e.target.value=''};

  $('#downloadTemplate').onclick=()=>download('c-resist-activity-template.csv',headers.join(',')+'\n'+['A1','Site survey','Preliminaries',3,'','','outdoor','medium','low'].map(csvCell).join(',')+'\n'+['A2','Excavate','Groundworks',5,'A1',JSON.stringify([{predecessorId:'A1',type:'FS',lag:0}]),'outdoor','high','high'].map(csvCell).join(',')+'\n');

  $('#exportCsv').onclick=()=>{if(!computed)return;const lines=[['id','name','baselineStart','baselineFinish','baselineFloatWorkingDays','adjustedStart','adjustedFinish','projectScheduleShiftCalendarDays','directWorkLossProductiveDays','directLostProductiveHours','uncertaintyLowerHours','uncertaintyUpperHours','reworkHours','recoveryWorkdays','directlyDisrupted','inheritedShift','productivityModelStatus','interventionStart','interventionFinish'].join(',')];for(const id of computed.order){const a=activities.find(x=>x.id===id),b=computed.base[id],r=computed.adj[id],ir=intervention.adj[id],sh=Math.round((Date.parse(r.finish+'T00:00:00Z')-Date.parse(b.finish+'T00:00:00Z'))/86400000);lines.push([a.id,a.name,b.start,b.finish,b.float,r.start,r.finish,sh,r.loss.toFixed(2),r.directLostProductiveHours.toFixed(2),r.uncertaintyLowerHours.toFixed(2),r.uncertaintyUpperHours.toFixed(2),r.reworkHours.toFixed(2),r.recoveryDays,r.direct,r.inherited,r.modelStatus||'',ir.start,ir.finish].map(csvCell).join(','))}download('c-resist-schedule-results.csv',lines.join('\n'))};

  $('#exportJson').onclick=()=>download('c-resist-project.json',JSON.stringify({schemaVersion:1,activities,projectStart:$('#projectStart').value,workdays:$('#workdays').value,workHours:+$('#workHours').value,holidays:$('#holidays').value,scenario:currentScenario(),intervention:{factor:+$('#interventionProductivity').value,recovery:+$('#interventionRecoveryDays').value}},null,2));

  $('#jsonImport').onchange=async e=>{try{const obj=JSON.parse(await e.target.files[0].text());if(obj.schemaVersion!==1||!Array.isArray(obj.activities))throw Error('Unsupported project JSON schema.');schedule(obj.activities,obj.projectStart,{weekdays:(obj.workdays||'1,2,3,4,5').split(',').map(Number),holidays:new Set((obj.holidays||'').split(',').filter(Boolean))},obj.scenario);if(obj.workHours!==undefined&&(!Number.isFinite(+obj.workHours)||+obj.workHours<=0||+obj.workHours>24))throw Error('Invalid working hours per day.');if(obj.intervention&&(!Number.isFinite(+obj.intervention.factor)||obj.intervention.factor<0||obj.intervention.factor>1||!Number.isInteger(+obj.intervention.recovery)||obj.intervention.recovery<0))throw Error('Invalid intervention assumptions.');activities=obj.activities;$('#projectStart').value=obj.projectStart;$('#workdays').value=obj.workdays||'1,2,3,4,5';$('#workHours').value=obj.workHours||8;$('#holidays').value=obj.holidays||'';Object.entries(obj.scenario||{}).forEach(([k,v])=>{const el=$('#'+({start:'scenarioStart',end:'scenarioEnd',hazard:'scheduleHazard',factor:'productivity',recovery:'recoveryDays'}[k]||''));if(el)el.value=v});if(obj.intervention){$('#interventionProductivity').value=obj.intervention.factor;$('#interventionRecoveryDays').value=obj.intervention.recovery}render()}catch(err){$('#scheduleMessage').textContent='Import rejected; existing programme kept. '+err.message;$('#scheduleMessage').className='schedule-error'}e.target.value=''};

  function csvCell(x){return '"'+String(x).replaceAll('"','""')+'"'}function download(name,txt){const a=document.createElement('a'),url=URL.createObjectURL(new Blob([txt],{type:'text/plain'}));a.href=url;a.download=name;a.click();URL.revokeObjectURL(url)}

  try{const saved=JSON.parse(localStorage.getItem(key)||'null');if(saved!==null&&(saved?.schemaVersion!==1||!Array.isArray(saved.activities)))throw Error('Saved project uses an unsupported schema.');if(saved){schedule(saved.activities,saved.projectStart,{weekdays:(saved.workdays||'1,2,3,4,5').split(',').map(Number),holidays:new Set((saved.holidays||'').split(',').filter(Boolean))},saved.scenario);activities=saved.activities;$('#projectStart').value=saved.projectStart;$('#workdays').value=saved.workdays||'1,2,3,4,5';$('#workHours').value=saved.workHours||8;$('#holidays').value=saved.holidays||'';if(saved.scenario){$('#scenarioStart').value=saved.scenario.start;$('#scenarioEnd').value=saved.scenario.end;$('#scheduleHazard').value=saved.scenario.hazard;$('#productivity').value=saved.scenario.factor;$('#recoveryDays').value=saved.scenario.recovery}if(saved.intervention){$('#interventionProductivity').value=saved.intervention.factor;$('#interventionRecoveryDays').value=saved.intervention.recovery}}}catch{persistenceWarning='Saved project was invalid or incompatible and was left untouched.';preserveBadSave=true}

  render();if(persistenceWarning){$('#scheduleMessage').textContent+=' '+persistenceWarning;$('#scheduleMessage').className='schedule-error'}

})();
