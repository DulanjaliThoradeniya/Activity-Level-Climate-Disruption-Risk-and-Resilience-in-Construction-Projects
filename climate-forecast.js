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

  const dateFromIso = value => new Date(`${value}T00:00:00Z`);
  const dayNumber = value => dateFromIso(value).getTime() / 86400000;
  const dayOfYear = value => {
    const date = dateFromIso(value);
    const start = Date.UTC(date.getUTCFullYear(), 0, 1);
    return (date.getTime() - start) / 86400000;
  };
  const escapeHtml = value => String(value).replace(/[&<>"']/g, character => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  })[character]);

  async function loadHistory() {
    if (!historyPromise) {
      historyPromise = fetch('data/climate-history.json')
        .then(response => {
          if (!response.ok) throw new Error(`NEA history data returned HTTP ${response.status}.`);
          return response.json();
        })
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

  function featureVector(isoDate, originDay) {
    const time = (dayNumber(isoDate) - originDay) / 365.25;
    const annual = dayOfYear(isoDate) / 365.25 * Math.PI * 2;
    const weekday = dateFromIso(isoDate).getUTCDay() / 7 * Math.PI * 2;
    return [
      1, time,
      Math.sin(annual), Math.cos(annual),
      Math.sin(annual * 2), Math.cos(annual * 2),
      Math.sin(annual * 3), Math.cos(annual * 3),
      Math.sin(weekday), Math.cos(weekday)
    ];
  }

  function solveLinearSystem(matrix, values) {
    const size = values.length;
    const augmented = matrix.map((row, index) => [...row, values[index]]);
    for (let column = 0; column < size; column += 1) {
      let pivot = column;
      for (let row = column + 1; row < size; row += 1) {
        if (Math.abs(augmented[row][column]) > Math.abs(augmented[pivot][column])) pivot = row;
      }
      if (Math.abs(augmented[pivot][column]) < 1e-10) throw new Error('The historical series is not sufficient to fit the model.');
      [augmented[column], augmented[pivot]] = [augmented[pivot], augmented[column]];
      const divisor = augmented[column][column];
      for (let index = column; index <= size; index += 1) augmented[column][index] /= divisor;
      for (let row = 0; row < size; row += 1) {
        if (row === column) continue;
        const factor = augmented[row][column];
        for (let index = column; index <= size; index += 1) augmented[row][index] -= factor * augmented[column][index];
      }
    }
    return augmented.map(row => row[size]);
  }

  function fitModel(observations) {
    const originDay = dayNumber(observations[0].date);
    const width = featureVector(observations[0].date, originDay).length;
    const matrix = Array.from({ length: width }, () => Array(width).fill(0));
    const values = Array(width).fill(0);
    for (const observation of observations) {
      const vector = featureVector(observation.date, originDay);
      for (let row = 0; row < width; row += 1) {
        values[row] += vector[row] * observation.value;
        for (let column = 0; column < width; column += 1) {
          matrix[row][column] += vector[row] * vector[column];
        }
      }
    }
    for (let index = 1; index < width; index += 1) matrix[index][index] += 1e-6;
    const coefficients = solveLinearSystem(matrix, values);
    return {
      predict(isoDate) {
        const estimate = featureVector(isoDate, originDay)
          .reduce((sum, feature, index) => sum + feature * coefficients[index], 0);
        return Math.max(0, estimate);
      }
    };
  }

  function metrics(actual, predicted) {
    const errors = actual.map((value, index) => value - predicted[index]);
    const mae = errors.reduce((sum, value) => sum + Math.abs(value), 0) / errors.length;
    const rmse = Math.sqrt(errors.reduce((sum, value) => sum + value * value, 0) / errors.length);
    const nonZero = actual.map((value, index) => ({ value, prediction: predicted[index] })).filter(item => item.value !== 0);
    const mape = nonZero.length
      ? nonZero.reduce((sum, item) => sum + Math.abs((item.value - item.prediction) / item.value), 0) / nonZero.length * 100
      : null;
    const actualMean = actual.reduce((sum, value) => sum + value, 0) / actual.length;
    const totalSquares = actual.reduce((sum, value) => sum + (value - actualMean) ** 2, 0);
    const residualSquares = errors.reduce((sum, value) => sum + value ** 2, 0);
    const rSquared = totalSquares > 0 ? 1 - residualSquares / totalSquares : null;
    return { mae, rmse, mape, rSquared, nonZeroDays: nonZero.length };
  }

  function format(value, precision) {
    return Number.isFinite(value) ? value.toFixed(precision) : '—';
  }

  function formatRSquared(value) {
    if (!Number.isFinite(value)) return '—';
    return Math.abs(value) < 0.005 ? '0.00' : value.toFixed(2);
  }

  function makeForecast(history, target) {
    const available = history
      .map(row => ({ date: row[0], value: row[target.index] }))
      .filter(row => /^\d{4}-\d{2}-\d{2}$/.test(row.date) && Number.isFinite(row.value))
      .sort((a, b) => a.date.localeCompare(b.date));
    if (available.length < 180) throw new Error('At least 180 valid daily NEA observations are needed for this forecast.');

    const testCount = Math.min(60, Math.max(30, Math.floor(available.length * 0.2)));
    const training = available.slice(0, -testCount);
    const test = available.slice(-testCount);
    const validationModel = fitModel(training);
    const testPredictions = test.map(item => validationModel.predict(item.date));
    const validation = metrics(test.map(item => item.value), testPredictions);
    const residualDeviation = Math.sqrt(
      test.reduce((sum, item, index) => {
        const error = item.value - testPredictions[index];
        return sum + error * error;
      }, 0) / test.length
    );

    const model = fitModel(available);
    const lastDate = dateFromIso(available[available.length - 1].date);
    const future = [];
    for (let offset = 1; offset <= 14; offset += 1) {
      const date = new Date(lastDate.getTime() + offset * 86400000).toISOString().slice(0, 10);
      const value = model.predict(date);
      future.push({
        date,
        value,
        lower: Math.max(0, value - 1.96 * residualDeviation),
        upper: value + 1.96 * residualDeviation
      });
    }
    const heldOut = test.map((item, index) => ({ ...item, predicted: testPredictions[index] }));
    return { available, test: heldOut, forecast: future, validation };
  }

  function lineChart(forecast, target) {
    const recent = forecast.available.slice(-365);
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
    const testStart = x(forecast.test[0].date);
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
    return `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Historical, test, and 14-day forecast chart for ${escapeHtml(target.label)}">${grid}<rect x="${testStart}" y="${top}" width="${Math.max(0, forecastStart - testStart)}" height="${height - top - bottom}" fill="#f4cc7b" opacity=".17"/><polygon points="${band}" fill="#71b5e8" opacity=".25"/><line x1="${forecastStart}" y1="${top}" x2="${forecastStart}" y2="${height - bottom}" stroke="#7c8b80" stroke-dasharray="4 4"/><path d="${historyPath}" fill="none" stroke="#263f32" stroke-width="1.7"/><path d="${testPath}" fill="none" stroke="#d39021" stroke-width="2" stroke-dasharray="5 4"/><path d="${futurePath}" fill="none" stroke="#2479b8" stroke-width="2.5"/>${dates}<g fill="#263f32" font-size="11"><text x="${left + 5}" y="${top + 14}">Historical observations</text><text x="${testStart + 5}" y="${top + 14}">Held-out validation</text><text x="${forecastStart + 5}" y="${top + 14}">14-day forecast</text></g></svg>`;
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

  function reportHtml({ area, station, target, forecast }) {
    const summary = forecast.validation;
    const peakDay = forecast.forecast.reduce((peak, point) => point.value > peak.value ? point : peak, forecast.forecast[0]);
    const mean = forecast.available.reduce((sum, point) => sum + point.value, 0) / forecast.available.length;
    const median = forecast.available.map(point => point.value).sort((a, b) => a - b)[Math.floor(forecast.available.length / 2)];
    const start = forecast.available[0].date;
    const end = forecast.available[forecast.available.length - 1].date;
    return `<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>NEA climate forecast</title><style>
      *{box-sizing:border-box}body{margin:0;padding:16px;background:#f6f8f6;color:#25372d;font:14px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}h1,h2,p{margin-top:0}h1{font-size:23px;margin-bottom:4px}h2{font-size:16px;margin-bottom:8px}.subtle,figcaption{color:#65766b;font-size:12px}.card{background:#fff;border:1px solid #e0e8e2;border-radius:12px;padding:16px;margin:0 0 12px;box-shadow:0 2px 8px #24372b08}.metrics{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px}.metric{padding:10px;border-radius:9px;background:#f4f8f5}.metric strong{display:block;font-size:18px;color:#275f45}.metric span{font-size:11px;color:#64746a}.chart{width:100%;overflow:hidden}.chart svg{width:100%;height:auto;display:block}.legend{display:flex;gap:14px;flex-wrap:wrap;color:#607167;font-size:11px;margin-top:8px}.swatch{display:inline-block;width:10px;height:10px;border-radius:3px;margin-right:4px}.two-col{display:grid;grid-template-columns:1fr 1fr;gap:12px}.callout{border-left:3px solid #4b8db2;padding-left:11px}.warning{color:#75551c;background:#fff8e8;padding:10px;border-radius:8px;font-size:12px}@media(max-width:620px){body{padding:10px}.metrics{grid-template-columns:repeat(2,minmax(0,1fr))}.two-col{grid-template-columns:1fr}h1{font-size:19px}}</style><body>
      <section class="card"><p class="subtle">NEA HISTORICAL STATION · EXPLORATORY MODEL</p><h1>${escapeHtml(target.label)} forecast · ${escapeHtml(area)}</h1><p class="subtle">${escapeHtml(station.name)} (${escapeHtml(station.code)}) · daily records used ${start} to ${end}</p><div class="metrics"><div class="metric"><strong>${format(forecast.forecast[0].value, target.precision)} ${target.unit}</strong><span>Day 1 estimate</span></div><div class="metric"><strong>${format(peakDay.value, target.precision)} ${target.unit}</strong><span>Highest 14-day estimate · ${peakDay.date}</span></div><div class="metric"><strong>${format(summary.mae, target.precision)} ${target.unit}</strong><span>Holdout MAE · ${forecast.test.length} days</span></div><div class="metric"><strong>${summary.mape === null ? '—' : `${format(summary.mape, 1)}%`}</strong><span>MAPE · ${summary.nonZeroDays} non-zero days</span></div></div><p class="subtle" style="margin:10px 0 0">RMSE ${format(summary.rmse, target.precision)} ${target.unit}. Validation is measured on the last ${forecast.test.length} observed days; MAPE excludes zero-valued observations.</p></section>
      <section class="card"><h2>Historical observations, validation and next 14 days</h2><div class="chart">${lineChart(forecast, target)}</div><div class="legend"><span><i class="swatch" style="background:#263f32"></i>Observed history</span><span><i class="swatch" style="background:#d39021"></i>Held-out model estimate</span><span><i class="swatch" style="background:#2479b8"></i>14-day estimate</span><span><i class="swatch" style="background:#71b5e8"></i>Approx. 95% residual band</span></div><p class="subtle" style="margin:10px 0 0">The graph shows the latest year for readability. Holdout validation uses the last ${forecast.test.length} records; the future prediction starts after the latest observed day.</p></section>
      <section class="two-col"><div class="card"><h2>Exploratory data: monthly average</h2><div class="chart">${monthlyChart(forecast.available, target)}</div><p class="subtle">Average ${escapeHtml(target.label.toLowerCase())} grouped by calendar month across the available historical record.</p></div><div class="card"><h2>Exploratory data: distribution</h2><div class="chart">${distributionChart(forecast.available, target)}</div><p class="subtle">Frequency of observed values in equal-width bins. Historical mean ${format(mean, target.precision)} ${target.unit}; median ${format(median, target.precision)} ${target.unit}.</p></div></section>
      <section class="card"><h2>Seasonal pattern and interpretation</h2><div class="chart">${componentChart(forecast, target)}</div><p class="callout">The model is a browser-side additive regression with a linear time trend, annual seasonal harmonics and a weekly pattern. This compact method is not Facebook Prophet and does not use AI. The component chart summarizes recent month-of-year differences from the recent mean; it is descriptive, not causal evidence.</p><p class="warning">This is an exploratory statistical estimate from a representative NEA station, not an official NEA forecast or a site-specific warning. Rainfall is intermittent, so the model may smooth short intense storms. The residual band summarizes model errors and is not a calibrated guarantee of coverage.</p><p class="subtle">Source: <a href="https://www.weather.gov.sg/climate-historical-daily/" target="_blank" rel="noopener">NEA Historical Daily Records</a>. Local 14-day values are derived from the bundled observations. Record mean ${format(mean, target.precision)} ${target.unit}.</p></section>
      <section class="card" aria-labelledby="model-accuracy-title"><h2 id="model-accuracy-title">Model accuracy and validation</h2><p>These scores compare predictions with observed values in the last ${forecast.test.length} days, which were held out from model fitting.</p><div class="metrics"><div class="metric"><strong>${format(summary.mae, target.precision)} ${target.unit}</strong><span>MAE · average absolute error</span></div><div class="metric"><strong>${format(summary.rmse, target.precision)} ${target.unit}</strong><span>RMSE · penalizes larger errors</span></div><div class="metric"><strong>${summary.mape === null ? '—' : `${format(summary.mape, 1)}%`}</strong><span>MAPE · non-zero observations only</span></div><div class="metric"><strong>${formatRSquared(summary.rSquared)}</strong><span>R² · compared with holdout mean</span></div></div><p class="subtle" style="margin:10px 0 0">Lower MAE, RMSE and MAPE indicate smaller errors. R² of 1 is a perfect match, 0 is no better than predicting the holdout mean, and a negative value is worse than that baseline. MAPE is excluded for zero-valued observations and can be misleading for intermittent rainfall; it is an error measure, not a simple “percent accurate” score. Results describe this station and validation period only, not guaranteed future accuracy.</p></section><script>parent.postMessage({type:'nea-climate-report-ready'},'*')</script>
      </body></html>`;
  }

  async function availableTargets(stationCode) {
    const data = await loadHistory();
    const records = data.stations[stationCode];
    if (!Array.isArray(records)) throw new Error(`No bundled NEA history is available for station ${stationCode}.`);
    return Object.entries(targetIndexes)
      .filter(([, index]) => records.reduce((count, row) => count + Number(Number.isFinite(row[index])), 0) >= 180)
      .map(([name]) => name);
  }

  async function generate({ area, station, targetName }) {
    const target = targets[targetName];
    if (!target) throw new Error('Select a supported forecast variable.');
    const data = await loadHistory();
    const records = data.stations[station.code];
    if (!Array.isArray(records)) throw new Error(`No bundled NEA history is available for station ${station.code}.`);
    const index = targetIndexes[targetName];
    const supportedTargets = await availableTargets(station.code);
    if (!supportedTargets.includes(targetName)) throw new Error(`${target.label} is not available for station ${station.code}; choose an enabled variable.`);
    const forecast = makeForecast(records, { ...target, index });
    return reportHtml({ area, station, target, forecast });
  }

  window.ClimateForecast = { availableTargets, generate };
})();
