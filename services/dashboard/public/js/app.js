/**
 * App - main orchestrator for the Infusion Trading Command Center
 * Initializes all panels and manages lifecycle
 */
import { Header } from './header.js';
import { Footer } from './footer.js?v=6.4.4-summary-chip-polish';
import { CockpitPanel } from './cockpit.js?v=7.0.0-signal-cockpit';
import { ScannerPanel } from './scanner.js?v=6.4.4-summary-chip-polish';
import { SignalBoard } from './signals.js?v=2.5.1-options-first-hybrid';
import { WatchlistPanel } from './watchlist.js';
import { SectorPanel } from './sectors.js';
import { AnalyticsPanel } from './analytics.js?v=6.4.4-summary-chip-polish';
import { AlertLog } from './alerts.js?v=6.4.4-summary-chip-polish';
import { ChartPanel } from './chart.js';
import { QuickControls } from './quick-controls.js?v=2.4.0-hybrid-scanner';
import { MarketHealthTop } from './market-health-top.js?v=2.4.0-hybrid-scanner';
import { MarketTicker } from './market-ticker.js?v=5.7.0-top-status-gift-nifty';
import { OptionCockpit } from './options.js?v=6.4.4-summary-chip-polish';
import { SectorRibbon } from './sector-ribbon.js?v=6.4.4-summary-chip-polish';
import { WorkbenchTabs } from './workbench-tabs.js?v=2.4.0-hybrid-scanner';
import { ScannerInsight } from './scanner-insight.js?v=6.4.4-summary-chip-polish';
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
import { OptimizerPanel } from './optimizer-panel.js?v=8.0.0-phase-d';
import { AiQueryPanel } from './ai-query-panel.js?v=8.0.0-phase-d';
import { initModeSwitch } from './mode-switch.js?v=8.0.0-new-shell';

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
