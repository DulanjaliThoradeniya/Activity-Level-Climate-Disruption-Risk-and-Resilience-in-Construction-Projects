(function(root) {
  'use strict';

  const heavyCandidates = new Set(['H02','H03','H04','H05','H06','H07','H09','H10','H12','H18','H19','H20']);
  const hierarchy = {ELIMINATION: 0, SUBSTITUTION: 1, ENGINEERING: 2, ADMINISTRATIVE: 3, PPE: 4};

  function selectRainfall(models, station, start, end) {
    const days = new Map();
    for (const [model, data] of Object.entries(models || {})) {
      const result = data?.stations?.[station]?.targets?.rainfall;
      const mae = result?.validation?.mae;
      if (typeof mae !== 'number' || !Number.isFinite(mae) || mae < 0 || !Array.isArray(result.forecast)) continue;
      for (const point of result.forecast) {
        if (!/^\d{4}-\d{2}-\d{2}$/.test(point.date) || point.date < start || point.date > end ||
            typeof point.value !== 'number' || !Number.isFinite(point.value) || point.value < 0) continue;
        const previous = days.get(point.date);
        if (!previous || mae < previous.mae || (mae === previous.mae && model < previous.model)) {
          days.set(point.date, {date: point.date, rainfall: point.value, model, mae});
        }
      }
    }
    return [...days.values()].sort((a, b) => a.date.localeCompare(b.date));
  }

  function screen(knowledge, examples, days) {
    if (!Array.isArray(days) || !days.length) return {status: 'unavailable', hazards: [], controls: []};
    const wet = days.filter(day => day.rainfall > 0);
    if (!wet.length) return {status: 'dry', hazards: [], controls: []};
    const heavy = wet.some(day => day.rainfall >= 20);
    if (!Array.isArray(examples)) throw Error('Illustrative hazard scores are unavailable.');
    const scores = new Map(examples.map(item => {
      if (!item.hazard_id || !Number.isInteger(item.severity) || item.severity < 1 || item.severity > 5 ||
          !Number.isInteger(item.likelihood) || item.likelihood < 1 || item.likelihood > 5 ||
          item.rpn !== item.severity * item.likelihood) throw Error('Invalid illustrative hazard score.');
      return [item.hazard_id, item];
    }));
    const hazards = (knowledge.hazards || []).filter(h => heavy || !heavyCandidates.has(h.hazard_id))
      .map(h => ({...h, score: scores.get(h.hazard_id) || null}));
    const ids = new Set(hazards.map(h => h.hazard_id));
    const controls = (knowledge.controls || []).filter(c => (c.hazard_ids || []).some(id => ids.has(id)))
      .map((control, index) => ({control, index}))
      .sort((a, b) => (hierarchy[a.control.hierarchy?.toUpperCase()] ?? 99) - (hierarchy[b.control.hierarchy?.toUpperCase()] ?? 99) || a.index - b.index)
      .slice(0, 5).map(item => item.control);
    return {status: 'wet', hazards, controls, heavy};
  }

  function summary(hazards) {
    const scores = hazards.map(h => h.score).filter(Boolean);
    if (!scores.length) return null;
    const range = key => {
      const values = scores.map(s => s[key]), min = Math.min(...values), max = Math.max(...values);
      return min === max ? String(min) : `${min}–${max}`;
    };
    const average = scores.reduce((sum, score) => sum + score.rpn, 0) / scores.length;
    return {severity: range('severity'), likelihood: range('likelihood'), rpn: range('rpn'), average};
  }

  function chartScore(assessment) {
    if (!assessment?.wholeActivityInWindow || !assessment.expected || assessment.days.length !== assessment.expected ||
        !assessment.result.hazards.length || !assessment.result.hazards.every(hazard => hazard.score)) return null;
    return assessment.summary;
  }

  function riskBand(summary) {
    if (!summary) return {label: 'Unavailable / incomplete', colour: '#7a8780'};
    if (summary.average <= 4) return {label: 'Low', colour: '#5e9f71'};
    if (summary.average <= 12) return {label: 'Medium', colour: '#d39b35'};
    return {label: 'High', colour: '#bc5b4b'};
  }

  root.CResistAssessmentScreen = {selectRainfall, screen, summary, chartScore, riskBand};
})(typeof globalThis === 'object' ? globalThis : this);
