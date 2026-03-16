/**
 * Tests for dashboard/costs.html — Cost Forecast Dashboard
 *
 * Validates HTML structure, JS functions, chart rendering,
 * export logic, what-if simulator, and budget tracking.
 */

const fs = require("fs");
const path = require("path");
const assert = require("assert");
const { describe, it } = require("node:test");

const htmlPath = path.join(__dirname, "..", "dashboard", "costs.html");
const html = fs.readFileSync(htmlPath, "utf8");

describe("Cost Forecast Dashboard HTML", () => {
  it("contains required structural elements", () => {
    assert.ok(html.includes('id="summaryCards"'), "missing summary cards");
    assert.ok(html.includes('id="costChart"'), "missing cost chart canvas");
    assert.ok(html.includes('id="budgetGauge"'), "missing budget gauge");
    assert.ok(html.includes('id="alertsList"'), "missing alerts list");
    assert.ok(html.includes('id="modelChart"'), "missing model chart");
    assert.ok(html.includes('id="modelTable"'), "missing model table");
    assert.ok(html.includes('id="dailyTable"'), "missing daily table");
  });

  it("has forecast controls", () => {
    assert.ok(html.includes('id="forecastDays"'), "missing forecast days selector");
    assert.ok(html.includes('id="budgetInput"'), "missing budget input");
    assert.ok(html.includes('id="historyDays"'), "missing history days selector");
  });

  it("has what-if simulator controls", () => {
    assert.ok(html.includes('id="trafficSlider"'), "missing traffic slider");
    assert.ok(html.includes('id="modelSwitch"'), "missing model switch select");
    assert.ok(html.includes('id="cacheSlider"'), "missing cache slider");
    assert.ok(html.includes('id="whatifResult"'), "missing what-if result");
    assert.ok(html.includes("updateWhatIf()"), "missing updateWhatIf call");
  });

  it("has navigation links", () => {
    assert.ok(html.includes('href="index.html"'), "missing link to sessions");
    assert.ok(html.includes('href="errors.html"'), "missing link to errors");
    assert.ok(html.includes('href="scorecards.html"'), "missing link to scorecards");
    assert.ok(html.includes('href="waterfall.html"'), "missing link to waterfall");
  });
});

describe("Model Pricing Data", () => {
  it("defines pricing for all models", () => {
    const models = ["gpt-4o", "gpt-4o-mini", "gpt-3.5-turbo", "claude-3-opus", "claude-3-sonnet", "claude-3-haiku"];
    models.forEach(m => assert.ok(html.includes(`'${m}'`), `missing model ${m}`));
  });

  it("includes input and output pricing", () => {
    const inputMatch = html.match(/input:\s*[\d.]+/g);
    const outputMatch = html.match(/output:\s*[\d.]+/g);
    assert.ok(inputMatch && inputMatch.length >= 6, "missing input pricing");
    assert.ok(outputMatch && outputMatch.length >= 6, "missing output pricing");
  });

  it("assigns colors to models", () => {
    const colorMatch = html.match(/color:\s*'#[0-9a-f]{6}'/g);
    assert.ok(colorMatch && colorMatch.length >= 6, "missing model colors");
  });
});

describe("Chart Functions", () => {
  it("defines renderCostChart function", () => {
    assert.ok(html.includes("function renderCostChart()"), "missing renderCostChart");
  });

  it("defines renderModelBreakdown function", () => {
    assert.ok(html.includes("function renderModelBreakdown()"), "missing renderModelBreakdown");
  });

  it("defines renderBudgetGauge function", () => {
    assert.ok(html.includes("function renderBudgetGauge()"), "missing renderBudgetGauge");
  });

  it("defines renderSummaryCards function", () => {
    assert.ok(html.includes("function renderSummaryCards()"), "missing renderSummaryCards");
  });

  it("defines renderDailyTable function", () => {
    assert.ok(html.includes("function renderDailyTable()"), "missing renderDailyTable");
  });

  it("defines renderAlerts function", () => {
    assert.ok(html.includes("function renderAlerts()"), "missing renderAlerts");
  });
});

