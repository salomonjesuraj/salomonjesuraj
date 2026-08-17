/**
 * App - main orchestrator for the Infusion Trading Command Center
 * Initializes all panels and manages lifecycle
 */
import { Header } from './header.js';
import { Footer } from './footer.js?v=6.4.4-summary-chip-polish';
import { CockpitPanel } from './cockpit.js?v=7.0.0-signal-cockpit';
import { ScannerPanel } from './scanner.js?v=13.12-vcp-fo-ban';
import { SignalBoard } from './signals.js?v=2.5.1-options-first-hybrid';
import { WatchlistPanel } from './watchlist.js';
import { SectorPanel } from './sectors.js';
import { AnalyticsPanel } from './analytics.js?v=6.4.4-summary-chip-polish';
import { AlertLog } from './alerts.js?v=6.4.4-summary-chip-polish';
import { ChartPanel } from './chart.js';
import { QuickControls } from './quick-controls.js?v=2.4.0-hybrid-scanner';
import { MarketHealthTop } from './market-health-top.js?v=breadth-health-1';
import { MarketTicker } from './market-ticker.js?v=vix-multiplier-1';
import { OptionCockpit } from './options.js?v=6.4.4-summary-chip-polish';
import { SectorRibbon } from './sector-ribbon.js?v=breadth-health-1';
import { WorkbenchTabs } from './workbench-tabs.js?v=2.4.0-hybrid-scanner';
import { ScannerInsight } from './scanner-insight.js?v=noise-reduction-1';
import { TriggerPanel } from './triggers.js?v=3.1.0-pro-terminal-ui';
import { AuthTokenDialog } from './auth-token-dialog.js?v=3.2.0-upstox-token-dialog';
import { RiskConsole } from './risk-console.js?v=6.4.4-summary-chip-polish';
import { NewsPanel } from './news-panel.js?v=6.4.4-summary-chip-polish';
import { EventsPanel } from './events-panel.js?v=5.4.0-event-gate-ui';
import { JournalPanel } from './journal-panel.js?v=6.4.4-summary-chip-polish';
import { ExecutionPanel } from './execution-panel.js?v=6.4.4-summary-chip-polish';
import { SafetyPanel } from './safety-panel.js?v=6.4.4-summary-chip-polish';
import { SectionControls } from './section-controls.js?v=6.4.4-summary-chip-polish';
import { OptionsAnalyticsPanel } from './options-analytics-panel.js?v=8.0.0-phase-d';
import { StrategySelectorPanel } from './strategy-selector-panel.js?v=13.6-dashboard';
import { OptimizerPanel } from './optimizer-panel.js?v=ml-classifier-1';
import { AiQueryPanel } from './ai-query-panel.js?v=8.0.0-phase-d';
import { initModeSwitch } from './mode-switch.js?v=8.0.0-new-shell';
import { CockpitV2Panel } from './cockpit-v2.js?v=8.0.0-new-shell';
import { WatchStripV2Panel } from './watch-strip-v2.js?v=8.0.0-new-shell';
import { ScannerV2Panel } from './scanner-v2.js?v=noise-reduction-2';
import { initRailV2 } from './rail-v2.js?v=8.0.0-new-shell';
import { TrackRecordV2Panel } from './track-record-v2.js?v=8.0.0-new-shell';
import { SignalAlertV2 } from './signal-alert-v2.js?v=8.0.0-new-shell';
import { IntegrityPanelV2 } from './integrity-panel-v2.js?v=8.0.0-new-shell';

class InfusionApp {
  constructor() {
    this._panels = [];
  }

