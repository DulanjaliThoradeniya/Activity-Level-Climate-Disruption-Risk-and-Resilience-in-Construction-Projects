const assert = require('node:assert/strict');
const test = require('node:test');
require('./assessment-screen.js');

const {selectRainfall, screen, summary} = globalThis.CResistAssessmentScreen;
const model = (mae, forecast) => ({stations:{S111:{targets:{rainfall:{validation:{mae},forecast}}}}});
const knowledge = {
  hazards:[{hazard_id:'H08',name:'Slippery surfaces'},{hazard_id:'H12',name:'Reduced visibility'},{hazard_id:'H01',name:'Water accumulation'}],
  controls:[
    {name:'Briefing',hierarchy:'ADMINISTRATIVE',hazard_ids:['H08']},
    {name:'Drainage',hierarchy:'ENGINEERING',hazard_ids:['H01']},
    {name:'Footwear',hierarchy:'PPE',hazard_ids:['H08']},
    {name:'Unrelated',hierarchy:'ELIMINATION',hazard_ids:['H20']},
  ],
};
const examples = [
  {hazard_id:'H08',severity:4,likelihood:3,rpn:12},
  {hazard_id:'H12',severity:4,likelihood:2,rpn:8},
  {hazard_id:'H01',severity:3,likelihood:2,rpn:6},
];

test('selects lowest valid station validation MAE for each available day', () => {
  const result=selectRainfall({
    xgboost:model(3,[{date:'2026-11-11',value:1},{date:'2026-11-12',value:2}]),
    tft:model(2,[{date:'2026-11-11',value:9},{date:'2026-11-12',value:null}]),
    lstm:model(null,[{date:'2026-11-11',value:0}]),
  },'S111','2026-11-11','2026-11-12');
  assert.deepEqual(result.map(day=>[day.date,day.rainfall,day.model]),[['2026-11-11',9,'tft'],['2026-11-12',2,'xgboost']]);
  assert.deepEqual(selectRainfall({},'S111','2026-11-11','2026-11-24'),[]);
});

test('distinguishes missing, dry, light and higher daily rainfall', () => {
  assert.equal(screen(knowledge,examples,[]).status,'unavailable');
  assert.equal(screen(knowledge,examples,[{rainfall:0}]).status,'dry');
  const light=screen(knowledge,examples,[{rainfall:19.9}]);
  assert.deepEqual(light.hazards.map(h=>h.hazard_id),['H08','H01']);
  assert.deepEqual(light.controls.map(c=>c.name),['Drainage','Briefing','Footwear']);
  assert.deepEqual(summary(light.hazards),{severity:'3–4',likelihood:'2–3',rpn:'6–12',average:9});
  const heavy=screen(knowledge,examples,[{rainfall:20}]);
  assert.equal(heavy.hazards.length,3);
  assert.equal(summary(heavy.hazards).average,26/3);
});

test('rejects invalid scores and limits controls to five', () => {
  assert.throws(()=>screen(knowledge,[{hazard_id:'H08',severity:4,likelihood:3,rpn:1}],[{rainfall:1}]),/Invalid/);
  const many={...knowledge,controls:Array.from({length:8},(_,i)=>({name:String(i),hierarchy:'ENGINEERING',hazard_ids:['H08']}))};
  assert.equal(screen(many,examples,[{rainfall:1}]).controls.length,5);
  assert.equal(summary([{score:null}]),null);
});

test('Gantt and assessment colours use complete scores and the same exact thresholds', () => {
  const {chartScore, riskBand} = globalThis.CResistAssessmentScreen;
  for (const [average, label, colour] of [
    [4, 'Low', '#5e9f71'], [4.1, 'Medium', '#d39b35'],
    [12, 'Medium', '#d39b35'], [12.1, 'High', '#bc5b4b']
  ]) {
    const data = {wholeActivityInWindow:true, expected:1, days:[{}], result:{hazards:[{score:{}}]}, summary:{average}};
    assert.equal(riskBand(chartScore(data)).label, label);
    assert.equal(riskBand(chartScore(data)).colour, colour);
    data.expected = 2;
    assert.equal(riskBand(chartScore(data)).colour, '#7a8780');
    data.expected = 1;
    data.result.hazards[0].score = null;
    assert.equal(chartScore(data), null);
  }
});
