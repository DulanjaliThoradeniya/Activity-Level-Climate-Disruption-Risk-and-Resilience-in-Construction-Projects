(function(root) {
  'use strict';

  const proxies = {
    S29: {code: 'S94', name: 'Pasir Ris Street 51', distanceKm: 2.63}
  };

  function hasCurrentForecast(models, station, today) {
    const start = Date.parse(`${today}T00:00:00Z`);
    if (!Number.isFinite(start)) return false;
    const dates = Array.from({length: 14}, (_, index) =>
      new Date(start + index * 86400000).toISOString().slice(0, 10));
    return Object.values(models || {}).some(model => {
      const result = model?.stations?.[station]?.targets?.rainfall;
      return typeof result?.validation?.mae === 'number' && Number.isFinite(result.validation.mae) &&
        result.validation.mae >= 0 && Array.isArray(result.forecast) && result.forecast.length === 14 &&
        result.forecast.every((point, index) => point.date === dates[index] &&
          typeof point.value === 'number' && Number.isFinite(point.value) && point.value >= 0);
    });
  }

  function resolve(models, station, today) {
    const proxy = proxies[station];
    if (!proxy || hasCurrentForecast(models, station, today) || !hasCurrentForecast(models, proxy.code, today)) return null;
    return proxy;
  }

  root.CResistRainfallStation = {resolve, hasCurrentForecast};
})(typeof globalThis === 'object' ? globalThis : this);
