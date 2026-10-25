(() => {

  const $ = selector => document.querySelector(selector);

  const $$ = selector => [...document.querySelectorAll(selector)];

  state.mode = 'current';

  const escapeHTML = value => String(value).replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));

  const phaseKeys = Object.keys(networks);

  const additions = Object.fromEntries(phaseKeys.map(phase => [phase, []]));

  const customTypes = {};

  let chooserView = 'activities';

  let networkView = 'activities';

  const controlAssignments = {};

  let activeAssessmentNode = null;

  const visibility = Object.fromEntries(phaseKeys.map(phase => [phase, {}]));

  const lanes = [

    { key: 'design', label: '01  DESIGN & CONTROL', ids: new Set(['designer','authority']) },

    { key: 'supply', label: '02  SUPPLIERS & SUBCONTRACTORS', ids: new Set(['plant','fuel','materials','concrete','steel','piling','precast','glass','hvac','electrical','drywall','finishes','safety','fire','demoSurvey','demoContractor','survey','formation','testing','earth','formwork']) },

    { key: 'delivery', label: '03  DELIVERY & EQUIPMENT', ids: new Set(['haul','drain','pump','crane','transport','logistics']) },

    { key: 'activity', label: '04  SITE ACTIVITIES & RELEASE', ids: new Set(['site','handover','qa','isolate','demo','wasteSort','pile','foundation','frame','floorCycle','facadeInstall','mepInstall','waterTest','partitions','finishInstall','mepSecond','systemTest','defects','training']) }

  ];

  const riskMatrix={1:['Low','Low','Low','Medium','Medium'],2:['Low','Medium','Medium','Medium','Medium'],3:['Low','Medium','Medium','Medium','High'],4:['Medium','Medium','Medium','High','High'],5:['Medium','Medium','High','High','High']};

  const severityText={1:'Negligible - negligible injury',2:'Minor - first-aid injury or temporary discomfort',3:'Moderate - injury or ill-health requiring medical treatment',4:'Major - serious injury, occupational disease or exposure',5:'Catastrophic - fatality, fatal disease or multiple major injuries'};

  const likelihoodText={1:'Rare - not expected, but possible',2:'Remote - not likely under normal circumstances',3:'Occasional - possible or known to occur',4:'Frequent - common occurrence',5:'Almost certain - continual or repeating experience'};

  const seriousTasks=new Set(['site','earth','haul','drain','pump','crane','piling','transport','formwork','precast','safety','demo','isolate','formation','pile','foundation','frame','floorCycle','facadeInstall']);

  const outdoorTasks=new Set(['site','earth','haul','drain','pump','crane','piling','transport','formwork','concrete','steel','precast','glass','drywall','demo','isolate','wasteSort','survey','formation','pile','foundation','frame','floorCycle','facadeInstall','mepInstall']);

  const wbsId=(phase,code,prefix='activity')=>`${prefix}_${phase}_${code.replaceAll('.','_')}`;

  const A=(phase,code,label,flood,heat,links=[],floodLevel=3,heatLevel=3)=>({id:wbsId(phase,code),code,label,flood,heat,links,floodLevel,heatLevel});

  const D=(phase,code,label,taskCodes)=>({id:wbsId(phase,code,'deliverable'),code,label,taskIds:taskCodes.map(taskCode=>wbsId(phase,taskCode))});

  const workBreakdowns={

    demolition:{groups:[

      {code:'1.1',title:'Mobilization & survey',tasks:[A('demolition','1.1.1','Locate utilities and survey hazardous materials','Flooded access can delay inspection of service chambers and survey areas.','Outdoor inspection exposes survey crews to heat and PPE burden.',['authority','demoSurvey','designer'],3,4),A('demolition','1.1.2','Isolate and verify live services','Water ingress or flooded chambers can prevent safe isolation checks.','Outdoor utility chambers expose workers to heat during isolation.',['authority','demoSurvey'],4,4),A('demolition','1.1.3','Set up exclusion zones and temporary protection','Floodwater can undermine barriers and restrict safe access.','Outdoor set-up work exposes crews to sun and heat.',['demoContractor','logistics'],3,3)]},

      {code:'1.2',title:'Clearing & controlled demolition',tasks:[A('demolition','1.2.1','Soft-strip non-structural elements','Water ingress can damage salvage and make internal areas unsafe.','Manual stripping and PPE increase heat strain.',['demoContractor','plant'],3,4),A('demolition','1.2.2','Carry out controlled structural demolition','Standing water and unstable access can suspend demolition plant operations.','Heavy outdoor work, dust PPE and machinery increase heat exposure.',['demoContractor','plant','safety'],5,5),A('demolition','1.2.3','Segregate, load and remove demolition waste','Flooded access interrupts loading and licensed waste haulage.','Outdoor loading and sorting increase exertion and heat.',['haul','logistics'],4,4)]},

      {code:'1.3',title:'Clearance & release',tasks:[A('demolition','1.3.1','Inspect the cleared site and close permits','Wet or inaccessible areas can delay inspection and clearance.','Outdoor inspection requires heat-safe work windows.',['authority','demoSurvey'],3,3),A('demolition','1.3.2','Release the work front for ground preparation','Flood damage or unresolved water ingress delays safe release.','Heat-related reduced crew capacity can delay release checks.',['designer','authority'],2,2)]}

    ],deliverables:[]},

    groundprep:{groups:[

      {code:'1.1',title:'Mobilization & survey',tasks:[A('groundprep','1.1.1','Set out control points and benchmarks','Standing water can obscure ground features and disrupt setting-out accuracy.','Survey crews work outdoors for extended periods; schedule set-out in cooler hours and provide shade.',['designer','survey'],3,3),A('groundprep','1.1.2','Mobilize excavators, dozers and rollers to site','Flooded or blocked access routes can delay heavy plant delivery and unloading.','Operators and ground guides face heat during outdoor unloading and equipment checks.',['plant','haul','logistics'],3,4)]},

      {code:'1.2',title:'Clearing & stripping activities',tasks:[A('groundprep','1.2.1','Bulldoze trees, bushes and surface vegetation','Saturated ground can restrict dozer access; floodwater can destabilise cleared areas.','Bulldozing is high-exertion outdoor work around moving plant in hot conditions.',['plant','haul','safety'],4,5),A('groundprep','1.2.2','Strip and stockpile topsoil','Flooding can saturate, erode or contaminate topsoil stockpiles.','Outdoor stripping and stockpile handling expose operators and ground crews to heat.',['plant','materials','logistics'],4,4)]},

      {code:'1.3',title:'Excavation & hauling activities',tasks:[A('groundprep','1.3.1','Rip hard strata where required','Rainwater can enter the work area and destabilise the plant working platform.','Ripping uses heavy plant outdoors; heat and fatigue can affect operators and ground guides.',['plant','drain','safety'],4,4),A('groundprep','1.3.2','Excavate building-pad, pond and utility-trench areas','Flooding can inundate excavations, undermine temporary support and create unsafe access.','Excavation is outdoor, high-exertion work around heavy equipment and exposed edges.',['plant','authority','drain','safety'],5,5),A('groundprep','1.3.3','Load excavated material into dump trucks','Flooded or unstable haul roads can interrupt loading and safe truck movements.','Outdoor loading crews and operators face heat, fatigue and plant interaction risks.',['plant','haul','safety'],4,5),A('groundprep','1.3.4','Haul soil to disposal or designated fill zones','Flooded access routes can stop haulage and interrupt downstream placement work.','Repeated outdoor truck cycles expose drivers and ground crews to heat and fatigue.',['haul','fuel','logistics'],4,4)]},

      {code:'1.4',title:'Placement & compaction activities',tasks:[A('groundprep','1.4.1','Spread soil in controlled loose layers (lifts)','Flooding saturates fill and prevents placement to the required lift thickness.','Outdoor grading and spreading involve sustained exertion and heat exposure.',['plant','materials'],4,4),A('groundprep','1.4.2','Condition soil moisture by watering or aerating','Flooding can over-saturate soil and extend drying and conditioning time.','Watering, aerating and equipment operation expose crews to heat outdoors.',['plant','fuel'],4,4),A('groundprep','1.4.3','Compact each lift using vibratory rollers','Waterlogged ground prevents compaction and can require drying, retesting and rework.','Roller operators and ground crews work outdoors beside hot, vibrating equipment.',['plant','fuel','safety'],5,4)]},

      {code:'1.5',title:'Quality control activities',tasks:[A('groundprep','1.5.1','Conduct sand-cone or nuclear-gauge density tests','Wet, flooded or inaccessible test locations delay reliable density testing and acceptance.','Field testing exposes QA staff to sun and hot ground; use shaded breaks and cooler work windows.',['formation','materials','safety'],3,3),A('groundprep','1.5.2','Complete final topographical as-built survey','Standing water can hide levels and prevent safe survey access or accurate readings.','As-built survey crews work outdoors and may need cooler-hour scheduling and shaded recovery.',['survey','formation','designer'],3,3)]}

    ],deliverables:[

      D('groundprep','1.1.1','Vegetation and topsoil stripped area',['1.2.1','1.2.2']),D('groundprep','1.1.2','Demolished and cleared surface obstructions',['1.2.1']),D('groundprep','1.1.3','Installed erosion and sediment control perimeter',['1.2.1']),

      D('groundprep','1.2.1','Zone A building-pad cut to formation level',['1.3.1','1.3.2']),D('groundprep','1.2.2','Zone B retention-pond excavated basin',['1.3.2']),D('groundprep','1.2.3','Utility and drainage route trench excavations',['1.3.2']),

      D('groundprep','1.3.1','Compacted Zone A subgrade platform',['1.4.1','1.4.2','1.4.3']),D('groundprep','1.3.2','Access-road structural-fill embankment',['1.4.1','1.4.2','1.4.3']),

      D('groundprep','1.4.1','Compaction density test reports',['1.5.1']),D('groundprep','1.4.2','Final topographical as-built survey',['1.5.2'])

    ]},

    substructure:{groups:[

      {code:'2.1',title:'Setting out & excavation',tasks:[A('substructure','2.1.1','Set out foundation and pile coordinates','Flooded ground can obscure survey references and delay setting out.','Outdoor survey and rig set-up expose crews to heat.',['designer','survey'],3,3),A('substructure','2.1.2','Excavate and support foundation work areas','Flooding can inundate excavations and challenge temporary support.','Excavation and shoring are high-exertion outdoor tasks.',['authority','drain','haul'],5,5)]},

      {code:'2.2',title:'Piling & ground improvement',tasks:[A('substructure','2.2.1','Prepare and verify piling working platform','Flooded or softened platforms can compromise rig stability.','Outdoor rig operations and ground crews face high heat exposure.',['piling','plant','drain'],5,5),A('substructure','2.2.2','Install piles and record installation data','Flooding affects rig access and can interrupt pile installation.','Piling crews work outdoors beside hot equipment and machinery.',['piling','concrete'],5,5)]},

      {code:'2.3',title:'Foundation construction',tasks:[A('substructure','2.3.1','Fix reinforcement and install formwork','Water ingress contaminates excavations and delays inspection.','Rebar and formwork are strenuous outdoor activities.',['steel','piling'],4,5),A('substructure','2.3.2','Place and cure foundation concrete','Floodwater can compromise pours; heat can accelerate moisture loss.','Hot weather affects workers, concrete handling and curing control.',['concrete','pump'],5,5)]},

      {code:'2.4',title:'Testing & release',tasks:[A('substructure','2.4.1','Test concrete and inspect foundation works','Flooding delays sampling, inspection and acceptance.','Outdoor inspection and sampling expose QA staff to heat.',['testing','authority'],3,3)]}

    ],deliverables:[]},

    superstructure:{groups:[

      {code:'3.1',title:'Frame logistics & access',tasks:[A('superstructure','3.1.1','Receive and stage structural components','Flooded roads or storage areas delay deliveries and damage materials.','Outdoor unloading and staging expose workers to heat.',['precast','steel','transport','logistics'],4,4),A('superstructure','3.1.2','Inspect lifting equipment and edge protection','Flooded access can delay equipment checks and safe access provision.','Outdoor checks and work-at-height preparation expose crews to heat.',['crane','safety','authority'],3,4)]},

      {code:'3.2',title:'Structural frame & floor cycles',tasks:[A('superstructure','3.2.1','Lift and erect columns, beams and precast elements','Wet access and flood-related route disruption can stop lifting.','Lifting and work at height raise heat strain and fatigue consequences.',['crane','precast','steel','transport'],5,5),A('superstructure','3.2.2','Install formwork, reinforcement and floor decks','Rainwater can collect on decks and delay safe work-at-height.','Outdoor formwork and rebar work combine exertion and height.',['formwork','safety'],4,5),A('superstructure','3.2.3','Place, finish and cure floor concrete','Rain can disrupt pours; standing water affects decks and access.','Heat affects concrete placement, worker capacity and curing.',['concrete','pump'],5,5)]},

      {code:'3.3',title:'Survey & structural release',tasks:[A('superstructure','3.3.1','Survey alignment and inspect completed frame','Wet access or obscured survey points delay inspection and sign-off.','Outdoor and elevated inspection requires safe heat work windows.',['designer','authority','safety'],3,4)]}

    ],deliverables:[]},

    envelope:{groups:[

      {code:'4.1',title:'Façade & roof installation',tasks:[A('envelope','4.1.1','Prepare façade access and lifting systems','Flooded access restricts hoists, mast climbers and material delivery.','Hot equipment and elevated outdoor work raise heat exposure.',['crane','transport','safety'],4,5),A('envelope','4.1.2','Install façade panels, glazing and weather seals','Rain and wet surfaces interrupt sealing and work at height.','Façade work at height exposes crews to heat and hot surfaces.',['glass','crane','safety'],5,5),A('envelope','4.1.3','Install roof waterproofing and drainage','Heavy rain interrupts roof work and can expose incomplete areas.','Roof work in direct sun creates significant heat exposure.',['safety','materials'],4,5)]},

      {code:'4.2',title:'MEP first-fix & closure',tasks:[A('envelope','4.2.1','Install MEP risers and first-fix services','Water ingress can expose electrical systems and damage installed services.','Hot plant rooms and outdoor plant handling increase worker heat exposure.',['hvac','electrical','authority'],4,4),A('envelope','4.2.2','Close façade interfaces and verify weather-tightness','Rainwater intrusion delays closure and may require repeat testing.','Heat can affect sealant application, curing and work-at-height windows.',['glass','safety'],4,4),A('envelope','4.2.3','Test envelope and release zone for fit-out','Rain can obscure test results or prevent repeatable testing.','Hot, humid conditions affect crews and some test procedures.',['authority','designer'],3,3)]}

    ],deliverables:[]},

    fitout:{groups:[

      {code:'5.1',title:'Partitions & ceilings',tasks:[A('fitout','5.1.1','Set out internal partitions and service openings','Water ingress damages boards and interrupts closure work.','Hot enclosed spaces increase fatigue during installation.',['designer','drywall','hvac','electrical'],3,3),A('fitout','5.1.2','Install partitions, ceilings and access panels','Wet materials and moisture delays affect board installation.','Ceiling-void work in warm spaces increases heat strain.',['drywall','logistics'],3,4)]},

      {code:'5.2',title:'Finishes & second-fix services',tasks:[A('fitout','5.2.1','Install floor, wall and paint finishes','Flooding/moisture damages finishes and delays curing.','Temperature-sensitive products need application and curing checks.',['finishes','logistics'],4,3),A('fitout','5.2.2','Complete MEP second-fix and equipment connections','Water ingress can damage equipment and require isolation/retesting.','Hot rooms and ceiling voids shorten safe work windows.',['hvac','electrical'],4,4)]},

      {code:'5.3',title:'Inspection & acceptance',tasks:[A('fitout','5.3.1','Inspect finishes and close quality defects','Moisture damage and inaccessible areas delay inspection.','Hot enclosed work areas increase fatigue during repair and inspection.',['qa','authority'],3,3)]}

    ],deliverables:[]},

    commissioning:{groups:[

      {code:'6.1',title:'System readiness',tasks:[A('commissioning','6.1.1','Verify utilities, access and commissioning permits','Flooded plant rooms or access routes delay system readiness.','Hot plant rooms increase heat exposure during readiness checks.',['authority','logistics','designer'],3,3),A('commissioning','6.1.2','Energize and inspect individual building systems','Water ingress can create electrical risk and damage components.','Hot rooms and equipment affect crew exposure and test windows.',['electrical','hvac','fire'],4,4)]},

      {code:'6.2',title:'Integrated testing & handover',tasks:[A('commissioning','6.2.1','Run integrated systems and performance tests','Flooded areas or power interruption stop integrated testing.','Hot plant-room access shortens safe testing periods.',['testing','hvac','electrical','fire'],4,3),A('commissioning','6.2.2','Close defects and repeat failed tests','Water damage requires drying, repair and repeat testing.','Hot work areas increase repair fatigue and can delay retests.',['testing','qa'],3,3),A('commissioning','6.2.3','Train operators and transfer asset records','Flooded access or unavailable facilities delay training/handover.','Heat may reduce attention during outdoor or plant-room training.',['authority','logistics','designer'],2,2)]}

    ],deliverables:[]}

  };

  const wbsActivities=new Map(Object.values(workBreakdowns).flatMap(definition=>definition.groups.flatMap(group=>group.tasks.map(task=>[task.id,task]))));

  const wbsDeliverables=new Map(Object.values(workBreakdowns).flatMap(definition=>definition.deliverables.map(item=>[item.id,item])));

  const catalogueTask=(phase,code)=>{
    const task=workBreakdowns[phase].groups.flatMap(group=>group.tasks).find(item=>item.code===code);
    if(!task)throw new Error(`Missing catalogue activity ${phase} ${code}`);
    return task;
  };

  const catalogueGroup=(phase,code,title,taskCodes)=>({code,title,tasks:taskCodes.map(taskCode=>catalogueTask(phase,taskCode))});

  const catalogueStages=[
    {key:'site-preparation',code:'SP',name:'Site Preparation',groups:workBreakdowns.demolition.groups},
    {key:'earthworks',code:'EW',name:'Earthworks',groups:workBreakdowns.groundprep.groups},
    {key:'foundation',code:'FD',name:'Foundation',groups:workBreakdowns.substructure.groups},
    {key:'superstructure',code:'SS',name:'Superstructure',groups:workBreakdowns.superstructure.groups},
    {key:'mep-installation',code:'MEP',name:'Mechanical, Electrical and Plumbing Installation',groups:[
      catalogueGroup('envelope','4.1','Building envelope & roof',['4.1.1','4.1.2','4.1.3']),
      catalogueGroup('envelope','4.2','Envelope closure & weather-tightness',['4.2.2','4.2.3']),
      catalogueGroup('envelope','4.2','MEP first-fix installation',['4.2.1']),
      catalogueGroup('fitout','5.2','MEP second-fix installation',['5.2.2'])
    ]},
    {key:'finishing-works',code:'FN',name:'Finishing Works',groups:[
      catalogueGroup('fitout','5.1','Partitions & ceilings',['5.1.1','5.1.2']),
      catalogueGroup('fitout','5.2','Finishes',['5.2.1']),
      catalogueGroup('fitout','5.3','Inspection & acceptance',['5.3.1'])
    ]},
    {key:'testing-commissioning-handover',code:'TC',name:'Testing, Commissioning and Handover',groups:workBreakdowns.commissioning.groups}
  ];

  const catalogueTasks=catalogueStages.flatMap(stage=>stage.groups.flatMap(group=>group.tasks));
  if(catalogueTasks.length!==wbsActivities.size||new Set(catalogueTasks.map(task=>task.id)).size!==wbsActivities.size)throw new Error('Activity catalogue stages must include every WBS activity exactly once.');

  const cataloguePhaseByTask=new Map(Object.entries(workBreakdowns).flatMap(([phase,definition])=>definition.groups.flatMap(group=>group.tasks.map(task=>[task.id,phase]))));

  window.CResistActivityCatalogue=catalogueStages.flatMap(stage=>stage.groups.flatMap(group=>group.tasks.map(task=>({id:task.id,code:task.code,name:task.label,phase:cataloguePhaseByTask.get(task.id),phaseCode:stage.code,phaseName:stage.name,scope:task.flood+' '+task.heat,links:task.links||[]}))));

  function renderActivityCatalogue(){
    const host=$('#activityCatalogue');
    if(!host)return;
    const selectedCount=stage=>stage.groups.flatMap(group=>group.tasks).filter(task=>window.CResistSchedule?.has(task.id)).length;
    host.innerHTML=catalogueStages.map(stage=>`<button type="button" class="catalogue-stage" data-open-stage="${stage.key}"><span><b>${stage.code}</b><strong>${escapeHTML(stage.name)}</strong></span><small><span id="catalogueCount_${stage.key}">${selectedCount(stage)} selected</span> / View activities</small></button>`).join('');
    let dialog=$('#stageCatalogueDialog');
    if(!dialog){dialog=document.createElement('dialog');dialog.id='stageCatalogueDialog';dialog.className='schedule-dialog stage-catalogue-dialog';document.body.append(dialog)}
    const showStage=key=>{
      const stage=catalogueStages.find(item=>item.key===key);
      if(!stage)return;
      dialog.innerHTML=`<div class="dialog-head"><div><p class="eyebrow">${stage.code} / CONSTRUCTION STAGE</p><h2>${escapeHTML(stage.name)}</h2></div><button type="button" class="dialog-close" data-stage-close aria-label="Close stage activities">Close</button></div><p class="stage-dialog-note">Select activities and set their planned working-day durations. Activity exposure and disruption are screened for rainfall and flooding only.</p><div class="catalogue-actions"><button type="button" data-stage-all>Select all activities</button><button type="button" data-stage-clear>Clear selections</button></div><div class="stage-activity-list">${stage.groups.map(group=>`<section class="catalogue-group"><h3>${escapeHTML(group.code)} - ${escapeHTML(group.title)}</h3>${group.tasks.map(task=>{const item=window.CResistSchedule?.get(task.id);return `<article class="stage-activity-row"><label class="stage-activity-name"><input type="checkbox" data-stage-select="${task.id}" ${item?'checked':''}><span><b>${escapeHTML(task.code)}</b> ${escapeHTML(task.label)}<small>${escapeHTML(task.flood)}</small></span></label><label>Duration (workdays)<input type="number" min="1" step="1" data-stage-field="durationDays" data-stage-id="${task.id}" value="${item?.durationDays||''}" placeholder="Set duration"></label><label>Relationship<select data-stage-field="relationship" data-stage-id="${task.id}">${['FS','SS','FF','SF'].map(type=>`<option ${type===(item?.relationship||'FS')?'selected':''}>${type}</option>`).join('')}</select></label><label>Predecessor codes<input data-stage-field="predecessorCodes" data-stage-id="${task.id}" value="${escapeHTML(item?.predecessorCodes||'')}" placeholder="e.g. 1.1.1, 1.1.2"></label><label>Successor codes<input data-stage-field="successorCodes" data-stage-id="${task.id}" value="${escapeHTML(item?.successorCodes||'')}" placeholder="e.g. 1.2.1"></label></article>`}).join('')}</section>`).join('')}</div>`;
      dialog.querySelector('.stage-dialog-note').textContent='Selected tasks default to outdoor exposure and medium rainfall sensitivity. Confirm both against the site layout and method statement. Set planned working-day duration; use Edit dependencies to define task links.';
      dialog.querySelectorAll('.stage-activity-row').forEach(row=>{
        row.querySelectorAll('label').forEach((label,index)=>{if(index>1)label.remove()});
        const checkbox=row.querySelector('[data-stage-select]'),item=window.CResistSchedule?.get(checkbox.dataset.stageSelect),button=document.createElement('button');
        [['exposure','Site exposure',['outdoor','partially sheltered','indoor','not confirmed'],item?.exposure||'outdoor'],['rainSensitivity','Rainfall sensitivity',['low','medium','high'],item?.rainSensitivity||'medium']].forEach(([field,title,options,value])=>{
          const label=document.createElement('label'),select=document.createElement('select');
          label.textContent=title;select.dataset.stageField=field;select.dataset.stageId=checkbox.dataset.stageSelect;select.disabled=!checkbox.checked;
          options.forEach(option=>{const element=document.createElement('option');element.value=option;element.textContent=option;select.append(element)});
          select.value=value;label.append(select);row.append(label);
        });
        button.type='button';button.textContent='Edit dependencies';button.disabled=!checkbox.checked;
        button.addEventListener('click',()=>window.CResistSchedule?.editDependencies(checkbox.dataset.stageSelect));
        checkbox.addEventListener('change',()=>{button.disabled=!checkbox.checked;row.querySelectorAll('[data-stage-field]').forEach(field=>field.disabled=!checkbox.checked)});
        row.append(button);
      });
      dialog.showModal();
      dialog.querySelector('[data-stage-close]').onclick=()=>dialog.close();
      const refresh=()=>{
        window.dispatchEvent(new Event('c-resist-schedule-change'));
        const counter=$(`#catalogueCount_${stage.key}`);
        if(counter)counter.textContent=`${selectedCount(stage)} selected`;
      };
      dialog.querySelectorAll('[data-stage-select]').forEach(input=>input.onchange=()=>{window.CResistSchedule?.select(input.dataset.stageSelect,input.checked);refresh()});
      dialog.querySelectorAll('[data-stage-field]').forEach(input=>input.onchange=()=>{window.CResistSchedule?.configure(input.dataset.stageId,input.dataset.stageField,input.value);refresh()});
      dialog.querySelector('[data-stage-all]').onclick=()=>{dialog.querySelectorAll('[data-stage-select]').forEach(input=>{input.checked=true;window.CResistSchedule?.select(input.dataset.stageSelect,true)});dialog.close();showStage(stage.key)};
      dialog.querySelector('[data-stage-clear]').onclick=()=>{dialog.querySelectorAll('[data-stage-select]').forEach(input=>{input.checked=false;window.CResistSchedule?.select(input.dataset.stageSelect,false)});refresh();dialog.close();showStage(stage.key)};
    };
    host.querySelectorAll('[data-open-stage]').forEach(button=>button.onclick=()=>showStage(button.dataset.openStage));
    window.addEventListener('c-resist-schedule-change',()=>{
      catalogueStages.forEach(stage=>{
        const counter=$(`#catalogueCount_${stage.key}`);
        if(counter)counter.textContent=`${selectedCount(stage)} selected`;
      });
    });
  }

  renderActivityCatalogue();



  const legacyActivityIds=new Set(['isolate','demo','wasteSort','handover','pile','foundation','frame','floorCycle','facadeInstall','mepInstall','waterTest','partitions','finishInstall','mepSecond','qa','systemTest','defects','training',...Object.values(networks).flatMap(network=>(network.nodes||[]).filter(node=>lanes[3].ids.has(node[0])).map(node=>node[0]))]);

  const optionalDeliverableIds=new Set(wbsDeliverables.keys());

  const floodExposure={earth:'Flooding can inundate excavations, soften formation and interrupt earthmoving.',drain:'Floodwater directly loads temporary drainage and dewatering capacity.',haul:'Flooded or blocked access can stop spoil haulage and deliveries.',formation:'Saturated ground can delay compaction and formation acceptance.',demo:'Heavy rain or standing water can destabilise demolition work areas and restrict access.',pile:'Flooded working platforms can affect piling rig stability and safe operation.',foundation:'Water ingress can affect excavation safety, reinforcement, concrete placement and curing.',crane:'Flooded access and wet lifting areas can constrain lifting.',transport:'Flooded routes can delay oversized loads and deliveries.',frame:'Flooded access and wet work areas can constrain frame work and lifting.',facadeInstall:'Rain and wet surfaces can restrict façade access, sealing and work at height.',mepInstall:'Water ingress can expose electrical systems and delay installation.',demoSurvey:'Flooded access or unsafe work areas can delay survey and hazardous-material inspection.',wasteSort:'Standing water and flooded storage can complicate segregation and waste removal.',excavate:'Floodwater can inundate excavation and undermine temporary support.',isolate:'Flooded service chambers and unsafe access can delay utility isolation.',survey:'Standing water can hide ground features and interrupt survey/set-out.',waterTest:'Heavy rainfall can disrupt façade water testing and obscure leakage observations.',partitions:'Water ingress can wet boards and disrupt internal partition work.',finishInstall:'Flooding or moisture can damage finishes and delay installation.',mepSecond:'Water ingress can damage installed electrical and mechanical systems.',systemTest:'Flooded plant rooms or power interruptions can stop integrated system tests.',defects:'Water damage and inaccessible areas can delay defect inspection and close-out.',training:'Flooded access or an unavailable building can delay safe operator training.',qa:'Wet or contaminated work can delay inspection and acceptance.',handover:'Flood damage and incomplete inspections can prevent a safe phase handover.'};

  const heatExposure={earth:'Outdoor excavation and earthmoving increase exertion and heat exposure for crews.',crane:'Heat can affect outdoor lifting crews, concentration and safe lifting windows.',frame:'Frame erection combines outdoor labour, lifting and work at height during hot conditions.',floorCycle:'Outdoor formwork, reinforcement and concrete cycles create sustained heat exposure.',facadeInstall:'Façade installation exposes workers at height and can make access equipment hot.',haul:'Outdoor haulage crews and equipment operate in heat; fatigue can affect safe movement.',demo:'Demolition is high-exertion outdoor work with heat, dust and PPE burden.',foundation:'Outdoor reinforcement, formwork and concrete placement create heat exposure.',mepInstall:'Outdoor plant handling can expose crews to heat; indoor work may be less exposed.',formwork:'Outdoor formwork involves exertion and prolonged exposure, often at height.',pile:'Piling crews operate outdoor rigs in heat, with exertion and hot equipment.',survey:'Set-out crews work outdoors and may be exposed for long periods.',finishInstall:'Heat can affect worker comfort and temperature-sensitive finishing products.',wasteSort:'Outdoor waste handling combines exertion, PPE and direct sun exposure.',isolate:'Utility isolation may involve outdoor chambers, PPE and sustained exertion in hot conditions.',waterTest:'Hot, humid conditions can affect workers and sealant performance during façade water testing.',partitions:'Hot work areas increase installer fatigue and may affect board adhesives or curing.',mepSecond:'Hot plant rooms and ceiling voids increase worker heat exposure during second-fix.',systemTest:'Hot plant rooms can raise exposure during commissioning and integrated system tests.',defects:'Hot enclosed work areas can increase fatigue during inspection and defect repairs.',training:'Heat can affect attention during outdoor or hot-area operator training.',qa:'Outdoor inspection and sampling expose QA teams to heat and sun.',handover:'Heat may affect safe inspections and the availability of crews for phase handover.'};

  const floodDisruption={earth:'Excavation pauses, water removal, unstable ground checks and rework.',drain:'Drainage capacity is exceeded, requiring pumping and work-front suspension.',haul:'Access or delivery delays interrupt spoil removal and follow-on tasks.',formation:'Compaction/QA is postponed until ground moisture and bearing conditions recover.',demo:'Unsafe access or unstable work areas pause controlled demolition and clearance.',pile:'Rig/platform checks and dewatering delay piling progress.',foundation:'Water removal, inspection and concrete/rebar rework delay foundation pours.',crane:'Lifting is suspended until access, base conditions and work areas are safe.',transport:'Route closure delays critical equipment and material arrivals.',frame:'Lifts or frame work are resequenced until access and work surfaces are safe.',facadeInstall:'Work-at-height pauses; wet seals or access conditions require inspection/rework.',mepInstall:'Electrical protection, drying and inspection delay installation or energisation.',waterTest:'Testing is rescheduled; rainwater may obscure test results.',partitions:'Wet boards or internal flooding require drying and replacement before closure.',finishInstall:'Finishes are delayed, dried or replaced after moisture checks.',mepSecond:'Water-damaged systems need isolation, drying and retesting.',systemTest:'Power or plant-room access interruptions postpone integrated tests.',defects:'Inspection and repair work is delayed until affected areas are safe and dry.',training:'Handover access or occupancy is delayed until safe conditions return.',qa:'Acceptance checks are postponed or repeated on wet/contaminated work.',handover:'Phase release is delayed pending inspection and corrective work.'};

  const heatDisruption={earth:'Heavy tasks shift to cooler hours; output falls or work pauses at heat triggers.',crane:'Lifting windows shorten or are paused when crew fitness or safe limits are affected.',frame:'Work-at-height and lifting are resequenced; fatigue can reduce productivity.',floorCycle:'Pour/curing windows and crew breaks need adjustment; quality checks may be added.',facadeInstall:'Access windows shorten; sealant handling and elevated work may be paused.',haul:'More breaks or shift changes slow vehicle cycles and material supply.',demo:'High-exertion work slows or stops for cooling, hydration and worker checks.',foundation:'Concrete placement and reinforcement work are resequenced around safe heat windows.',mepInstall:'Outdoor handling slows; hot plant or rooms may need work/rest and cooling controls.',formwork:'Work-at-height slows or pauses; crews rotate to reduce exposure.',pile:'Rig work pauses or slows for crew recovery and equipment checks.',survey:'Outdoor set-out is shifted to cooler hours, delaying work release.',finishInstall:'Temperature-sensitive products may need storage, application and curing checks.',wasteSort:'Handling productivity falls as crews take additional cooling/rest breaks.',isolate:'Outdoor chamber work is delayed or split into shorter safe work periods.',waterTest:'Testing may move to cooler windows and require sealant performance checks.',partitions:'Installation slows and adhesives/curing may need temperature checks.',mepSecond:'Hot-space work is split into shorter periods; commissioning tasks may be resequenced.',systemTest:'Plant-room access windows shorten, delaying commissioning.',defects:'Inspection and repair shifts to cooler windows, delaying close-out.',training:'Sessions are rescheduled or moved to cooler/indoor spaces.',qa:'Sampling and outdoor inspection take longer or move to cooler hours.',handover:'Inspections or crew availability delays phase release.'};

  const namesById = id => ({ designer:'Design & engineering', authority:'Permits & inspections', plant:'Plant hire', fuel:'Fuel supplier', materials:'Ground materials', concrete:'Concrete supplier', steel:'Steel fabricator', piling:'Piling subcontractor', precast:'Precast factory', glass:'Facade contractor', hvac:'HVAC subcontractor', electrical:'Electrical subcontractor', drywall:'Drywall subcontractor', finishes:'Finish materials', haul:'Haulage & spoil removal', drain:'Temporary drainage', pump:'Concrete pump crew', crane:'Crane hire', transport:'Heavy haulage', logistics:'Site logistics', site:'Construction work zone', earth:'Earthworks crew', formwork:'Formwork subcontractor', testing:'Materials testing', handover:'Phase handover', safety:'Safety subcontractor', fire:'Fire systems subcontractor', qa:'Quality & testing team', demoSurvey:'Hazard survey team', demoContractor:'Demolition contractor', isolate:'Utility isolation', demo:'Controlled demolition', wasteSort:'Waste segregation', survey:'Survey and set-out', formation:'Formation QA', pile:'Pile installation', foundation:'Foundation works', frame:'Structural frame', floorCycle:'Floor cycle', facadeInstall:'Facade installation', mepInstall:'MEP first-fix', waterTest:'Weather-tightness test', partitions:'Install partitions', finishInstall:'Install finishes', mepSecond:'MEP second-fix', systemTest:'Integrated system test', defects:'Defect close-out', training:'Operator training' }[id] || id);

  function phaseNodes(phase){

    const definition=workBreakdowns[phase],tasks=definition.groups.flatMap(group=>group.tasks.map(task=>[task.id,0,0,`${task.code} ${task.label}`,`${group.code} ${group.title}`,task]));

    const outputs=definition.deliverables.map(item=>[item.id,0,0,`${item.code} ${item.label}`,'WBS deliverable',item]);

    return [...networks[phase].nodes,...tasks,...outputs,...additions[phase]];

  }

  const activeNodes = () => {

    const phase=state.phase;

    return phaseNodes(phase).filter(node=>{

      const id=node[0],selection=visibility[phase][id];

      if(selection===false)return false;

      if(legacyActivityIds.has(id)&&selection!==true)return false;

      if(optionalDeliverableIds.has(id)&&selection!==true)return false;

      return true;

    });

  };

  const nodeGroup = node => wbsActivities.has(node[0])||legacyActivityIds.has(node[0])?'activities':wbsDeliverables.has(node[0])?'deliverables':customTypes[node[0]]?(customTypes[node[0]]==='activity'?'activities':'stakeholders'):(laneFor(node[0])===3?'activities':'stakeholders');

  function phaseEdges(nodes=activeNodes()){

    const selected=new Set(nodes.map(node=>node[0])),edges=[...networks[state.phase].edges];

    let previousGroupLast=null;

    workBreakdowns[state.phase].groups.forEach(group=>{

      const selectedTasks=group.tasks.filter(task=>selected.has(task.id));

      selectedTasks.forEach((task,index)=>{

        if(index)edges.push([selectedTasks[index-1].id,task.id]);

        task.links.forEach(actorId=>{if(selected.has(actorId))edges.push([actorId,task.id]);});

      });

      if(previousGroupLast&&selectedTasks.length)edges.push([previousGroupLast,selectedTasks[0].id]);

      if(selectedTasks.length)previousGroupLast=selectedTasks[selectedTasks.length-1].id;

    });

    workBreakdowns[state.phase].deliverables.forEach(item=>item.taskIds.forEach(taskId=>{if(selected.has(taskId)&&selected.has(item.id))edges.push([taskId,item.id]);}));

    return [...new Map(edges.map(edge=>[`${edge[0]}→${edge[1]}`,edge])).values()];

  }

  function viewNodes() {

    const all=activeNodes(),edges=phaseEdges(all),primary=all.filter(node=>nodeGroup(node)===networkView),primaryIds=new Set(primary.map(node=>node[0])),connectorIds=new Set();

    edges.forEach(([from,to])=>{

      const fromNode=all.find(node=>node[0]===from),toNode=all.find(node=>node[0]===to);

      if(!fromNode||!toNode)return;

      if(primaryIds.has(from)&&!primaryIds.has(to))connectorIds.add(to);

      if(primaryIds.has(to)&&!primaryIds.has(from))connectorIds.add(from);

    });

    if(networkView==='activities'){

      const order=[...workBreakdowns[state.phase].groups.flatMap(group=>group.tasks.map(task=>task.id)),...(networks[state.phase].nodes||[]).filter(node=>legacyActivityIds.has(node[0])).map(node=>node[0])];

      const sequence=id=>{const index=order.indexOf(id);return index<0?order.length:index;};

      primary.sort((a,b)=>sequence(a[0])-sequence(b[0]));

    }

    const connectors=all.filter(node=>connectorIds.has(node[0]));

    const visibleIds=new Set([...primaryIds,...connectorIds]);

    return {primary,connectors,nodes:[...connectors,...primary],primaryIds,edges:edges.filter(([from,to])=>visibleIds.has(from)&&visibleIds.has(to))};

  }

  function buildNodeEditor() {

    const host = $('#nodeToggleList');

    const phase=state.phase,allNodes=phaseNodes(phase),bulkGroups={};

    const checkbox=node=>{const id=node[0],title=node[3]||namesById(id),custom=additions[phase].includes(node),isOptional=legacyActivityIds.has(id)||optionalDeliverableIds.has(id),checked=isOptional?visibility[phase][id]===true:visibility[phase][id]!==false;return `<label class="node-chip"><input type="checkbox" data-node="${id}" ${checked?'checked':''}><span>${escapeHTML(title)}</span>${custom?`<button type="button" class="remove-node" data-remove="${id}" aria-label="Remove ${escapeHTML(title)}">Remove</button>`:''}</label>`;};

    const groupBlock=(key,code,title,nodes)=>{bulkGroups[key]=nodes.map(node=>node[0]);const selected=nodes.filter(node=>{const value=visibility[phase][node[0]];return (legacyActivityIds.has(node[0])||optionalDeliverableIds.has(node[0]))?value===true:value!==false;}).length;return `<section class="wbs-node-group"><label class="wbs-group-title"><input type="checkbox" data-bulk="${key}" ${selected===nodes.length?'checked':''}><span><b>${escapeHTML(code)}</b> ${escapeHTML(title)}</span><small>${selected}/${nodes.length}</small></label><div class="wbs-leaf-list">${nodes.map(checkbox).join('')}</div></section>`;};

    if(chooserView==='activities'){

      const definition=workBreakdowns[phase];

      const rootTitle=phase==='groundprep'?'1.0 Earthworks Execution Activities':`${networks[phase].title.split(' - ')[0]} · execution activity WBS`;

      let html=`<div class="wbs-tree-heading">${escapeHTML(rootTitle)} · activity/process breakdown</div>`;

      html+=definition.groups.map(group=>groupBlock(`abs_${group.code.replaceAll('.','_')}`,group.code,group.title,group.tasks.map(task=>allNodes.find(node=>node[0]===task.id)).filter(Boolean))).join('');

      if(definition.deliverables.length){const labels={'1.1':'Site preparation deliverables','1.2':'Excavation & cut deliverables','1.3':'Fill & embankment deliverables','1.4':'Site testing & handover documentation'};html+='<div class="wbs-tree-heading deliverable-heading">1.0 Earthworks Project · deliverable-oriented WBS · optional connector nodes</div>';const grouped=Object.groupBy?Object.groupBy(definition.deliverables,item=>item.code.split('.').slice(0,2).join('.')):definition.deliverables.reduce((result,item)=>((result[item.code.split('.').slice(0,2).join('.')]||=[]).push(item),result),{});html+=Object.entries(grouped).map(([code,items])=>groupBlock(`deliverable_${code.replaceAll('.','_')}`,code,labels[code]||`Deliverables ${code}`,items.map(item=>allNodes.find(node=>node[0]===item.id)).filter(Boolean))).join('');}

      const legacy=allNodes.filter(node=>legacyActivityIds.has(node[0]));if(legacy.length)html+=groupBlock('optional_phase_nodes','Optional','Existing phase activities and milestones',legacy);

      const customActivities=additions[phase].filter(node=>customTypes[node[0]]==='activity');if(customActivities.length)html+=groupBlock('custom_activities','Custom','Additional project activities',customActivities);

      host.innerHTML=html;

    }else{

      host.innerHTML=lanes.slice(0,3).map((lane,index)=>groupBlock(`stakeholders_${lane.key}`,`${index+1}.0`,lane.label.replace(/^\d+\s*/,''),allNodes.filter(node=>nodeGroup(node)==='stakeholders'&&laneFor(node[0])===index))).join('');

    }

    Object.entries(bulkGroups).forEach(([key,ids])=>{const input=host.querySelector(`[data-bulk="${key}"]`),count=ids.filter(id=>activeNodes().some(node=>node[0]===id)).length;if(input)input.indeterminate=count>0&&count<ids.length;});

    host.querySelectorAll('[data-node]').forEach(input => input.addEventListener('change', () => { visibility[state.phase][input.dataset.node] = input.checked; simulate(); }));

    host.querySelectorAll('[data-bulk]').forEach(input=>input.addEventListener('change',()=>{bulkGroups[input.dataset.bulk].forEach(id=>visibility[phase][id]=input.checked);simulate();}));

    host.querySelectorAll('[data-remove]').forEach(button => button.addEventListener('click', event => { event.preventDefault();event.stopPropagation();additions[state.phase] = additions[state.phase].filter(node => node[0] !== button.dataset.remove); delete visibility[state.phase][button.dataset.remove]; simulate(); }));

    $('#nodeCountSummary').textContent = `${activeNodes().length} of ${allNodes.length} project nodes selected in ${networks[phase].title.split(' - ')[0]}`;

  }

  function laneFor(id) { if(wbsActivities.has(id)||legacyActivityIds.has(id)||wbsDeliverables.has(id))return 3;if(customTypes[id])return lanes.findIndex(lane=>lane.key===customTypes[id]);const index=lanes.findIndex(lane => lane.ids.has(id));return index < 0 ? 3 : index; }

  function cascadeCount(id) {

    const edges=phaseEdges(activeNodes()),seen=new Set([id]),queue=[id];

    while(queue.length){const current=queue.shift();edges.filter(edge=>edge[0]===current).forEach(edge=>{if(!seen.has(edge[1])){seen.add(edge[1]);queue.push(edge[1]);}});}

    return Math.max(0,seen.size-1);

  }

  function assessNode(id,values) {

    const current=state.mode==='current',wx=window.neaOutlook||{};

    const climateTrigger=!current;

    const hazard=current?`Regional NEA outlook: ${wx.summary||'unavailable'}`:(state.hazard==='flood'?'Scenario: rainfall/flood-related disruption':'Scenario: heat disruption');

    const hazardType=current?'none':(state.hazard==='heat'?'heat':'flood'),intensity=current?0:values.severity;

    const likelihood=current?2:values.likelihood;

    const floodSensitivity={site:4,drain:5,haul:4,earth:5,formation:4,excavate:5,pile:4,foundation:4,isolate:3,demo:4,wasteSort:3,plant:3,materials:3,concrete:3,pump:4,transport:4,logistics:3,testing:2,authority:1};

    const heatSensitivity={site:4,earth:5,demo:5,isolate:3,wasteSort:4,frame:5,floorCycle:5,crane:5,formwork:4,foundation:4,facadeInstall:5,mepInstall:4,partitions:3,finishInstall:3,mepSecond:3,systemTest:3,training:2,haul:3,plant:3,safety:4};

    const wbsTask=wbsActivities.get(id);

    const sensitivity=hazardType==='none'?0:hazardType==='compound'?Math.max(wbsTask?.floodLevel||floodSensitivity[id]||2,wbsTask?.heatLevel||heatSensitivity[id]||2):hazardType==='heat'?(wbsTask?.heatLevel||heatSensitivity[id]|| (outdoorTasks.has(id)?3:2)):(wbsTask?.floodLevel||floodSensitivity[id]||2);

    const floodText=wbsTask?.flood||floodExposure[id]||'Flooding can affect site access, work-front conditions, stored materials or connected activity dependencies.';

    const heatText=wbsTask?.heat||heatExposure[id]||(outdoorTasks.has(id)?'Outdoor work can expose crews to heat and reduce safe work time.':'Indirect heat exposure through coordination, supply or schedule dependencies.');

    const floodImpact=floodDisruption[id]||'Potential site-access, delivery or work-front interruption, with inspection and restart checks before work resumes.';

    const heatImpact=heatDisruption[id]||'Potentially shorter safe work windows, additional rest/resequencing and reduced outdoor productivity.';

    const hazardExposure=current?`Regional outlook information is displayed separately (${wx.region||'region unavailable'}). This feed is not mapped to activity-level stoppage or productivity in Stage 1.`:hazardType==='heat'?`Heat exposure: ${heatText} Illustrative impact assumption: ${heatImpact}`:`Rainfall-related exposure: ${floodText} Illustrative impact assumption: ${floodImpact}`;

    const reach=cascadeCount(id),cascade=Math.min(2,Math.floor(reach/3)),durationStress=current?0:values.duration-1;

    const clamp=(number,min,max)=>Math.max(min,Math.min(max,number));

    const initialConsequence=climateTrigger?clamp(.45*sensitivity+.35*intensity+.08*durationStress+.25*cascade,1,5):0;

    const controls=(values.protection*1.4)+(values.recovery*.8)+(values.sitePlan?.35:0)+(values.backupPower&&hazardType!=='heat'?.3:0)+(values.buffer&&['materials','concrete','steel','precast','finishes','drywall'].includes(id)?.3:0)+(values.backup&&['concrete','steel','precast','glass','hvac','electrical','fuel','demoContractor'].includes(id)?.3:0)+(values.altRoute&&['haul','transport','logistics'].includes(id)?.3:0);

    const consequence=climateTrigger?clamp(initialConsequence-controls,1,5):0;

    const initialProjectRpn=Math.round(likelihood*initialConsequence*10)/10,projectRpn=Math.round(likelihood*consequence*10)/10,projectLevel=current?'Unavailable':projectRpn<=4?'Low':projectRpn<=8?'Medium':'High';

    const serious=seriousTasks.has(id)||outdoorTasks.has(id)||Boolean(wbsTask&&sensitivity>=4),severity=serious?(hazardType==='heat'?3:4):(['designer','authority'].includes(id)?2:3);

    const workerControls=(values.sitePlan?.8:0)+(values.backupPower&&hazardType!=='heat'?.35:0)+(values.protection*.65);

    const intensityExposure=Math.max(0,intensity-1)*.25,durationExposure=current?0:durationStress*.08;

    const workerBaseLikelihood=serious?3:2,taskExposure=outdoorTasks.has(id)?.5:0,workerLikelihood=clamp(Math.round(workerBaseLikelihood+taskExposure+intensityExposure+durationExposure-workerControls),1,5);

    const rpn=severity*workerLikelihood,level=riskMatrix[severity][workerLikelihood-1];

    const severityWhy=serious?(hazardType==='heat'?'Outdoor physical work can credibly cause heat-related illness requiring medical treatment; task exertion and work at height increase concern.':hazardType==='none'?'This WSH severity is a task-based worker-safety screening estimate; no weather disruption is currently triggered. Confirm the credible harm outcome with the project RA team.':'Floodwater, unstable ground, mobile plant or electrical exposure can credibly cause serious injury if controls fail.'):(severity===2?'This coordination or approval role has lower direct physical exposure; first-aid harm is used as the screening consequence.':'Material handling, installation or inspection can credibly cause an injury requiring medical treatment.');

    const likelihoodWhy=current?`WSH worker likelihood is a task-based screening placeholder, not derived from regional weather text or a bodily-harm probability. Confirm actual worker exposure and controls with the RA team. NEA outlook is informational only.`:`WSH worker likelihood is a task-exposure screening estimate informed by activity context and controls. The assumed weather-event likelihood is used only in the separate climate screen; it is not bodily-harm likelihood. Verify with the RA team.`;

    const projectWhy=current?'The regional official outlook is shown in the left panel. Stage 1 does not convert forecast text or air temperature into activity stoppage, productivity loss, or injury likelihood. Use the dated scenario inputs for schedule stress testing.':`${hazardType==='heat'?'Heat disruption is conditional on the entered event and productivity assumption.':'Rainfall-related disruption is conditional on the entered event and productivity assumption.'} Schedule effects are calculated separately from this illustrative climate screen. ${reach?`${reach} downstream node${reach===1?'':'s'} sit beyond this dependency in the selected activity network.`:'No downstream dependency is currently linked.'}`;

    return {id,hazard,hazardType,hazardExposure,severity,likelihood:workerLikelihood,rpn,level,severityWhy,likelihoodWhy,forecast:wx.summary||'Forecast unavailable',projectLikelihood:likelihood,initialConsequence,consequence,initialProjectRpn,projectRpn,projectLevel,projectWhy,reach};

  }

  const levelClass=level=>level.toLowerCase();

  function controlRecommendations(id,assessment) {

    const hazard=assessment.hazardType==='none'?'none':assessment.hazardType==='heat'?'heat':'flood';

    const activity=['site','earth','haul','drain','pump','crane','piling','transport','formwork'].includes(id);

    const riskAction=assessment.level==='High'?'Not acceptable: reduce to at least Medium before work starts; management review is required, and controls should not rely on PPE or interim measures.':assessment.level==='Medium'?'Tolerable only with careful review: reduce ALARP within a defined period and obtain management attention; suitable interim controls may be used while longer-term controls are implemented.':'Acceptable under the selected matrix: no additional controls may be needed, but monitor and review frequently to ensure risk does not increase.';

    return [

      ['WSH risk-level action',riskAction],

      ['Elimination',assessment.level==='High'?'Do not start or continue the task while its WSH risk is High; eliminate exposure where practicable or add controls and re-rate to Medium or Low first.':'Remove avoidable exposure by planning work outside the forecast hazard window.'],

      ['Substitution',hazard==='heat'?'Use prefabrication or lower-exertion methods where practicable.':'Use off-site prefabrication or a lower-exposure work method where practicable.'],

      ['Engineering controls',hazard==='heat'?'Provide shaded/cool recovery areas, ventilation and cooled rest spaces.':hazard==='none'?'Maintain task safeguards, safe access, plant controls and approved temporary works.':'Install barriers, drainage/dewatering, non-slip access and protected/elevated electrical equipment.'],

      ['Administrative controls',hazard==='heat'?'Set heat work-rest cycles, acclimatisation, hydration, monitoring and stop-work triggers.':hazard==='none'?'Apply the task risk assessment, brief crews on controls and review conditions before work.':'Use weather monitoring, safe access rules, lifting/plant exclusion zones and a documented stop-work/restart check.'],

      ['PPE',activity?'Select task-appropriate PPE and inspect it; use only as a supplementary residual control.':'Use task-appropriate PPE as a supplementary residual control, not a substitute for upstream measures.']

    ];

  }

  function openAssessment(id) {

    const node=activeNodes().find(item=>item[0]===id);if(!node)return;

    activeAssessmentNode=id;

    const result=assessNode(id,scenario()),dialog=$('#nodeRiskDialog'),name=node[3]||namesById(id);

    const currentInputs=state.mode==='current',runValues=scenario();

    $('#dialogNodeName').textContent=name;$('#dialogHazard').textContent=`${result.hazard}. Activity exposure: ${result.hazardExposure} ${currentInputs?'The regional forecast is informational and is not converted into activity-level impact.':'Scenario assumptions: event likelihood '+runValues.likelihood+'/5 for the separate screening score; disruption work is calculated from explicit productivity and recovery settings.'}`;$('#dialogProjectScore').textContent=currentInputs?'Climate screen unavailable in outlook mode':`Climate screen ${result.initialProjectRpn} → ${result.projectRpn} / 25` ;$('#dialogProjectBand').textContent=`${result.projectLevel} disruption risk`;$('#dialogProjectBand').className=`risk-band ${levelClass(result.projectLevel)}`;$('#dialogProjectWhy').textContent=currentInputs?result.projectWhy:`Scenario screening score uses assumed event likelihood x residual activity consequence. It is separate from schedule loss and WSH RPN. ${result.projectWhy}`;

    $('#dialogSeverity').textContent=`${result.severity} - ${['','Negligible','Minor','Moderate','Major','Catastrophic'][result.severity]}`;

    $('#dialogSeverityReason').textContent=result.severityWhy;$('#dialogLikelihood').textContent=`${result.likelihood} - ${['','Rare','Remote','Occasional','Frequent','Almost certain'][result.likelihood]}`;

    $('#dialogLikelihoodReason').textContent=result.likelihoodWhy;$('#dialogRpn').textContent=`${result.severity} x ${result.likelihood} = ${result.rpn}`;

    $('#dialogRiskBand').textContent=`${result.level} risk`;$('#dialogRiskBand').className=`risk-band ${levelClass(result.level)}`;

    $('#dialogBasis').textContent=`WSH screening: ${severityText[result.severity]}. RPN is Severity x Likelihood. Risk band follows the descriptive 5 x 5 matrix shown in the results panel. Severity reflects the credible harm outcome; existing controls reduce likelihood only where they affect exposure.`;

    $('#dialogControls').innerHTML=controlRecommendations(id,result).map(([name,action])=>`<li><b>${name}</b><span>${escapeHTML(action)}</span></li>`).join('');

    const assignment=controlAssignments[id]||{};$('#controlOwner').value=assignment.owner||'';$('#controlDueDate').value=assignment.due||'';

    if(!dialog.open)dialog.showModal();

  }

  function renderDependencyMap() {

    const phase=state.phase,svg=$('#systemMap'),model=viewNodes(),visible=model.nodes,values=scenario(),points={},nodeW=158,nodeH=70,rowDefs=[];

    const highlightConnections=id=>{

      if(!id){svg.querySelectorAll('.hierarchy-edge,.hier-node,.connector-node').forEach(element=>element.classList.remove('is-focused','is-muted','is-selected','is-linked'));const note=$('.network-tab-note');note.textContent=networkView==='activities'?'Hover or focus an activity to trace its direct connections.':'Hover or focus a stakeholder/resource to trace its direct connections.';return;}

      const related=new Set([id]);

      model.edges.forEach(([from,to])=>{if(from===id)related.add(to);if(to===id)related.add(from);});

      svg.querySelectorAll('.hierarchy-edge').forEach(edge=>{const linked=edge.dataset.from===id||edge.dataset.to===id;edge.classList.toggle('is-focused',linked);edge.classList.toggle('is-muted',!linked);});

      svg.querySelectorAll('.hier-node,.connector-node').forEach(node=>{const selected=node.dataset.node===id,linked=related.has(node.dataset.node);node.classList.toggle('is-selected',selected);node.classList.toggle('is-linked',!selected&&linked);node.classList.toggle('is-muted',!linked);});

      const note=$('.network-tab-note');

      const node=model.primary.find(item=>item[0]===id),count=Math.max(0,related.size-1);note.textContent=`${node?.[3]||'Node'} · ${count} direct connection${count===1?'':'s'} highlighted`;

    };

    const addRow=(key,label,nodes,kind='primary')=>{if(nodes.length)rowDefs.push({key,label,nodes,kind});};

    const wbs=workBreakdowns[phase];

    if(networkView==='activities'){

      lanes.slice(0,3).forEach((lane,index)=>addRow(`support_${lane.key}`,`SUPPORTING CONNECTORS · ${lane.label.replace(/^\d+\s*/, '')}`,model.connectors.filter(node=>nodeGroup(node)==='stakeholders'&&laneFor(node[0])===index),'connector'));

      wbs.groups.forEach((group,index)=>{

        const groupTasks=group.tasks.map(task=>task.id),primary=model.primary.filter(node=>groupTasks.includes(node[0]));

        addRow(`activity_${group.code}`,`PRIMARY ACTIVITIES · ${group.code} ${group.title}`,primary,'activity');

        const parentCode=group.code;

        const outputs=model.connectors.filter(node=>wbsDeliverables.has(node[0])&&wbsDeliverables.get(node[0]).code.split('.').slice(0,2).join('.')===parentCode);

        addRow(`outputs_${group.code}`,`SUPPORTING WBS OUTPUTS · ${group.code}`,outputs,'connector');

      });

      const optional=model.primary.filter(node=>legacyActivityIds.has(node[0])||(!wbsActivities.has(node[0])&&!wbsDeliverables.has(node[0])));

      addRow('optional_activities','PRIMARY ACTIVITIES · OPTIONAL PHASE NODES',optional,'activity');

    }else{

      lanes.slice(0,3).forEach((lane,index)=>addRow(`stakeholder_${lane.key}`,`PRIMARY STAKEHOLDERS & RESOURCES · ${lane.label.replace(/^\d+\s*/, '')}`,model.primary.filter(node=>laneFor(node[0])===index),'primary'));

      wbs.groups.forEach(group=>{

        const groupTasks=new Set(group.tasks.map(task=>task.id));

        addRow(`activity_connectors_${group.code}`,`SUPPORTING ACTIVITY CONNECTORS · ${group.code} ${group.title}`,model.connectors.filter(node=>groupTasks.has(node[0])),'connector');

      });

      addRow('other_activity_connectors','SUPPORTING PHASE ACTIVITY CONNECTORS',model.connectors.filter(node=>!wbsActivities.has(node[0])&&!wbsDeliverables.has(node[0])),'connector');

    }

    const maxColumns=5,usableWidth=930,rowLayouts=[];

    let nextY=10;

    rowDefs.forEach(row=>{

      const columns=Math.min(maxColumns,Math.max(1,row.nodes.length)),subRows=Math.ceil(row.nodes.length/maxColumns),height=Math.max(96,30+subRows*80);

      rowLayouts.push({...row,y:nextY,height,columns});nextY+=height+10;

    });

    const height=Math.max(530,nextY);svg.setAttribute('viewBox',`0 0 1000 ${height}`);

    let markup='<defs><marker id="arrowRefine" markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto"><path d="M0,0 L0,6 L6,3 z" fill="#8aa99b"/></marker></defs>';

    rowLayouts.forEach((row,index)=>{

      const y=row.y,rowNodes=row.nodes,cellWidth=usableWidth/row.columns;

      markup+=`<rect class="lane-bg lane-${index%4} ${row.kind==='connector'?'connector-lane':''}" x="12" y="${y}" width="976" height="${row.height}" rx="9"/><text class="lane-title" x="28" y="${y+16}">${escapeHTML(row.label)}</text>`;

      rowNodes.forEach((node,i)=>{const subRow=Math.floor(i/maxColumns),column=i%maxColumns,x=12+(column*cellWidth)+(cellWidth-nodeW)/2,top=y+22+subRow*80;points[node[0]]={x,y:top,row:index};});

    });

    model.edges.forEach(([from,to])=>{

      const a=points[from],b=points[to];if(!a||!b)return;

      const ax=a.x+nodeW/2,ay=a.y+nodeH/2,bx=b.x+nodeW/2,by=b.y+nodeH/2,dx=bx-ax,dy=by-ay;

      let x1=ax,y1=ay,x2=bx,y2=by,c1x,c1y,c2x,c2y;

      if(Math.abs(dy)>Math.abs(dx)){y1=dy>0?a.y+nodeH:a.y;y2=dy>0?b.y:b.y+nodeH;c1x=x1;c2x=x2;c1y=y1+dy*.42;c2y=y2-dy*.42;}

      else{x1=dx>0?a.x+nodeW:a.x;x2=dx>0?b.x:b.x+nodeW;c1x=x1+dx*.42;c2x=x2-dx*.42;c1y=y1;c2y=y2;}

      markup+=`<path class="hierarchy-edge" data-from="${from}" data-to="${to}" d="M${x1} ${y1} C${c1x} ${c1y},${c2x} ${c2y},${x2} ${y2}" marker-end="url(#arrowRefine)"/>`;

    });

    visible.forEach(node=>{

      const id=node[0],pos=points[id],title=node[3]||namesById(id),isPrimary=model.primaryIds.has(id);

      if(!isPrimary){markup+=`<g class="connector-node" data-node="${id}" aria-label="Supporting connector: ${escapeHTML(title)}" transform="translate(${pos.x+12},${pos.y+9})"><rect width="134" height="52" rx="5"/><text x="67" y="30" text-anchor="middle">${escapeHTML(title.length>23?title.slice(0,21)+'...':title)}</text></g>`;return;}

      const assessment=assessNode(id,values),status=`CLIMATE ${assessment.projectLevel.toUpperCase()}${assessment.projectLevel==='Unavailable'?'':` · ${assessment.projectRpn}`}`,cls=levelClass(assessment.projectLevel),shortTitle=title.length>24?title.slice(0,22)+'...':title,sub=`WSH ${assessment.level.toUpperCase()} · S${assessment.severity} × L${assessment.likelihood} = ${assessment.rpn}`;

      markup+=`<g class="hier-node primary-node ${cls}" data-node="${id}" tabindex="0" role="button" aria-label="${escapeHTML(title)}, climate disruption screen ${assessment.projectLevel}, score ${assessment.projectRpn} of 25; WSH ${assessment.level}, severity ${assessment.severity} times likelihood ${assessment.likelihood}, RPN ${assessment.rpn}. Hover or focus to highlight direct connections; select for details." transform="translate(${pos.x},${pos.y})"><rect width="${nodeW}" height="${nodeH}" rx="8"/><circle cx="15" cy="16" r="4"/><text class="hier-state" x="${nodeW-9}" y="19" text-anchor="end">${status}</text><text class="hier-name" x="13" y="42">${escapeHTML(shortTitle)}</text><text class="hier-sub" x="13" y="58">${escapeHTML(sub)}</text></g>`;

    });

    svg.innerHTML=markup;

    svg.querySelectorAll('.hier-node').forEach(group=>{const id=group.dataset.node;group.addEventListener('pointerenter',()=>highlightConnections(id));group.addEventListener('pointerleave',()=>highlightConnections(null));group.addEventListener('focus',()=>highlightConnections(id));group.addEventListener('blur',()=>highlightConnections(null));group.addEventListener('click',()=>openAssessment(id));group.addEventListener('keydown',event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();openAssessment(id);}})});

    $('#mapTitle').textContent=networks[phase].title;

    $('#phaseSummary').textContent=networks[phase].summary;

    $('#eventTag').textContent=state.mode==='current'?(window.neaOutlook?`NEA 24H - ${window.neaOutlook.summary}`:'NEA FEED UNAVAILABLE'):(state.hazard==='flood'?'FLOOD SCENARIO':'EXTREME HEAT SCENARIO');

    $('#eventTag').className=`event-tag ${state.mode==='scenario'&&state.hazard==='heat'?'heat-tag':'flood-tag'}`;

    if($('#mapFootCount'))$('#mapFootCount').textContent=`${model.primary.length} primary · ${model.connectors.length} supporting connectors`;

    const focusNode=model.primary.find(node=>node[0]===focusedId),focusedLinks=focusedId?model.edges.filter(([from,to])=>from===focusedId||to===focusedId).length:0;

    $('.network-tab-note').textContent=networkView==='activities'?'Hover or focus an activity to trace its direct connections.':'Hover or focus a stakeholder/resource to trace its direct connections.';

    buildNodeEditor();

  }

  function renderMap() {

    if (window.renderCalendarSchedule) { window.renderCalendarSchedule(); return; }

    const stages=[

      {key:'demolition',label:'01  DEMOLITION'},

      {key:'groundprep',label:'02  GROUND PREPARATION'},

      {key:'substructure',label:'03  SUBSTRUCTURE'},

      {key:'superstructure',label:'04  SUPERSTRUCTURE'},

      {key:'envelope',label:'05  BUILDING ENVELOPE + MEP'},

      {key:'fitout',label:'06  FIT-OUT'},

      {key:'commissioning',label:'07  TESTING + HANDOVER'}

    ];

    const colors=['#337f68','#4d8e76','#668f6c','#b4864b','#4c8290','#8a7392','#6f8661'];

    const activities=[];

    stages.forEach((stage,stageIndex)=>{

      workBreakdowns[stage.key].groups.forEach(group=>group.tasks.forEach(task=>{

        const text=task.label.toLowerCase();

        const base=/survey|locate|isolate|verify|inspect|release|train|close|mobiliz|set out|set up|permit|test|energiz/.test(text)?2:

          /excavat|demolit|pile|foundation|frame|concrete|haul|install|erect|compaction|spread|waterproof|roof/.test(text)?6:4;

        const variation=(activities.length+stageIndex)%3-1;

        const duration=Math.max(2,base+variation);

        const predecessor=activities[activities.length-1]||null;

        activities.push({

          stage:stage.label,stageIndex,group:`${group.code}  ${group.title}`,

          code:task.code,label:task.label,duration,

          start:predecessor?predecessor.start+predecessor.duration:0,

          predecessor:predecessor?predecessor.code:'—'

        });

      }));

    });

    const chartStart=450,dayWidth=4.5,rowHeight=38,stageHeight=34,groupHeight=25,axisHeight=58;

    const totalDays=activities.reduce((total,activity)=>total+activity.duration,0);

    const chartWidth=chartStart+Math.max(totalDays,70)*dayWidth+24;

    const rows=[];

    let y=axisHeight;

    let activityIndex=0;

    stages.forEach((stage,stageIndex)=>{

      rows.push({type:'stage',label:stage.label,y,height:stageHeight,stageIndex});

      y+=stageHeight;

      workBreakdowns[stage.key].groups.forEach(group=>{

        rows.push({type:'group',label:`${group.code}  ${group.title}`,y,height:groupHeight,stageIndex});

        y+=groupHeight;

        group.tasks.forEach(()=>{

          const activity=activities[activityIndex++];

          activity.y=y;

          rows.push({type:'activity',activity,y,height:rowHeight,stageIndex});

          y+=rowHeight;

        });

      });

    });

    const chartHeight=y+12;

    let markup=`<svg xmlns="http://www.w3.org/2000/svg" width="${chartWidth}" height="${chartHeight}" viewBox="0 0 ${chartWidth} ${chartHeight}" role="img" aria-labelledby="ganttTitle ganttDescription"><title id="ganttTitle">Illustrative whole-project construction activity Gantt chart</title><desc id="ganttDescription">${activities.length} activities across ${stages.length} stages, with estimated durations and finish-to-start relationships. This is an AI-generated planning example.</desc><defs><marker id="ganttArrow" markerWidth="6" markerHeight="6" refX="5" refY="3" orient="auto"><path d="M0 0L6 3L0 6Z" fill="#8da99b"/></marker></defs>`;

    markup+='<rect class="gantt-header-bg" x="0" y="0" width="'+chartWidth+'" height="'+axisHeight+'"/>';

    markup+='<text class="gantt-col-head" x="12" y="22">STAGE / ACTIVITY</text><text class="gantt-col-head" x="310" y="22">DURATION</text><text class="gantt-col-head" x="365" y="22">PREDECESSOR</text><text class="gantt-col-head" x="'+chartStart+'" y="22">TIMELINE</text>';

    markup+=`<text class="gantt-axis-label" x="${chartStart}" y="44">DAY 1</text>`;

    for(let day=14;day<=totalDays;day+=14){

      const x=chartStart+day*dayWidth;

      markup+=`<line class="gantt-grid-line" x1="${x}" y1="31" x2="${x}" y2="${chartHeight-8}"/><text class="gantt-axis-label" x="${x+3}" y="44">WEEK ${Math.floor(day/7)+1}</text>`;

    }

    rows.forEach(row=>{

      if(row.type==='stage'){

        markup+=`<rect class="gantt-stage-row" x="0" y="${row.y}" width="${chartWidth}" height="${row.height}"/><rect x="0" y="${row.y}" width="4" height="${row.height}" fill="${colors[row.stageIndex]}"/><text class="gantt-stage-label" x="12" y="${row.y+22}">${escapeHTML(row.label)}</text><text class="gantt-stage-count" x="${chartWidth-12}" y="${row.y+21}" text-anchor="end">${workBreakdowns[stages[row.stageIndex].key].groups.reduce((count,group)=>count+group.tasks.length,0)} ACTIVITIES</text>`;

      }else if(row.type==='group'){

        markup+=`<rect class="gantt-group-row" x="0" y="${row.y}" width="${chartWidth}" height="${row.height}"/><text class="gantt-group-label" x="20" y="${row.y+17}">${escapeHTML(row.label)}</text>`;

      }else{

        const activity=row.activity,x=chartStart+activity.start*dayWidth,width=Math.max(9,activity.duration*dayWidth),center=row.y+row.height/2;

        markup+=`<rect class="gantt-task-row" x="0" y="${row.y}" width="${chartWidth}" height="${row.height}"/><text class="gantt-code" x="20" y="${row.y+23}">${escapeHTML(activity.code)}</text><text class="gantt-activity-label" x="69" y="${row.y+23}" aria-label="${escapeHTML(activity.label)}" title="${escapeHTML(activity.label)}">${escapeHTML(activity.label.length>42?activity.label.slice(0,39)+'...':activity.label)}</text><text class="gantt-duration" x="310" y="${row.y+23}">${activity.duration} days</text><text class="gantt-predecessor" x="365" y="${row.y+23}">${activity.predecessor==='—'?'START':`FS · ${escapeHTML(activity.predecessor)}`}</text>`;

        if(activity.predecessor!=='—'){

          const fromX=chartStart+(activity.start)*dayWidth;

          const previous=activities[activities.indexOf(activity)-1];

          const previousCenter=previous.y+rowHeight/2;

          markup+=`<path class="gantt-dependency" d="M${fromX} ${previousCenter}V${center-8}" marker-end="url(#ganttArrow)"/>`;

        }

        markup+=`<rect class="gantt-bar" x="${x}" y="${row.y+11}" width="${width}" height="16" rx="4" fill="${colors[activity.stageIndex]}"><title>${escapeHTML(activity.label)} · ${activity.duration} days · predecessor ${escapeHTML(activity.predecessor)}</title></rect>`;

      }

    });

    markup+='</svg>';

    $('#activityGantt').innerHTML=markup;

    $('#mapTitle').textContent='Activity Gantt chart';

    $('#phaseSummary').textContent='AI-generated illustrative durations and finish-to-start relationships; replace with project data.';

    $('#eventTag').textContent='ILLUSTRATIVE';

    $('#eventTag').className='event-tag flood-tag';

    if($('#mapFootCount'))$('#mapFootCount').textContent=`${activities.length} activities · ${stages.length} stages`;

  }

  function stagedActions(v,highNodes) {

    const heat=state.mode==='scenario'&&state.hazard==='heat',flood=state.mode==='scenario'&&state.hazard==='flood',phase=networks[state.phase].title.split(' - ')[0];

    const names=highNodes.slice(0,2).map(item=>item.node[3]||namesById(item.node[0]));

    const focus=names.length?names.join(' and '):`${phase} work front`;

    const context=state.mode==='current'?'the user-reviewed regional outlook':'the selected future scenario';

    return [

      {name:'Prepare',when:'Before the event',items:[`Set action triggers for ${context}; assign a decision owner for ${phase}.`,`Review the sequence and weather window for ${focus}.`,`Confirm supplier, route, pump/power, worker welfare and site-contact arrangements.`]},

      {name:'Absorb',when:'Maintain critical work',items:heat&&flood?[`Use heat work-rest and shaded recovery while protecting excavations, access and electrical equipment from flooding.`,`Keep pumps, communications and protected materials available for ${focus}; move strenuous tasks out of unsafe windows.`,`Suspend work fronts that are flooded, unstable or beyond worker heat limits; resequence safe tasks.`]:heat?[`Move strenuous outdoor work to cooler periods; use work-rest cycles, shade, hydration and buddy monitoring.`,`Keep critical supervision, communications and protected materials available for ${focus}.`,`Pause activities that cannot maintain safe exposure limits; resequence unaffected indoor or protected work.`]:flood?[`Protect excavations, temporary drainage, pumps, electrical points, access and stored materials for ${focus}.`,`Use barriers, dewatering, elevated storage and an alternate access or delivery route where available.`,`Maintain safe essential work only; suspend inundated or unstable work fronts.`]:[`Maintain routine weather monitoring and verify that site protections remain serviceable for ${focus}.`,`Keep supervisors, supplier contacts and safe work procedures ready for a change in conditions.`,`Continue planned work while reviewing any local condition that differs from the regional outlook.`]},

      {name:'Respond',when:'When disruption is occurring',items:heat&&flood?[`Activate the site emergency plan; account for workers and move them to safe, cool areas.`,`Stop work in flooded or unstable areas and where heat limits are exceeded; isolate affected power and secure plant.`,`Log worker, equipment, work-front and supplier impacts, including concurrent rain and heat effects.`]:heat?[`Apply the site heat action level; stop or reduce work, move workers to cool recovery areas and seek medical help for symptoms.`,`Suspend work at height, lifting or high-exertion tasks when safe operating limits are exceeded.`,`Record affected crews, equipment, work fronts and supplier interruptions; notify the site lead.`]:flood?[`Stop work in flooded, unstable or electrically unsafe areas; account for workers and activate the site emergency plan.`,`Isolate affected power, secure plant and prevent unsafe access; deploy trained pumping or response crews.`,`Record water levels, damaged work, blocked routes and interrupted deliveries before clean-up.`]:[`Monitor site conditions, reinforce stop-work thresholds and escalate any trigger that differs from the area forecast.`,`Account for the site team and maintain communications with critical subcontractors and suppliers.`,`Record emerging changes so work can be paused safely if exposure increases.`]},

      {name:'Recover',when:'Restore safe production',items:heat&&flood?[`Verify workers have recovered and inspect temporary works, excavations, access, electrical systems and completed work before restart.`,`Remove water and repair flood protection; assess heat-related material, equipment and quality effects.`,`Rebook crews and suppliers, then resequence the ${phase} work package around both hazards.`]:heat?[`Reassess worker condition and forecast conditions before restart; phase work back with suitable rest and supervision.`,`Check affected materials, equipment and completed work for heat-related quality or operating issues.`,`Resequence work, rebook crews and suppliers, and update the short-term programme.`]:flood?[`Inspect temporary works, excavations, drainage, access, electrical systems and completed work before restart.`,`Remove water and debris safely; repair protection and replace damaged or contaminated materials.`,`Rebook affected suppliers and crews, then resequence the ${phase} work package.`]:[`Confirm safe conditions and functioning site protections before returning to normal sequencing.`,`Check that key suppliers, crews and equipment remain available for the selected phase.`,`Update the look-ahead programme if the outlook or work-front conditions change.`]},

      {name:'Adapt',when:'After the event',items:[`Compare expected and actual disruption, downtime, resource loss and recovery actions for ${phase}.`,`Update activity sensitivities, supplier dependencies, protection and restart thresholds using site records.`,`Carry lessons and revised controls into the next construction phase and future scenario reviews.`]}

    ];

  }

  function renderStages(v,highNodes) {

    $('#stageActions').innerHTML=stagedActions(v,highNodes).map((stage,index)=>`<details class="stage-card" ${index===0?'open':''}><summary><span class="stage-step">0${index+1}</span><strong>${stage.name}</strong><small>${stage.when}</small></summary><ul>${stage.items.map(item=>`<li>${escapeHTML(item)}</li>`).join('')}</ul></details>`).join('');

  }

  function simulate() {

    const v=scenario(),current=activeNodes().filter(node=>!wbsDeliverables.has(node[0])),assessments=current.map(node=>({node,result:assessNode(node[0],v)}));

    const rank={Low:1,Medium:2,High:3},worst=[...assessments].sort((a,b)=>rank[b.result.projectLevel]-rank[a.result.projectLevel]||b.result.projectRpn-a.result.projectRpn)[0]?.result||{projectLevel:'Low',projectRpn:1},worstWsh=[...assessments].sort((a,b)=>rank[b.result.level]-rank[a.result.level]||b.result.rpn-a.result.rpn)[0]?.result||{level:'Low',severity:1,likelihood:1,rpn:1};

    const count=assessments.filter(item=>item.result.projectLevel!=='Low').length,high=assessments.filter(item=>item.result.projectLevel==='High').sort((a,b)=>b.result.projectRpn-a.result.projectRpn),wshHigh=assessments.filter(item=>item.result.level==='High').length;

    $('#riskScore').textContent=Number(worst.projectRpn).toFixed(1);$('#riskLevel').textContent=worst.projectLevel.toUpperCase();$('#riskCaption').textContent=worst.projectLevel==='High'?'High disruption screen - review controls and response':worst.projectLevel==='Medium'?'Moderate disruption screen - review cascading effects':'Lower disruption screen - monitor after changes';

    $('.risk-card').className=`risk-card ${worst.projectLevel.toLowerCase()}`;$('#riskMeter').style.width=`${Math.min(100,worst.projectRpn*4)}%`;$('#riskMeter').style.background=worst.projectLevel==='High'?'#d76d54':worst.projectLevel==='Medium'?'#dfa343':'#58a078';

    $('#wshRiskNote').textContent=`Separate WSH safety screen: highest selected node is ${worstWsh.level} (S${worstWsh.severity} x L${worstWsh.likelihood} = RPN ${worstWsh.rpn}); ${wshHigh} node${wshHigh===1?'':'s'} rated High. Verify worker exposure, severity, likelihood and existing controls with the RA team.`;

    $('#nodeValue').textContent=count;$('#highImpactCount').textContent=`${high.length} high`;$('#highImpactList').innerHTML=high.length?high.map(item=>`<li><span class="impact-dot"></span>${escapeHTML(item.node[3]||namesById(item.node[0]))}<strong>Screen ${Number(item.result.projectRpn).toFixed(1)}</strong></li>`).join(''):'<li class="no-high-impact">No nodes currently rate High in the climate disruption screen. Medium-risk nodes may still transmit impacts.</li>';

    const mode=state.mode,modeWeather=window.neaOutlook;$('#currentMode').hidden=false;$('#scenarioMode').hidden=false;$('.dashboard').dataset.mode=mode;

    $('#runBtn').textContent='Run future scenario';$('#riskMetricLabel').textContent=mode==='current'?'Current outlook - climate disruption screen':'Future scenario - climate disruption screen';

    $('#modeFootnote').textContent=mode==='current'?'Official regional outlook is informational; activity disruption is not inferred':'Scenario event likelihood is separate from WSH worker likelihood and schedule effects';

    $('#headerMode').textContent='CLIMATE FORECAST';

    $('#eventTag').textContent='ILLUSTRATIVE';$('#eventTag').className='event-tag flood-tag';

    renderStages(v,high);$('#interventionList').innerHTML='';$('#interventionCount').textContent='5 response stages';renderMap();

    if(mode==='current'){$('#riskMetricLabel').textContent='Official outlook - informational only';$('#riskLevel').textContent='NOT MODELED';$('#riskScore').textContent='—';$('#riskCaption').textContent=modeWeather?'Regional forecast is not mapped to activity stoppage':'Weather feed unavailable; exposure remains unknown';$('.risk-card').className='risk-card';$('#riskMeter').style.width='0%';$('#nodeValue').textContent='—';$('#highImpactCount').textContent='Not calculated';$('#highImpactList').innerHTML=modeWeather?'<li>Official regional outlook is visible in Project context. Activity impacts require explicit scenario assumptions.</li>':'<li>Weather data are unavailable. Treat exposure as unknown and check an authoritative source.</li>';$('#modeFootnote').textContent=modeWeather?'Official forecast data are displayed; schedule effects are not inferred from forecast text.':'Weather unavailable; missing data are not treated as safe conditions.';}

  }

  window.renderMap=renderMap;window.simulate=simulate;

  const matrixBody=$('.matrix-table tbody'),matrixScores={5:[5,10,15,20,25],4:[4,8,12,16,20],3:[3,6,9,12,15],2:[2,4,6,8,10],1:[1,2,3,4,5]};

  matrixBody.innerHTML=Object.keys(matrixScores).sort((a,b)=>b-a).map(severity=>`<tr><th>${severity} ${['','Negligible','Minor','Moderate','Major','Catastrophic'][severity]}</th>${matrixScores[severity].map((score,index)=>{const level=riskMatrix[severity][index];return `<td class="risk-${level.toLowerCase()}"><b>${score}</b><small>${level[0]}</small></td>`;}).join('')}</tr>`).join('');



  const mapFootLabel=$('#phaseSummary').closest('.map-panel').querySelector('.map-foot span:last-child');if(mapFootLabel)mapFootLabel.textContent='ACTIVITY · DURATION · PREDECESSOR · STAGE';

  $('#addNodeForm').addEventListener('submit',event=>{event.preventDefault();const field=$('#newNodeName'),name=field.value.trim();if(!name)return;const type=$('#newNodeType').value,id='custom_'+Date.now();customTypes[id]=type;additions[state.phase].push([id,0,0,name,`${type==='supply'?'supplier / subcontractor':type==='delivery'?'delivery / equipment':type==='design'?'design / control':'site activity'}`]);visibility[state.phase][id]=true;field.value='';simulate();});

  function syncLabels(){ $('#severityOut').textContent=`${$('#severity').value} / 5`;$('#durationOut').textContent=`${$('#duration').value} days`;$('#protectionOut').textContent=`${$('#protection').value}%`;$('#recoveryOut').textContent=`${$('#recovery').value}%`; }

  $$('.phase').forEach(button=>button.addEventListener('click',()=>{$$('.phase').forEach(item=>item.classList.toggle('active',item===button));state.phase=button.dataset.phase;simulate();}));

  $$('.event').forEach(button=>button.addEventListener('click',()=>{$$('.event').forEach(item=>item.classList.toggle('active',item===button));state.hazard=button.dataset.hazard;simulate();}));

  ['severity','duration','eventLikelihood','protection','recovery','scale','alternate','altRoute','buffer','sitePlan','backupPower'].forEach(id=>$('#'+id).addEventListener('input',()=>{syncLabels();simulate();}));

  $('#resetBtn')?.addEventListener('click',()=>{state.phase='demolition';state.hazard='flood';state.mode='current';$('#location').value='City';$('#scale').value='low';$('#severity').value=4;$('#duration').value=6;$('#eventLikelihood').value=2;$('#protection').value=35;$('#recovery').value=45;['alternate','altRoute','sitePlan','backupPower'].forEach(id=>$('#'+id).checked=false);$('#buffer').checked=true;$$('.phase').forEach(button=>button.classList.toggle('active',button.dataset.phase==='demolition'));$$('.event').forEach(button=>button.classList.toggle('active',button.dataset.hazard==='flood'));$$('.mode-tab').forEach(button=>{button.classList.toggle('active',button.dataset.mode==='current');button.setAttribute('aria-selected',String(button.dataset.mode==='current'));});syncLabels();simulate();window.refreshWeather?.();});

  syncLabels();

  $$('.mode-tab').forEach(button=>button.addEventListener('click',()=>{$$('.mode-tab').forEach(tab=>{tab.classList.toggle('active',tab===button);tab.setAttribute('aria-selected',String(tab===button));});state.mode=button.dataset.mode;simulate();}));

  $('#runBtn').addEventListener('click',event=>{event.stopImmediatePropagation();state.mode='scenario';$('#scheduleHazard').value='rain';const start=$('#projectStart').value,day=new Date(`${start}T12:00:00Z`),duration=+$('#duration').value;if(start&&!Number.isNaN(day.getTime())){const end=new Date(day);end.setUTCDate(end.getUTCDate()+Math.max(0,duration-1));$('#scenarioStart').value=start;$('#scenarioEnd').value=end.toISOString().slice(0,10)}simulate();window.activateClimateScenario?.();},{capture:true});

  $('#closeRiskDialog').addEventListener('click',()=>$('#nodeRiskDialog').close());$('#nodeRiskDialog').addEventListener('click',event=>{if(event.target.id==='nodeRiskDialog')event.target.close();});

  $('#openNodeOptions').addEventListener('click',()=>$('#nodeOptionsDialog').showModal());

  $$('.chooser-tab').forEach(button=>button.addEventListener('click',()=>{$$('.chooser-tab').forEach(tab=>{tab.classList.toggle('active',tab===button);tab.setAttribute('aria-selected',String(tab===button));});chooserView=button.dataset.chooser;buildNodeEditor();}));

  $('#closeNodeOptions').addEventListener('click',()=>$('#nodeOptionsDialog').close());

  $('#nodeOptionsDialog').addEventListener('click',event=>{if(event.target.id==='nodeOptionsDialog')event.target.close();});

  $('#controlOwner').addEventListener('input',event=>{if(activeAssessmentNode)(controlAssignments[activeAssessmentNode]||= {}).owner=event.target.value;});$('#controlDueDate').addEventListener('input',event=>{if(activeAssessmentNode)(controlAssignments[activeAssessmentNode]||= {}).due=event.target.value;});

  const help=document.createElement('div');help.className='help-tooltip';help.setAttribute('role','tooltip');document.body.appendChild(help);let activeHelp=null;

  function positionHelp(target){const rect=target.getBoundingClientRect(),width=290;help.textContent=target.dataset.tip;help.style.left=`${Math.max(12,Math.min(innerWidth-width-12,rect.left))}px`;help.style.top=`${Math.min(innerHeight-120,Math.max(12,rect.bottom+7))}px`;help.classList.add('visible');target.setAttribute('aria-describedby','floatingHelp');help.id='floatingHelp';activeHelp=target;}

  $$('.help').forEach(button=>{button.addEventListener('mouseenter',()=>positionHelp(button));button.addEventListener('focus',()=>positionHelp(button));button.addEventListener('mouseleave',()=>{help.classList.remove('visible');activeHelp=null});button.addEventListener('blur',()=>{help.classList.remove('visible');activeHelp=null})});

  window.addEventListener('resize',()=>{if(activeHelp)positionHelp(activeHelp)});

  simulate();

})();
