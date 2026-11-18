const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const test = require('node:test');
require('./assessment-screen.js');

const source = fs.readFileSync('schedule.js', 'utf8');
const start = source.indexOf('  async function showProvisionalExamples(');
const end = source.indexOf('  const stableValue=', start);
assert(start >= 0 && end > start);
const render = source.slice(start, end);
const bundle = JSON.parse(fs.readFileSync('data/provisional-wsh-examples.json', 'utf8'));

test('hazards group same-date rows and the RPN card matches the Gantt band', () => {
  const start = source.indexOf('  function renderWeatherAssessment(data)');
  const end = source.indexOf('  async function showProvisionalExamples', start);
  const screen = globalThis.CResistAssessmentScreen;
  const knowledge = {hazards:[{hazard_id:'H01',name:'Pooling'}, {hazard_id:'H08',name:'Slips'}], controls:[]};
  const examples = [{hazard_id:'H01',severity:3,likelihood:2,rpn:6},{hazard_id:'H08',severity:4,likelihood:3,rpn:12}];
  const days = [{date:'2026-11-11',rainfall:5,model:'tft',mae:2},{date:'2026-11-12',rainfall:6,model:'tft',mae:2}];
  const result = screen.screen(knowledge, examples, days);
  const data = {station:'S111',days,result,knowledge,examples,summary:screen.summary(result.hazards),expected:2,wholeActivityInWindow:true};
  const context = {window:{CResistAssessmentScreen:screen},esc:String,data};
  const html = vm.runInNewContext(source.slice(start,end)+'\nrenderWeatherAssessment(data)',context);
  assert.match(html, /rowspan="2">2026-11-11/);
  assert.match(html, /rowspan="2">2026-11-12/);
  assert.match(html, /--rpn-colour:#d39b35/);
  assert.match(html, /Average 9\.0 · Medium/);
  data.wholeActivityInWindow = false;
  const incomplete = vm.runInNewContext(source.slice(start,end)+'\nrenderWeatherAssessment(data)',context);
  assert.match(incomplete, /--rpn-colour:#7a8780/);
  assert.match(incomplete, /Gantt colour: unavailable \/ incomplete/);
});

async function renderExamples(data, code = 'SP-01') {
  const host = {isConnected: true, innerHTML: ''};
  const context = {
    loadJSON: async () => {
      if (data instanceof Error) throw data;
      return data;
    },
    esc: value => String(value).replace(/[&<>"']/g, char => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[char])),
    host,
    code,
  };
  await vm.runInNewContext(render + '\nshowProvisionalExamples(code, host)', context);
  return host.innerHTML;
}

test('shows only labelled hypothetical per-hazard workbook examples', async () => {
  const html = await renderExamples(bundle);
  assert.match(html, /Synthetic, desk-based assumptions/);
  assert.match(html, /not surveyed, site-verified or approved/);
  assert.match(html, /must not be used for safety decisions/);
  assert.match(html, /<td>H08<\/td><td>4<\/td><td>3<\/td><td>12<\/td><td>Medium<\/td>/);
});

test('reports missing and invalid examples without assigning a risk', async () => {
  for (const data of [new Error('fetch failed'), {...bundle, activities: {}}, {...bundle, activities: {'SP-01': [{hazard_id:'H08', severity:4, likelihood:3, rpn:1, example_class:'Low'}]}}]) {
    const html = await renderExamples(data);
    assert.match(html, /Provisional examples unavailable/);
    assert.match(html, /WSH risk remains unassessed/);
    assert.doesNotMatch(html, /<table>/);
  }
});

const forecastStart = source.indexOf('  let forecastContextVersion=0');
const forecastEnd = source.indexOf('  function refreshExperimentalForecastContext()', forecastStart);
assert(forecastStart >= 0 && forecastEnd > forecastStart);
const forecastRenderer = source.slice(forecastStart, forecastEnd);

async function renderForecast({forecastData, request, activity, planned, location = 'Ang Mo Kio'}) {
  const host = {isConnected: true, innerHTML: ''};
  const context = {
    host, activity, planned, location,
    loadJSON: async () => {
      if (forecastData instanceof Error) throw forecastData;
      return forecastData;
    },
    buildPredictionRequest: () => {
      if (request instanceof Error) throw request;
      return request;
    },
    esc: value => String(value).replace(/[&<>"']/g, char => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[char])),
  };
  await vm.runInNewContext(forecastRenderer + '\nshowExperimentalForecastContext(host, activity, planned, location)', context);
  return host.innerHTML;
}

test('shows only overlapping forecasts and labels them as context, not risk', async () => {
  const forecastData = {models: {
    xgboost: {stations: {S24: {targets: {rainfall: {forecast: [{date:'2026-11-11', value:12}]}}}}},
    lightgbm: {stations: {S24: {targets: {mean_temperature: {forecast: [{date:'2026-11-11', value:29}]}}}}},
  }};
  const request = {nea_station_id:'S24', forecast_days:[
    {date:'2026-11-11', rainfall_mm:12, mean_temperature_c:29, forecast_model_by_target:{rainfall:'xgboost', mean_temperature:'lightgbm'}},
    {date:'2026-11-14', rainfall_mm:99, mean_temperature_c:99, forecast_model_by_target:{rainfall:'xgboost', mean_temperature:'lightgbm'}},
  ]};
  const activity = {exposure:'outdoor', rainSensitivity:'high', heatSensitivity:'low'};
  const html = await renderForecast({forecastData, request, activity, planned:{start:'2026-11-10',finish:'2026-11-12'}});
  assert.match(html, /not a risk assessment/);
  assert.match(html, /outdoor; rain sensitivity: high; heat sensitivity: low/);
  assert.match(html, /2026-11-11<\/td><td>12 mm · xgboost<\/td><td>29 °C · lightgbm/);
  assert.doesNotMatch(html, /2026-11-14/);
  assert.match(html, /examples below are not weather-specific predictions/);
});

test('reports missing, non-overlapping and invalid forecasts without risk inference', async () => {
  const activity = {exposure:'indoor', rainSensitivity:'low', heatSensitivity:'low'};
  const planned = {start:'2026-11-10',finish:'2026-11-12'};
  const missing = await renderForecast({forecastData:new Error('forecast missing'), activity, planned});
  assert.match(missing, /forecast missing/);
  assert.match(missing, /No WSH rating has been inferred/);
  const nonoverlap = await renderForecast({forecastData:{models:{}}, request:{nea_station_id:'S24',forecast_days:[]}, activity, planned});
  assert.match(nonoverlap, /No current forecast dates overlap/);
  const invalid = await renderForecast({forecastData:{models:{xgboost:{stations:{S24:{targets:{rainfall:{forecast:[{date:'2026-11-11',value:null}]}}}}}}},request:{nea_station_id:'S24',forecast_days:[{date:'2026-11-11',forecast_model_by_target:{rainfall:'xgboost'}}]},activity,planned});
  assert.match(invalid, /Invalid rainfall forecast/);
  assert.match(invalid, /No WSH rating has been inferred/);
});
