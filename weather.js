window.neaHistoricalStationByArea = {
  "City": {"code":"S111","name":"Scotts Road"},
  "Kallang": {"code":"S119","name":"Nicoll Highway"},
  "Marine Parade": {"code":"S113","name":"Marine Parade Road"},
  "Novena": {"code":"S111","name":"Scotts Road"},
  "Queenstown": {"code":"S77","name":"Alexandra Road"},
  "Tanglin": {"code":"S79","name":"Somerset Road"},
  "Toa Payoh": {"code":"S88","name":"Toa Payoh North"},
  "Bukit Merah": {"code":"S77","name":"Alexandra Road"},
  "Bukit Timah": {"code":"S90","name":"Bukit Timah Road"},
  "Ang Mo Kio": {"code":"S109","name":"Ang Mo Kio Avenue 5"},
  "Bishan": {"code":"S88","name":"Toa Payoh North"},
  "Central Water Catchment": {"code":"S08","name":"Lower Peirce Reservoir"},
  "Hougang": {"code":"S43","name":"Kim Chuan Road"},
  "Mandai": {"code":"S40","name":"Mandai Lake Road"},
  "Punggol": {"code":"S81","name":"Punggol Central"},
  "Seletar": {"code":"S25","name":"Seletar Meteorological Station"},
  "Sembawang": {"code":"S80","name":"Sembawang Meteorological Station"},
  "Sengkang": {"code":"S81","name":"Punggol Central"},
  "Serangoon": {"code":"S43","name":"Kim Chuan Road"},
  "Woodlands": {"code":"S104","name":"Woodlands Avenue 9"},
  "Yishun": {"code":"S104","name":"Woodlands Avenue 9"},
  "Lim Chu Kang": {"code":"S112","name":"Lim Chu Kang Road"},
  "Sungei Kadut": {"code":"S66","name":"Kranji Reservoir"},
  "Bedok": {"code":"S107","name":"East Coast Park"},
  "Changi": {"code":"S24","name":"Changi Meteorological Station"},
  "Geylang": {"code":"S43","name":"Kim Chuan Road"},
  "Pasir Ris": {"code":"S29","name":"Pasir Ris Road"},
  "Paya Lebar": {"code":"S06","name":"Paya Lebar Meteorological Station"},
  "Tampines": {"code":"S84","name":"Tampines Avenue 5"},
  "Pulau Ubin": {"code":"S106","name":"Jalan Noordin (Pulau Ubin)"},
  "Pulau Tekong": {"code":"S24","name":"Changi Meteorological Station"},
  "Boon Lay": {"code":"S33","name":"Jurong Pier Road"},
  "Bukit Batok": {"code":"S64","name":"Bukit Panjang Road"},
  "Bukit Panjang": {"code":"S64","name":"Bukit Panjang Road"},
  "Choa Chu Kang": {"code":"S114","name":"Choa Chu Kang Avenue 4"},
  "Clementi": {"code":"S50","name":"Clementi Road"},
  "Jalan Bahar": {"code":"S117","name":"Banyan Road (Jurong Island)"},
  "Jurong East": {"code":"S33","name":"Jurong Pier Road"},
  "Jurong Island": {"code":"S117","name":"Banyan Road (Jurong Island)"},
  "Jurong West": {"code":"S114","name":"Choa Chu Kang Avenue 4"},
  "Pioneer": {"code":"S117","name":"Banyan Road (Jurong Island)"},
  "Tengah": {"code":"S23","name":"Tengah Meteorological Station"},
  "Tuas": {"code":"S115","name":"Tuas South Avenue 3"},
  "Western Islands": {"code":"S117","name":"Banyan Road (Jurong Island)"},
  "Western Water Catchment": {"code":"S112","name":"Lim Chu Kang Road"},
  "Sentosa": {"code":"S60","name":"Artillery Avenue (Sentosa)"},
  "Southern Islands": {"code":"S60","name":"Artillery Avenue (Sentosa)"}
};

