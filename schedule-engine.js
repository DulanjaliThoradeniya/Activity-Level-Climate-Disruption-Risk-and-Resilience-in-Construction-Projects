/* Pure calendar scheduling functions shared by the UI and lightweight checks.
   Dates use YYYY-MM-DD calendar labels; finish dates are inclusive working days. */
(() => {
  const add=(s,n)=>{const d=new Date(s+'T00:00:00Z');d.setUTCDate(d.getUTCDate()+n);return d.toISOString().slice(0,10)};
  const isDate=s=>/^\d{4}-\d\d-\d\d$/.test(s)&&!Number.isNaN(Date.parse(s+'T00:00:00Z'))&&new Date(s+'T00:00:00Z').toISOString().slice(0,10)===s;
  const work=(s,c)=>c.weekdays.includes(new Date(s+'T00:00:00Z').getUTCDay()||7)&&!c.holidays.has(s);
  const next=(s,c)=>{let d=s;while(!work(d,c))d=add(d,1);return d};
  const after=(s,c)=>{let d=add(s,1);while(!work(d,c))d=add(d,1);return d};
  const addWork=(s,n,c)=>{let d=next(s,c);for(let i=1;i<n;i++){d=add(d,1);while(!work(d,c))d=add(d,1)}return d};
  const shiftWork=(s,n,c)=>{let d=s,step=n<0?-1:1;while(!work(d,c))d=add(d,step);for(let i=0;i<Math.abs(n);i++){d=add(d,step);while(!work(d,c))d=add(d,step)}return d};
  const beforeStart=(s,c)=>{let d=add(s,-1);while(!work(d,c))d=add(d,-1);return d};
  const relationshipTypes=new Set(['FS','SS','FF','SF']);
  function parseCSV(text){const rows=[];let row=[],cell='',q=false;for(let i=0;i<text.length;i++){const ch=text[i];if(q&&ch==='"'&&text[i+1]==='"'){cell+='"';i++}else if(ch==='"')q=!q;else if(ch===','&&!q){row.push(cell);cell=''}else if((ch==='\n'||ch==='\r')&&!q){if(ch==='\r'&&text[i+1]==='\n')i++;row.push(cell);if(row.some(x=>x.trim()))rows.push(row);row=[];cell=''}else cell+=ch}row.push(cell);if(row.some(x=>x.trim()))rows.push(row);if(q)throw Error('CSV contains an unclosed quoted field.');return rows}
  function calculate(list,start,cal,scenario=null){
    if(!isDate(start))throw Error('Project start must be a valid YYYY-MM-DD date.');
    if(!cal.weekdays.length||cal.weekdays.some(d=>!Number.isInteger(d)||d<1||d>7))throw Error('Choose at least one valid working weekday (1=Monday through 7=Sunday).');
    if([...cal.holidays].some(d=>!isDate(d)))throw Error('Non-working dates must use valid YYYY-MM-DD dates.');
    if(scenario&&(!isDate(scenario.start)||!isDate(scenario.end)||scenario.end<scenario.start||!['rain','heat'].includes(scenario.hazard)||!Number.isFinite(+scenario.factor)||scenario.factor<0||scenario.factor>1||!Number.isInteger(+scenario.recovery)||scenario.recovery<0))throw Error('Scenario requires valid dates, one hazard, productivity from 0 to 1, and non-negative whole recovery workdays.');
    const ids=new Map();
    list.forEach(a=>{if(typeof a.id!=='string'||!a.id.trim()||ids.has(a.id))throw Error(`Duplicate or empty activity ID: ${a.id||'(empty)'}`);ids.set(a.id,a);if(typeof a.name!=='string'||!a.name.trim()||typeof a.stage!=='string'||!a.stage.trim())throw Error(`Activity ${a.id}: name and stage are required.`);if(!Number.isInteger(+a.durationDays)||+a.durationDays<1)throw Error(`Activity ${a.id}: durationDays must be a positive whole number.`);if(!['outdoor','partially sheltered','indoor','not confirmed'].includes(a.exposure))throw Error(`Activity ${a.id}: invalid exposure.`);for(const f of ['heatSensitivity','rainSensitivity'])if(!['low','medium','high'].includes(a[f]))throw Error(`Activity ${a.id}: ${f} must be low, medium, or high.`)});
    const dependencies=new Map();
    for(const a of list){
      const raw=Array.isArray(a.dependencies)?a.dependencies:(a.predecessors||'').split(',').map(predecessorId=>predecessorId.trim()).filter(Boolean).map(predecessorId=>({predecessorId,type:a.relationship||'FS',lag:a.lag??0}));
      const seen=new Set(),links=raw.map(link=>{
        if(!link||typeof link.predecessorId!=='string'||!link.predecessorId.trim())throw Error(`Activity ${a.id}: each dependency needs a predecessor ID.`);
        const predecessorId=link.predecessorId.trim(),type=link.type||'FS',lag=Number(link.lag??0);
        if(predecessorId===a.id)throw Error(`${a.id} cannot depend on itself.`);
        if(!ids.has(predecessorId))throw Error(`${a.id} has missing predecessor ${predecessorId}.`);
        if(!relationshipTypes.has(type))throw Error(`Activity ${a.id}: relationship for ${predecessorId} must be FS, SS, FF, or SF.`);
        if(!Number.isInteger(lag))throw Error(`Activity ${a.id}: lag for ${predecessorId} must be a whole number of working days.`);
        if(seen.has(predecessorId))throw Error(`Activity ${a.id}: duplicate dependency on ${predecessorId}.`);
        seen.add(predecessorId);
        return {predecessorId,type,lag};
      });
      dependencies.set(a.id,links);
    }
    const order=[],marks=new Map();
    function visit(id){if(marks.get(id)===1)throw Error(`Dependency cycle includes ${id}.`);if(marks.get(id)===2)return;marks.set(id,1);dependencies.get(id).forEach(link=>visit(link.predecessorId));marks.set(id,2);order.push(id)}
    list.forEach(a=>visit(a.id));
    const predecessors=new Map(order.map(id=>[id,dependencies.get(id)]));
    const successors=new Map(order.map(id=>[id,[]])),successorLinks=new Map(order.map(id=>[id,[]]));
    for(const [successorId,links] of predecessors)links.forEach(link=>{successors.get(link.predecessorId).push(successorId);successorLinks.get(link.predecessorId).push({...link,successorId})});
    const duration=id=>+ids.get(id).durationDays;
    function constrainedStart(link,predecessor,successorDuration){
      switch(link.type){
        case 'FS':return shiftWork(after(predecessor.finish,cal),link.lag,cal);
        case 'SS':return shiftWork(predecessor.start,link.lag,cal);
        case 'FF':return shiftWork(predecessor.finish,link.lag-successorDuration+1,cal);
        case 'SF':return shiftWork(predecessor.start,link.lag-successorDuration+1,cal);
        default:throw Error(`Unsupported dependency relationship: ${link.type}`);
      }
    }
    const base={},latest={};let finishProject=start;
    for(const id of order){
      const candidates=predecessors.get(id).map(link=>constrainedStart(link,base[link.predecessorId],duration(id)));
      const s=next(candidates.length?candidates.reduce((latestDate,date)=>date>latestDate?date:latestDate,start):start,cal);
      const f=addWork(s,duration(id),cal);
      base[id]={start:s,finish:f,duration:duration(id)};
      if(f>finishProject)finishProject=f;
    }
    for(const id of [...order].reverse()){
      const constraints=successorLinks.get(id).map(link=>{
        const successor=latest[link.successorId],successorFinish=addWork(successor.start,duration(link.successorId)-1,cal);
        switch(link.type){
          case 'FS':return shiftWork(shiftWork(beforeStart(successor.start,cal),-link.lag,cal),-duration(id)+1,cal);
          case 'SS':return shiftWork(successor.start,-link.lag,cal);
          case 'FF':return shiftWork(shiftWork(successorFinish,-link.lag,cal),-duration(id)+1,cal);
          case 'SF':return shiftWork(successorFinish,-link.lag,cal);
          default:throw Error(`Unsupported dependency relationship: ${link.type}`);
        }
      });
      const terminalStart=shiftWork(finishProject,-duration(id)+1,cal);
      const latestStart=constraints.length?constraints.reduce((earliest,date)=>date<earliest?date:earliest,terminalStart):terminalStart;
      latest[id]={start:latestStart};
    }
    for(const id of order){let n=base[id].start,days=0;while(n<latest[id].start){if(work(n,cal))days++;n=add(n,1)}base[id].float=days;base[id].critical=days===0}
    const adj={};
    for(const id of order){
      const a=ids.get(id),links=predecessors.get(id),durationDays=duration(id);
      const dependencyStarts=links.map(link=>constrainedStart(link,adj[link.predecessorId],durationDays));
      let day=next(dependencyStarts.length?dependencyStarts.reduce((latestDate,date)=>date>latestDate?date:latestDate,base[id].start):base[id].start,cal);
      let remaining=durationDays,loss=0,eventDays=[],direct=false,started=day,factor=scenario?Math.max(0,Math.min(1,scenario.factor)):1;
      while(remaining>1e-9){
        if(!work(day,cal)){day=add(day,1);continue}
        const sensitivity=scenario&&scenario.hazard==='heat'?a.heatSensitivity:a.rainSensitivity,exposed=['outdoor','partially sheltered'].includes(a.exposure)&&sensitivity!=='low',inEvent=scenario&&day>=scenario.start&&day<=scenario.end&&exposed,prod=inEvent?factor:1;
        if(inEvent){direct=true;eventDays.push(day)}
        remaining-=prod;
        if(inEvent)loss+=1-prod;
        if(remaining>1e-9)day=add(day,1);
      }
      let fin=day;if(direct&&scenario.recovery>0)fin=addWork(after(fin,cal),scenario.recovery,cal);
      adj[id]={start:started,finish:fin,direct,loss,eventDays,base:base[id],inherited:links.some(link=>adj[link.predecessorId].finish>base[link.predecessorId].finish)};
    }
    return {order,base,adj,finishProject,adjustedFinish:order.reduce((m,id)=>adj[id].finish>m?adj[id].finish:m,start),successors,successorLinks,dependencies:predecessors};
  }
  const api={calculate,add,work,parseCSV};if(typeof module!=='undefined'&&module.exports)module.exports=api;else window.CResistScheduleEngine=api;
})();