describe("Forecast Logic", () => {
  it("defines generateData function", () => {
    assert.ok(html.includes("function generateData()"), "missing generateData");
  });

  it("defines computeForecast function", () => {
    assert.ok(html.includes("function computeForecast()"), "missing computeForecast");
  });

  it("uses linear regression for forecasting", () => {
    assert.ok(html.includes("slope"), "missing slope in regression");
    assert.ok(html.includes("intercept"), "missing intercept in regression");
  });

  it("computes confidence intervals", () => {
    assert.ok(html.includes("1.96"), "missing 1.96 z-score for 95% CI");
    assert.ok(html.includes("upper"), "missing upper bound");
    assert.ok(html.includes("lower"), "missing lower bound");
  });

  it("supports multiple forecast horizons", () => {
    ["7", "14", "30", "60", "90"].forEach(d => {
      assert.ok(html.includes(`value="${d}"`), `missing ${d}-day option`);
    });
  });
});

describe("Export Functions", () => {
  it("defines JSON export", () => {
    assert.ok(html.includes("function exportJSON()"), "missing exportJSON");
    assert.ok(html.includes("agentlens-cost-forecast.json"), "missing JSON filename");
  });

  it("defines CSV export", () => {
    assert.ok(html.includes("function exportCSV()"), "missing exportCSV");
    assert.ok(html.includes("agentlens-cost-forecast.csv"), "missing CSV filename");
  });

  it("defines PNG export", () => {
    assert.ok(html.includes("function exportPNG()"), "missing exportPNG");
    assert.ok(html.includes("agentlens-cost-chart.png"), "missing PNG filename");
  });

  it("has export dropdown menu", () => {
    assert.ok(html.includes('id="exportMenu"'), "missing export menu");
    assert.ok(html.includes("toggleExport()"), "missing toggleExport");
  });
});

describe("Budget Tracking", () => {
  it("renders budget progress bar", () => {
    assert.ok(html.includes("budget-track"), "missing budget track");
    assert.ok(html.includes("budget-fill"), "missing budget fill");
  });

  it("shows expected spend marker", () => {
    assert.ok(html.includes("budget-marker"), "missing budget marker");
    assert.ok(html.includes("Expected"), "missing expected label");
  });
});

describe("What-If Simulator", () => {
  it("supports traffic change scenarios", () => {
    assert.ok(html.includes('min="-50"'), "missing negative traffic range");
    assert.ok(html.includes('max="100"'), "missing max traffic range");
  });

  it("supports model switching", () => {
    assert.ok(html.includes("gpt-3.5-turbo"), "missing GPT-3.5 option");
    assert.ok(html.includes("gpt-4o-mini"), "missing GPT-4o Mini option");
    assert.ok(html.includes("claude-3-haiku"), "missing Haiku option");
  });

  it("supports cache hit rate simulation", () => {
    assert.ok(html.includes('max="80"'), "missing max cache rate");
    assert.ok(html.includes("cacheRate"), "missing cache rate variable");
  });

  it("shows savings or increase indicator", () => {
    assert.ok(html.includes("whatif-savings"), "missing savings class");
    assert.ok(html.includes("whatif-increase"), "missing increase class");
  });
});

describe("Responsive Design", () => {
  it("has mobile breakpoints", () => {
    assert.ok(html.includes("max-width: 900px"), "missing tablet breakpoint");
    assert.ok(html.includes("max-width: 600px"), "missing mobile breakpoint");
  });
});

describe("Dark Theme Integration", () => {
  it("uses shared stylesheet", () => {
    assert.ok(html.includes('href="styles.css"'), "missing styles.css link");
  });

  it("uses CSS custom properties", () => {
    assert.ok(html.includes("var(--bg-secondary)"), "missing bg-secondary var");
    assert.ok(html.includes("var(--border)"), "missing border var");
    assert.ok(html.includes("var(--accent)"), "missing accent var");
  });
});