(() => {
  const $ = selector => document.querySelector(selector);
  const areaSelect = $('#location'), weatherCard = $('#weatherCard');
  const areaRegion = area => {
    if(['Jurong East','Jurong West','Boon Lay','Bukit Batok','Bukit Panjang','Choa Chu Kang','Clementi','Jalan Bahar','Jurong Island','Pioneer','Tengah','Tuas','Western Islands','Western Water Catchment','Lim Chu Kang','Sungei Kadut'].includes(area)) return 'west';
    if(['Bedok','Changi','Geylang','Pasir Ris','Paya Lebar','Tampines','Pulau Ubin','Pulau Tekong','Marine Parade'].includes(area)) return 'east';
    if(['Woodlands','Yishun','Sembawang','Mandai','Seletar','Punggol','Sengkang','Hougang','Ang Mo Kio','Bishan','Serangoon','Central Water Catchment'].includes(area)) return 'north';
    if(['Sentosa','Southern Islands','Bukit Merah','Queenstown'].includes(area)) return 'south';
    return 'central';
  };
  const asTime = value => value ? new Date(value).toLocaleString([], {month:'short',day:'numeric',weekday:'short',hour:'2-digit',minute:'2-digit'}) : '';
  async function updateWeather() {
    const area=areaSelect.value, region=areaRegion(area);
    weatherCard.classList.add('loading');$('#weatherArea').textContent=area;$('#weatherForecast').textContent='Loading NEA 24-hour outlook...';
    try {
      const response=await fetch('https://api.data.gov.sg/v1/environment/24-hour-weather-forecast',{cache:'no-store'});
      if(!response.ok)throw new Error('NEA forecast unavailable');
      const payload=await response.json(), item=payload.items?.[0]; if(!item)throw new Error('Forecast unavailable');
      if(item.valid_period?.end&&new Date(item.valid_period.end).getTime()<Date.now())throw new Error('Forecast validity has expired');
      const general=item.general||{}, outlook=(item.periods||[]).map(period=>({time:period.time,forecast:period.regions?.[region]})).filter(row=>row.forecast);
      const localOutlook=outlook.map(row=>`${asTime(row.time.start)} ${row.forecast}`).join(' - ');
      const temperature=general.temperature||{}, humidity=general.relative_humidity||{};
      const valid=item.valid_period||{};
      $('#weatherWindow').textContent=`Regional 24-hour outlook (${region}) - valid ${valid.start?asTime(valid.start):'start unavailable'} to ${valid.end?asTime(valid.end):'end unavailable'}`;
      $('#weatherForecast').textContent=`${general.forecast||'Forecast available'} - ${temperature.low??'-'} to ${temperature.high??'-'} C${localOutlook?` - ${localOutlook}`:''}`;
      $('#weatherUpdated').textContent=`Source: data.gov.sg / NEA · issued ${item.update_timestamp?asTime(item.update_timestamp):'time unavailable'} · RH ${humidity.low??'-'} to ${humidity.high??'-'}%`;
      $('.weather-live').textContent='NEA FEED';
      $('#weatherIcon').textContent=/thunder|rain|shower/i.test(`${general.forecast} ${localOutlook}`)?'RAIN':(temperature.high||0)>=33?'HOT':'MILD';
      window.neaOutlook={area,region,summary:general.forecast||'NEA forecast',temperatureLow:temperature.low,temperatureHigh:temperature.high,humidityLow:humidity.low,humidityHigh:humidity.high,wind:general.wind,periods:outlook,updated:item.update_timestamp};
    } catch {
      $('#weatherWindow').textContent='Forecast validity unavailable';$('#weatherForecast').textContent='NEA weather data are unavailable. Weather exposure is unknown; missing data are not treated as safe.';$('#weatherUpdated').textContent='Source status: unavailable';$('#weatherIcon').textContent='WX';$('.weather-live').textContent='UNAVAILABLE';window.neaOutlook=null;
    } finally {weatherCard.classList.remove('loading');window.simulate?.();}
  }

  const climateDialog=document.createElement('dialog');
  climateDialog.id='climateForecastDialog';
  climateDialog.className='schedule-dialog climate-forecast-dialog';
  climateDialog.innerHTML=`<div class="dialog-head"><div><p class="eyebrow">NEA HISTORICAL CLIMATE MODEL</p><h2 id="climateForecastTitle">Climate forecast</h2></div><button class="dialog-close" id="closeClimateForecast" type="button" aria-label="Close climate forecast">×</button></div><p id="climateForecastStation" class="climate-forecast-station"></p><div class="climate-forecast-controls"><label>Forecast variable<select id="climateForecastTarget"><option value="rainfall">Daily rainfall (mm)</option><option value="maximum_temperature">Daily maximum temperature (°C)</option><option value="mean_temperature">Daily mean temperature (°C)</option><option value="minimum_temperature">Daily minimum temperature (°C)</option></select></label><button id="generateClimateForecast" type="button">Generate 14-day forecast</button></div><p id="climateForecastStatus" class="climate-forecast-status" role="status" aria-live="polite" hidden></p><iframe id="climateForecastFrame" title="Climate forecast and exploratory data analysis" sandbox="allow-scripts" hidden></iframe><div id="climateForecastHelp" class="climate-forecast-help" hidden><p id="climateForecastError">The bundled NEA climate history could not be loaded.</p><a href="https://www.weather.gov.sg/climate-historical-daily/" target="_blank" rel="noopener">NEA Historical Daily Records</a></div>`;
  document.body.append(climateDialog);

  const reportFrame=climateDialog.querySelector('#climateForecastFrame');
  const reportStatus=climateDialog.querySelector('#climateForecastStatus');
  const reportHelp=climateDialog.querySelector('#climateForecastHelp');
  const generateButton=climateDialog.querySelector('#generateClimateForecast');
  climateDialog.querySelector('#climateForecastTarget').value='rainfall';
  let reportReadyHandler;
  let forecastRequest;

  async function loadClimateForecast() {
    const area=areaSelect.value;
    const station=window.neaHistoricalStationByArea?.[area];
    climateDialog.querySelector('#climateForecastTitle').textContent=`${area} climate forecast`;
    climateDialog.querySelector('#climateForecastStation').textContent=station
      ? `NEA historical station used: ${station.name} (${station.code}). Historical station data are used as a representative proxy for this project area.`
      : 'No NEA historical station is mapped to this project area.';
    if(forecastRequest)forecastRequest.abort();
    forecastRequest=new AbortController();
    reportFrame.hidden=true;
    reportHelp.hidden=true;
    if(!station) {
      reportStatus.hidden=false;
      reportStatus.textContent='A historical climate report is not available for this project area.';
      return;
    }
    reportStatus.hidden=false;
    reportStatus.textContent='Loading bundled NEA records and preparing the 14-day forecast in your browser…';
    generateButton.disabled=true;
    generateButton.textContent='Preparing forecast…';
    try {
      const targetSelect=climateDialog.querySelector('#climateForecastTarget');
      const availableTargets=await window.ClimateForecast.availableTargets(station.code);
      for(const option of targetSelect.options)option.disabled=!availableTargets.includes(option.value);
      if(!availableTargets.includes(targetSelect.value))targetSelect.value=availableTargets.includes('rainfall')?'rainfall':(availableTargets[0]||'');
      if(!targetSelect.value)throw new Error(`No supported climate variables are available for station ${station.code}.`);
      const target=targetSelect.value;
      const reportHtml=await window.ClimateForecast.generate({area,station,targetName:target});
      if(forecastRequest.signal.aborted)return;
      if(reportReadyHandler)window.removeEventListener('message',reportReadyHandler);
      reportReadyHandler=event=>{
        if(event.source!==reportFrame.contentWindow||event.data?.type!=='nea-climate-report-ready')return;
        reportFrame.hidden=false;
        reportStatus.hidden=true;
        window.removeEventListener('message',reportReadyHandler);
        reportReadyHandler=null;
      };
      window.addEventListener('message',reportReadyHandler);
      reportFrame.srcdoc=reportHtml;
    } catch(error) {
      if(error.name==='AbortError')return;
      reportStatus.hidden=true;
      reportHelp.hidden=false;
      climateDialog.querySelector('#climateForecastError').textContent=`Could not prepare the NEA climate forecast: ${error.message} Open this website over HTTP(S) so its bundled climate-history.json file can be loaded, then retry.`;
    } finally {
      generateButton.disabled=false;
      generateButton.textContent='Generate 14-day forecast';
    }
  }

  function openClimateForecast() {
    climateDialog.showModal();
    loadClimateForecast();
  }

  $('#currentTab').textContent='Climate Forecast';
  $('#currentTab').addEventListener('click',openClimateForecast);
  generateButton.addEventListener('click',loadClimateForecast);
  climateDialog.querySelector('#closeClimateForecast').addEventListener('click',()=>climateDialog.close());
  climateDialog.addEventListener('close',()=>{
    if(forecastRequest)forecastRequest.abort();
    if(reportReadyHandler)window.removeEventListener('message',reportReadyHandler);
    reportReadyHandler=null;
  });
  climateDialog.addEventListener('click',event=>{if(event.target===climateDialog)climateDialog.close()});

  areaSelect.addEventListener('change',updateWeather);
  window.refreshWeather=updateWeather;
  updateWeather();setInterval(updateWeather,6*60*60*1000);
})();