  init() {
    console.log('[Infusion] Initializing Command Center...');

    // Phase N1: Classic/New shell toggle. The attribute is already set
    // correctly by index.html's anti-FOUC inline script before this file
    // even loads (same precedent as theme.js, which is never explicitly
    // .init()'d here either) -- this just wires the always-visible switch
    // button's click handler.
    initModeSwitch();

    const sectionControls = new SectionControls(document);
    sectionControls.init();
    this._panels.push(sectionControls);

    const authTokenDialog = new AuthTokenDialog();
    authTokenDialog.init();
    this._panels.push(authTokenDialog);

    // Header
    const header = new Header(document.getElementById('header'));
    header.init();
    this._panels.push(header);

    // Quick Controls Bar
    const ticker = new MarketTicker(document.getElementById('marketTicker'));
    ticker.init();
    this._panels.push(ticker);

    const marketHealthTop = new MarketHealthTop(document.getElementById('marketHealthTop'));
    marketHealthTop.init();
    this._panels.push(marketHealthTop);

    const sectorRibbon = new SectorRibbon(document.getElementById('sectorRibbon'));
    sectorRibbon.init();
    this._panels.push(sectorRibbon);

    // Quick Controls Bar
    const qc = new QuickControls(document.getElementById('quickControls'));
    qc.init();
    this._panels.push(qc);

    const riskConsole = new RiskConsole(document.getElementById('riskConsole'));
    riskConsole.init();
    this._panels.push(riskConsole);

    const workbenchTabs = new WorkbenchTabs(document.getElementById('workbenchTabs'));
    workbenchTabs.init();
    this._panels.push(workbenchTabs);

    // Footer
    const footer = new Footer(document.getElementById('footer'));
    footer.init();
    this._panels.push(footer);

    // Signal Cockpit — the default view. Mounted before everything else so
    // it paints first.
    const cockpit = new CockpitPanel(document.getElementById('signalCockpit'));
    cockpit.init();
    this._panels.push(cockpit);

    // New shell (Phase N3) -- mounted unconditionally alongside Classic's
    // panels, same reasoning as everywhere else in this rollout: both
    // shells stay live at all times so the toggle is instant, and
    // api.js's subscribe() already dedupes polling per endpoint so this
    // costs nothing extra on the backend even while New is hidden.
    const cockpitV2 = new CockpitV2Panel(document.getElementById('cockpitV2'));
    cockpitV2.init();
    this._panels.push(cockpitV2);

    const watchStripV2 = new WatchStripV2Panel(document.getElementById('watchStripV2'));
    watchStripV2.init();
    this._panels.push(watchStripV2);

    const scannerV2 = new ScannerV2Panel(document.getElementById('scannerV2'));
    scannerV2.init();
    this._panels.push(scannerV2);

    const trackRecordV2 = new TrackRecordV2Panel(document.getElementById('trackRecordV2'));
    trackRecordV2.init();
    this._panels.push(trackRecordV2);

    const signalAlertV2 = new SignalAlertV2();
    signalAlertV2.init();
    this._panels.push(signalAlertV2);

    // Phase N5: left rail -- a SECOND instance of each existing panel class,
    // pointed at New shell's own *V2 containers, not a relocated DOM node
    // (see index.html's comment on #newShell for why: Classic's own tabs
    // must never end up empty because New "borrowed" their element).
    // Confirmed none of these 15 classes reach for a hardcoded
    // document.getElementById internally (grepped before writing this) --
    // every one of them scopes exclusively to the container it's given, so
    // two independent instances are safe. Both instances share the same
    // api.js singleton, so this doesn't double any actual network polling.
    initRailV2();

    const watchlistV2 = new WatchlistPanel(document.getElementById('watchlistBodyV2'));
    watchlistV2.init();
    this._panels.push(watchlistV2);

    const optionCockpitV2 = new OptionCockpit(
      document.getElementById('optionCockpitBodyV2'),
      document.getElementById('optionStatusV2')
    );
    optionCockpitV2.init();
    this._panels.push(optionCockpitV2);

    const riskConsoleV2 = new RiskConsole(document.getElementById('riskConsoleV2'));
    riskConsoleV2.init();
    this._panels.push(riskConsoleV2);

    const scannerInsightV2 = new ScannerInsight(document.getElementById('scannerInsightPanelV2'));
    scannerInsightV2.init();
    this._panels.push(scannerInsightV2);

    const triggerPanelV2 = new TriggerPanel(document.getElementById('triggerBodyV2'));
    triggerPanelV2.init();
    this._panels.push(triggerPanelV2);

    const newsPanelV2 = new NewsPanel(document.getElementById('newsBodyV2'));
    newsPanelV2.init();
    this._panels.push(newsPanelV2);

    const eventsPanelV2 = new EventsPanel(document.getElementById('eventsBodyV2'));
    eventsPanelV2.init();
    this._panels.push(eventsPanelV2);

    const journalPanelV2 = new JournalPanel(document.getElementById('journalBodyV2'));
    journalPanelV2.init();
    this._panels.push(journalPanelV2);

    const executionPanelV2 = new ExecutionPanel(document.getElementById('executionBodyV2'));
    executionPanelV2.init();
    this._panels.push(executionPanelV2);

    const safetyPanelV2 = new SafetyPanel(document.getElementById('safetyBodyV2'));
    safetyPanelV2.init();
    this._panels.push(safetyPanelV2);

    const alertLogV2 = new AlertLog(document.getElementById('alertBodyV2'));
    alertLogV2.init();
    this._panels.push(alertLogV2);

    const analyticsV2 = new AnalyticsPanel(document.getElementById('analyticsBodyV2'));
    analyticsV2.init();
    this._panels.push(analyticsV2);

    const optionsAnalyticsPanelV2 = new OptionsAnalyticsPanel(document.getElementById('optionsAnalyticsPanelV2'));
    optionsAnalyticsPanelV2.init();
    this._panels.push(optionsAnalyticsPanelV2);

    const strategySelectorPanelV2 = new StrategySelectorPanel(document.getElementById('strategySelectorPanelV2'));
    strategySelectorPanelV2.init();
    this._panels.push(strategySelectorPanelV2);

    const optimizerPanelV2 = new OptimizerPanel(document.getElementById('optimizerPanelV2'));
    optimizerPanelV2.init();
    this._panels.push(optimizerPanelV2);

    const aiQueryPanelV2 = new AiQueryPanel(document.getElementById('aiQueryPanelV2'));
    aiQueryPanelV2.init();
    this._panels.push(aiQueryPanelV2);

    const integrityPanelV2 = new IntegrityPanelV2(document.getElementById('integrityPanelV2'));
    integrityPanelV2.init();
    this._panels.push(integrityPanelV2);

    // Scanner (Priority 1)
    const scanner = new ScannerPanel(document.getElementById('scannerPanel'));
    scanner.init();
    this._panels.push(scanner);

    const scannerInsight = new ScannerInsight(document.getElementById('scannerInsightPanel'));
    scannerInsight.init();
    this._panels.push(scannerInsight);

    // Signal Board (optional in hybrid layout)
    const signalBody = document.getElementById('signalBody');
    if (signalBody) {
      const signals = new SignalBoard(signalBody);
      signals.init();
      this._panels.push(signals);
    }

    // Watchlist (Priority 3)
    const watchlist = new WatchlistPanel(document.getElementById('watchlistBody'));
    watchlist.init();
    this._panels.push(watchlist);

    // Market Health
    const optionCockpit = new OptionCockpit(
      document.getElementById('optionCockpitBody'),
      document.getElementById('optionStatus')
    );
    optionCockpit.init();
    this._panels.push(optionCockpit);

    // Sectors (optional legacy panel)
    const sectorBody = document.getElementById('sectorBody');
    if (sectorBody) {
      const sectors = new SectorPanel(sectorBody);
      sectors.init();
      this._panels.push(sectors);
    }

    // Analytics (Priority 5)
    const analytics = new AnalyticsPanel(document.getElementById('analyticsBody'));
    analytics.init();
    this._panels.push(analytics);

    // Alert Log
    const alertLog = new AlertLog(document.getElementById('alertBody'));
    alertLog.init();
    this._panels.push(alertLog);

    const triggerPanel = new TriggerPanel(document.getElementById('triggerBody'));
    triggerPanel.init();
    this._panels.push(triggerPanel);

    const newsPanel = new NewsPanel(document.getElementById('newsBody'));
    newsPanel.init();
    this._panels.push(newsPanel);

    const eventsPanel = new EventsPanel(document.getElementById('eventsBody'));
    eventsPanel.init();
    this._panels.push(eventsPanel);

    const journalPanel = new JournalPanel(document.getElementById('journalBody'));
    journalPanel.init();
    this._panels.push(journalPanel);

    const executionPanel = new ExecutionPanel(document.getElementById('executionBody'));
    executionPanel.init();
    this._panels.push(executionPanel);

    const safetyPanel = new SafetyPanel(document.getElementById('safetyBody'));
    safetyPanel.init();
    this._panels.push(safetyPanel);

    // Phase D: Options Analytics / Optimizer / Ask Infusion -- surfaces
    // Phase 11/12 backend endpoints that had no UI until now.
    const optionsAnalyticsPanel = new OptionsAnalyticsPanel(document.getElementById('optionsAnalyticsPanel'));
    optionsAnalyticsPanel.init();
    this._panels.push(optionsAnalyticsPanel);

    // Phase 13.6 dashboard surfacing: ranked multi-leg strategy shortlist.
    const strategySelectorPanel = new StrategySelectorPanel(document.getElementById('strategySelectorPanel'));
    strategySelectorPanel.init();
    this._panels.push(strategySelectorPanel);

    const optimizerPanel = new OptimizerPanel(document.getElementById('optimizerPanel'));
    optimizerPanel.init();
    this._panels.push(optimizerPanel);

    const aiQueryPanel = new AiQueryPanel(document.getElementById('aiQueryPanel'));
    aiQueryPanel.init();
    this._panels.push(aiQueryPanel);

    // TradingView Chart
    const chart = new ChartPanel(
      document.getElementById('chartContainer'),
      document.getElementById('chartSymbol')
    );
    chart.init();
    this._panels.push(chart);

    // Listen for badge count updates
    document.addEventListener('signals:count', (e) => {
      const badge = document.getElementById('signalCount');
      if (badge) badge.textContent = e.detail;
    });
    document.addEventListener('watchlist:count', (e) => {
      const badge = document.getElementById('watchlistCount');
      if (badge) badge.textContent = e.detail;
    });

    // Keyboard shortcuts
    document.addEventListener('keydown', (e) => {
      if (e.target.tagName === 'INPUT' || e.target.tagName === 'SELECT') return;
      // Future: keyboard shortcuts for panel navigation
    });

    console.log('[Infusion] Command Center initialized.');
  }

  destroy() {
    this._panels.forEach(p => p.destroy());
  }
}

// Boot
document.addEventListener('DOMContentLoaded', () => {
  window.__infusion = new InfusionApp();
  window.__infusion.init();
});
