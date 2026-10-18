const data = JSON.parse(document.getElementById("progress-data").textContent);
const root = document.querySelector(".viz-root");
const charts = [];

function token(name) {
  return getComputedStyle(root).getPropertyValue(name).trim();
}

function topOfStack(datasets, index) {
  for (let d = datasets.length - 1; d >= 0; d--) {
    if (datasets[d].data[index] > 0) return d;
  }
  return -1;
}

function baseOptions() {
  const muted = token("--text-muted");
  const grid = token("--gridline");
  return {
    responsive: true,
    maintainAspectRatio: false,
    animation: false,
    interaction: { mode: "index", intersect: false },
    plugins: {
      legend: { position: "top", align: "start", labels: { color: token("--text-secondary"), boxWidth: 10, boxHeight: 10, usePointStyle: true, pointStyle: "rectRounded" } },
      tooltip: { backgroundColor: token("--surface-raised"), titleColor: token("--text-primary"), bodyColor: token("--text-secondary"),
                 borderColor: token("--border"), borderWidth: 1, padding: 10, boxPadding: 4, usePointStyle: true },
    },
    scales: {
      x: { grid: { display: false }, border: { color: token("--baseline") }, ticks: { color: muted } },
      y: { beginAtZero: true, grid: { color: grid }, border: { display: false }, ticks: { color: muted, precision: 0 } },
    },
  };
}

function weekly() {
  const canvas = document.getElementById("weekly-chart");
  if (!canvas) return;
  const surface = token("--surface-1");
  const datasets = data.categories
    .filter((c) => c in data.weekly)
    .map((c) => ({
      label: c,
      data: data.weekly[c],
      backgroundColor: token(`--series-${data.categories.indexOf(c) + 1}`),
      borderColor: surface,
      borderWidth: { top: 2 },
      borderSkipped: false,
      maxBarThickness: 24,
    }));
  datasets.forEach((ds, d) => {
    ds.borderRadius = (ctx) => (topOfStack(datasets, ctx.dataIndex) === d ? { topLeft: 4, topRight: 4 } : 0);
  });
  const options = baseOptions();
  options.scales.x.stacked = true;
  options.scales.y.stacked = true;
  options.plugins.tooltip.filter = (item) => item.raw > 0;
  charts.push(new Chart(canvas, { type: "bar", data: { labels: weekLabels(data.weekly.weeks), datasets }, options }));
}

function accuracy() {
  const canvas = document.getElementById("accuracy-chart");
  if (!canvas) return;
  const color = token("--series-1");
  const options = baseOptions();
  options.plugins.legend.display = false;
  options.scales.y.max = 100;
  options.scales.y.ticks.callback = (v) => `${v}%`;
  options.plugins.tooltip.callbacks = { label: (item) => `${data.accuracy[item.dataIndex].text}` };
  charts.push(new Chart(canvas, {
    type: "line",
    data: {
      labels: data.accuracy.map((m) => m.month),
      datasets: [{ data: data.accuracy.map((m) => Math.round(m.value * 100)), borderColor: color, backgroundColor: color,
                   borderWidth: 2, pointRadius: 4, pointHoverRadius: 6, pointBorderColor: token("--surface-1"), pointBorderWidth: 2, tension: 0 }],
    },
    options,
  }));
}

function weekLabels(weeks) {
  return weeks.map((w) => new Date(w + "T00:00:00").toLocaleDateString(undefined, { month: "short", day: "numeric" }));
}

function minutes() {
  const canvas = document.getElementById("minutes-chart");
  if (!canvas) return;
  const options = baseOptions();
  options.plugins.legend.display = false;
  options.plugins.tooltip.callbacks = { label: (item) => `${item.raw} minutes` };
  charts.push(new Chart(canvas, {
    type: "bar",
    data: {
      labels: weekLabels(data.minutes.weeks),
      datasets: [{ data: data.minutes.minutes, backgroundColor: token("--series-1"), maxBarThickness: 24,
                   borderRadius: { topLeft: 4, topRight: 4 }, borderSkipped: "start" }],
    },
    options,
  }));
}

function calibration() {
  const canvas = document.getElementById("calibration-chart");
  if (!canvas) return;
  const color = token("--series-1");
  const options = baseOptions();
  options.plugins.legend.display = false;
  options.scales.y.beginAtZero = false;
  options.scales.y.ticks.callback = (v) => `${v > 0 ? "+" : ""}${v}`;
  options.plugins.tooltip.callbacks = {
    label: (item) => `${item.raw > 0 ? "+" : ""}${item.raw} pts over ${data.calibration[item.dataIndex].count} predictions`,
  };
  charts.push(new Chart(canvas, {
    type: "line",
    data: {
      labels: data.calibration.map((m) => m.month),
      datasets: [{ data: data.calibration.map((m) => Math.round(m.gap * 100)), borderColor: color, backgroundColor: color,
                   borderWidth: 2, pointRadius: 4, pointHoverRadius: 6, pointBorderColor: token("--surface-1"), pointBorderWidth: 2 }],
    },
    options,
  }));
}

function render() {
  charts.splice(0).forEach((c) => c.destroy());
  weekly();
  minutes();
  accuracy();
  calibration();
}

window.addEventListener("DOMContentLoaded", render);
window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", render);
