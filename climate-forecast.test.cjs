const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function makeSeries(days, valueAt) {
  const rows = [];
  const start = Date.UTC(2021, 0, 1);
  for (let index = 0; index < days; index += 1) {
    const date = new Date(start + index * 86400000).toISOString().slice(0, 10);
    rows.push([date, valueAt(index)]);
  }
  return rows;
}

async function run() {
  const observations = makeSeries(500, index => Math.max(0, 5 + Math.sin(index * Math.PI * 2 / 365) * 2 + (index % 9 === 0 ? 3 : 0)));
  const context = {
    window: {},
    fetch: async () => ({
      ok: true,
      json: async () => ({ schemaVersion: 1, updatedThrough: '2022-05', stations: { S24: observations } })
    })
  };
  vm.runInNewContext(fs.readFileSync('climate-forecast.js', 'utf8'), context);
  assert.equal(JSON.stringify(await context.window.ClimateForecast.availableTargets('S24')), JSON.stringify(['rainfall']));
  const html = await context.window.ClimateForecast.generate({
    area: 'Test Area',
    station: { code: 'S24', name: 'Test Station' },
    targetName: 'rainfall'
  });
  assert.match(html, /14-day forecast/);
  assert.match(html, /Holdout MAE/);
  assert.match(html, /RMSE/);
  assert.match(html, /MAPE/);
  assert.match(html, /Model accuracy and validation/);
  assert.match(html, /R²/);
  assert.match(html, /negative value is worse than that baseline/);
  assert.match(html, /Exploratory data: monthly average/);
  assert.match(html, /Seasonal pattern and interpretation/);
  assert.match(html, /not Facebook Prophet/);
  assert.match(html, /nea-climate-report-ready/);
  await assert.rejects(
    context.window.ClimateForecast.generate({
      area: 'Test Area',
      station: { code: 'S24', name: 'Test Station' },
      targetName: 'maximum_temperature'
    }),
    /not available/
  );
}

run().then(() => console.log('Browser forecast checks passed.')).catch(error => {
  console.error(error);
  process.exitCode = 1;
});
