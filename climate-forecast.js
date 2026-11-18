(() => {
  const targets = {
    rainfall: { label: 'Daily rainfall', unit: 'mm', precision: 1 },
    mean_temperature: { label: 'Daily mean temperature', unit: '°C', precision: 1 },
    maximum_temperature: { label: 'Daily maximum temperature', unit: '°C', precision: 1 },
    minimum_temperature: { label: 'Daily minimum temperature', unit: '°C', precision: 1 }
  };
  const targetIndexes = {
    rainfall: 1,
    mean_temperature: 2,
    maximum_temperature: 3,
    minimum_temperature: 4
  };
  let historyPromise;
  let modelForecastPromise;
  let multiModelForecastPromise;
  let offlineBundlePromise;

  const dateFromIso = value => new Date(`${value}T00:00:00Z`);
  const dayNumber = value => dateFromIso(value).getTime() / 86400000;
  const singaporeToday = () => new Date(Date.now() + 8 * 60 * 60 * 1000).toISOString().slice(0, 10);
  const escapeHtml = value => String(value).replace(/[&<>"']/g, character => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  })[character]);
  function loadOfflineBundle() {
    if (!offlineBundlePromise) {
      offlineBundlePromise = new Promise((resolve, reject) => {
        const script = document.createElement('script');
        script.src = `data/climate-offline.js?${Date.now()}`;
        script.onload = () => {
          if (!window.CResistClimateOffline) {
            reject(new Error('The local climate data bundle is invalid. Rebuild it before opening this file.'));
          } else {
            resolve(window.CResistClimateOffline);
          }
          script.remove();
        };
        script.onerror = () => {
          script.remove();
          reject(new Error('The local climate data bundle could not be loaded. Rebuild it before opening this file.'));
        };
        document.head.append(script);
      }).catch(error => {
        offlineBundlePromise = null;
        throw error;
      });
    }
    return offlineBundlePromise;
  }

  function nextSingaporeSevenAm(now = new Date()) {
    const singaporeNow = new Date(now.getTime() + 8 * 60 * 60 * 1000);
    const wallClockSevenAm = Date.UTC(
      singaporeNow.getUTCFullYear(),
      singaporeNow.getUTCMonth(),
      singaporeNow.getUTCDate(),
      7,
    ) - 8 * 60 * 60 * 1000;
    if (now.getTime() >= wallClockSevenAm) {
      return new Date(wallClockSevenAm + 24 * 60 * 60 * 1000);
    }
    return new Date(wallClockSevenAm);
  }

  async function fetchClimateData(path, label) {
    if (window.location?.protocol === 'file:') {
      const bundle = await loadOfflineBundle();
      if (!Object.prototype.hasOwnProperty.call(bundle, path)) {
        throw new Error(`${label} is missing from the local climate data bundle.`);
      }
      return bundle[path];
    }
    const response = await fetch(path, { cache: 'no-store' });
    if (!response.ok) throw new Error(`${label} returned HTTP ${response.status}.`);
    return response.json();
  }

  async function loadHistory() {
    if (!historyPromise) {
      historyPromise = fetchClimateData('data/climate-history.json', 'NEA history data')
        .then(data => {
          if (data.schemaVersion !== 1 || !data.stations) throw new Error('The bundled NEA history file has an unsupported format.');
          return data;
        })
        .catch(error => {
          historyPromise = null;
          throw error;
        });
    }
    return historyPromise;
  }

  async function loadModelForecasts() {
    if (!modelForecastPromise) {
      modelForecastPromise = fetchClimateData('data/climate-forecasts.json', 'Python climate model data')
        .then(data => {
          if (data.schemaVersion !== 1 || !data.stations) throw new Error('The Python climate model data has an unsupported format.');
          return data;
        })
        .catch(error => {
          modelForecastPromise = null;
          throw error;
        });
    }

    return modelForecastPromise;
  }

  async function loadMultiModelForecasts() {
    if (!multiModelForecastPromise) {
      multiModelForecastPromise = fetchClimateData('data/climate-multi-model-forecasts.json', 'Multi-model forecast data')
        .then(payload => {
          if (payload.schemaVersion !== 1 || !payload.models) {
            throw new Error('The multi-model forecast data has an unsupported format.');
          }
          return payload;
        })
        .catch(error => {
          multiModelForecastPromise = null;
          return null;
        });
    }
    return multiModelForecastPromise;
  }

  function refresh() {
    historyPromise = null;
    modelForecastPromise = null;
    multiModelForecastPromise = null;
    offlineBundlePromise = null;
    if (window.location?.protocol === 'file:') delete window.CResistClimateOffline;
    return Promise.all([loadHistory(), loadModelForecasts()]);
  }

  function format(value, precision) {
    return Number.isFinite(value) ? value.toFixed(precision) : '—';
  }

  function makeForecast(history, target, modelResult, estimatedValues = {}) {
    const available = history
      .map(row => ({
        date: row[0],
        value: row[target.index],
        estimated: Array.isArray(estimatedValues[row[0]]) && estimatedValues[row[0]].includes(target.name)
      }))
      .filter(row => /^\d{4}-\d{2}-\d{2}$/.test(row.date) && Number.isFinite(row.value))
      .sort((a, b) => a.date.localeCompare(b.date));
    if (available.length < 180) throw new Error('At least 180 valid daily NEA observations are needed for this forecast.');
    if (!modelResult || !Array.isArray(modelResult.forecast) || !modelResult.validation) {
      throw new Error('No Python-generated forecast is available for this station and variable.');
    }
    if (modelResult.forecast.length !== 14) throw new Error('The Python climate model data does not contain a 14-day forecast.');
    const latestDate = modelResult.data_end;
    if (!/^\d{4}-\d{2}-\d{2}$/.test(latestDate || '')) throw new Error('The saved forecast has no valid input end date.');
    const today = singaporeToday();
    const firstForecastDate = modelResult.forecast[0]?.date;
    if (!/^\d{4}-\d{2}-\d{2}$/.test(firstForecastDate || '')) throw new Error('The saved forecast has no valid start date.');
    const forecastGapDays = Math.max(0, Math.floor(dayNumber(today) - dayNumber(firstForecastDate)));
    return {
      available,
      test: Array.isArray(modelResult.backtest) ? modelResult.backtest : [],
      forecast: modelResult.forecast,
      forecastGapDays,
      validation: modelResult.validation,
      modelName: modelResult.model_name,
      modelDescription: modelResult.model_description,
      dataEnd: modelResult.data_end,
      hasEstimatedHistory: Object.values(estimatedValues).some(fields => Array.isArray(fields) && fields.includes(target.name)),
      issueDate: modelResult.generated_at || null,
      diagnosticsUrl: modelResult.diagnostics_url || null,
      modelSelection: modelResult.validation.model_selection || null,
      comparisonModels: Array.isArray(modelResult.validation.comparison_models)
        ? modelResult.validation.comparison_models
        : null
    };
  }

  function splitLineChart(forecast, target, split) {
    const history = forecast.available.filter(point =>
      point.date >= split.training_start_date && point.date <= split.testing_end_date && !point.estimated
    );
    if (!history.length) throw new Error('No verified historical data is available for the saved split.');
    const test = forecast.test.filter(point =>
      point.date >= split.testing_start_date && point.date <= split.testing_end_date && Number.isFinite(point.predicted)
    );
    const values = [
      ...history.map(point => point.value),
      ...test.map(point => point.predicted),
      ...forecast.forecast.flatMap(point => [point.lower, point.upper, point.value]).filter(Number.isFinite)
    ];
    const maximum = Math.max(...values, 1);
    const minimum = target.unit === 'mm'
      ? 0
      : Math.min(...values) - Math.max(1, (maximum - Math.min(...values)) * 0.1);
    const range = Math.max(maximum - minimum, 1);
    const left = 55, historyEnd = 650, futureStart = 705, right = 880, top = 38, bottom = 258;
    const startDay = dayNumber(split.training_start_date);
    const endDay = dayNumber(split.testing_end_date);
    const historyX = date => left + (dayNumber(date) - startDay) / Math.max(endDay - startDay, 1) * (historyEnd - left);
    const firstFutureDay = dayNumber(forecast.forecast[0].date);
    const lastFutureDay = dayNumber(forecast.forecast[forecast.forecast.length - 1].date);
    const futureX = date => futureStart + (dayNumber(date) - firstFutureDay) / Math.max(lastFutureDay - firstFutureDay, 1) * (right - futureStart);
    const y = value => top + (1 - (value - minimum) / range) * (bottom - top);
    const path = (points, x, key) => points.map((point, index) =>
      `${index ? 'L' : 'M'}${x(point.date).toFixed(1)},${y(point[key]).toFixed(1)}`
    ).join(' ');
    const testStart = historyX(split.testing_start_date);
    const band = [
      ...forecast.forecast.map(point => `${futureX(point.date).toFixed(1)},${y(point.upper).toFixed(1)}`),
      ...forecast.forecast.slice().reverse().map(point => `${futureX(point.date).toFixed(1)},${y(point.lower).toFixed(1)}`)
    ].join(' ');
    const grid = Array.from({ length: 5 }, (_, index) => {
      const value = minimum + range * index / 4;
      const lineY = y(value);
      return `<line x1="${left}" y1="${lineY}" x2="${right}" y2="${lineY}" stroke="#e5ece7"/><text x="${left - 8}" y="${lineY + 4}" text-anchor="end" fill="#6d7c72" font-size="11">${format(value, target.precision)}</text>`;
    }).join('');
    const total = split.training_count + split.testing_count;
    const trainPercent = (100 * split.training_count / total).toFixed(1);
    const testPercent = (100 * split.testing_count / total).toFixed(1);
    return `<svg viewBox="0 0 900 310" role="img" aria-label="Training, held-out testing, and expanded 14-day forecast for ${escapeHtml(target.label)}" style="min-width:700px">
      <rect data-region="training" x="${left}" y="${top}" width="${testStart - left}" height="${bottom - top}" fill="#b9dac5" opacity=".22"/>
      <rect data-region="testing" x="${testStart}" y="${top}" width="${historyEnd - testStart}" height="${bottom - top}" fill="#f4cc7b" opacity=".25"/>
      <rect data-region="forecast" x="${futureStart}" y="${top}" width="${right - futureStart}" height="${bottom - top}" fill="#71b5e8" opacity=".12"/>
      ${grid}<polygon points="${band}" fill="#71b5e8" opacity=".25"/>
      <path d="${path(history, historyX, 'value')}" fill="none" stroke="#263f32" stroke-width="1.2"/>
      <path d="${path(test, historyX, 'predicted')}" fill="none" stroke="#d39021" stroke-width="1.4" stroke-dasharray="5 4"/>
      <path data-series="forecast" d="${path(forecast.forecast, futureX, 'value')}" fill="none" stroke="#2479b8" stroke-width="2.5"/>
      <line x1="${testStart}" y1="${top}" x2="${testStart}" y2="${bottom}" stroke="#ad853f" stroke-dasharray="4 4"/>
      <g fill="#263f32" font-size="11"><text x="${left}" y="15">Training ${trainPercent}% (${split.training_count} observations)</text><text x="${testStart + 4}" y="15">Test ${testPercent}%</text><text x="${futureStart}" y="15">14-day forecast</text>
      <text x="${left}" y="282">${escapeHtml(split.training_start_date)}</text><text x="${testStart}" y="282" text-anchor="middle">${escapeHtml(split.testing_start_date)}</text><text x="${historyEnd}" y="282" text-anchor="end">${escapeHtml(split.testing_end_date)}</text>
      <text x="${futureStart}" y="282">${escapeHtml(forecast.forecast[0].date)}</text><text x="${right}" y="297" text-anchor="end">${escapeHtml(forecast.forecast[forecast.forecast.length - 1].date)}</text>
      <text x="${testStart + 4}" y="30">${split.testing_count} observations</text><text x="${futureStart}" y="30">Expanded date scale</text></g></svg>`;
  }

  function lineChart(forecast, target) {
    const split = forecast.validation?.data_split;
    if (split && Number.isInteger(split.training_count) && split.training_count > 0
      && Number.isInteger(split.testing_count) && split.testing_count > 0
      && [split.training_start_date, split.training_end_date, split.testing_start_date, split.testing_end_date]
        .every(value => /^\d{4}-\d{2}-\d{2}$/.test(value || ''))
      && split.training_start_date <= split.training_end_date
      && split.training_end_date < split.testing_start_date
      && split.testing_start_date <= split.testing_end_date
      && split.testing_end_date < forecast.forecast[0].date) {
      return splitLineChart(forecast, target, split);
    }
    const firstForecastDate = forecast.forecast[0].date;
    const recent = forecast.available.filter(point => point.date < firstForecastDate).slice(-365);
    if (!recent.length) throw new Error('No historical data precedes the saved forecast.');
    const firstDay = dayNumber(recent[0].date);
    const lastDay = dayNumber(forecast.forecast[forecast.forecast.length - 1].date);
    const values = [
      ...recent.map(point => point.value),
      ...forecast.test.map(point => point.predicted),
      ...forecast.forecast.flatMap(point => [point.lower, point.upper])
    ];
    const maximum = Math.max(...values, 1);
    const minimum = target.unit === 'mm'
      ? 0
      : Math.max(0, Math.min(...values) - Math.max(1, (maximum - Math.min(...values)) * 0.1));
    const valueRange = Math.max(maximum - minimum, 1);
    const width = 900;
    const height = 310;
    const left = 55;
    const right = 20;
    const top = 18;
    const bottom = 42;
    const x = date => left + (dayNumber(date) - firstDay) / (lastDay - firstDay) * (width - left - right);
    const y = value => top + (1 - (value - minimum) / valueRange) * (height - top - bottom);
    const historyPath = recent.map((point, index) => `${index ? 'L' : 'M'}${x(point.date).toFixed(1)},${y(point.value).toFixed(1)}`).join(' ');
    const testPath = forecast.test.map((point, index) => `${index ? 'L' : 'M'}${x(point.date).toFixed(1)},${y(point.predicted).toFixed(1)}`).join(' ');
    const futurePath = forecast.forecast.map((point, index) => `${index ? 'L' : 'M'}${x(point.date).toFixed(1)},${y(point.value).toFixed(1)}`).join(' ');
    const band = [
      ...forecast.forecast.map(point => `${x(point.date).toFixed(1)},${y(point.upper).toFixed(1)}`),
      ...forecast.forecast.slice().reverse().map(point => `${x(point.date).toFixed(1)},${y(point.lower).toFixed(1)}`)
    ].join(' ');
    const hasTest = forecast.test.length > 0;
    const testStart = hasTest ? x(forecast.test[0].date) : left;
    const testEnd = hasTest ? x(forecast.test[forecast.test.length - 1].date) : left;
    const forecastStart = x(forecast.forecast[0].date);
    const grid = Array.from({ length: 5 }, (_, index) => {
      const value = minimum + valueRange * index / 4;
      const lineY = y(value);
      return `<line x1="${left}" y1="${lineY}" x2="${width - right}" y2="${lineY}" stroke="#e5ece7"/><text x="${left - 8}" y="${lineY + 4}" text-anchor="end" fill="#6d7c72" font-size="11">${format(value, target.precision)}</text>`;
    }).join('');
    const dates = [0, 1, 2, 3].map(index => {
      const date = new Date((firstDay + (lastDay - firstDay) * index / 3) * 86400000).toISOString().slice(0, 7);
      const labelX = left + (width - left - right) * index / 3;
      return `<text x="${labelX}" y="${height - 13}" text-anchor="middle" fill="#6d7c72" font-size="11">${date}</text>`;
    }).join('');
    return `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Historical, test, and 14-day forecast chart for ${escapeHtml(target.label)}">${grid}${hasTest ? `<rect x="${testStart}" y="${top}" width="${Math.max(0, testEnd - testStart)}" height="${height - top - bottom}" fill="#f4cc7b" opacity=".17"/>` : ''}<polygon points="${band}" fill="#71b5e8" opacity=".25"/><line x1="${forecastStart}" y1="${top}" x2="${forecastStart}" y2="${height - bottom}" stroke="#7c8b80" stroke-dasharray="4 4"/><path d="${historyPath}" fill="none" stroke="#263f32" stroke-width="1.7"/>${hasTest ? `<path d="${testPath}" fill="none" stroke="#d39021" stroke-width="2" stroke-dasharray="5 4"/>` : ''}<path data-series="forecast" d="${futurePath}" fill="none" stroke="#2479b8" stroke-width="2.5"/>${dates}<g fill="#263f32" font-size="11"><text x="${left + 5}" y="${top + 14}">Historical data</text>${hasTest ? `<text x="${testStart + 5}" y="${top + 14}">Held-out validation</text>` : ''}<text x="${Math.min(forecastStart + 5, width - right - 90)}" y="${top + 14}">14-day forecast</text></g></svg>`;
  }

  function monthlyChart(history, target) {
    const sums = Array(12).fill(0);
    const counts = Array(12).fill(0);
    for (const point of history) {
      const month = Number(point.date.slice(5, 7)) - 1;
      sums[month] += point.value;
      counts[month] += 1;
    }
    const averages = sums.map((sum, index) => counts[index] ? sum / counts[index] : 0);
    const maximum = Math.max(...averages, 1);
    const barWidth = 48;
    const gap = 20;
    return `<svg viewBox="0 0 850 250" role="img" aria-label="Monthly average ${escapeHtml(target.label)}"><line x1="38" y1="210" x2="830" y2="210" stroke="#cfd9d1"/>${averages.map((value, index) => {
      const x = 48 + index * (barWidth + gap);
      const height = value / maximum * 170;
      return `<rect x="${x}" y="${210 - height}" width="${barWidth}" height="${height}" rx="4" fill="#4388a8"/><text x="${x + barWidth / 2}" y="230" text-anchor="middle" fill="#66756c" font-size="11">${new Date(Date.UTC(2024, index, 1)).toLocaleString('en', { month: 'short', timeZone: 'UTC' })}</text>`;
    }).join('')}</svg>`;
  }

  function distributionChart(history, target) {
    const values = history.map(point => point.value);
    const minimum = Math.min(...values);
    const maximum = Math.max(...values);
    const edges = target.unit === 'mm'
      ? [0, 0.1, 0.5, 1, 2, 5, 10, 20, 40, 80, 160, Infinity]
      : Array.from({ length: 13 }, (_, index) => minimum + (maximum - minimum || 1) * index / 12);
    const bins = Array(edges.length - 1).fill(0);
    for (const value of values) {
      const index = edges.findIndex((edge, edgeIndex) => edgeIndex > 0 && value < edge);
      bins[index < 0 ? bins.length - 1 : index - 1] += 1;
    }
    const peak = Math.max(...bins, 1);
    return `<svg viewBox="0 0 850 250" role="img" aria-label="Distribution of ${escapeHtml(target.label)} values"><line x1="38" y1="210" x2="830" y2="210" stroke="#cfd9d1"/>${bins.map((count, index) => {
      const width = 56;
      const x = 48 + index * 64;
      const height = count / peak * 170;
      const lower = edges[index];
      const label = Number.isFinite(lower) ? format(lower, target.precision) : 'open';
      return `<rect x="${x}" y="${210 - height}" width="${width}" height="${height}" rx="4" fill="#64a080"/><text x="${x + width / 2}" y="229" text-anchor="middle" fill="#66756c" font-size="10">${label}</text>`;
    }).join('')}</svg>`;
  }

  function componentChart(forecast, target) {
    const recent = forecast.available.slice(-365);
    const means = Array(12).fill(0);
    const counts = Array(12).fill(0);
    for (const point of recent) {
      const month = Number(point.date.slice(5, 7)) - 1;
      means[month] += point.value;
      counts[month] += 1;
    }
    const seasonal = means.map((sum, index) => counts[index] ? sum / counts[index] : 0);
    const baseline = recent.reduce((sum, point) => sum + point.value, 0) / recent.length;
    const maxAbs = Math.max(...seasonal.map(value => Math.abs(value - baseline)), 0.5);
    const zero = 120;
    return `<svg viewBox="0 0 850 250" role="img" aria-label="Seasonal component"><line x1="38" y1="${zero}" x2="830" y2="${zero}" stroke="#cfd9d1"/>${seasonal.map((value, index) => {
      const width = 48;
      const x = 48 + index * 65;
      const difference = value - baseline;
      const height = Math.abs(difference) / maxAbs * 90;
      const y = difference >= 0 ? zero - height : zero;
      return `<rect x="${x}" y="${y}" width="${width}" height="${height}" rx="3" fill="${difference >= 0 ? '#4388a8' : '#d99a42'}"/><text x="${x + width / 2}" y="231" text-anchor="middle" fill="#66756c" font-size="11">${new Date(Date.UTC(2024, index, 1)).toLocaleString('en', { month: 'short', timeZone: 'UTC' })}</text>`;
    }).join('')}<text x="48" y="18" fill="#66756c" font-size="12">Above / below recent mean · ${escapeHtml(target.unit)}</text></svg>`;
  }

  function reportHtml({ area, station, target, forecast, screeningDays = [] }) {
    const summary = forecast.validation;
    const peakDay = forecast.forecast.reduce((peak, point) => point.value > peak.value ? point : peak, forecast.forecast[0]);
    if (forecast.modelName === 'Prophet') {
      const forecastRows = forecast.forecast.map(point =>
        `<tr><td>${escapeHtml(point.date)}</td><td>${format(point.value, target.precision)} ${target.unit}</td><td>${format(point.lower, target.precision)}–${format(point.upper, target.precision)} ${target.unit}</td></tr>`
      ).join('');
      const forecastStart = forecast.forecast[0].date;
      const forecastEnd = forecast.forecast[forecast.forecast.length - 1].date;
      const lowestDay = forecast.forecast.reduce((lowest, point) => point.value < lowest.value ? point : lowest, forecast.forecast[0]);
      const isRainfall = target.index === targetIndexes.rainfall;
      const isMaximumTemperature = target.index === targetIndexes.maximum_temperature;
      const isMinimumTemperature = target.index === targetIndexes.minimum_temperature;
      const isMeanTemperature = target.index === targetIndexes.mean_temperature;
      const isTemperature = isMaximumTemperature || isMinimumTemperature || isMeanTemperature;
      const maximumLabel = isRainfall ? 'Maximum rainfall over 14 forecast days' : isTemperature ? 'Maximum forecast temperature' : 'Highest estimate';
      const minimumLabel = isRainfall ? 'Minimum rainfall over 14 forecast days' : isTemperature ? 'Minimum forecast temperature' : 'Lowest estimate';
      const mapeValue = Number.isFinite(Number(summary.mape)) ? `${format(summary.mape, 1)}%` : 'Unavailable';
      const issueText = forecast.issueDate ? ` · generated ${escapeHtml(forecast.issueDate)}` : '';
      const evaluationWindow = Number.isFinite(summary.n) ? summary.n : forecast.forecast.length;
      return `<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Prophet climate forecast</title><style>*{box-sizing:border-box}body{margin:0;padding:16px;background:#f6f8f6;color:#25372d;font:14px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}h1,h2,p{margin-top:0}h1{font-size:23px;margin-bottom:4px}h2{font-size:16px;margin-bottom:8px}.subtle,figcaption{color:#65766b;font-size:12px}.card{background:#fff;border:1px solid #e0e8e2;border-radius:12px;padding:16px;margin:0 0 12px}.metrics{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px}.metric{padding:10px;border-radius:9px;background:#f4f8f5}.metric strong{display:block;font-size:18px;color:#275f45}.metric span{font-size:11px;color:#64746a}.chart{width:100%;overflow:hidden}.chart svg{width:100%;height:auto;display:block}.two-col{display:grid;grid-template-columns:1fr 1fr;gap:12px}table{width:100%;border-collapse:collapse;font-size:12px}th,td{text-align:left;padding:7px;border-bottom:1px solid #e5ece7}a{color:#28765d}.note{padding:11px;border-left:3px solid #d99b39;background:#fff8e9}@media(max-width:620px){body{padding:10px}.metrics{grid-template-columns:repeat(2,minmax(0,1fr))}.two-col{grid-template-columns:1fr}h1{font-size:19px}}</style><body><section class="card"><p class="subtle">NEA HISTORICAL OBSERVATIONS · PROPHET MODEL</p><h1>${escapeHtml(target.label)} forecast · ${escapeHtml(area)}</h1><p class="subtle">${escapeHtml(station.name)} (${escapeHtml(station.code)}) · observations through ${escapeHtml(forecast.dataEnd)} · forecast ${forecastStart} to ${forecastEnd}${issueText}</p><div class="metrics"><div class="metric"><strong>${forecast.forecast.length} days</strong><span>Forecast horizon</span></div><div class="metric"><strong>${evaluationWindow} days</strong><span>Evaluation window</span></div><div class="metric"><strong>${format(peakDay.value, target.precision)} ${target.unit}</strong><span>${maximumLabel} · ${peakDay.date}</span></div><div class="metric"><strong>${format(lowestDay.value, target.precision)} ${target.unit}</strong><span>${minimumLabel} · ${lowestDay.date}</span></div></div>${forecast.forecastGapDays > 0 ? `<p class="note">The latest published NEA daily observation is ${forecast.forecastGapDays} days old. These Prophet estimates are generated daily from the latest available history and are not an official NEA weather forecast.</p>` : ''}</section><section class="card"><h2>Recent observations, held-out test and upcoming Prophet estimates</h2><div class="chart">${lineChart(forecast, target)}</div></section><section class="card"><h2>Daily forecast and 95% model interval</h2><table><thead><tr><th>Date</th><th>Prophet estimate</th><th>95% model interval</th></tr></thead><tbody>${forecastRows}</tbody></table><p class="subtle">Intervals describe model uncertainty and are not official warnings or guaranteed ranges.</p></section><section class="card"><h2>Validation error results</h2><div class="metrics"><div class="metric"><strong>${format(summary.mae, target.precision)} ${target.unit}</strong><span>Test-period MAE · ${summary.n} days</span></div><div class="metric"><strong>${format(summary.rmse, target.precision)} ${target.unit}</strong><span>Test-period RMSE</span></div><div class="metric"><strong>${mapeValue}</strong><span>Test-period MAPE</span></div></div><p class="subtle">Prophet was trained on NEA daily historical observations and scored on a held-out final period. These retrospective validation errors do not guarantee future accuracy.</p><p><a href="https://www.weather.gov.sg/climate-historical-daily/" target="_blank" rel="noopener">Source: NEA Historical Daily Records</a></p></section></body></html>`;
    }
    if (forecast.modelName === 'Saved model comparison' && Array.isArray(forecast.comparisonModels)) {
      const modelLabels = {
        xgboost: 'XGBoost',
        lightgbm: 'LightGBM',
        lstm: 'LSTM',
        tft: 'TFT',
        neuralprophet_new: 'NeuralProphet'
      };
      const selectedModels = new Set(screeningDays.map(day => day.model));
      const scoreRows = forecast.comparisonModels.map(model => {
        const validation = model.validation || {};
        const occurrence = validation.occurrence || {};
        const amount = validation.heavy_rain || {};
        const r2 = Number.isFinite(Number(validation.r2)) ? format(validation.r2, 3) : 'Not published';
        const isRainfall = target.index === targetIndexes.rainfall;
        return `<tr${selectedModels.has(model.id) ? ' style="background:#e2f4e7" data-risk-selected="true"' : ''}><th scope="row">${escapeHtml(modelLabels[model.id] || model.name)}${selectedModels.has(model.id) ? ' · selected for rain screen' : ''}</th><td>${format(validation.mae, target.precision)} ${target.unit}</td><td>${format(validation.rmse, target.precision)} ${target.unit}</td><td>${r2}</td><td>${isRainfall ? format(occurrence.f1, 3) : 'Not applicable'}</td><td>${isRainfall ? format(occurrence.brier, 3) : 'Not applicable'}</td><td>${isRainfall ? `${format(amount.mae, target.precision)} ${target.unit}` : 'Not applicable'}</td></tr>`;
      }).join('');
      const forecastStart = forecast.forecast[0].date;
      const forecastEnd = forecast.forecast[forecast.forecast.length - 1].date;
      return `<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Climate model comparison</title><style>*{box-sizing:border-box}body{margin:0;padding:16px;background:#f5f8f6;color:#25372d;font:14px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}h1,h2,p{margin-top:0}h1{font-size:23px;margin-bottom:4px}h2{font-size:17px;margin-bottom:8px}.subtle{color:#65766b;font-size:12px}.card{background:#fff;border:1px solid #e0e8e2;border-radius:12px;padding:16px;margin:0 0 12px}.pipeline{display:grid;grid-template-columns:repeat(5,minmax(130px,1fr));gap:8px}.pipeline div{padding:10px;border-radius:9px;background:#f1f7f3;text-align:center;font-weight:700}.pipeline span{display:block;font-size:11px;color:#64746a;font-weight:400}.table-wrap{overflow-x:auto}table{width:100%;border-collapse:collapse;font-size:12px}th,td{padding:7px;border-bottom:1px solid #e5ece7;text-align:left}th{background:#f7faf8}.note{padding:11px;border-left:3px solid #d99b39;background:#fff8e9}@media(max-width:620px){body{padding:10px}.pipeline{grid-template-columns:1fr}}</style><body><section class="card"><p class="subtle">MODEL COMPARISON · SAVED PUBLISHED RESULTS</p><h1>Five-model comparison · ${escapeHtml(area)}</h1><p class="subtle">${escapeHtml(station.name)} (${escapeHtml(station.code)}) · ${escapeHtml(target.label)} · 14-day forecasts ${forecastStart} to ${forecastEnd}</p><p>All results below use the same NEA historical observations, station, variable, cleaning process, training period and 14-day forecast horizon. Models were compared using retained historical observations rather than changing a model to fit a preferred result.</p></section><section class="card"><h2>Experimental comparison structure</h2><div class="pipeline"><div>NEA historical data<span>Rainfall, temperature and humidity where available</span></div><div>Data preparation<span>Same stations, training periods and forecast inputs</span></div><div>Five models<span>XGBoost · LightGBM · LSTM · TFT · NeuralProphet</span></div><div>14-day backtesting<span>Compare predictions with actual observations</span></div><div>C-RESIST connection<span>Use the strongest validated forecast</span></div></div></section><section class="card"><h2>Station-specific validation results</h2><div class="table-wrap"><table><thead><tr><th>Model</th><th>MAE</th><th>RMSE</th><th>R²</th><th>Rain occurrence F1</th><th>Rain occurrence Brier score</th><th>Rain amount MAE (wet days / heavy rain)</th></tr></thead><tbody>${scoreRows}</tbody></table></div><p class="subtle">MAE measures average prediction error; RMSE gives greater weight to larger errors; R² measures predictive fit and should be considered alongside MAE and RMSE. For rainfall, occurrence and rainfall amount are assessed separately because zero-rainfall observations dominate the daily series.</p></section><section class="card"><h2>How rainfall is interpreted</h2><ul><li><strong>Rain occurrence:</strong> F1-score evaluates correct classification of rainy and dry days.</li><li><strong>Rain probability:</strong> Brier score evaluates predicted rain probability calibration (lower is better).</li><li><strong>Rain amount:</strong> wet-day or heavy-rain MAE isolates forecast magnitude errors from dry-day zeros.</li></ul><p class="note">A model should be selected using station-specific retrospective performance and the full metric set, not MAE alone. These results do not guarantee future accuracy and are not official NEA warnings.</p></section><script>parent.postMessage({type:'nea-climate-report-ready'},'*')</script></body></html>`;
    }
    const mean = forecast.available.reduce((sum, point) => sum + point.value, 0) / forecast.available.length;
    const median = forecast.available.map(point => point.value).sort((a, b) => a - b)[Math.floor(forecast.available.length / 2)];
    const start = summary.data_split?.training_start_date || forecast.available[0].date;
    const end = summary.data_split?.testing_end_date || forecast.available[forecast.available.length - 1].date;
    const forecastStart = forecast.forecast[0].date;
    const forecastEnd = forecast.forecast[forecast.forecast.length - 1].date;
    const isRainfall = target.index === targetIndexes.rainfall;
    const candidateLabels = {
      tuned_pipeline: 'Validation-selected two-stage pipeline',
      xgboost_original: 'Original two-stage XGBoost (0.5)',
      xgboost: 'XGBoost',
      prophet: 'Prophet',
      no_rain: 'No-rain baseline',
      seasonal: 'Seasonal baseline',
      persistence: 'Persistence baseline',
      lightgbm: 'LightGBM',
      lstm: 'LSTM',
      tft: 'Temporal Fusion Transformer',
      neuralprophet_new: 'NeuralProphet (New)'
    };
    const candidateRows = Object.entries(summary.comparison?.candidates || {}).map(([name, metrics]) =>
      `<tr><th scope="row">${candidateLabels[name] || escapeHtml(name)}</th><td>${metrics.n}</td><td>${format(metrics.mae, target.precision)} ${target.unit}</td><td>${format(metrics.rmse, target.precision)} ${target.unit}</td>${isRainfall ? `<td>${format(metrics.mean_bias, target.precision)} ${target.unit}</td><td>${format(metrics.heavy_rain?.mae, target.precision)} ${target.unit} (${metrics.heavy_rain?.n ?? 0} days ≥${metrics.heavy_rain_occurrence?.threshold_mm ?? 10} mm)</td><td>${format(metrics.occurrence?.accuracy, 2)}</td><td>${format(metrics.occurrence?.precision, 2)}</td><td>${format(metrics.occurrence?.recall, 2)}</td><td>${format(metrics.occurrence?.f1, 2)}</td><td>${format(metrics.occurrence?.false_positive_rate ?? metrics.occurrence?.false_alarm_rate, 2)}</td><td>${format(metrics.occurrence?.brier, 2)}</td>` : ''}</tr>`
    ).join('');
    const leadLabels = {
      days_1_3: 'Days 1–3',
      days_4_7: 'Days 4–7',
      days_8_14: 'Days 8–14'
    };
    const leadRows = Object.entries(summary.by_lead || {}).map(([name, metrics]) => {
      const candidates = summary.candidate_by_lead?.[name];
      return `<tr><th scope="row">${leadLabels[name] || escapeHtml(name)}</th><td>${metrics.n}</td>${isRainfall ? `<td>${format(metrics.mae, target.precision)} ${target.unit}</td><td>${format(metrics.rmse, target.precision)} ${target.unit}</td><td>${format(candidates?.tuned_pipeline?.mae, target.precision)} ${target.unit}</td><td>${format(candidates?.tuned_pipeline?.rmse, target.precision)} ${target.unit}</td><td>${format(candidates?.xgboost_original?.mae, target.precision)} ${target.unit}</td><td>${format(candidates?.xgboost_original?.rmse, target.precision)} ${target.unit}</td><td>${format(candidates?.prophet?.mae, target.precision)} ${target.unit}</td><td>${format(candidates?.prophet?.rmse, target.precision)} ${target.unit}</td><td>${format(metrics.baselines?.no_rain?.mae, target.precision)} ${target.unit}</td><td>${format(metrics.baselines?.seasonal?.mae, target.precision)} ${target.unit}</td><td>${format(metrics.baselines?.persistence?.mae, target.precision)} ${target.unit}</td>` : `<td>${format(metrics.mae, target.precision)} ${target.unit}</td><td>${format(metrics.rmse, target.precision)} ${target.unit}</td>`}</tr>`;
    }).join('');
    const occurrence = summary.occurrence || {};
    const occurrenceText = !isRainfall ? '' : summary.data_split
      ? `<p>Rain occurrence (wet day ≥${format(occurrence.threshold_mm, 0)} mm) · precision ${format(occurrence.precision, 2)} · recall ${format(occurrence.recall, 2)} · F1 ${format(occurrence.f1, 2)} · false-positive rate ${format(occurrence.false_alarm_rate, 2)}. No calibrated event probability is available for a Brier score.</p>`
      : `<p>Rain occurrence (rain &gt; 0 mm; threshold ${format(occurrence.threshold, 2)}) · accuracy ${format(occurrence.accuracy, 2)} · precision ${format(occurrence.precision, 2)} · recall ${format(occurrence.recall, 2)} · F1 ${format(occurrence.f1, 2)} · false-positive rate ${format(occurrence.false_positive_rate, 2)} · Brier ${format(occurrence.brier, 2)}. ${occurrence.rainy_days} wet days among ${occurrence.n} scored forecasts.</p>`;
    const selection = forecast.modelSelection;
    const selectionText = isRainfall && selection
      ? `<p>${selection.evaluation_profile === 'extended_changi' ? 'Extended Changi assessment.' : 'Standard station assessment.'} Selection used ${selection.validation_period.origins} validation origins (${selection.validation_period.unique_dates} unique dates; ${selection.validation_period.target_pairs} forecast-target pairs) from ${selection.validation_period.start} to ${selection.validation_period.end}. The untouched final test covers ${selection.test_period.origins} origins, ${selection.test_period.unique_dates} unique dates, and ${selection.test_period.target_pairs} forecast-target pairs from ${selection.test_period.start} to ${selection.test_period.end}. Selected model: ${escapeHtml(selection.selected_name)}. Best tuned candidate: ${escapeHtml(JSON.stringify(selection.tuned_candidate))}.</p>`
      : '';
    const validationCandidatesText = isRainfall && selection?.best_tuned_candidates?.length
      ? `<h3>Top tuned candidates (validation only)</h3><div class="table-wrap"><table><thead><tr><th>Classifier</th><th>Amount model</th><th>Calibration</th><th>Strategy</th><th>Threshold</th><th>MAE</th><th>RMSE</th><th>Wet-day recall</th><th>Brier</th></tr></thead><tbody>${selection.best_tuned_candidates.map(candidate => `<tr><td>${escapeHtml(candidate.classifier)}</td><td>${escapeHtml(candidate.amount_model)}</td><td>${escapeHtml(candidate.calibration)}</td><td>${escapeHtml(candidate.strategy)}</td><td>${format(candidate.threshold, 2)}</td><td>${format(candidate.metrics.mae, target.precision)} ${target.unit}</td><td>${format(candidate.metrics.rmse, target.precision)} ${target.unit}</td><td>${format(candidate.metrics.occurrence?.recall, 2)}</td><td>${format(candidate.metrics.occurrence?.brier, 2)}</td></tr>`).join('')}</tbody></table></div>`
      : '';
    const intervalText = isRainfall && summary.interval
      ? `<p>Nominal 90% lead-window intervals from rolling-validation residual quantiles (not a formal conformal guarantee); empirical coverage on the final test: ${Object.entries(summary.interval.by_lead).map(([name, interval]) => `${leadLabels[name]} ${format(interval.empirical_coverage, 2)} (n=${interval.n})`).join(' · ')}. Coverage is measured on the held-out period and may differ from nominal.</p>`
      : '';
    const issueText = forecast.issueDate
      ? ` · forecast generated ${escapeHtml(forecast.issueDate)}`
      : '';
    const intervalLabel = summary.data_split
      ? 'Residual spread interval (±1.96 test residual standard deviations)'
      : isRainfall
      ? 'Nominal 90% rolling-validation residual interval'
      : 'Prophet model interval (nominal 95%)';
    const chartDescription = summary.data_split
      ? `The graph shows the verified training and test periods, with the 14-day forecast on an expanded date scale. Validation uses ${summary.forecast_origins} non-overlapping 14-day forecast origins. Scroll horizontally on narrow screens.`
      : `The graph shows the latest year for readability. Rainfall uses ${summary.folds.length} non-overlapping rolling-origin folds with a 14-day horizon; temperature uses ${summary.folds.length} rolling origins. Forecasts begin the day after the latest observation.`;
    const historyNotice = forecast.hasEstimatedHistory
      ? `<p class="warning">${summary.data_split ? 'The split chart and model inputs exclude estimated values. Exploratory summaries may include' : 'The displayed history includes'} Open-Meteo gridded historical forecast estimates for missing station observations. These estimates are not NEA measurements; see the input data provenance for the affected dates and variables.</p>`
      : '';
    const diagnosticsLink = forecast.diagnosticsUrl
      ? ` · <a href="${escapeHtml(forecast.diagnosticsUrl)}" target="_blank" rel="noopener">Changi rainfall diagnostic plots</a>`
      : '';
    const staleNotice = forecast.forecastGapDays > 0
      ? `<p class="warning">The forecast starts ${forecast.forecastGapDays} days before today, based on model inputs through ${forecast.dataEnd}. Some or all forecast dates may have passed.</p>`
      : '';
    return `<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>NEA climate forecast</title><style>
      *{box-sizing:border-box}body{margin:0;padding:16px;background:#f6f8f6;color:#25372d;font:14px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}h1,h2,p{margin-top:0}h1{font-size:23px;margin-bottom:4px}h2{font-size:16px;margin-bottom:8px}.subtle,figcaption{color:#65766b;font-size:12px}.card{background:#fff;border:1px solid #e0e8e2;border-radius:12px;padding:16px;margin:0 0 12px;box-shadow:0 2px 8px #24372b08}.metrics{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px}.metric{padding:10px;border-radius:9px;background:#f4f8f5}.metric strong{display:block;font-size:18px;color:#275f45}.metric span{font-size:11px;color:#64746a}.chart{width:100%;overflow:hidden}.chart svg{width:100%;height:auto;display:block}.legend{display:flex;gap:14px;flex-wrap:wrap;color:#607167;font-size:11px;margin-top:8px}.swatch{display:inline-block;width:10px;height:10px;border-radius:3px;margin-right:4px}.two-col{display:grid;grid-template-columns:1fr 1fr;gap:12px}.callout{border-left:3px solid #4b8db2;padding-left:11px}.warning{color:#75551c;background:#fff8e8;padding:10px;border-radius:8px;font-size:12px}table{width:100%;border-collapse:collapse;font-size:12px}th,td{text-align:left;padding:7px;border-bottom:1px solid #e5ece7;white-space:nowrap}div.table-wrap{overflow-x:auto}@media(max-width:620px){body{padding:10px}.metrics{grid-template-columns:repeat(2,minmax(0,1fr))}.two-col{grid-template-columns:1fr}h1{font-size:19px}}</style><body>
      <section class="card"><p class="subtle">NEA HISTORICAL STATION · PYTHON-GENERATED MODEL</p><h1>${escapeHtml(target.label)} forecast · ${escapeHtml(area)}</h1><p class="subtle">${escapeHtml(station.name)} (${escapeHtml(station.code)}) · daily records used ${start} to ${end} · 14-day forecast window ${forecastStart} to ${forecastEnd}${issueText} · ${escapeHtml(forecast.modelName)}</p><div class="metrics"><div class="metric"><strong>${format(forecast.forecast[0].value, target.precision)} ${target.unit}</strong><span>Day 1 estimate</span></div><div class="metric"><strong>${format(peakDay.value, target.precision)} ${target.unit}</strong><span>Highest 14-day estimate · ${peakDay.date}</span></div><div class="metric"><strong>${format(summary.mae, target.precision)} ${target.unit}</strong><span>${isRainfall ? 'Final test' : 'Rolling backtest'} MAE · ${summary.n} predictions</span></div><div class="metric"><strong>${format(summary.rmse, target.precision)} ${target.unit}</strong><span>${isRainfall ? 'Final test' : 'Rolling backtest'} RMSE</span></div></div>${staleNotice}${historyNotice}</section>
      <section class="card"><h2>Historical observations, validation and 14-day forecast</h2><div class="chart"${summary.data_split ? ' style="overflow-x:auto" tabindex="0" role="region" aria-label="Scrollable forecast chart"' : ''}>${lineChart(forecast, target)}</div><div class="legend"><span><i class="swatch" style="background:#263f32"></i>Observed history</span><span><i class="swatch" style="background:#d39021"></i>Python model held-out evaluation</span><span><i class="swatch" style="background:#2479b8"></i>14-day Python model forecast</span><span><i class="swatch" style="background:#71b5e8"></i>${intervalLabel}</span></div><p class="subtle" style="margin:10px 0 0">${chartDescription}</p></section>
      <section class="two-col"><div class="card"><h2>Exploratory data: monthly average</h2><div class="chart">${monthlyChart(forecast.available, target)}</div><p class="subtle">Average ${escapeHtml(target.label.toLowerCase())} grouped by calendar month across the available historical record.</p></div><div class="card"><h2>Exploratory data: distribution</h2><div class="chart">${distributionChart(forecast.available, target)}</div><p class="subtle">Frequency of observed values in equal-width bins. Historical mean ${format(mean, target.precision)} ${target.unit}; median ${format(median, target.precision)} ${target.unit}.</p></div></section>
      <section class="card"><h2>Seasonal pattern and interpretation</h2><div class="chart">${componentChart(forecast, target)}</div><p class="callout">${forecast.modelDescription} The component chart summarizes recent month-of-year differences from the recent mean; it is descriptive, not causal evidence.</p><p class="warning">This is an exploratory statistical estimate from a representative NEA station, not an official NEA forecast or a site-specific warning. Rainfall is intermittent, and historical daily data cannot predict the timing of individual storms. Interval coverage is empirical and may differ from nominal.</p><p class="subtle">Source: <a href="https://www.weather.gov.sg/climate-historical-daily/" target="_blank" rel="noopener">NEA Historical Daily Records</a>. Forecast and validation numbers are generated by the Python model builder; the browser renders those results. Record mean ${format(mean, target.precision)} ${target.unit}; median ${format(median, target.precision)} ${target.unit}.${diagnosticsLink}</p></section>
      <section class="card" aria-labelledby="model-accuracy-title"><h2 id="model-accuracy-title">Model accuracy and validation</h2><p>${isRainfall ? 'MAE and RMSE shown below are from the untouched final chronological test period.' : 'MAE and RMSE aggregate held-out observations from chronological rolling-origin folds.'} Each fold predicts the next 14 calendar days. ${escapeHtml(summary.comparison?.selection_rule || 'Saved published-model validation results.')}</p>${selectionText}${validationCandidatesText}${occurrenceText}${intervalText}<h3>Model and baseline comparison</h3><div class="table-wrap"><table><thead><tr><th>Candidate</th><th>n</th><th>MAE</th><th>RMSE</th>${isRainfall ? '<th>Mean bias</th><th>Heavy-rain MAE</th><th>Occurrence accuracy</th><th>Precision</th><th>Recall</th><th>F1</th><th>False-positive rate</th><th>Brier</th>' : ''}</tr></thead><tbody>${candidateRows}</tbody></table></div><h3>Performance by forecast lead</h3><div class="table-wrap"><table><thead><tr><th>Lead time</th><th>n</th>${isRainfall ? '<th>Selected MAE</th><th>Selected RMSE</th><th>Tuned pipeline MAE</th><th>Tuned pipeline RMSE</th><th>Original XGBoost MAE</th><th>Original XGBoost RMSE</th><th>Prophet MAE</th><th>Prophet RMSE</th><th>No-rain MAE</th><th>Seasonal MAE</th><th>Persistence MAE</th>' : `<th>${escapeHtml(forecast.modelName)} MAE</th><th>${escapeHtml(forecast.modelName)} RMSE</th>`}</tr></thead><tbody>${leadRows}</tbody></table></div><p class="subtle">Rainfall MAPE is intentionally omitted: zero-heavy daily rainfall makes it unstable and misleading. ${summary.data_split ? 'Wet-day and heavy-rain thresholds are fixed at 1 mm and 20 mm respectively.' : 'Occurrence thresholds are selected on the validation period; Brier is the mean squared probability error.'} Results are station-specific retrospective estimates, not guaranteed future accuracy.</p></section><script>parent.postMessage({type:'nea-climate-report-ready'},'*')</script>
      </body></html>`;
  }

  async function availableTargets(stationCode) {
    const [data, modelData, multiData] = await Promise.all([
      loadHistory(),
      loadModelForecasts(),
      loadMultiModelForecasts()
    ]);
    const records = data.stations[stationCode];
    if (!Array.isArray(records)) throw new Error(`No bundled NEA history is available for station ${stationCode}.`);
    const proxy = window.CResistRainfallStation?.resolve(multiData?.models, stationCode, singaporeToday());
    return Object.entries(targetIndexes)
      .filter(([name, index]) => {
        const code = name === 'rainfall' && proxy ? proxy.code : stationCode;
        const targetRecords = data.stations[code] || [];
        return targetRecords.reduce((count, row) => count + Number(Number.isFinite(row[index])), 0) >= 180
        && (
          Boolean(modelData.stations[code]?.targets?.[name])
          || Object.values(multiData?.models || {}).some(model =>
            Boolean(model.stations?.[code]?.targets?.[name]?.forecast?.length === 14)
          )
        );
      })
      .map(([name]) => name);
  }

  async function generate({ area, station, targetName, modelId = 'existing_model' }) {
    const target = targets[targetName];
    const multiData = await loadMultiModelForecasts();
    const proxy = targetName === 'rainfall' ? window.CResistRainfallStation?.resolve(multiData?.models, station.code, singaporeToday()) : null;
    if (modelId === 'existing_model' && !proxy) {
      try {
        const response = await fetch(`http://127.0.0.1:8765/api/forecast?location=${encodeURIComponent(area)}&target=${encodeURIComponent(targetName)}`);
        if (response.ok) {
          const live = await response.json();
          if (typeof live.reportHtml === 'string') return live.reportHtml;
        }
      } catch (error) {
        // Fall back to the bundled daily model when the optional local Python service is not running.
      }
    }
    if (!target) throw new Error('Select a supported forecast variable.');
    const [data, modelData] = await Promise.all([loadHistory(), loadModelForecasts()]);
    const requestedStation = station;
    if (proxy) station = proxy;
    const records = data.stations[station.code];
    if (!Array.isArray(records)) throw new Error(`No bundled NEA history is available for station ${station.code}.`);
    const index = targetIndexes[targetName];
    const supportedTargets = await availableTargets(requestedStation.code);
    if (!supportedTargets.includes(targetName)) throw new Error(`${target.label} is not available for station ${station.code}; choose an enabled variable.`);
    let result = modelData.stations[station.code]?.targets?.[targetName];
    const multiModelIds = ['xgboost', 'lightgbm', 'lstm', 'tft', 'neuralprophet_new'];
    if (multiModelIds.includes(modelId)) {
      const multiData = await loadMultiModelForecasts();
      const savedResult = multiData?.models?.[modelId]?.stations?.[station.code]?.targets?.[targetName];
      if (savedResult?.forecast?.length !== 14) throw new Error(`No 14-day ${modelId} forecast is available for ${station.code} and ${target.label}.`);
      result = savedResult;
      if (result) {
        result = {
          ...result,
          validation: {
            ...result.validation,
            folds: Array.isArray(result.validation?.folds) ? result.validation.folds : [],
            comparison: {
              candidates: {
                [modelId]: result.validation
              },
              selection_rule: `${result.model_name || modelId} validation metrics on the final chronological holdout period.`
            },
            by_lead: result.validation?.by_lead || {},
            candidate_by_lead: {},
            occurrence: result.validation?.data_split ? (result.validation.occurrence || {}) : {
              threshold: 0,
              accuracy: 0,
              precision: 0,
              recall: 0,
              f1: 0,
              false_positive_rate: 0,
              brier: 0,
              rainy_days: 0,
              n: 0
            }
          }
        };
      }
    }
    if (modelId === 'comparison') {
      const multiData = await loadMultiModelForecasts();
      const available = multiModelIds
        .map(id => ({ id, result: multiData?.models?.[id]?.stations?.[station.code]?.targets?.[targetName] }))
        .filter(item => item.result?.forecast?.length === 14
          && item.result.forecast.every(point => Number.isFinite(Number(point.value)))
          && item.result.forecast.every(point => targetName !== 'rainfall'
            || !Number.isFinite(Number(point.lower))
            || Number(point.lower) >= 0));
      if (available.length < 2) throw new Error('At least two verified model results are required for comparison.');
      result = {
        ...available[0].result,
        model_name: 'Saved model comparison',
        model_description: 'Comparison of all five available saved climate models.',
        validation: {
          n: available.length,
          mae: available[0].result.validation?.mae,
          rmse: available[0].result.validation?.rmse,
          comparison: {
            candidates: Object.fromEntries(available.map(item => [
              item.id,
              item.result.validation || { n: 0, mae: null, rmse: null }
            ])),
            selection_rule: 'Five bundled saved models compared on their published station-specific validation metrics.'
          },
          folds: [],
          by_lead: {},
          candidate_by_lead: {},
          occurrence: {
            threshold: 0,
            accuracy: 0,
            precision: 0,
            recall: 0,
            f1: 0,
            false_positive_rate: 0,
            brier: 0,
            rainy_days: 0,
            n: 0
          },
          comparison_models: available.map(item => ({
            id: item.id,
            name: item.result.model_name,
            validation: item.result.validation,
            forecast: item.result.forecast,
            backtest: Array.isArray(item.result.backtest) ? item.result.backtest : []
          }))
        }
      };
    }
    if (!result) throw new Error(`No Python-generated forecast is available for ${target.label} at station ${station.code}.`);
    const reportTarget = { ...target, index, name: targetName };
    const forecast = makeForecast(records, reportTarget, result, data.estimatedValues?.[station.code]);
    if (modelId !== 'existing_model' && !multiModelIds.includes(modelId) && modelId !== 'comparison') {
      throw new Error('This model has not passed runtime compatibility checks and is not available yet.');
    }
    const today = singaporeToday();
    const screeningEnd = new Date(Date.parse(`${today}T00:00:00Z`) + 13 * 86400000).toISOString().slice(0, 10);
    const screeningDays = modelId === 'comparison' && targetName === 'rainfall'
      ? window.CResistAssessmentScreen.selectRainfall(multiData?.models, station.code, today, screeningEnd)
      : [];
    let report = reportHtml({ area, station, target: reportTarget, forecast, screeningDays });
    if (modelId === 'comparison') {
      const groups = [];
      for (const day of screeningDays) {
        const last = groups[groups.length - 1];
        if (last && last.model === day.model && last.mae === day.mae && dayNumber(day.date) === dayNumber(last.end) + 1) last.end = day.date;
        else groups.push({start: day.date, end: day.date, model: day.model, mae: day.mae});
      }
      const selection = targetName !== 'rainfall'
        ? 'The project rain-hazard screen uses rainfall forecasts, not this temperature comparison.'
        : groups.length
          ? groups.map(group => `${escapeHtml(group.start)}${group.end === group.start ? '' : ` to ${escapeHtml(group.end)}`}: ${escapeHtml(group.model)} · station-specific validation MAE ${group.mae.toFixed(2)} mm`).join('<br>')
          : 'No eligible rainfall model forecasts are available for the current 14-day screening window.';
      report = report.replace('<h2>Station-specific validation results</h2>', `<h2>Station-specific validation results</h2><p class="note"><strong>Project rain-hazard screening model</strong><br>${selection}<br><small>For a scheduled activity on these dates, the screen selects the lowest available station-specific validation MAE for each day; ties use model ID order. MAE is a historical validation summary, not a separate accuracy score for each forecast date. Past validation does not guarantee future accuracy.</small></p>`);
      report = report.replace('A model should be selected using station-specific retrospective performance and the full metric set, not MAE alone.', 'Other metrics provide additional context; the project rain-hazard screen uses station-specific validation MAE as described above.');
    }
    const notices = [];
    if (proxy) notices.push(`Rainfall proxy: selected station ${requestedStation.name} (${requestedStation.code}) lacks a current forecast. This forecast and its history use ${proxy.name} (${proxy.code}), approximately ${proxy.distanceKm} km away, not measurements at the selected station.`);
    if (forecast.hasEstimatedHistory && !report.includes('These estimates are not NEA measurements')) {
      notices.push('The displayed history includes Open-Meteo gridded historical forecast estimates for missing station observations. These estimates are not NEA measurements; see the input data provenance for the affected dates and variables.');
    }
    if (forecast.available[forecast.available.length - 1].date >= forecast.forecast[0].date) {
      notices.push('The chart stops historical data before this saved forecast begins to prevent overlap. Later observations are not shown on the chart; refresh the model results for a current forecast.');
    }
    return notices.length ? report.replace('<body>', `<body>${notices.map(notice => `<p class="warning">${escapeHtml(notice)}</p>`).join('')}`) : report;
  }

  window.ClimateForecast = { availableTargets, generate, refresh };
})();
