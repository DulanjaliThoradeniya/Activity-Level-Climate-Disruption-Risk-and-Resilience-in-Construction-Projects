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
  const asTime = value => value ? new Date(value).toLocaleString([], {weekday:'short',hour:'2-digit',minute:'2-digit'}) : '';
  async function updateWeather() {
    const area=areaSelect.value, region=areaRegion(area);
    weatherCard.classList.add('loading');$('#weatherArea').textContent=area;$('#weatherForecast').textContent='Loading NEA 24-hour outlook...';
    try {
      const response=await fetch('https://api.data.gov.sg/v1/environment/24-hour-weather-forecast',{cache:'no-store'});
      if(!response.ok)throw new Error('NEA forecast unavailable');
      const payload=await response.json(), item=payload.items?.[0]; if(!item)throw new Error('Forecast unavailable');
      const general=item.general||{}, outlook=(item.periods||[]).map(period=>({time:period.time,forecast:period.regions?.[region]})).filter(row=>row.forecast);
      const localOutlook=outlook.map(row=>`${asTime(row.time.start)} ${row.forecast}`).join(' - ');
      const temperature=general.temperature||{}, humidity=general.relative_humidity||{};
      $('#weatherWindow').textContent=`24-hour outlook - ${region} region`;
      $('#weatherForecast').textContent=`${general.forecast||'Forecast available'} - ${temperature.low??'-'} to ${temperature.high??'-'} C${localOutlook?` - ${localOutlook}`:''}`;
      $('#weatherUpdated').textContent=`Updated ${asTime(item.update_timestamp)} - RH ${humidity.low??'-'} to ${humidity.high??'-'}%`;
      $('#weatherIcon').textContent=/thunder|rain|shower/i.test(`${general.forecast} ${localOutlook}`)?'RAIN':(temperature.high||0)>=33?'HOT':'MILD';
      window.neaOutlook={area,region,summary:general.forecast||'NEA forecast',temperatureLow:temperature.low,temperatureHigh:temperature.high,humidityLow:humidity.low,humidityHigh:humidity.high,wind:general.wind,periods:outlook,updated:item.update_timestamp};
    } catch {
      $('#weatherWindow').textContent='24-hour feed unavailable';$('#weatherForecast').textContent='The live forecast is temporarily unavailable. Open NEA weather or retry later.';$('#weatherUpdated').textContent='The current risk screen will use a neutral weather baseline.';$('#weatherIcon').textContent='WX';window.neaOutlook=null;
    } finally {weatherCard.classList.remove('loading');window.simulate?.();}
  }
  areaSelect.addEventListener('change',updateWeather);
  $('#resetBtn').addEventListener('click',()=>{
    state.phase='demolition';state.hazard='flood';state.mode='current';
    areaSelect.value='City';$('#scale').value='low';
    ['severity','duration','protection','recovery'].forEach((id, index)=>{const input=$('#'+id);if(input)input.value=[4,6,35,45][index];});
    ['alternate','altRoute','sitePlan','backupPower'].forEach(id=>{const input=$('#'+id);if(input)input.checked=false;});
    if($('#buffer'))$('#buffer').checked=true;
    document.querySelectorAll('.phase').forEach(button=>button.classList.toggle('active',button.dataset.phase==='demolition'));
    document.querySelectorAll('.event').forEach(button=>button.classList.toggle('active',button.dataset.hazard==='flood'));
    document.querySelectorAll('.mode-tab').forEach(button=>{button.classList.toggle('active',button.dataset.mode==='current');button.setAttribute('aria-selected',String(button.dataset.mode==='current'));});
    window.syncLabels?.();window.simulate?.();updateWeather();
  });
  updateWeather();setInterval(updateWeather,6*60*60*1000);
})();

