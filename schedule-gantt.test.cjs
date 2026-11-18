const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const engine = require('./schedule-engine.js');
require('./assessment-screen.js');

const source = fs.readFileSync('schedule.js', 'utf8');
const start = source.indexOf('  function drawChart()');
const end = source.indexOf('  function drawResults()', start);
assert.ok(start >= 0 && end > start);
const draw = source.slice(start, end);

function render(projectStart, finish, today) {
  const elements = {
    '#projectStart': {value: projectStart},
    '#activityGantt': {innerHTML: ''},
    '#activityCount': {textContent: ''},
    '#ganttForecastPeriod': {textContent: ''}
  };
  const context = {
    computed: {finishProject: finish, base: {A: {start: projectStart, finish}}},
    activities: [{id: 'A', name: 'Test activity'}],
    assessmentInputs: {}, assessmentInputsPromise: null,
    window: {CResistAssessmentScreen: globalThis.CResistAssessmentScreen},
    $: selector => elements[selector],
    add: engine.add,
    singaporeToday: () => today,
    dateDelta: (from, to) => (Date.parse(`${to}T00:00:00Z`) - Date.parse(`${from}T00:00:00Z`)) / 86400000,
    esc: String
  };
  vm.runInNewContext(draw + '\ndrawChart()', context);
  return elements;
}

for (const [description, start, finish, offset] of [
  ['overlapping', '2026-11-09', '2026-11-18', 2],
  ['finished before today', '2026-10-01', '2026-10-10', 41],
  ['starting after the window', '2026-12-01', '2026-12-02', 0]
]) {
  test(`marks the complete 14-day window for a schedule ${description}`, () => {
    const elements = render(start, finish, '2026-11-11');
    const svg = elements['#activityGantt'].innerHTML;
    const band = svg.match(/data-gantt-forecast-window="2026-11-11\/2026-11-24" x="([\d.]+)" y="35" width="([\d.]+)"/);
    const line = svg.match(/data-gantt-today="2026-11-11" x1="([\d.]+)" x2="([\d.]+)"/);
    assert.ok(band);
    assert.ok(line);
    const unit = Number(band[2]) / 14;
    assert.ok(Math.abs(Number(band[1]) - (250 + offset * unit)) < 0.00001);
    assert.equal(Number(line[1]), Number(band[1]));
    assert.equal(Number(line[2]), Number(band[1]));
    assert.match(svg, /Today · 2026-11-11/);
    assert.match(svg, new RegExp(`Planned ${start} to ${finish}`));
    assert.match(elements['#ganttForecastPeriod'].textContent, /2026-11-11 to 2026-11-24/);
  });
}
