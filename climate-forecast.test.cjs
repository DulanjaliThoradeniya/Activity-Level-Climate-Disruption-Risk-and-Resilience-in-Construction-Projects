const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function makeSeries(days, valueAt, start = Date.UTC(2021, 0, 1)) {
  const rows = [];
  for (let index = 0; index < days; index += 1) {
    const date = new Date(start + index * 86400000).toISOString().slice(0, 10);
    rows.push([date, valueAt(index)]);
  }
  return rows;
}

async function run() {
  const observations = makeSeries(500, index => Math.max(0, 5 + Math.sin(index * Math.PI * 2 / 365) * 2 + (index % 9 === 0 ? 3 : 0)));
  let historyForTest = observations;
  let estimatedForTest = {};
  const latestDate = observations[observations.length - 1][0];
  const forecast = Array.from({ length: 14 }, (_, index) => {
    const date = new Date(Date.parse(`${latestDate}T00:00:00Z`) + (index + 1) * 86400000).toISOString().slice(0, 10);
    return { date, value: 2, lower: 0, upper: 5, rain_probability: 0.4 };
  });
  const metrics = {
    n: 70,
    mae: 3,
    rmse: 5,
    mean_bias: -1,
    heavy_rain: { n: 4, mae: 8, rmse: 9 },
    occurrence: { n: 70, rainy_days: 20, threshold: 0.35, accuracy: 0.7, precision: 0.4, recall: 0.5, f1: 0.44, false_positive_rate: 0.2, brier: 0.2 }
  };
  const baselines = {
    no_rain: { ...metrics, mae: 3.2, rmse: 5.2 },
    seasonal: { ...metrics, mae: 4, rmse: 6 },
    persistence: { ...metrics, mae: 5, rmse: 7 }
  };
  const leadMetrics = { n: 15, mae: 2, rmse: 3, baselines };
  const leadCandidates = {
    tuned_pipeline: { n: 15, mae: 1.8, rmse: 2.8 },
    xgboost: { n: 15, mae: 1.8, rmse: 2.8 },
    xgboost_original: { n: 15, mae: 2.2, rmse: 3.2 },
    prophet: { n: 15, mae: 2.1, rmse: 3.1 }
  };
  const modelForecasts = {
    schemaVersion: 1,
    stations: {
      S24: {
        targets: {
          rainfall: {
            model: 'xgboost',
            model_name: 'Two-stage XGBoost',
            model_description: 'Python-trained, two-stage XGBoost rainfall model.',
            data_end: latestDate,
            generated_at: '2026-10-26T03:20:42Z',
            diagnostics_url: 'data/climate-rainfall-diagnostics-S24.png',
            forecast,
            backtest: observations.slice(-14).map(([date, actual], index) => ({
              date,
              actual,
              predicted: 1 + index / 10
            })),
            validation: {
              ...metrics,
              folds: [{ origin: '2022-05-15', start: '2022-05-16', end: '2022-05-29', n: 14 }],
              by_lead: {
                days_1_3: leadMetrics,
                days_4_7: leadMetrics,
                days_8_14: leadMetrics
              },
              candidate_by_lead: {
                days_1_3: leadCandidates,
                days_4_7: leadCandidates,
                days_8_14: leadCandidates
              },
              model_selection: {
                selected: 'tuned_pipeline',
                selected_name: 'Two-stage XGBoost',
                evaluation_profile: 'extended_changi',
                tuned_candidate: { classifier: 'xgboost', threshold: 0.35 },
                best_tuned_candidates: [{
                  classifier: 'xgboost',
                  amount_model: 'xgboost',
                  calibration: 'sigmoid',
                  strategy: 'thresholded',
                  threshold: 0.35,
                  metrics
                }],
                validation_period: { start: '2024-10-01', end: '2025-09-29', origins: 26, target_pairs: 364, unique_dates: 364 },
                test_period: { start: '2025-10-01', end: '2026-09-29', origins: 26, target_pairs: 364, unique_dates: 364 }
              },
              interval: {
                nominal_coverage: 0.9,
                by_lead: {
                  days_1_3: { n: 15, empirical_coverage: 0.9 },
                  days_4_7: { n: 20, empirical_coverage: 0.85 },
                  days_8_14: { n: 35, empirical_coverage: 0.91 }
                }
              },
              baselines,
              comparison: {
                selected: 'xgboost',
                selection_rule: 'Select two-stage XGBoost by rolling MAE.',
                candidates: {
                  tuned_pipeline: metrics,
                  xgboost_original: { ...metrics, mae: 4 },
                  xgboost: metrics,
                  prophet: { ...metrics, mae: 4 },
                  no_rain: baselines.no_rain,
                  seasonal: baselines.seasonal,
                  persistence: baselines.persistence
                }
              }
            }
          }
        }
      }
    }
  };
  const savedResult = modelForecasts.stations.S24.targets.rainfall;
  const incompleteLstmResult = {
    ...savedResult,
    validation: { n: 0, mae: null, rmse: null }
  };
  const multiModelForecasts = {
    schemaVersion: 1,
    models: {
      xgboost: { stations: { S24: { targets: { rainfall: savedResult } } } },
      lightgbm: { stations: { S24: { targets: { rainfall: savedResult } } } },
      lstm: { stations: { S24: { targets: { rainfall: incompleteLstmResult } } } },
      tft: { stations: { S24: { targets: { rainfall: savedResult } } } },
      neuralprophet_new: { stations: { S24: { targets: { rainfall: savedResult } } } }
    }
  };
  const comparisonValidation = {
    ...savedResult.validation,
    data_split: {
      testing_count: 70,
      testing_start_date: '2026-01-01',
      testing_end_date: '2026-03-11'
    },
    by_horizon: {
      '14': { n: 5, mae: 2, rmse: 3, r2: -0.2 }
    }
  };
  for (const model of Object.values(multiModelForecasts.models)) {
    model.status = 'ready';
    model.stations.S24.targets.rainfall.validation = comparisonValidation;
  }
  const context = {
    window: { location: { protocol: 'https:' } },
    fetch: async url => {
      assert.match(url, /^data\/climate-/);
      return {
        ok: true,
        json: async () => {
          if (url.endsWith('climate-history.json')) {
            return { schemaVersion: 1, updatedThrough: '2022-05', stations: { S24: historyForTest }, estimatedValues: { S24: estimatedForTest } };
          }
          if (url.endsWith('climate-multi-model-forecasts.json')) {
            return multiModelForecasts;
          }
          return modelForecasts;
        }
      };
    }
  };
  const source = fs.readFileSync('climate-forecast.js', 'utf8');
  assert.doesNotMatch(source, /function fitModel/);
  vm.runInNewContext(fs.readFileSync('assessment-screen.js', 'utf8'), context.window);
  vm.runInNewContext(source, context);
  assert.equal(JSON.stringify(await context.window.ClimateForecast.availableTargets('S24')), JSON.stringify(['rainfall']));
  const html = await context.window.ClimateForecast.generate({
    area: 'Test Area',
    station: { code: 'S24', name: 'Test Station' },
    targetName: 'rainfall'
  });
  const savedLightgbm = multiModelForecasts.models.lightgbm.stations.S24.targets.rainfall;
  const firstHistoricalDay = Date.UTC(2006, 0, 1);
  const lastHistoricalDay = Date.UTC(2026, 10, 14);
  historyForTest = makeSeries((lastHistoricalDay - firstHistoricalDay) / 86400000 + 1, index => index % 7, firstHistoricalDay);
  const trainingCount = Math.floor(historyForTest.length * 0.8);
  const testingCount = historyForTest.length - trainingCount;
  const requestedForecast = Array.from({ length: 14 }, (_, index) => ({
    date: new Date(lastHistoricalDay + (index + 1) * 86400000).toISOString().slice(0, 10),
    value: 2, lower: 0, upper: 5
  }));
  estimatedForTest = { [historyForTest[20][0]]: ['rainfall'] };
  multiModelForecasts.models.lightgbm.stations.S24.targets.rainfall = {
    ...savedLightgbm,
    data_end: '2026-11-14',
    forecast: requestedForecast,
    backtest: historyForTest.slice(trainingCount).map(([date, actual]) => ({ date, actual, predicted: 2 })),
    validation: {
      ...comparisonValidation,
      forecast_origins: 109,
      occurrence: { threshold_mm: 1, precision: 0.4, recall: 0.5, f1: 0.44, false_alarm_rate: 0.2 },
      heavy_rain_occurrence: { threshold_mm: 20 },
      data_split: {
        training_count: trainingCount,
        testing_count: testingCount,
        training_start_date: historyForTest[0][0],
        training_end_date: historyForTest[trainingCount - 1][0],
        testing_start_date: historyForTest[trainingCount][0],
        testing_end_date: historyForTest[historyForTest.length - 1][0]
      }
    }
  };
  await context.window.ClimateForecast.refresh();
  const splitHtml = await context.window.ClimateForecast.generate({
    area: 'Test Area', station: { code: 'S24', name: 'Test Station' },
    targetName: 'rainfall', modelId: 'lightgbm'
  });
  const splitChart = splitHtml.match(/<svg viewBox="0 0 900 310"[\s\S]*?<\/svg>/)[0];
  assert.match(splitChart, /data-region="training"/);
  assert.match(splitChart, /data-region="testing"/);
  assert.match(splitChart, /data-region="forecast"/);
  assert.ok(splitChart.includes(`${trainingCount} observations`));
  assert.ok(splitChart.includes(`${testingCount} observations`));
  assert.match(splitChart, /2006-01-01/);
  assert.match(splitChart, /2026-11-14/);
  assert.match(splitChart, /2026-11-15/);
  assert.match(splitChart, /2026-11-28/);
  assert.match(splitHtml, /109 non-overlapping 14-day forecast origins/);
  assert.match(splitHtml, /tabindex="0" role="region" aria-label="Scrollable forecast chart"/);
  assert.match(splitChart, /min-width:700px/);
  assert.match(splitHtml, /Scroll horizontally on narrow screens/);
  assert.match(splitHtml, /wet day ≥1 mm/);
  assert.match(splitHtml, /precision 0\.40 · recall 0\.50/);
  assert.match(splitHtml, /days ≥20 mm/);
  assert.equal((splitHtml.match(/These estimates are not NEA measurements/g) || []).length, 1);
  assert.match(splitHtml, /split chart and model inputs exclude estimated values/);
  assert.doesNotMatch(splitHtml, /The graph shows the latest year for readability/);
  assert.doesNotMatch(splitHtml, /threshold 0\.00/);
  assert.doesNotMatch(splitHtml, /Nominal 90% rolling-validation residual interval/);
  const forecastRegion = splitChart.match(/data-region="forecast"[^>]*width="([\d.]+)"/);
  assert.ok(Number(forecastRegion[1]) >= 100, 'forecast is visible in a separate expanded panel');
  multiModelForecasts.models.lightgbm.stations.S24.targets.rainfall = savedLightgbm;
  historyForTest = observations;
  estimatedForTest = {};
  await context.window.ClimateForecast.refresh();
  assert.match(html, /14-day forecast/);
  const chart = html.match(/<svg viewBox="0 0 900 310"[\s\S]*?<\/svg>/)[0];
  const forecastPath = chart.match(/data-series="forecast" d="([^"]+)"/)[1];
  assert.equal((forecastPath.match(/[ML]/g) || []).length, 14, 'forecast contains exactly the 14 model points, without an extra bridge');
  const forecastXs = [...forecastPath.matchAll(/[ML](-?[\d.]+),/g)].map(match => Number(match[1]));
  assert.ok(forecastXs.every((x, index) => !index || x > forecastXs[index - 1]), 'forecast line never doubles back');
  assert.match(html, /forecast window \d{4}-\d{2}-\d{2} to \d{4}-\d{2}-\d{2}/);
  assert.match(html, /Final test MAE/);
  assert.match(html, /RMSE/);
  assert.match(html, /Model accuracy and validation/);
  assert.match(html, /No-rain baseline/);
  assert.match(html, /Seasonal baseline/);
  assert.match(html, /Persistence baseline/);
  assert.match(html, /Two-stage XGBoost/);
  assert.match(html, /Occurrence accuracy/);
  assert.match(html, /Prophet RMSE/);
  assert.match(html, /XGBoost RMSE/);
  assert.match(html, /Final test/);
  assert.match(html, /26 validation origins/);
  assert.match(html, /Top tuned candidates \(validation only\)/);
  assert.match(html, /Wet-day recall/);
  assert.match(html, /Nominal 90% lead-window intervals from rolling-validation residual quantiles/);
  assert.match(html, /Changi rainfall diagnostic plots/);
  assert.match(html, /Rainfall MAPE is intentionally omitted/);
  assert.match(html, /Days 1–3/);
  assert.match(html, /Days 4–7/);
  assert.match(html, /Days 8–14/);
  assert.match(html, /Exploratory data: monthly average/);
  assert.match(html, /Seasonal pattern and interpretation/);
  assert.match(html, /Python model held-out evaluation/);
  assert.match(html, /nea-climate-report-ready/);
  observations.push([new Date(Date.parse(`${latestDate}T00:00:00Z`) + 30 * 86400000).toISOString().slice(0, 10), 123]);
  await context.window.ClimateForecast.refresh();
  const overlappingHtml = await context.window.ClimateForecast.generate({
    area: 'Test Area', station: { code: 'S24', name: 'Test Station' }, targetName: 'rainfall'
  });
  const overlappingChart = overlappingHtml.match(/<svg viewBox="0 0 900 310"[\s\S]*?<\/svg>/)[0];
  const overlappingPath = overlappingChart.match(/data-series="forecast" d="([^"]+)"/)[1];
  const overlappingXs = [...overlappingPath.matchAll(/[ML](-?[\d.]+),/g)].map(match => Number(match[1]));
  assert.equal(overlappingXs.length, 14);
  assert.ok(overlappingXs.every((x, index) => !index || x > overlappingXs[index - 1]), 'newer history must not create a backward bridge');
  await assert.rejects(
    context.window.ClimateForecast.generate({
      area: 'Test Area',
      station: { code: 'S24', name: 'Test Station' },
      targetName: 'maximum_temperature'
    }),
    /not available/
  );
  modelForecasts.stations.S24.targets.rainfall.model_name = 'Prophet';
  modelForecasts.stations.S24.targets.rainfall.validation = { n: 70, mae: 3, rmse: 5 };
  await context.window.ClimateForecast.refresh();
  const prophetHtml = await context.window.ClimateForecast.generate({
    area: 'Test Area',
    station: { code: 'S24', name: 'Test Station' },
    targetName: 'rainfall'
  });
  assert.match(prophetHtml, /PROPHET MODEL/);
  assert.match(prophetHtml, /Daily forecast and 95% model interval/);
  assert.match(prophetHtml, /Prophet was trained on NEA daily historical observations/);
  for (const modelId of ['lightgbm', 'lstm', 'tft', 'neuralprophet_new']) {
    const savedHtml = await context.window.ClimateForecast.generate({
      area: 'Test Area',
      station: { code: 'S24', name: 'Test Station' },
      targetName: 'rainfall',
      modelId
    });
    assert.match(savedHtml, /14-day forecast/);
  }
  const comparisonHtml = await context.window.ClimateForecast.generate({
    area: 'Test Area',
    station: { code: 'S24', name: 'Test Station' },
    targetName: 'rainfall',
    modelId: 'comparison'
  });
  assert.match(comparisonHtml, /14-day forecast/);
  assert.match(comparisonHtml, /XGBoost/);
  assert.match(comparisonHtml, /NeuralProphet/);
  assert.match(comparisonHtml, /Experimental comparison structure/);
  assert.match(comparisonHtml, /Rain occurrence F1/);
  assert.match(comparisonHtml, /Rain occurrence Brier score/);

  let localLoads = 0;
  const fileContext = {
    window: { location: { protocol: 'file:' } },
    document: {
      createElement: () => ({ remove() {} }),
      head: {
        append(script) {
          assert.match(script.src, /^data\/climate-offline\.js\?/);
          localLoads += 1;
          fileContext.window.CResistClimateOffline = {
            'data/climate-history.json': { schemaVersion: 1, stations: { S24: observations } },
            'data/climate-forecasts.json': modelForecasts,
            'data/climate-multi-model-forecasts.json': multiModelForecasts
          };
          script.onload();
        }
      }
    },
    fetch: async () => { throw new Error('Direct-file forecasts must not fetch published data.'); }
  };
  vm.runInNewContext(source, fileContext);
  assert.equal(JSON.stringify(await fileContext.window.ClimateForecast.availableTargets('S24')), JSON.stringify(['rainfall']));
  assert.equal(localLoads, 1, 'all forecast data comes from one local bundle');
  await fileContext.window.ClimateForecast.refresh();
  assert.equal(localLoads, 2, 'refresh reloads the local bundle');
  const missingBundleContext = {
    window: { location: { protocol: 'file:' } },
    document: {
      createElement: () => ({ remove() {} }),
      head: { append: script => script.onerror() }
    }
  };
  vm.runInNewContext(source, missingBundleContext);
  await assert.rejects(missingBundleContext.window.ClimateForecast.availableTargets('S24'), /local climate data bundle could not be loaded/);
}

run().then(() => console.log('Browser forecast checks passed.')).catch(error => {
  console.error(error);
  process.exitCode = 1;
});
