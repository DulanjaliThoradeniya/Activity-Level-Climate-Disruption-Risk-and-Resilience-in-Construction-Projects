const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const source = fs.readFileSync('rainfall-station.js', 'utf8');
const context = {};
vm.runInNewContext(source, context);
const resolver = context.CResistRainfallStation;

function forecast(start, mae = 2) {
  const first = Date.parse(`${start}T00:00:00Z`);
  return {
    validation: {mae},
    forecast: Array.from({length: 14}, (_, day) => ({
      date: new Date(first + day * 86400000).toISOString().slice(0, 10),
      value: day
    }))
  };
}
function models(stations) {
  return {xgboost: {stations: Object.fromEntries(Object.entries(stations).map(([code, result]) =>
    [code, {targets: {rainfall: result}}]))}};
}

test('uses the verified nearest proxy only when it has a current complete forecast', () => {
  const current = forecast('2026-11-11');
  const proxy = resolver.resolve(models({S94: current}), 'S29', '2026-11-11');
  assert.equal(proxy.code, 'S94');
  assert.equal(proxy.distanceKm, 2.63);
  assert.equal(resolver.resolve(models({S94: current}), 'S25', '2026-11-11'), null);
  assert.equal(resolver.resolve(models({S94: current, S29: current}), 'S29', '2026-11-11'), null);
  assert.equal(resolver.resolve(models({S94: current}), 'S29', '2026-11-12'), null);
  assert.equal(resolver.resolve(models({S94: forecast('2026-11-11', null)}), 'S29', '2026-11-11'), null);
  current.forecast[4].value = -1;
  assert.equal(resolver.resolve(models({S94: current}), 'S29', '2026-11-11'), null);
});

test('climate report and assessment use the labelled proxy and preserve the selected model', async () => {
  const history = JSON.parse(fs.readFileSync('data/climate-history.json', 'utf8'));
  const bundle = JSON.parse(fs.readFileSync('data/climate-multi-model-forecasts.json', 'utf8'));
  const legacy = JSON.parse(fs.readFileSync('data/climate-forecasts.json', 'utf8'));
  for (const [model, data] of Object.entries(bundle.models)) {
    delete data.stations.S29;
    const result = data.stations.S94.targets.rainfall;
    result.data_end = '2026-11-10';
    result.forecast = forecast('2026-11-11').forecast;
    result.validation.mae = model === 'lightgbm' ? 1 : 2;
  }
  class FixedDate extends Date {
    constructor(...args) { super(...(args.length ? args : ['2026-11-11T04:00:00Z'])); }
    static now() { return Date.parse('2026-11-11T04:00:00Z'); }
  }
  const browser = {Date: FixedDate, location: {protocol: 'https:'}};
  browser.window = browser;
  browser.fetch = async path => ({ok: true, json: async () =>
    path.endsWith('climate-history.json') ? history :
      path.endsWith('climate-multi-model-forecasts.json') ? bundle : legacy});
  vm.runInNewContext(source, browser);
  vm.runInNewContext(fs.readFileSync('climate-forecast.js', 'utf8'), browser);
  vm.runInNewContext(fs.readFileSync('assessment-screen.js', 'utf8'), browser);
  const proxy = browser.CResistRainfallStation.resolve(bundle.models, 'S29', '2026-11-11');
  assert.equal(proxy.code, 'S94');
  assert.ok((await browser.ClimateForecast.availableTargets('S29')).includes('rainfall'));
  const html = await browser.ClimateForecast.generate({
    area: 'Pasir Ris', station: {code: 'S29', name: 'Pasir Ris Road'},
    targetName: 'rainfall', modelId: 'lightgbm'
  });
  assert.match(html, /Rainfall proxy: selected station Pasir Ris Road \(S29\)/);
  assert.match(html, /Pasir Ris Street 51 \(S94\)/);
  assert.match(html, /2026-11-11 to 2026-11-24/);
  assert.match(html, /Open-Meteo gridded historical forecast estimates/);
  assert.equal((html.match(/data-series="forecast"/g) || []).length, 1);
  const comparison = await browser.ClimateForecast.generate({
    area: 'Pasir Ris', station: {code: 'S29', name: 'Pasir Ris Road'},
    targetName: 'rainfall', modelId: 'comparison'
  });
  assert.match(comparison, /2026-11-11 to 2026-11-24: lightgbm · station-specific validation MAE 1.00 mm/);
  assert.match(comparison, /data-risk-selected="true"><th scope="row">LightGBM · selected for rain screen/);
  bundle.models.lightgbm.stations.S94.targets.rainfall.forecast[3].value = -1;
  const mixed = await browser.ClimateForecast.generate({
    area: 'Pasir Ris', station: {code: 'S29', name: 'Pasir Ris Road'},
    targetName: 'rainfall', modelId: 'comparison'
  });
  assert.match(mixed, /2026-11-11 to 2026-11-13: lightgbm/);
  assert.match(mixed, /2026-11-14: lstm · station-specific validation MAE 2.00 mm/);
  assert.match(mixed, /2026-11-15 to 2026-11-24: lightgbm/);
  const days = browser.CResistAssessmentScreen.selectRainfall(bundle.models, proxy.code, '2026-11-11', '2026-11-24');
  assert.equal(days.length, 14);
  assert.equal(days[0].model, 'lightgbm');
});
