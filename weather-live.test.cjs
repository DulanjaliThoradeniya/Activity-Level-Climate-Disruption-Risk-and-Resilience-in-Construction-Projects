const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const test = require('node:test');

const source = fs.readFileSync('weather.js', 'utf8');
const start = source.indexOf('  let liveRequestVersion=0;');
const end = source.indexOf('  generateButton.textContent=', start);
assert.ok(start >= 0 && end > start);
const liveSource = source.slice(start, end);
const forecastStart = source.indexOf('  async function loadClimateForecast(');
const forecastEnd = source.indexOf('  function openClimateForecast()', forecastStart);
assert.ok(forecastStart >= 0 && forecastEnd > forecastStart);
const forecastSource = source.slice(forecastStart, forecastEnd);

test('shows current station readings separately and reports missing readings', async () => {
  const liveReadings = {textContent: ''};
  const context = {
    liveReadings,
    asTime: value => value,
    fetch: async url => ({
      ok: true,
      json: async () => ({items: [{
        timestamp: new Date().toISOString(),
        readings: url.includes('air-temperature') ? [{station_id: 'S111', value: 30}] : []
      }]})
    }),
  };
  await vm.runInNewContext(liveSource + '\nloadLiveReadings({code:"S111"})', context);
  assert.match(liveReadings.textContent, /Rain in latest 5 minutes: unavailable/);
  assert.match(liveReadings.textContent, /Air temperature: 30 °C/);
  assert.match(liveReadings.textContent, /not completed daily model inputs/);
});

test('rejects stale readings instead of presenting them as current', async () => {
  const liveReadings = {textContent: ''};
  const context = {
    liveReadings,
    asTime: value => value,
    fetch: async () => ({
      ok: true,
      json: async () => ({items: [{timestamp:'2020-01-01T00:00:00Z', readings:[{station_id:'S111', value:1}]}]})
    }),
  };
  await vm.runInNewContext(liveSource + '\nloadLiveReadings({code:"S111"})', context);
  assert.match(liveReadings.textContent, /not current/);
});

test('refreshes forecast data and replaces the report frame on each render', async () => {
  let refreshes = 0;
  const frames = [];
  function makeFrame() {
    return {
      hidden: true,
      cloneNode: () => makeFrame(),
      replaceWith(next) { frames.push(next); }
    };
  }
  const target = { value: 'rainfall', options: [{ value: 'rainfall' }] };
  const elements = {
    '#climateForecastTitle': {},
    '#climateForecastStation': {},
    '#climateForecastTarget': target
  };
  const context = {
    AbortController,
    areaSelect: { value: 'Queenstown' },
    climateDialog: { querySelector: selector => elements[selector] },
    window: {
      location: { protocol: 'file:' },
      neaHistoricalStationByArea: { Queenstown: { code: 'S77', name: 'Alexandra Road' } },
      ClimateForecast: {
        refresh: async () => { refreshes += 1; },
        availableTargets: async () => ['rainfall'],
        generate: async () => '<html>forecast</html>'
      }
    },
    forecastRequest: null,
    reportFrame: makeFrame(),
    reportHelp: {},
    reportStatus: {},
    generateButton: {},
    selectedModel: 'xgboost',
    loadLiveReadings() {}
  };
  await vm.runInNewContext(forecastSource + '\nloadClimateForecast(true)', context);
  assert.equal(refreshes, 1);
  assert.equal(frames.length, 1);
  assert.equal(frames[0].hidden, false);
  assert.equal(frames[0].srcdoc, '<html>forecast</html>');
  await vm.runInNewContext(forecastSource + '\nloadClimateForecast()', context);
  assert.equal(refreshes, 1);
  assert.equal(frames.length, 2);
});
